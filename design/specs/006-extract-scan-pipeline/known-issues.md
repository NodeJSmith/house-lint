# Known Issues

Real issues found while building this feature and intentionally left unfixed.

## KI-001: A repository can ship forged cache entries that replay as its lint results

Status: filed (#65)
Recorded: 2026-10-30 (56)
Source: ship-challenge
Reason not fixed now: out-of-scope
Affected files:
- src/house_lint/cache.py
- src/house_lint/pipeline.py

Issue:
The default cache lives inside the scanned project, and its namespace and entry names are computable from house-lint's version, its source fingerprint, and the content and config hashes. A repository that commits a real `.house-lint-cache/` directory holding a matching entry has that entry replayed as the file's result, so it can hide its own findings from a plain `house-lint check`. The symlink check doesn't apply, because no symlink is involved. `master` behaves the same way.

Why deferred:
The extraction's posture is no behaviour change, and any fix changes what a run reports for a repository that ships a cache directory.

Recommended follow-up:
Decide what a cache entry found in a fresh checkout should mean, for example by ignoring a default cache directory that git tracks, or by moving the default cache outside the project.

Acceptance criteria:
- A forged entry committed to a repository does not change the results of `house-lint check`, and a test pins it.
