---
topic: "Python linter implementation, configuration, and escape hatches"
date: 2026-08-19
status: Draft
---

# Prior Art: Python Linter Implementation, Configuration, and Escape Hatches

## The Problem

Building a custom Python linter means making foundational choices about how checks are implemented (AST visitors, regex, declarative patterns), how configuration is discovered and resolved (file formats, gitignore, inheritance), and how users can suppress or override findings (inline comments, baselines, per-rule severity). These choices are hard to change later and determine how the tool scales, integrates with editors/CI, and gets adopted by existing codebases with pre-existing violations.

## How We Do It Today

house-lint uses a closed rule set registered in two parallel `MappingProxyType` dicts keyed by rule ID (`HSL001`-`HSL004` always-on, `HSL101`-`HSL103` opt-in, `HSL900` for suppression diagnostics). Each detector is a function conforming to a `Detector` protocol, lazy-imported from `src/house_lint/rules/` on first dispatch. Configuration is strict TOML via `[tool.house-lint]` in `pyproject.toml`, with `_strict_keys()` rejecting any unrecognized key. Rule selection uses `select`/`ignore` in config plus `--select`/`--ignore` CLI flags resolved by a shared `_effective_rule_selection()` function. Discovery walks up from cwd looking for `pyproject.toml` with `[tool.house-lint]`, falling back to `.git` or cwd. Gitignore support reads root `.gitignore` only. Inline suppression uses `# house-lint: {ignore|ignore-next|ignore-file}[RULE1,RULE2] - reason` with mandatory reasons, and `HSL900` audits suppression validity (unused, malformed, conflicting). No plugin system, no baseline/ratchet mechanism, no autofix capability.

## Patterns Found

### Pattern 1: Single-parse, multi-rule dispatch via visitor

**Used by**: Ruff, Pylint, stdlib-`ast`-based flake8 plugins.
**How it works**: The file is parsed into a tree once. A single traversal dispatches to every rule registered for each node type. Rules are small functions receiving the node plus context (scope, imports, parent chain) and emitting diagnostics. Both Ruff and Pylint converge on this despite different languages and rule catalogs, because re-parsing per-rule (flake8's federated model) is the dominant cost at scale.
**Strengths**: Scales to hundreds of rules without proportional parse cost; rule authoring stays simple; new rules don't touch the traversal engine.
**Weaknesses**: Rules needing cross-node or whole-file context require explicit state threading; a badly-behaved rule can be harder to isolate than an independent checker.
**Example**: https://deepwiki.com/astral-sh/ruff/9.4-adding-lint-rules

### Pattern 2: Federated / plugin-of-plugins architecture

**Used by**: Flake8 (wrapping pycodestyle, pyflakes, mccabe, and third-party plugins via `entry_points`).
**How it works**: The host tool defines a thin plugin protocol and lets independently-maintained packages implement checkers. The host owns configuration, CLI, and reporting; each plugin owns its checking logic and may use different tree representations.
**Strengths**: Low coupling; plugins versioned independently; marketplace of community rule packs.
**Weaknesses**: Performance suffers from re-parsing per plugin; inconsistent error formats and autofix support across plugins.
**Example**: https://flake8.pycqa.org/en/latest/internal/plugin_handling.html

### Pattern 3: Declarative pattern-matching rules (Semgrep)

**Used by**: Semgrep (YAML rules with `pattern`/metavariables).
**How it works**: Rule authors write example code snippets with metavariables (`$X`, `$FUNC(...)`) that match structurally against the parsed AST. The engine owns traversal; rule authors need no AST expertise. A tokenizer fallback ("spacegrep") handles unparseable files.
**Strengths**: Dramatically lowers the bar for new rules; rules read almost like the code they match.
**Weaknesses**: Less expressive for semantic analysis, type inference, or cross-file resolution.
**Example**: https://semgrep.dev/docs/writing-rules/generic-pattern-matching

### Pattern 4: Hierarchical closest-config-wins discovery with gitignore-aware walking

**Used by**: Ruff (closest `pyproject.toml`/`ruff.toml` with tool section wins; relative paths resolve against config file's directory).
**How it works**: For each file, configuration comes from the nearest ancestor config file containing the tool's section. Files without the section are skipped. File discovery respects `.gitignore`, `.ignore`, and `.git/info/exclude` by default, with opt-out.
**Strengths**: Monorepo-safe without per-invocation flag gymnastics; gitignore-aware discovery excludes venvs/build artifacts automatically.
**Weaknesses**: Explicit CLI paths bypass directory-walk excludes (documented edge case in Ruff issue #9585).
**Example**: https://docs.astral.sh/ruff/configuration/

### Pattern 5: Specificity-ranked select/ignore rule resolution

**Used by**: Ruff (`select`/`ignore`/`extend-select`/`extend-ignore` with prefix-specificity tie-breaking).
**How it works**: Rules use hierarchical code prefixes (e.g., `E`, `E5`, `E501`). When layers disagree, the more specific prefix wins; at equal specificity, `ignore` wins. `extend-select`/`extend-ignore` add to inherited sets rather than replacing them.
**Strengths**: Deterministic, documented algorithm; supports broad category enablement with narrow carve-outs.
**Weaknesses**: Not obvious at a glance; requires naming discipline in rule code hierarchy.
**Example**: https://docs.astral.sh/ruff/settings/

### Pattern 6: Inline suppression with self-auditing (unused-noqa detection)

**Used by**: Flake8/Ruff (`# noqa: CODE`), Pylint (`# pylint: disable=rule`), Ruff's RUF100 (flags unused suppressions).
**How it works**: Trailing or dedicated comments name specific rule codes to suppress. Modern tools require specific codes over bare blanket suppression. A second-order lint rule flags suppressions that no longer suppress anything, preventing suppression rot.
**Strengths**: Zero-infrastructure, per-code scoping is self-documenting, self-auditing closes the main long-term failure mode.
**Weaknesses**: Doesn't help with large pre-existing codebases needing thousands of suppressions; multiple tools sharing a line's comments can conflict.
**Example**: https://deepwiki.com/astral-sh/ruff/3.5-suppression-system

### Pattern 7: Baseline / ratchet mechanisms for legacy-codebase adoption

**Used by**: Generic pattern (qntm's canonical writeup, Notion's custom ESLint ratcheting). Ruff has an open feature request (astral-sh/ruff#5391).
**How it works**: A snapshot of current violations (or per-file-per-rule counts) is captured and committed. The tool only reports violations not in the baseline (snapshot model) or fails when counts increase (ratchet model). The ratchet enforces gradual cleanup: each fix permanently lowers the ceiling.
**Strengths**: Enables strict linting immediately on large codebases without a "fix everything first" migration; ratchet variant drives active cleanup.
**Weaknesses**: Adds infrastructure (baseline file maintenance); pure baseline without ratcheting can become "linting theater."
**Example**: https://qntm.org/ratchet, https://www.notion.com/blog/how-we-evolved-our-code-notions-ratcheting-system-using-custom-eslint-rules

### Pattern 8: CST for lossless/autofix-capable tooling

**Used by**: LibCST (Instagram), Fixit, ufmt.
**How it works**: Parse to a concrete syntax tree retaining all tokens (comments, whitespace) while exposing an AST-like API. Enables safe automated rewrites preserving formatting.
**Strengths**: Enables reliable autofix/codemods without a formatter re-run; still feels like AST for rule authors.
**Weaknesses**: Heavier dependency; only pays for itself if autofix or comment-aware rules are needed.
**Example**: https://libcst.readthedocs.io/en/latest/why_libcst.html

### Pattern 9: Domain-specific checklist/exemption system (HA Quality Scale)

**Used by**: Home Assistant Core (`quality_scale.yaml` + `hassfest`).
**How it works**: A sidecar YAML file per integration names required rules for a quality tier (Bronze/Silver/Gold/Platinum) and marks rules as not-applicable with a stated reason. Validated by `hassfest` rather than a general-purpose linter.
**Strengths**: Forces explicit, reviewable reasons for exemptions; ties into maturity/tier model.
**Weaknesses**: Requires bespoke validation tooling; only fits artifact-level concerns, not per-line issues.
**Example**: https://www.home-assistant.io/docs/quality_scale/

## Anti-Patterns

- **Excluding by directory-walk but still linting on explicit CLI paths.** Ruff's `exclude` only affects discovery walks; explicit paths (as from pre-commit) bypass it. House-lint should make a deliberate, documented decision here. (https://github.com/astral-sh/ruff/issues/9585)

- **Bare `# noqa` with no code.** Silences every rule on a line, swallowing future unrelated violations. Convention across tools: always require specific codes. (https://deepwiki.com/astral-sh/ruff/3.5-suppression-system)

- **Baseline without ratcheting ("linting theater").** A snapshot that never shrinks is permanent debt. The ratchet (count can only go down, enforced in CI) is the documented fix. (https://qntm.org/ratchet)

- **Re-parsing per checker/plugin.** Flake8's federated model causes proportional slowdown per plugin. Single-parse multi-rule dispatch avoids this. (https://compileralchemy.substack.com/p/ruff-internals-of-a-rust-backed-python)

## Relevance to Us

House-lint already follows several best practices:
- **Single-dispatch pattern**: While not using a formal AST visitor, the detector protocol + registry approach is conceptually similar to Pattern 1. Each rule function receives parsed source and emits findings.
- **Strict TOML config**: Rejecting unknown keys is stricter than Ruff (which silently ignores them) — this is a feature, not a gap.
- **Self-auditing suppression**: HSL900 already implements the RUF100 pattern (flagging unused/malformed suppressions), with the additional requirement of mandatory reasons — stricter than any surveyed tool.
- **Gitignore support**: Already present, though limited to root `.gitignore` only (Ruff reads nested `.gitignore` files too).

Gaps worth considering:
- **No baseline/ratchet mechanism**: If house-lint is adopted by existing HA custom component codebases with pre-existing violations, a "fix everything first" requirement will block adoption. Ruff itself doesn't have this yet (open issue #5391), so house-lint would be filling a real gap.
- **No `extend-select`/`extend-ignore`**: The current `select`/`ignore` model replaces rather than extends. As the rule catalog grows, additive modifiers become important for config inheritance.
- **No hierarchical config discovery**: Single-root-per-process currently. Not a problem for house-lint's current audience but would block monorepo use cases.
- **Root `.gitignore` only**: Nested `.gitignore` files are ignored, which could cause false positives in repos with vendored or generated code in subdirectories.

## Recommendation

1. **Keep the current single-parse, closed-rule-set architecture.** House-lint doesn't need a plugin ecosystem yet. The Detector protocol is clean enough to open later if needed.

2. **Consider adding `extend-select`/`extend-ignore`** as the rule catalog grows. Ruff's specificity-ranked resolution is well-documented and worth studying, but may be overkill for house-lint's current 7-rule set.

3. **Prioritize a baseline/ratchet mechanism** if adoption by existing codebases is a goal. The snapshot-diff approach (simpler) is a good starting point; the full ratchet (per-file-per-rule counts, CI enforcement) can come later. This would be a genuine differentiator — Ruff doesn't have it.

4. **Extend gitignore support to nested `.gitignore` files.** The `pathspec` library already handles this; it's mostly a discovery-walk change.

5. **No need for CST/LibCST now.** Autofix is not on the roadmap, and the current rules don't need comment-awareness beyond what suppression parsing already does.

6. **The HA Quality Scale model is interesting** for house-lint's domain-specific rules but is a different layer than inline suppression — worth noting as a potential future feature, not an immediate need.

## Sources

Note: these URLs were not live-verified.

### Reference implementations
- https://github.com/PyCQA/flake8 — Flake8 core (federated plugin model)
- https://github.com/astral-sh/ruff — Ruff (single-parse, multi-rule Rust linter)
- https://deepwiki.com/astral-sh/ruff/9.4-adding-lint-rules — Ruff rule registration internals
- https://deepwiki.com/astral-sh/ruff/3.5-suppression-system — Ruff suppression system
- https://github.com/astral-sh/ruff/issues/5391 — Ruff baseline feature request (open)

### Blog posts & writeups
- https://compileralchemy.substack.com/p/ruff-internals-of-a-rust-backed-python — Ruff internals
- https://medium.com/@vasschiavo/the-evolution-of-ruffs-parser-77f2a83f4838 — Ruff parser evolution
- http://atodorov.org/blog/2018/01/05/how-to-write-pylint-checker-plugins/ — Pylint plugin loading
- https://stummjr.github.io/post/building-a-custom-flake8-plugin/ — Custom flake8 plugin tutorial
- https://instagram-engineering.com/static-analysis-at-scale-an-instagram-story-8f498ab71a0c — Instagram/LibCST at scale
- https://qntm.org/ratchet — Canonical ratchet pattern writeup
- https://www.notion.com/blog/how-we-evolved-our-code-notions-ratcheting-system-using-custom-eslint-rules — Notion's ESLint ratcheting
- https://medium.com/@SaezChristopher/5-reasons-why-you-should-use-a-linter-baseline-on-your-project-b52a523ae3ce — Linter baseline rationale
- https://grokipedia.com/page/Semgrep — Semgrep overview

### Documentation & standards
- https://docs.astral.sh/ruff/rules/ — Ruff rules catalog
- https://docs.astral.sh/ruff/configuration/ — Ruff configuration
- https://docs.astral.sh/ruff/settings/ — Ruff settings reference
- https://docs.astral.sh/ruff/rules/unused-noqa/ — RUF100 unused noqa
- https://pylint.readthedocs.io/en/stable/development_guide/how_tos/custom_checkers.html — Pylint checker authoring
- https://pylint.readthedocs.io/en/stable/user_guide/usage/run.html — Pylint config discovery
- https://flake8.pycqa.org/en/latest/internal/plugin_handling.html — Flake8 plugin handling
- https://semgrep.dev/docs/writing-rules/generic-pattern-matching — Semgrep generic patterns
- https://libcst.readthedocs.io/en/latest/why_libcst.html — LibCST rationale
- https://www.home-assistant.io/docs/quality_scale/ — HA Integration Quality Scale
- https://developers.home-assistant.io/blog/2020/04/16/hassfest/ — Hassfest for custom components
- https://github.com/astral-sh/ruff/issues/9585 — Ruff exclude vs explicit paths edge case
