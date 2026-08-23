# Changelog Quality (release-please)

This project uses [release-please](https://github.com/googleapis/release-please) to generate changelog entries from conventional commit messages. Every PR that lands on `master` becomes a changelog line item, unless its type is excluded. Write commit messages (and therefore PR titles) with this in mind.

## Which types appear in the changelog

Only these types generate changelog entries (configured in `release-please-config.json`):

| Type | Changelog section | Use for |
|---|---|---|
| `feat` | Features | New user-facing functionality |
| `fix` | Bug Fixes | Something was broken, now it works |
| `perf` | Performance Improvements | Measurable performance gains |
| `refactor` | Refactoring | Structural changes notable enough for users to know about |
| `docs` | Documentation | User-facing documentation (README, docs site, public docstrings) |

These types are excluded from the changelog — use them for internal work:

| Type | Use for |
|---|---|
| `chore` | Internal work: deps, tooling, internal scripts, and — see the override below — `.claude/` instruction files |
| `ci` | CI/CD pipeline changes |
| `test` | Test infrastructure, coverage improvements |

**Key distinction:** `docs:` is for documentation that users read (README, docs site pages, public API docstrings).

### Override: `.claude/` files are `chore:`, not `docs:`

The global Claudefiles commit convention types rule/skill/agent-prompt changes as `docs:`, on the theory that instructions are documentation. house-lint deliberately overrides that here: this repo's changelog is read by real PyPI users, and release-please treats `docs:` as changelog-visible, so following the global convention would put internal AI-tooling notes ("add changelog-review command") in front of package consumers who have no use for them. Type `.claude/` work `chore:` in this repo. If the global convention changes to account for this case, revisit.

## How release-please reads commits

release-please parses each commit that lands on `master` as a conventional commit: the subject line becomes the changelog bullet, and a `BREAKING CHANGE:` footer in the commit body becomes the breaking-change note.

For a squash-merged PR, what GitHub actually puts in that commit depends on this repo's settings (`gh api repos/NodeJSmith/house-lint --jq '{squash_merge_commit_title, squash_merge_commit_message}'`):

- **Subject** (`squash_merge_commit_title: COMMIT_OR_PR_TITLE`): a single-commit PR gets that commit's own title; a multi-commit PR gets the PR title. Either way, GitHub's merge box lets you edit it before confirming.
- **Body** (`squash_merge_commit_message: COMMIT_MESSAGES`): the squash commit body defaults to the *concatenated bodies of every commit in the PR* — **not** the PR description.

That second point is the one that trips people up: a `BREAKING CHANGE:` footer written only in the PR body does not carry through by default. It has to live in an actual commit message on the branch, or you have to paste it into GitHub's squash-merge message box by hand before confirming the merge.

## PR titles are changelog entries

For a multi-commit PR (the common case), the PR title becomes the squash commit subject, which is the one-line changelog entry users read. Write it as a **user-facing description**, not a developer-facing one.

Good — tells a user what changed for them:
- `feat: add HSL103 opt-in rule for docstring placement`
- `fix: gitignore discovery misses directory-only negations`
- `feat!: rename ignore-file pragma to house-lint-ignore-file`

Bad — internal jargon, implementation details, or vague bundling:
- `feat: rework detector registry lazy-import plumbing`
- `fix: bundle three small-scope issue fixes`
- `refactor: tech debt cleanup across rule modules`

### Rules

1. Imperative mood, lowercase: `add X`, `fix Y`, not `Added X` or `Adds Y`.
2. Describe the user-visible outcome. What can the user now do, or what broke that's now fixed?
3. No bundle PRs in the title. If a PR bundles N fixes, the title should describe the theme; individual items belong in commit bodies, since that's what release-please actually reads.
4. No internal-only entries. A purely internal PR (CI, test infra, `.claude/` tooling) uses `chore:`, `ci:`, or `test:` — all excluded from the changelog.

## Breaking changes MUST have a footer — in a commit, not just the PR body

When a PR contains a breaking change (`feat!:`, `fix!:`, `refactor!:`), a `BREAKING CHANGE:` footer must exist in the commit body that release-please will actually see. Given this repo's `COMMIT_MESSAGES` squash setting (above), that means: write the footer into the commit message on the branch, or, when confirming the squash-merge, edit the commit message box on GitHub to include it. Don't just write it in the PR description and assume it flows through.

The footer must explain:
1. What changed
2. What user code is affected
3. What the user needs to do

### Example commit message

```
feat!: rename ignore-file pragma to house-lint-ignore-file

BREAKING CHANGE: The `ignore-file` pragma is renamed to `house-lint-ignore-file`.
Suppression files using the old spelling must be updated or HSL900 will flag
them as unrecognized pragmas.
```

The `BREAKING CHANGE:` footer is a [conventional commit trailer](https://www.conventionalcommits.org/en/v1.0.0/#specification). It must be:
- Preceded by a blank line
- On its own line starting with `BREAKING CHANGE: ` (with the colon and space)
- Able to span multiple lines (continuation lines are indented or just flow naturally)

### Multiple breaking changes

Do not use multiple `BREAKING CHANGE:` footers. release-please's own commit parser
(`@conventional-commits/parser`) only turns the *first* `BREAKING CHANGE:` footer in a commit
into a note; every subsequent one is silently dropped, not merged and not appended. A commit with
several separate footers surfaces exactly one breaking-change bullet in the changelog.

Use one `BREAKING CHANGE:` footer, with a `####` header and a `- ` bulleted list for each
additional item on the lines that follow (release-please's documented extended-context format):

```
BREAKING CHANGE: This release renames the suppression pragma family.
#### Renamed pragmas
- `ignore-file` is renamed to `house-lint-ignore-file`.
- `ignore-next` is renamed to `house-lint-ignore-next`.
```

## Pre-release changelog review

Before merging a release-please PR, review the generated changelog and manually edit the
**CHANGELOG.md file** on the release-please branch to:

1. Remove internal entries: CI changes, test infrastructure, refactors with no user-visible behavior change.
2. Expand vague entries: if a commit subject is too terse, add context from the PR body.
3. Group by feature area: reorganize flat lists into topic-grouped sections when a release has 5+ entries.
4. Verify breaking change descriptions actually tell the user what to do, not just what changed internally.

### Do NOT edit the PR body (CRITICAL)

Only edit the `CHANGELOG.md` file on the release-please branch. Never rewrite the PR description body on GitHub.

Release-please uses its own PR body format (the `:robot: I have created a release *beep* *boop*` block) to recognize merged release PRs. After a release PR is squash-merged, release-please runs again, finds the PR by title, and parses the body to confirm it's a release PR. If the body doesn't match the expected format, release-please treats the merge as a normal commit: no tag, no GitHub Release, no publish.

**What to edit:** `CHANGELOG.md` on the branch (commit and push to the release-please branch).
**What to leave alone:** the PR description on GitHub — release-please owns that.

### Recovery: manual release

If a release-please PR is merged but no tag/release appears:

1. Check the post-merge workflow run for `✖ Pull request body did not match`.
2. Create the tag: `git tag v<version> <merge-commit-sha> && git push origin v<version>`.
3. Create the GitHub Release: `gh release create v<version> --target <sha> --notes-file <changelog-excerpt>`.
4. Trigger publish manually: `gh workflow run "Release Please" -f tag_name=v<version>` (the workflow's `workflow_dispatch` input, per `.github/workflows/release-please.yml`).
5. Close any spurious release-please PR that was opened for the next version.
