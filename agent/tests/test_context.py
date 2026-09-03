from pathlib import Path

from pi_coding.context import discover_project_context
from pi_coding.paths import PiPaths
from pi_coding.resources import PiResourcePaths


def test_discovers_user_project_and_agents_context_files(tmp_path: Path) -> None:
    pi_home = tmp_path / "home" / ".pi-agent"
    agents_home = tmp_path / "home" / ".agents"
    project = tmp_path / "project"
    nested = project / "pkg"
    nested.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (pi_home).mkdir(parents=True)
    (agents_home).mkdir(parents=True)
    (project / ".pi-agent").mkdir()
    (project / ".agents").mkdir()

    (pi_home / "AGENTS.md").write_text("User Pi instructions", encoding="utf-8")
    (agents_home / "AGENTS.md").write_text("User agents instructions", encoding="utf-8")
    (project / "AGENTS.md").write_text("Project instructions", encoding="utf-8")
    (nested / "AGENTS.md").write_text("Nested instructions", encoding="utf-8")
    (nested / ".pi-agent").mkdir()
    (nested / ".agents").mkdir()
    (nested / ".pi-agent" / "AGENTS.md").write_text("Project Pi instructions", encoding="utf-8")
    (nested / ".agents" / "AGENTS.md").write_text("Project agents instructions", encoding="utf-8")

    context_files = discover_project_context(
        PiResourcePaths(
            root=pi_home,
            agents_root=agents_home,
            cwd=nested,
            paths=PiPaths(home=pi_home, agents_home=agents_home),
        )
    )

    assert [Path(context_file.path) for context_file in context_files] == [
        pi_home / "AGENTS.md",
        agents_home / "AGENTS.md",
        project / "AGENTS.md",
        nested / "AGENTS.md",
        nested / ".pi-agent" / "AGENTS.md",
        nested / ".agents" / "AGENTS.md",
    ]
    assert [context_file.content for context_file in context_files] == [
        "User Pi instructions",
        "User agents instructions",
        "Project instructions",
        "Nested instructions",
        "Project Pi instructions",
        "Project agents instructions",
    ]
