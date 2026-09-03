# Pi Agent for Python contributor instructions

Preserve the three public layers:

```text
pi_ai      provider/model streaming layer
pi_agent   portable agent harness, loop, tools, events, and sessions
pi_coding  CLI app, resources, skills, extensions, commands, and TUI
```

`pi_agent` must not depend on the CLI, Textual, Rich, configuration paths, or
application-specific resource loading. Keep async boundaries explicit and prefer typed,
small implementations over speculative abstractions.

Use `uv` and the locked environment. Run focused tests while developing, then run:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Use fake providers and fake tools for deterministic tests. Update both READMEs for
user-facing changes, and keep packaged self-documentation under
`src/pi_coding/data/docs/` aligned with the implementation.
