# Changelog

All notable changes to `house-lint` are documented here.

## [0.2.0](https://github.com/NodeJSmith/house-lint/compare/v0.1.2...v0.2.0) (2026-08-21)


### Features

* add prek config to dogfood house-lint's own hooks ([#22](https://github.com/NodeJSmith/house-lint/issues/22)) ([6f439bf](https://github.com/NodeJSmith/house-lint/commit/6f439bf64bf7431df22ead36989ac2e3867a7123))
* nested gitignore, extend-select, per-file-ignores, caching ([#23](https://github.com/NodeJSmith/house-lint/issues/23)) ([42617ba](https://github.com/NodeJSmith/house-lint/commit/42617ba91f7012c67cc8de273ee9556e71c79537))
* ship built-in HSL101 spec token families ([#28](https://github.com/NodeJSmith/house-lint/issues/28)) ([fb16213](https://github.com/NodeJSmith/house-lint/commit/fb162133f7c631a77e58f9ad69ad490f5490ffd4))


### Bug Fixes

* replace flattened gitignore matcher with per-directory stack ([#29](https://github.com/NodeJSmith/house-lint/issues/29)) ([e9d748b](https://github.com/NodeJSmith/house-lint/commit/e9d748bb1b050ab23f0ac0d3ac126bea27409f0a))


### Performance Improvements

* **pre-commit:** batch file checks into a single house-lint invocation ([#20](https://github.com/NodeJSmith/house-lint/issues/20)) ([775288e](https://github.com/NodeJSmith/house-lint/commit/775288e08df0155eb4fc244541aa4cd0a8226f6b))


### Refactoring

* extract per-file rule resolution out of _scan ([#33](https://github.com/NodeJSmith/house-lint/issues/33)) ([0508761](https://github.com/NodeJSmith/house-lint/commit/050876196853d83418a8f3c83fea21af8b226718))
* **registry:** hoist lazy imports to module level ([#19](https://github.com/NodeJSmith/house-lint/issues/19)) ([fa5abe7](https://github.com/NodeJSmith/house-lint/commit/fa5abe7b65ba439de21126317842160dda04ac62))
* resolve codebase audit findings ([#8](https://github.com/NodeJSmith/house-lint/issues/8)) ([cf49dd9](https://github.com/NodeJSmith/house-lint/commit/cf49dd902179fff5657bcb5889597714615a1052))


### Documentation

* add prior-art research briefs for linter best practices ([#10](https://github.com/NodeJSmith/house-lint/issues/10)) ([e3b9bd7](https://github.com/NodeJSmith/house-lint/commit/e3b9bd7f10850771f48e1753a4e9ce75d818d2c9))
* resolve KI-007 as not a bug ([#31](https://github.com/NodeJSmith/house-lint/issues/31)) ([3efb3b8](https://github.com/NodeJSmith/house-lint/commit/3efb3b8ded21f3023fd8d0e0c851418032db710e))

## [0.1.2](https://github.com/NodeJSmith/house-lint/compare/v0.1.1...v0.1.2) (2026-08-11)


### Documentation

* remove name references from CLI help, pyproject, and docs ([#6](https://github.com/NodeJSmith/house-lint/issues/6)) ([da6fd35](https://github.com/NodeJSmith/house-lint/commit/da6fd35a087241602b2c03bac5f6199754e6a3a4))

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
