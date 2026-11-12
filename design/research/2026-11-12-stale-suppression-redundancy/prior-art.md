---
topic: "How linters handle redundant and non-enabled suppression comments"
date: 2026-11-12
status: Draft
---

# Prior Art: Redundant and Non-Enabled Suppression Comments

## The Problem

HSL901 (spec 015) reports stale pragmas. Review of PR #93 converged on two of its mechanisms:

1. **Redundant overlap.** A pragma is called `redundant` when another pragma for the same rule already suppresses all its findings. `_redundant_claims` groups claims by shared target or shared finding, picks a survivor per group by `_survival_key`, and flags claims whose findings are a subset of the survivor's. It has been patched at every gate (nesting invariant, subset guard, docs wording), and the PR round found it quadratic in findings: identical `ignore-file` pragmas build one identical group per finding.
2. **Project-rule pragmas.** A pragma naming a project rule (HSL104) is never called unused or redundant (spec 010 D9), but is still reported as naming a disabled rule. Projects that run HSL104 only in pre-push must also run HSL901 only there, and `docs/rules.md` wrongly says such pragmas are never reported.

## How We Do It Today

`suppressions.apply_suppressions` lets every claim suppress, then `_unused_and_redundant_reports` reports claims with no candidates as unused and `_redundant_claims`'s set as redundant. `scan_pragmas` emits `unused suppression for disabled rule X` for any pragma naming a rule not enabled for the file, project rules included.

## Patterns Found

### Pattern 1: Credit-one-owner with fixed precedence (no subset check)

**Used by**: Ruff, ESLint, Pylint
**How it works**: Each suppressed finding is credited to exactly one directive, chosen by a fixed rule at suppression time. Anything never credited is "unused". Redundant directives fall out as unused with no separate pass. Ruff: file-level, then range, then line, and within range suppressions first match wins. ESLint: block pass before line pass, and the last applicable block directive wins within a pass. Pylint: one `(msgid, line) -> origin` entry, overwritten by each later disable.
Reproduced: Ruff `# ruff: noqa: F401` + `import os  # noqa: F401` flags the line `noqa` as `unused: F401`. ESLint flags the earlier of two identical block disables and any line directive under a block disable. Pylint flags a module-level `disable` when a line-level `disable` for the same message also covers the finding (and the first of two identical module-level disables).
**Strengths**: Linear in findings; no group or survivor logic; one place defines which directive "owns" a finding; fixing by deleting the unused one is always safe because the owner still covers it.
**Weaknesses**: Which directive is reported depends on the precedence order, not on "the redundant one is the narrower one". Pylint reports the broader module-level pragma when a line-level one exists (the opposite of "line-level is redundant"), and ESLint reports the earlier of two equal block disables. Both are order artifacts, not a considered choice.
**Example**: https://github.com/eslint/eslint/blob/34a8b1b978f0e06d595822173a3c782c5ac661ba/lib/linter/apply-disable-directives.js#L359-L365
**Read**: Ruff `checkers/noqa.rs` + `suppression.rs` (opened); ESLint `apply-disable-directives.js` (opened, plus run); Pylint `utils/file_state.py` (opened, plus run on q/q2/q3 samples).

### Pattern 2: Judge a pragma only when its rule ran (or report non-enabled as a separate, opt-out category)

**Used by**: ESLint (partial), Pylint (partial), Ruff (the opposite, flagged as a problem), mypy (opposite)
**How it works**: ESLint builds `rulesToIgnore` from the `ruleFilter` (rules configured on but not executed this run) and exempts their directives, plus all blanket directives when any rule was filtered. Rules configured "off" are NOT exempt (reproduced). Pylint reports a disabled message's pragma as useless in my runs (`--disable=all` and `--disable=fixme`), and its maintainers called the resulting false positives a design mistake (#3512). Ruff and mypy report non-enabled/disabled-code pragmas as unused by design; Ruff labels them "non-enabled", and issue 8492 (open, needs-decision) asks for a split so a narrowed run can skip them.
**Strengths**: ESLint's filter avoids false removals in subset runs. Ruff's labeled bucket lets the reader see why.
**Weaknesses**: Ruff's design makes `--select RUF100 --fix` destructive (reproduced: with `--select RUF100` alone, `F401` file-level and line pragmas all come back "non-enabled"). ESLint's exemption exists only for the programmatic `ruleFilter`, not for "rule off in config".
**Example**: https://github.com/eslint/eslint/blob/34a8b1b978f0e06d595822173a3c782c5ac661ba/lib/linter/apply-disable-directives.js#L540-L564 ; https://github.com/astral-sh/ruff/issues/8492
**Read**: ESLint (opened + run), Ruff `checkers/noqa.rs` lines 186-215 (opened + run with `--select RUF100`), Pylint (run; `pylinter.py` `_add_one_message`/`prepare_checkers` opened), mypy `errors.py` (opened). Pylint `prepare_checkers` skipping checkers with no enabled messages is my explanation for the disabled-message result; I did not trace it further.

### Pattern 3: Exempt cross-file or context-dependent rules from the unused check

**Used by**: Pylint (hard-coded set), Ruff (`external` for foreign codes, unknown codes skipped)
**How it works**: Pylint keeps a frozenset of message IDs (`R0801` duplicate-code, `R0401` cyclic-import, deprecated-*) that `iter_spurious_suppression_messages` skips. The commit that added duplicate-code to the set shipped with the test "duplicate-code and useless-suppression work well together" (both enabled, no output). Pylint's own comment gives reasons for the others (incomplete context, multi-interpreter), not for duplicate-code. Note the exemption is on "unused" only: whether a `disable=duplicate-code` pragma is flagged when the rule is off was not separately checked, but in Pylint's flow a never-credited raw disable is the only way to get `useless-suppression`, and the set filters exactly that site.
**Strengths**: One line per rule; no per-file proof needed for a project-scoped finding.
**Weaknesses**: A real stale pragma for these rules is never caught.
**Example**: https://github.com/pylint-dev/pylint/blob/6187a9918b93153ae71ae4def042f5e35952770b/pylint/constants.py#L108-L124
**Read**: Pylint `constants.py`, `file_state.py` (opened); test `tests/test_similar.py::test_useless_suppression` (opened).

### Pattern 4: Unused-only, no overlap analysis

**Used by**: mypy, flake8-noqa
**How it works**: Per-line used-code sets (mypy) or per-comment matched-code sets (flake8-noqa). File-level directives are skipped entirely (mypy `ignored_files`) or only syntax-checked (flake8-noqa NQA011-013). Duplicate codes are caught only inside one comment (NQA005; Ruff's `duplicated`).
**Strengths**: Trivial; no ordering questions.
**Weaknesses**: A file-level plus line-level pair is never reported.
**Example**: https://github.com/python/mypy/blob/0a2a29160771a99bca0729fd53f4df25dec7c9de/mypy/errors.py#L908-L938
**Read**: mypy `errors.py` (opened); flake8-noqa README, `noqa_filter.py` (opened).

## Anti-Patterns

- **Reporting pragmas for rules the run did not enable, with an autofix** (Ruff RUF100 `--select` narrowing). Evidence: issue 8492 comments (mcdigman: "will remove all statements like `noqa: ANN001` from the entire repo"; Quexington: `--select RUF100 --fix` removes both noqas); reproduced locally. Open and `needs-decision` as of today.
- **No stated reason for an exemption.** Pylint's `R0801` entry has no comment explaining it, so the next maintainer cannot tell whether it is a cross-file limitation or a test workaround.

## Gaps

- No peer found with subset-based or group-and-pick-survivor redundancy detection; all three that detect overlap do it by crediting one owner. If one exists (e.g. in Biome, oxlint, RuboCop `Lint/RedundantCopDisableDirective`, or golangci-lint `nolintlint`) I did not look; the brief scope was the five named projects. RuboCop's redundant-disable cop handles overlapping disables and is the most likely other precedent.
- Pylint disabled-message behavior: I ran it (flagged) but did not trace why in code beyond reading `_add_one_message` and `prepare_checkers`; the "checker skipped" explanation is inferred.
- Pylint commit 52b8898 has no written rationale for exempting duplicate-code; I did not find a linked issue (`gh search issues` returned nothing).
- mypy and flake8-noqa were read, not run.
- ESLint was run on 9.39.5 from npm; the clone is main at 34a8b1b. The applyDirectives logic read matches the observed output, but I did not diff the two versions.
- Ruff behavior with `# ruff: ignore[...]` range suppressions was read from code only (`suppression.rs`), not run; the `# noqa` / `# ruff: noqa` behavior was run.

## Relevance to Us

No peer has a subset check or overlap groups. Ruff, ESLint and Pylint all **credit each finding to exactly one directive** by a fixed precedence and report any directive never credited. That is linear in findings, and deleting an uncredited directive is safe by construction: every finding it could have suppressed is credited to another directive that stays. Our survivor-plus-subset machinery buys the same guarantee with more code, and the quadratic cost comes from the grouping itself.

The credit model fits house-lint with no change to what gets suppressed: every claim still suppresses (spec 015's all-suppress decision). Only attribution changes. Credit each candidate to its lowest-`_survival_key` owner, computed once per claim. A claim credited nothing is reported: `redundant` if it owned candidates, `unused` if it owned none. Under today's nested actions this reports the same claims as now. Two empty claims sharing a target both become `unused` instead of one `redundant`, which is the more accurate message anyway.

On non-enabled rules, Ruff reports them even under `--select` narrowing, and its own issue #8492 (open, `needs-decision`) describes the exact friction #91 fixed. ESLint exempts rules its filter skipped. Pylint exempts `duplicate-code` from `useless-suppression` entirely and recommends running it in CI or pre-push, which is the HSL104 split. Our half-exemption sits between Pylint and Ruff and inherits the friction of both.

## Recommendation

1. **Replace `_redundant_claims` with one-owner credit.** Credit each candidate to its best-ranked owning claim and report the claims credited nothing. This removes the groups, the survivor and the subset guard, fixes the quadratic cost, and keeps the safe-to-delete guarantee. Keep the `redundant`/`unused` wording split, since it costs one length check.
2. **Exempt project rules from HSL901 entirely, following Pylint.** Don't report a pragma naming a project rule as naming a disabled rule either. That makes the docs sentence true and drops the README's HSL104 hook advice. The cost is missing an `ignore[HSL104]` in a project that never runs HSL104, which is rare and harmless.

The alternative to either is accepting the limitation: dedupe the groups (CodeRabbit's one-liner) and fix the docs sentence. That leaves both mechanisms more complex than any peer's.

Coverage: five peers, all read in code; Ruff, Pylint and ESLint also run. Not surveyed: RuboCop, golangci-lint `nolintlint`, Biome, oxlint.

## Sources

Search tool: `exa-search` (worked, no fallback needed). All source reads were of shallow clones at the SHAs below; behavior claims marked "ran" were reproduced with the tool versions noted (scratchpad `t/` and `esl/` dirs).

| Project | SHA read | Version run |
|---|---|---|
| Ruff | c91de8a012cb87efe279b762020a0d28dd75ddb5 | 0.17.4 |
| Pylint | 6187a9918b93153ae71ae4def042f5e35952770b | 4.1.3 |
| ESLint | 34a8b1b978f0e06d595822173a3c782c5ac661ba | 9.39.5 (npm) |
| mypy | 0a2a29160771a99bca0729fd53f4df25dec7c9de | not run |
| flake8-noqa | 902dcdec541382d6a1a78a1603abd8d51446a002 | not run |

### Ruff: noqa checker (RUF100 driver)
- **URL**: https://github.com/astral-sh/ruff/blob/c91de8a012cb87efe279b762020a0d28dd75ddb5/crates/ruff_linter/src/checkers/noqa.rs (lines 59-95 waterfall, 123-260 unused logic)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: Diagnostics are suppressed by a fixed-precedence waterfall: file-level first (`continue` on match), then range suppressions, then end-of-line `noqa` ("Apply file-level suppressions first / ranged next / end-of-line last"). A line `noqa` therefore never sees a diagnostic a file-level directive already absorbed, so it ends up unused. No subset or overlap computation exists.
- **Relevance**: Overlap detection comes for free from ordering plus "unmatched = unused".

### Ruff: UnusedNOQA violation and range-suppression check
- **URL**: https://github.com/astral-sh/ruff/blob/c91de8a012cb87efe279b762020a0d28dd75ddb5/crates/ruff_linter/src/suppression.rs (check_suppression ~382-396; check_suppressions ~472-575)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: For `# ruff: ignore/disable[...]` range suppressions, `check_suppression` returns on the first matching suppression and sets `used` only on that one. A second overlapping range suppression for the same rule is left unused and reported. The unused report has three buckets (`unused`, `non-enabled`, `duplicated`); "non-enabled" is `Rule::from_code(code)` succeeds but `!context.is_rule_enabled(rule)`.
- **Relevance**: Same first-match-wins credit design; "non-enabled" is a deliberate, reported category.

### Ruff: unused_noqa.rs docs
- **URL**: https://github.com/astral-sh/ruff/blob/c91de8a012cb87efe279b762020a0d28dd75ddb5/crates/ruff_linter/src/rules/ruff/rules/unused_noqa.rs
- **Type**: reference implementation + rule docs
- **Opened**: yes
- **Key takeaway**: Message builder confirms the `unused:` / `non-enabled:` / `duplicated:` split. Codes unknown to Ruff are ignored (that is RUF102's job) and the `external` setting lists codes owned by other linters.
- **Relevance**: Unknown-vs-disabled-vs-unused are three separate states.

### Ruff issue 8492: separate rule for unknown/disabled noqa codes
- **URL**: https://github.com/astral-sh/ruff/issues/8492
- **Type**: issue (open, label `needs-decision`)
- **Opened**: yes (body and all 5 comments)
- **Key takeaway**: Users hit exactly house-lint's problem: RUF100's "non-enabled" check makes `ruff check --select RUF100 --fix` (or a narrowed `--select`) strip `noqa`s for rules the run simply didn't enable. Proposed fix: split into `RUF100-unused` (only rules Ruff actually checked) and a separate non-enabled/unknown rule. Not implemented as of this read.
- **Relevance**: Direct evidence that peer behavior (c) is a known pain point, and the proposed split is "only judge pragmas whose rule ran".

### Ruff issue 2194 and 2424
- **URL**: https://github.com/astral-sh/ruff/issues/2194 ; https://github.com/astral-sh/ruff/issues/2424
- **Type**: issues (closed)
- **Opened**: yes
- **Key takeaway**: Maintainer answer was a workaround, not a behavior change: use the `external` setting and run `--extend-select RUF100` on top of the standard config so the run enforces the normal selection.
- **Relevance**: Ruff's stance is "run RUF100 with your full config"; users find that insufficient for narrowed runs.

### Pylint: FileState (useless-suppression bookkeeping)
- **URL**: https://github.com/pylint-dev/pylint/blob/6187a9918b93153ae71ae4def042f5e35952770b/pylint/utils/file_state.py (`_set_message_state_on_line` 166-176; `handle_ignored_message` 209-228; `iter_spurious_suppression_messages` 230-256)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: `_suppression_mapping[(msgid, line)]` holds exactly one originating pragma line per (message, line); each disable overwrites it. When a message is suppressed, only that origin is credited (`_ignored_msgs[(msgid, orig_line)]`). Any raw disable never credited is `useless-suppression`. Overlap detection is the single-origin map, not a subset check.
- **Relevance**: Last-writer-wins crediting, same shape as ESLint and Ruff.

### Pylint: INCOMPATIBLE_WITH_USELESS_SUPPRESSION
- **URL**: https://github.com/pylint-dev/pylint/blob/6187a9918b93153ae71ae4def042f5e35952770b/pylint/constants.py (lines 108-124)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: Hard-coded exemption set: `R0401` cyclic-import, `W0402`/`W1505`/`W1511`-`W1513` deprecated-*, and `R0801` duplicate-code. The comment gives reasons only for cyclic-import ("incomplete context") and deprecated-* ("multi-interpreter projects"); it gives none for duplicate-code.
- **Relevance**: Direct precedent for exempting cross-file rules (question 3).

### Pylint commit 52b8898 "Do not report useless-suppression on duplicate-code"
- **URL**: https://github.com/pylint-dev/pylint/compare/v2.13.9...v2.14.0 (commit 52b8898, 2022-05-05; touches constants.py plus `tests/test_similar.py::test_useless_suppression` and `tests/regrtest_data/duplicate_code/useless_suppression/`)
- **Type**: changelog / commit
- **Opened**: yes (commit file list and the test; commit message has no rationale)
- **Key takeaway**: Shipped together with enabling duplicate-code by default (#6121). The test enables both rules, puts `# pylint: disable=duplicate-code` at the top of two files, and asserts no messages. The stated reason is not in the commit; the plausible one (a `disable=duplicate-code` can't be proven used per-file) is my inference [no source found].
- **Relevance**: Pylint chose "never call it useless" for the cross-file rule, same as house-lint's current stance for unused.

### Pylint issue 3512 discussion
- **URL**: https://github.com/pylint-dev/pylint/issues/3512
- **Type**: issue discussion
- **Opened**: search snippet only
- **Key takeaway**: Maintainer quote: "What made `no-self-use` so painful was that we didn't think through the effect on `useless-suppression`... `# pylint: disable=fixme` is not really useless if the check would have run but for the default state... Useless suppression is supposed to help clean up your code base after pylint fixes false positives or you fix your code."
- **Relevance**: Pylint maintainers regard flagging a pragma for a rule the run didn't enable as a design hazard.

### Pylint docs: message_control.rst, faq.rst
- **URL**: https://github.com/pylint-dev/pylint/blob/6187a9918b93153ae71ae4def042f5e35952770b/doc/user_guide/messages/message_control.rst ("Detecting useless disables", ~line 199) ; .../doc/faq.rst (~line 85)
- **Type**: documentation
- **Opened**: yes
- **Key takeaway**: `useless-suppression` is opt-in. FAQ recommends disabling whole-codebase checks like `duplicate-code` for fast runs and running them in CI or a `pre-push` hook.
- **Relevance**: Pylint ships the same pre-push split house-lint users have.

### ESLint: apply-disable-directives.js
- **URL**: https://github.com/eslint/eslint/blob/34a8b1b978f0e06d595822173a3c782c5ac661ba/lib/linter/apply-disable-directives.js (lines 340-367 credit logic; 543-564 rulesToIgnore; 566-585 two passes)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: Two sequential passes: block directives (`disable`/`enable`) over all problems, then line directives (`disable-line`/`disable-next-line`) over the output. In a pass, each problem credits only the last applicable disable directive (`usedDisableDirectives.add(disableDirectivesForProblem.at(-1))`). A problem already suppressed by the block pass gets `suppressions` concatenated but its line directive is not credited (`if (problem.suppressions) {concat} else {credit}`). `rulesToIgnore` (rules skipped by a `ruleFilter`; plus `null` for blanket directives) exempts directives for rules that did not run.
- **Relevance**: The closest analogue to the redundancy question, and it needs no subset logic.

### ESLint run (behavioral check)
- **URL**: local run, eslint 9.39.5, `reportUnusedDisableDirectives: "error"`
- **Type**: experiment
- **Opened**: ran
- **Key takeaway**: With `/* eslint-disable no-console */` twice, then `console.log(1); // eslint-disable-line no-console`: reported unused were the FIRST block disable (line 1) and the line directive (3:17); the second block disable was credited. A `disable-next-line no-debugger` for a rule configured `"off"` was reported unused.
- **Relevance**: Confirms "last block wins, line loses to block" and that a configured-off rule is judged, not exempted.

### mypy: Errors.generate_unused_ignore_errors
- **URL**: https://github.com/python/mypy/blob/0a2a29160771a99bca0729fd53f4df25dec7c9de/mypy/errors.py (add_error_info ~765-795; generate_unused_ignore_errors 908-938)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: Per-line only. `used_ignored_lines[file][line]` records codes; unused codes are reported (`c in codes.error_codes`, so foreign codes are skipped). If the error code is disabled, the `type: ignore` is deliberately not marked used (code comment: "Error code is disabled - don't mark the current 'type: ignore' comment as used"), so it is reported unused. Files in `ignored_files` (`# mypy: ignore-errors`) are skipped entirely. No cross-comment overlap logic.
- **Relevance**: mypy reports a pragma for a disabled rule, and has no redundancy machinery.

### flake8-noqa
- **URL**: https://github.com/plinss/flake8-noqa/blob/902dcdec541382d6a1a78a1603abd8d51446a002/README.md (error table, lines 104-115) and flake8_noqa/noqa_filter.py (~150-175)
- **Type**: reference implementation
- **Opened**: yes
- **Key takeaway**: NQA101-103 cover line `# noqa` only (blanket with no violations; no matching codes; some unmatched codes). NQA005 flags duplicate codes inside one comment. File-level `# flake8: noqa` gets only syntax checks (NQA011-013), never unused checks. No overlap detection, no handling of non-enabled codes beyond "no violation matched".
- **Relevance**: The minimal baseline: unused plus within-comment duplicates.
