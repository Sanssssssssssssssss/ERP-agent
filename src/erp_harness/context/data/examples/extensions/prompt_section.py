"""Append structured, always-on instructions to Pi's system prompt."""

from erp_harness.runtime.hooks import ExtensionAPI


def setup(pi: ExtensionAPI) -> None:
    """Add a labeled procedure while this extension generation is active."""
    pi.add_prompt_section(
        "Review procedure",
        """Read the complete diff before editing.

Run the relevant checks before reporting success:

```bash
uv run pytest
uv run ruff check .
```
""",
    )
