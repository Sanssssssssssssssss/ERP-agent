"""Harbor task templates for SaaS-Bench."""

from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent

TEMPLATE_FILES = {
    "instruction": TEMPLATES_DIR / "procurement" / "supply_planning" / "instruction.md.jinja2",
    "task_toml": TEMPLATES_DIR / "shared" / "task.toml.jinja2",
    "dockerfile": TEMPLATES_DIR / "shared" / "Dockerfile.jinja2",
    "entrypoint": TEMPLATES_DIR / "shared" / "entrypoint.sh.jinja2",
    "test_sh": TEMPLATES_DIR / "procurement" / "supply_planning" / "test.sh.jinja2",
    "checks": TEMPLATES_DIR / "procurement" / "supply_planning" / "checks.py.jinja2",
    "solve_sh": TEMPLATES_DIR / "procurement" / "supply_planning" / "solve.sh.jinja2",
}
