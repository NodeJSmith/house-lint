---
topic: "gitignore-style exclusion/inclusion in linters and file-walking tools"
date: 2026-08-20
status: Draft
---

# Prior Art: gitignore-style exclusion/inclusion

## The Problem

Any tool that walks a source tree has to decide which files to skip, and the de facto
contract is "behave like `.gitignore`." That contract is much harder than it looks: patterns
are relative to the `.gitignore` file that declares them, deeper files override shallower
ones, the last matching line wins within a tier, trailing slashes make a pattern
directory-only, and — the load-bearing rule — **a negation can never re-include a file whose
parent directory was excluded**, because git never lists an excluded directory in the first
place.

That last rule is not a quirk of the pattern language. It is a consequence of *how* git
matches: directory-by-directory, during traversal, with the walker supplying ground truth for
"is this entry a directory." A matcher that flattens every pattern into one root-anchored set
and matches full path strings has no way to express "this directory was never entered," and so
cannot reproduce the rule. Tools discover this the hard way, repeatedly.

## How We Do It Today

house-lint is a **hybrid**: it prunes directories during `os.walk` (traversal model) but
decides membership by rewriting every nested pattern to be root-anchored and matching the full
relative path against one combined `pathspec.GitIgnoreSpec` (flattened model). Everything
except glob-to-regex compilation is hand-rolled in `_FileSelector`. `docs/configuration.md`
already documents one deliberate divergence (`!sub/` re-includes everything beneath it) and
attributes it precisely: `pathspec` "compiles every pattern as a prefix search and so cannot
distinguish 'this pattern matched this entry' from 'it matched an ancestor'."

The two halves of the hybrid are where the bugs live — walk-time pruning and flattened
matching have to agree about ancestor exclusion, and `_combined_gitignore_spec`'s
short-circuit to `IGNORE_EVERYTHING` is the seam holding them together.

## Patterns Found

### Pattern 1: Per-directory matcher stack, evaluated during traversal

**Used by**: git itself (`dir.c`, `git ls-files --exclude-per-directory`), ripgrep's `ignore`
crate, fd, ruff (transitively), `gitignorefile` (Python — claimed, unverified).

**How it works**: The walker descends one level at a time. At each directory it loads (or
reuses a cached) matcher built from *that directory's own* `.gitignore` plus inherited parent
matchers, and asks "is this specific entry — file or directory — ignored, whitelisted, or
unmatched?" A directory that is excluded is never entered, so its contents are never asked
about. That is exactly why the man page says re-inclusion under an excluded parent is
impossible. The `ignore` crate's `matched_stripped` walks candidate matches **in reverse**
(last pattern wins) and accepts a directory-only glob as the deciding match only when the
caller has passed a real `is_dir` flag derived from `readdir`/`stat` — never inferred from
pattern text.

**Strengths**: The only model that reproduces git's pruning behaviour and the non-reinclusion
rule, because traversal *is* the source of truth. Supports per-level precedence tiers
naturally. Matchers cache and reuse cleanly as the walker moves between siblings.

**Weaknesses**: Couples matching to the walk — can't be bolted onto a flat file list after the
fact. More moving parts: matcher construction, caching, stack push/pop per directory boundary.
And it is not a correctness guarantee on its own (see ruff #17392 below).

**Example**: https://github.com/BurntSushi/ripgrep/blob/master/crates/ignore/src/gitignore.rs

### Pattern 2: Full-path matching against a flattened, root-anchored pattern set

**Used by**: `pathspec`'s `GitIgnoreSpec`, house-lint today, and the common "read `.gitignore`,
build one `PathSpec`, filter a glob list" recipe.

**How it works**: Every `.gitignore` in the tree is read once, its patterns rewritten to be
root-anchored, and all of them compiled into one ordered spec. Each candidate's full relative
path is matched against that spec with last-match-wins applied globally.

**Strengths**: Simple, single-pass, easy to test in isolation. No coupling to walk order. Works
on an already-known flat file list.

**Weaknesses**: Structurally cannot represent "this directory was pruned, so nothing under it
should be asked about." A negation matching a path *under* an excluded ancestor still flips the
outcome. Directory-vs-file classification is done by inspecting pattern text and priority
rather than asking the filesystem — the root cause `pathspec` #81 identifies. Also prone to
independent anchoring bugs (#93: `foo**/bar` matching `foobar`, bracket expressions).

**Example**: https://github.com/cpburnz/python-pathspec/issues/81

### Pattern 3: Shell out to real git

**Used by**: `git-check-ignore` (PyPI wrapper); available to any tool willing to take the
dependency.

**How it works**: Batch candidate paths through `git check-ignore --stdin -v`, or take git's
own file list via `git ls-files --others --exclude-standard`.

**Strengths**: Perfect fidelity by definition — it *is* git, including edge cases nobody has
found yet.

**Weaknesses**: Requires `git` on `PATH` and the tree to be inside a repository; needs a
fallback outside one. Subprocess overhead versus in-process matching. Submodule and worktree
behaviour needs separate handling [no source found for submodule specifics].

**Example**: https://git-scm.com/docs/git-ls-files

### Pattern 4: Tri-state match result carrying the winning glob

**Used by**: the `ignore` crate (ripgrep, fd, ruff).

**How it works**: Matching returns `None`, `Ignore(T)`, or `Whitelist(T)`, where `T` identifies
*which* glob won. Because the result carries the winning pattern, the caller can interrogate it
— e.g. `is_only_dir()` — before accepting the match, instead of the matcher collapsing
everything to a boolean.

**Strengths**: Lets caller-side rules layer cleanly without re-deriving pattern metadata.
Composes with override/whitelist matchers via the same type.

**Weaknesses**: More API surface; easy to accidentally collapse `Whitelist` into `Ignore`.

**Example**: https://docs.rs/ignore/latest/ignore/enum.Match.html

### Pattern 5: Bespoke, intentionally non-gitignore-compatible semantics

**Used by**: ESLint flat config's `ignores`, `.dockerignore`, GitHub CODEOWNERS.

**How it works**: Define a narrower matching contract "inspired by" gitignore but not
equivalent, and document the difference. ESLint's flat-config patterns anchor to the config
file's directory rather than matching at any depth. Real `.gitignore` parity, when wanted, is
delegated to a separate adapter (`eslint-config-flat-gitignore`, built on `node-ignore`).

**Strengths**: Sidesteps the entire class of fidelity bugs by not claiming fidelity. Matching
engine only needs to be internally consistent and documented.

**Weaknesses**: Surprises users who assume gitignore syntax transfers. Needs an adapter and a
second dependency for anyone who wants real parity.

**Example**: https://eslint.org/docs/latest/use/configure/ignore

## Anti-Patterns

- **Classifying directory-vs-file patterns from pattern text instead of asking the
  filesystem.** `pathspec` #81 traces its divergence to an internal priority scheme that ranks
  "directory patterns" below "file patterns" purely from parsing the glob string, rather than
  carrying an `is_only_dir()` flag and letting the caller — which knows from `readdir` — decide.
  https://github.com/cpburnz/python-pathspec/issues/81
- **Flattening all `.gitignore` files into one root-anchored set.** Structurally incapable of
  representing the non-reinclusion rule, which is why that exact bug has been independently
  rediscovered in at least three unrelated codebases (`pathspec` #81, `node-ignore`'s historical
  `fstream-ignore` fix, `graphify` #882). https://git-scm.com/docs/gitignore
- **Assuming "uses gitignore syntax" means git parity.** For most tools it means only "globs,
  one per line, `#` comments, maybe `!`" — not implicit depth-anchoring, per-directory
  precedence stacking, or the non-reinclusion rule.
  https://nesbitt.io/2026/02/12/the-many-flavors-of-ignore-files.html
- **Trusting the right architecture to be bug-free.** ruff, on the `ignore` crate lineage, still
  has an open bug where `.gitignore` fidelity differs between `ruff check src/foo` and
  `ruff check src/`. Differential testing against real `git check-ignore` remains necessary
  regardless of architecture. https://github.com/astral-sh/ruff/issues/17392

## Relevance to Us

Three findings land directly on house-lint's situation.

**1. The documented divergence is not a house-lint quirk — it is the defining weakness of
Pattern 2.** `docs/configuration.md`'s explanation ("compiles every pattern as a prefix search
and so cannot distinguish 'this pattern matched this entry' from 'it matched an ancestor'") is
almost verbatim the root cause `pathspec` #81 identifies. The newly-found under-linting case is
the same family. Continuing to patch individual manifestations is treating symptoms of an
architectural choice.

**2. house-lint is already halfway to Pattern 1, and the bugs live in the seam.** It already
prunes during `os.walk`, already passes an explicit `is_dir` into `_ignored` (which the survey
identifies as the correct discipline), and already has ancestor-exclusion short-circuiting. What
it does not have is *per-level* matching — it flattens, then compensates for the flattening with
`IGNORE_EVERYTHING`. That compensation is where the residual bug almost certainly lives. Moving
to per-level matchers would keep `pathspec` as the glob→regex compiler while removing the
rewrite-and-flatten step (`_prefix_pattern`, `_collapse_double_star_run`,
`_normalize_contents_glob`) that exists only to serve flattening.

**3. ruff #17392 is a near-exact analogue of the `..` bug just fixed** — gitignore fidelity
differing between an explicit subpath and a directory root. Independent confirmation that the
explicit-path-vs-walk-root seam is a recurring hazard, and that the parity/fuzz suites are the
right investment regardless of which architecture wins.

Two constraints narrow the options. `CLAUDE.md` records that file discovery deliberately does
**not** shell out to git, which rules out Pattern 3 without an explicit reversal of that
decision — and Pattern 3 would also break scanning outside a repository. And house-lint's whole
parity apparatus exists to claim git fidelity, so Pattern 5 (drop the claim) would mean deleting
the suites that make house-lint trustworthy here.

## Recommendation

**Do not attempt the Pattern 1 refactor inside this PR.** It is the right end state, but it
rewrites the core of `discovery.py`, and this branch is already 30+ commits deep with two
landed fixes and a red test.

Sequence instead:

1. **Now, in this PR**: `xfail` the residual with the minimised repro, and correct
   `docs/configuration.md`'s guarantee. That doc currently claims the divergence "always errs
   toward linting a file git would ignore, never toward silently skipping one." That claim is
   false as of this finding, and it is the *only* stated justification for accepting Pattern 2.
   Leaving it standing is the real defect. Also regenerate the divergence-rate table (the fuzz
   numbers moved).
2. **File an issue** for the Pattern 1 migration, attaching this survey — specifically the
   `matched_stripped` mechanism (reverse iteration, `!glob.is_only_dir() || is_dir`) as the
   reference design, and the `Match<T>` tri-state as the shape to port.
3. **Before committing to that migration**, spend an hour on `gitignorefile` — the survey
   flags it as claiming per-directory traversal but could not verify its internals. If it is
   genuinely Pattern 1, it may be a dependency swap rather than a rewrite. Verify by reading
   its source and running house-lint's existing parity suite against it; the suites make that a
   cheap experiment.

The honest framing for the PR: house-lint's ignore engine is a Pattern 2 implementation with
Pattern 1 aspirations, the direction guarantee that made that acceptable has been falsified, and
the fix is architectural rather than another patch.

## Sources

Note: these URLs were not live-verified by me; they come from the research pass.

### Reference implementations
- https://github.com/BurntSushi/ripgrep/blob/master/crates/ignore/src/gitignore.rs — the `matched_stripped` reverse-iteration + `is_only_dir` mechanism
- https://github.com/BurntSushi/ripgrep/tree/master/crates/ignore — crate extracted from ripgrep to isolate this complexity
- https://docs.rs/ignore/latest/ignore/enum.Match.html — tri-state match result
- https://docs.rs/ignore/latest/ignore/struct.WalkBuilder.html — precedence tiering by ignore-file type, then depth
- https://github.com/kaelzhang/node-ignore — JS implementation that had to fix the same non-reinclusion bug
- https://pypi.org/project/gitignorefile — Python, claims per-directory traversal (unverified)

### Bug reports & experience
- https://github.com/cpburnz/python-pathspec/issues/81 — `GitIgnoreSpec` vs git, directory-pattern priority root cause
- https://github.com/cpburnz/python-pathspec/issues/93 — further pathspec divergences (`foo**/bar`, brackets)
- https://github.com/astral-sh/ruff/issues/17392 — gitignore fidelity differs by walk-root spelling
- https://github.com/safishamsi/graphify/issues/882 — third independent tool, same non-reinclusion bug
- https://github.com/BurntSushi/ripgrep/discussions/2824 — internal-slash anchoring rule

### Documentation & standards
- https://git-scm.com/docs/gitignore — the non-reinclusion rule and its performance rationale
- https://git-scm.com/docs/git-ls-files — `--exclude-per-directory`, git's own traversal model
- https://pypi.org/project/pathspec/ — maintainers acknowledge git's semantics are edge-case-heavy
- https://eslint.org/docs/latest/use/configure/ignore — deliberate non-parity
- https://github.com/antfu/eslint-config-flat-gitignore — delegation adapter
- https://prettier.io/docs/ignore — simplified subset (root `.gitignore` only)
- https://nesbitt.io/2026/02/12/the-many-flavors-of-ignore-files.html — survey of ignore-file dialects
- https://waylonwalker.com/gitignore-python/ — the common naive Python recipe
