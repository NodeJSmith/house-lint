# Research Brief: Standalone Python House-Style Linter

The full research brief for this design was produced during `/mine-define` and is retained at:

`/tmp/opencode/mine-define-research.SpexvD/brief.md`

Key conclusions used by the design:

- Use a static seven-rule registry and shared parsed-source cache.
- Use Cyclopts for the CLI and `pathspec` for matching explicitly loaded ignore patterns.
- Keep models as standard-library dataclasses; avoid Pydantic and project-root dependencies.
- Use stable `HSL###` rule IDs independent of the final distribution name.
- The `llm-lint` distribution name is occupied on PyPI; `house-lint` was unregistered through the PyPI JSON API at research time but is not guaranteed available.
- Validate against Hassette and `/home/jessica/source/claude-code-recall` without modifying either repository.

Source evidence and detailed citations remain in the temporary full brief above.
