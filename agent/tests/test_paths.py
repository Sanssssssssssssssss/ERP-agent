from pathlib import Path

from pi_coding.paths import PiPaths


def test_pi_paths_user_locations(tmp_path: Path) -> None:
    paths = PiPaths(home=tmp_path / ".pi-agent", agents_home=tmp_path / ".agents")

    assert paths.sessions_dir == tmp_path / ".pi-agent" / "sessions"
    assert paths.user_skills_dir == tmp_path / ".pi-agent" / "skills"
    assert paths.user_prompts_dir == tmp_path / ".pi-agent" / "prompts"
    assert paths.user_agents_skills_dir == tmp_path / ".agents" / "skills"
    assert paths.user_agents_prompts_dir == tmp_path / ".agents" / "prompts"


def test_pi_paths_project_locations(tmp_path: Path) -> None:
    paths = PiPaths(home=tmp_path / "home", agents_home=tmp_path / "agents")
    cwd = tmp_path / "project"

    assert paths.project_pi_agent_dir(cwd) == cwd / ".pi-agent"
    assert paths.project_agents_dir(cwd) == cwd / ".agents"
    assert paths.project_skills_dir(cwd) == cwd / ".pi-agent" / "skills"
    assert paths.project_prompts_dir(cwd) == cwd / ".pi-agent" / "prompts"
    assert paths.project_agents_skills_dir(cwd) == cwd / ".agents" / "skills"
    assert paths.project_agents_prompts_dir(cwd) == cwd / ".agents" / "prompts"


def test_default_session_path_uses_home_sessions_and_readable_project_path(
    tmp_path: Path,
) -> None:
    paths = PiPaths(home=tmp_path / "home", agents_home=tmp_path / "agents")
    cwd = tmp_path / "repos" / "exploration" / "pi"
    cwd.mkdir(parents=True)

    session_path = paths.default_session_path(cwd)

    assert session_path.name == "default.jsonl"
    assert session_path.parent.parent == tmp_path / "home" / "sessions"
    assert "repos-exploration-pi-" in session_path.parent.name
    assert len(session_path.parent.name.rsplit("-", maxsplit=1)[-1]) == 6
    assert session_path.parent.exists()
