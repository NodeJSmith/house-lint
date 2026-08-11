# Changelog

All notable changes to `house-lint` are documented here.

## [0.1.1](https://github.com/NodeJSmith/house-lint/compare/v0.1.0...v0.1.1) (2026-08-11)


### Documentation

* trim README — depersonalize, remove setup notes, merge non-goals ([#4](https://github.com/NodeJSmith/house-lint/issues/4)) ([57beef2](https://github.com/NodeJSmith/house-lint/commit/57beef2a3f21ef68f86e6f69f1ec5466fdd28d5f))

## [0.1.0] (2026-08-11)

Initial public release of Jessica's opinionated Python house-style linter.

- Hardens source discovery, result validation, and suppression handling for release use (#1).
- Adds the `house-lint check` and `house-lint rules` commands for Python 3.11+.
- Ships default rules `HSL001`–`HSL004`, opt-in rules `HSL101`–`HSL103`, and always-on suppression diagnostics `HSL900`.
- Adds strict root/configuration/path discovery, deterministic text and schema-versioned JSON output, and documented exit categories.
- Adds statement-aware `house-lint:` suppressions. Existing Hassette annotations such as `# lazy-import:`, `# constant-after-def:`, and `# file-size-exempt:` are not compatible; use the documented reasoned pragma grammar when adopting this package.
- Adds distributable pre-commit metadata that filters Python filenames before invoking the strict CLI.

### Compatibility

The command-line, TOML, JSON, rule-ID, and suppression surfaces are compatibility contracts. Before 1.0, changes may occur, but releases will document migration steps here.
