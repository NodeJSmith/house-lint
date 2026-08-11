# Read-only acceptance record

**Run date:** 2026-08-11
**Package:** `house-lint 0.1.0` from this checkout
**Runtime:** Python 3.14.5 (`uv run`)
**Consumer repositories changed:** none

The PyPI JSON endpoint `https://pypi.org/pypi/house-lint/json` returned HTTP 404 on 2026-08-11. This is an availability check, not a reservation or a publication claim.

## Arrangement shared by both runs

Each consumer was copied with `rsync -a` to `/tmp/<acceptance-root>/project`. The copy excluded `.git/`, `.claude/`, `.opencode/`, `.venv/`, `venv/`, `.nox/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.pyright/`, `node_modules/`, `build/`, `dist/`, and `htmlcov/`. The agent-worktree directories were excluded so the explicit `project` path represented the consumer checkout rather than nested checkout copies.

Each temporary root contains this independent `pyproject.toml`; no consumer configuration was edited:

```toml
[tool.house-lint]
include = []
select = ["HSL001", "HSL002", "HSL003", "HSL004", "HSL101", "HSL102", "HSL103"]

[tool.house-lint.rules.HSL102]
max_lines = 800

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["AC", "FR", "NFR", "WP"]
hash = "optional"
min_digits = 1
suffix = "optional-lower-alpha"
scopes = ["comments", "docstrings", "filenames"]
case_sensitive = true

[[tool.house-lint.rules.HSL101.tokens]]
prefixes = ["T"]
min_digits = 2
not_followed_by_time = true
scopes = ["comments", "docstrings", "filenames"]
```

This intentionally enables all opt-in rules to exercise every detector. `include = []` is valid because `project` is the explicit scan directory.

## Hassette

- **Consumer version:** `hassette 0.52.0`; requires Python `>=3.11,<3.15`.
- **Arrangement:** `/tmp/llm-lint-acceptance-hassette-clean/project` with the shared temporary configuration at `/tmp/llm-lint-acceptance-hassette-clean/pyproject.toml`.
- **Command:**

  ```bash
  uv run house-lint check project --root /tmp/llm-lint-acceptance-hassette-clean --config /tmp/llm-lint-acceptance-hassette-clean/pyproject.toml --format json
  ```

- **Result:** exit `1`; parseable schema-version-1 JSON; 1,400 scanned, 1,287 skipped, 32 findings, 0 errors, 0 suppressed.
- **Rule counts:** `HSL002` 9, `HSL102` 23; every other rule, including `HSL900`, 0.

### Findings and disposition

All `HSL102` findings are preserved, legitimate file-size hits and match the old size check: `scripts/seed_db.py`; `src/hassette/api/api.py`, `bus/bus.py`, `bus/sync.py`, `core/app_lifecycle_service.py`, `core/command_executor.py`, `core/core.py`, `core/scheduler_service.py`, `core/state_proxy.py`, `core/websocket_service.py`, `scheduler/scheduler.py`, `test_utils/harness.py`, and `test_utils/recording_api.py`; and `tests/e2e/mock_fixtures.py`, `tests/integration/bus/test_execution_modes.py`, `tests/integration/test_listeners.py`, `tests/integration/test_scheduler_mode.py`, `tests/integration/test_websocket_service.py`, `tests/unit/cli/test_client.py`, `tests/unit/core/test_app_lifecycle_service.py`, `tests/unit/core/test_command_executor_pipeline.py`, `tests/unit/test_autodetect_apps.py`, and `tests/unit/test_logging.py`.

All nine `HSL002` findings are expected migration candidates, not detector false positives: `src/hassette/__main__.py:9`; `src/hassette/app/utils.py:31,94`; `src/hassette/resources/operations.py:76`; `src/hassette/utils/app_utils.py:201,356`; `tests/conftest.py:222`; and `tests/integration/test_dashboard_without_ha.py:66,172`. The legacy checker accepts these through `# lazy-import:` annotations. The new linter deliberately drops that annotation grammar and does not modify the consumer; adoption would require explicit `house-lint: ignore[...] - reason` pragmas or code changes.

### Original-script comparison and matrix deltas

The copied consumer's original scripts returned: `check_llm_cruft.py` 0, `check_lazy_imports.py` 0, `check_type_checking_position.py` 0, `check_constants_position.py` 0, `check_spec_tokens.py` 0, `check_file_size.py` 1, and `check_exception_names.py` 0. The old file-size script reported the same 23 files over 800 lines.

| Rule | Preserve/generalize/drop outcome in this run |
| --- | --- |
| `HSL001` | Preserve prose-only divider/filler detection; generalized suppression support. No findings. The old no-exemption policy is dropped. |
| `HSL002` | Preserve function-depth detection; generalized to unified suppression. Dropped `# lazy-import:` explains all nine new findings. |
| `HSL003` | Preserve guard/later-import detection; generalized suppression support; the old no-suppression behavior is dropped. No findings. |
| `HSL004` | Preserve uppercase/dunder/derived-binding heuristic; generalized suppression support; dropped `# constant-after-def:`. No findings. |
| `HSL101` | Preserve prose/filename scopes and time guard; generalize hard-coded tokens to the temporary constrained families and add suppression support. No findings. |
| `HSL102` | Preserve `splitlines()` and strict `>` threshold; generalize configuration and file-level suppression; drop `# file-size-exempt:` and warning-only presentation. The 23 findings match the old check. |
| `HSL103` | Preserve `exc`/`*_exc` policy; generalize allowed names and suppression support; drop the old no-suppression policy. No findings. |

No Hassette hook, annotation, script, dependency, or source file was changed.

## claude-code-recall

- **Consumer version:** `ccrecall 0.22.0`; requires Python `>=3.11`.
- **Arrangement:** `/tmp/llm-lint-acceptance-claude-code-recall-final/project` with the shared temporary configuration at `/tmp/llm-lint-acceptance-claude-code-recall-final/pyproject.toml`.
- **Command:**

  ```bash
  uv run house-lint check project --root /tmp/llm-lint-acceptance-claude-code-recall-final --config /tmp/llm-lint-acceptance-claude-code-recall-final/pyproject.toml --format json
  ```

- **Result:** exit `1` (not exit 4); parseable schema-version-1 JSON; 97 scanned, 258 skipped, 57 findings, 0 errors, 0 suppressed.
- **Rule counts:** `HSL004` 24, `HSL102` 13, `HSL103` 20; `HSL001`, `HSL002`, `HSL003`, `HSL101`, and `HSL900` 0.

### Finding triage

Every finding is a legitimate hit under the deliberately enabled Jessica house-style policy; none is a detector false positive. This is an acceptance observation, not an instruction to migrate the consumer.

| Rule | Findings, each disposition |
| --- | --- |
| `HSL004` | **Legitimate:** `src/ccrecall/cli/commands.py:57,58,69,89,94,95,97,99,100`; `formatting.py:41,42,219`; `health.py:268`; `hooks/session_selection.py:75,93`; `summary_enrichment.py:69,81`; `tests/test_backfill_embeddings.py:54`, `test_parsing.py:42`, `test_session_tail.py:85,89,90,91`, and `test_summary_enrichment.py:33`. These are module constants after the first class/function. |
| `HSL102` | **Legitimate:** `src/ccrecall/llm_summarizer.py`; `tests/test_backfill_embeddings.py`, `test_backfill_llm_summaries.py`, `test_backfill_tool_content.py`, `test_context_injection.py`, `test_db.py`, `test_formatting.py`, `test_import_pipeline.py`, `test_llm_summarizer.py`, `test_search.py`, `test_session_ops.py`, `test_summarizer.py`, and `test_sync_hook.py`. Each exceeds the configured 800-line threshold. |
| `HSL103` | **Legitimate:** `src/ccrecall/config.py:154,169`; `dates.py:85`; `hooks/backfill_embeddings.py:323`, `backfill_status.py:138`, `backfill_summaries.py:78,87,95`, `backfill_tool_content.py:308,537`, `sync_current.py:244`; `llm_summary_db.py:204,213,237,253`; `models.py:35`; `recent_chats.py:213`; and `search_cli.py:120,157,264`. Each binds an exception as `e`, outside `exc`/`*_exc`. |

No consumer file or configuration was changed. The run proves the strict CLI completes a full explicit-directory scan without an internal failure and leaves adoption decisions with the consumer.
