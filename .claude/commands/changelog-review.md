# Changelog Review

Review and rewrite the release-please changelog PR before merging.

## Context

- release-please config: !`cat release-please-config.json 2>/dev/null`
- Changelog quality rule: !`cat .claude/rules/changelog-quality.md 2>/dev/null`
- Current CHANGELOG.md head: !`head -30 CHANGELOG.md 2>/dev/null`

## Step 1: Find the release PR

Run `gh pr list --search "chore(master): release" --state open --json number,title,headRefName --limit 5` (house-lint's release workflow targets `master`, so release-please titles its PRs `chore(master): release ...` — this assumes release-please's default naming; if `release-please-config.json` ever adds a custom `pull-request-title-pattern`, update this search accordingly).

If no PR found, stop and tell the user. If multiple, ask which one.

**Re-run this review if new commits land on `master` before you merge.** release-please updates the open release PR in place whenever it runs again, which regenerates `CHANGELOG.md` from the current commit set — a manual rewrite pushed earlier can be silently overwritten. Treat "the branch changed since I last reviewed it" as a reason to redo Step 2 onward, not just re-push.

## Step 2: Checkout and read

1. Fetch and checkout the release-please branch.
2. Read the new release section in `CHANGELOG.md` (the topmost `## [x.y.z]` heading).
3. Find the latest existing tag: `git tag --sort=-v:refname --list 'v*' | head -1` — call this `<prev-tag>`. This release hasn't been tagged yet, so the range for "what's new in this release" is `<prev-tag>..HEAD` on the checked-out branch.
4. List the commits in this release: `git log --oneline <prev-tag>..HEAD`.

## Step 3: Gather PR context

For each commit that produced a changelog entry:

1. Read the commit's own body: `git log -1 --format=%B <sha>` on `master`, using the `<sha>` from Step 2's `git log --oneline`. This is what release-please actually parsed — per this repo's `COMMIT_MESSAGES` squash setting, it's the concatenated bodies of every commit in the PR, which is where bundled-PR item details and `BREAKING CHANGE:` footers actually live (see the rule file's note on bundled PRs).
2. Fetch the PR description via `gh pr view <number> --json title,body` using the `(#NNN)` reference in the commit subject, as supplementary narrative context only — release-please never reads this field, so treat anything found only here as unconfirmed unless it's also in the commit body.

Focus on:

- What user-facing behavior changed
- Breaking change migration details (from the commit body — step 1)
- Bundled-item details for multi-fix commits (from the commit body — step 1)
- Whether the change is internal-only

## Step 3.5: Check for external contributors

Run `uv run python scripts/release_contributors.py <prev-tag> HEAD` (same range as Step 2).

If external contributors are found, surface them as a finding:

> External contributors detected — ensure attribution in the changelog:
>
> <script output>

When rewriting entries in Step 4, add "thanks @username!" (or "thanks Name!" if no GitHub username is available) to the relevant changelog bullet.

If no external contributors are found, continue silently.

## Step 4: Rewrite

Rewrite the release section into user-facing language, per the type table in `.claude/rules/changelog-quality.md`:

- Remove `ci:`, `test:`, and `chore:` entries, and any `refactor:`/`docs:` entry with no user-visible effect.
- Keep and rewrite `feat:`, `fix:`, `perf:`, user-facing `docs:`, and user-facing `refactor:` entries (CLI flags, TOML config, rule IDs, output schema) to describe the outcome for the user, not the implementation.

**Breaking changes:** each must explain (1) what changed, (2) what user code is affected, (3) what to do. Use field-by-field details when a CLI flag, config key, or output schema changed. Put these in a `### Breaking Changes` section at the top.

**Grouping:** when 5+ entries remain, group by feature area with `### Section` headers:
- `### Breaking Changes` (always first if present)
- Topic sections like `### Rules`, `### CLI`, `### Configuration`, `### Discovery`, `### Suppressions`
- `### Bug Fixes` (always last)
- `### Documentation` (only if user-facing docs changed)

For a release with fewer than 5 kept entries, a flat bullet list is fine — don't force sections onto a short release.

**Format:**
- `- ` bullets with bold lead-in for breaking changes
- Issue references as `(#NNN)`, no commit SHAs
- Preserve the `## [x.y.z](compare-link) (date)` heading exactly

## Step 5: Push

1. Show a summary: entries removed, entries rewritten, breaking changes added.
2. Ask for approval:

```
AskUserQuestion:
  question: "Ready to push the rewritten changelog to the release-please branch?"
  header: "Push"
  multiSelect: false
  options:
    - label: "Push it"
      description: "Commit and push to the release-please branch"
    - label: "Show the diff"
      description: "Show the full diff first, then ask again"
```

3. Commit with `chore: rewrite changelog with user-facing descriptions` and push. This commit lives on the release-please branch, not `master`, so its type doesn't affect changelog generation either way — `chore:` just names it accurately as release-branch maintenance.
