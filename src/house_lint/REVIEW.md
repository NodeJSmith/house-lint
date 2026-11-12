# REVIEW.md — src/house_lint/

## Single read, honest cache
Does every path that scans a file get its bytes from `source.load_file` exactly once, and does
the cache key in `pipeline.py` (`_cache_keys`) hash that same `LoadedFile.content`? Check any new
rule or tool call that reads a path itself: is its result cached, and if so, what does it hash?

## Catalog as the source of truth
Does the change add a rule ID, tier, language, or options type anywhere other than its row in
`rule_catalog.py`? Look for a second hardcoded list (in `config.py`, `cli.py`, or a reporter)
that would drift from `RULES`, `DEFAULT_SELECT`, or `ORDINARY_RULES`.

## Per-file rule set reaches every consumer
`config.applicable_rules` narrows the enabled set per file. Do detector dispatch, the cache key,
each project rule's input, and `suppressions.apply_suppressions` all receive that narrowed set,
or does one of them read `config.enabled_rules` directly?

## Suppression guarantees
Can any pragma, config key, or CLI flag silence an HSL900 report? And can a pragma HSL901 reports
as `redundant` be the only owner of some finding, so deleting it would unsuppress that finding?
Trace `_credited_claims` and `_owns`, not just the docstrings.

## Breaking surface
Does the change alter a CLI flag, config key, JSON field, exit code, or pragma spelling? If so,
are `docs/` and `README.md` updated in the same diff, with no shim left for the old spelling?
