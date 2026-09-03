from pathlib import Path

import pytest

from pi_coding import PiPaths, PiResourcePaths
from pi_coding.resources import (
    ResourceError,
    derive_description,
    discover_system_prompt_resources,
    parse_markdown_resource,
)


def test_resource_paths_use_pi_subdirectories(tmp_path: Path) -> None:
    paths = PiResourcePaths(root=tmp_path, agents_root=None)

    assert paths.skills_dir == tmp_path / "skills"
    assert paths.prompts_dir == tmp_path / "prompts"
    assert paths.skills_dirs == (tmp_path / "skills",)
    assert paths.prompts_dirs == (tmp_path / "prompts",)


def test_resource_paths_include_agents_and_project_directories(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    pi_home = tmp_path / "home" / ".pi-agent"
    agents_home = tmp_path / "home" / ".agents"
    paths = PiResourcePaths(
        root=pi_home,
        agents_root=agents_home,
        cwd=cwd,
        paths=PiPaths(home=pi_home, agents_home=agents_home),
    )

    assert paths.skills_dirs == (
        pi_home / "skills",
        agents_home / "skills",
        cwd / ".pi-agent" / "skills",
        cwd / ".agents" / "skills",
    )
    assert paths.prompts_dirs == (
        pi_home / "prompts",
        agents_home / "prompts",
        cwd / ".pi-agent" / "prompts",
        cwd / ".agents" / "prompts",
    )
    assert paths.system_prompt_path == pi_home / "SYSTEM.md"
    assert paths.append_system_prompt_path == pi_home / "APPEND_SYSTEM.md"


def test_system_prompt_files_use_project_values_before_user_values(tmp_path: Path) -> None:
    cwd = tmp_path / "project"
    pi_home = tmp_path / "home" / ".pi-agent"
    agents_home = tmp_path / "home" / ".agents"
    (cwd / ".pi-agent").mkdir(parents=True)
    pi_home.mkdir(parents=True)
    agents_home.mkdir(parents=True)
    (pi_home / "SYSTEM.md").write_text("User base", encoding="utf-8")
    (cwd / ".pi-agent" / "SYSTEM.md").write_text("Project base", encoding="utf-8")
    (pi_home / "APPEND_SYSTEM.md").write_text("User append", encoding="utf-8")
    (cwd / ".pi-agent" / "APPEND_SYSTEM.md").write_text("Project append", encoding="utf-8")
    # `.agents` is not a system-prompt configuration location.
    (agents_home / "SYSTEM.md").write_text("Agents base", encoding="utf-8")

    resources = discover_system_prompt_resources(
        PiResourcePaths(root=pi_home, agents_root=agents_home, cwd=cwd)
    )

    assert resources.custom_prompt == "Project base"
    assert resources.custom_prompt_path == cwd / ".pi-agent" / "SYSTEM.md"
    assert resources.append_prompt == "Project append"
    assert resources.append_prompt_paths == (cwd / ".pi-agent" / "APPEND_SYSTEM.md",)
    assert [(item.severity, item.path) for item in resources.diagnostics] == [
        ("info", cwd / ".pi-agent" / "SYSTEM.md"),
        ("warning", pi_home / "SYSTEM.md"),
        ("info", cwd / ".pi-agent" / "APPEND_SYSTEM.md"),
        ("warning", pi_home / "APPEND_SYSTEM.md"),
    ]


def test_overlapping_user_and_project_append_path_is_loaded_once(tmp_path: Path) -> None:
    pi_home = tmp_path / ".pi-agent"
    pi_home.mkdir()
    append_path = pi_home / "APPEND_SYSTEM.md"
    append_path.write_text("Once", encoding="utf-8")

    resources = discover_system_prompt_resources(
        PiResourcePaths(root=pi_home, agents_root=None, cwd=tmp_path)
    )

    assert resources.append_prompt == "Once"
    assert resources.append_prompt_paths == (append_path,)
    assert [(item.name, item.path) for item in resources.diagnostics] == [("append", append_path)]


def test_explicit_replacement_shadows_file_but_append_files_still_load(tmp_path: Path) -> None:
    pi_home = tmp_path / ".pi-agent"
    pi_home.mkdir()
    (pi_home / "SYSTEM.md").write_bytes(b"\xff")
    (pi_home / "APPEND_SYSTEM.md").write_text("User append", encoding="utf-8")

    resources = discover_system_prompt_resources(
        PiResourcePaths(root=pi_home, agents_root=None),
        custom_prompt_explicit=True,
    )

    assert resources.custom_prompt is None
    assert resources.append_prompt == "User append"
    assert resources.append_prompt_paths == (pi_home / "APPEND_SYSTEM.md",)
    assert len(resources.diagnostics) == 2
    assert "explicit startup value" in resources.diagnostics[0].message
    assert "selected user" in resources.diagnostics[1].message


def test_selected_system_prompt_file_must_be_readable_utf8(tmp_path: Path) -> None:
    pi_home = tmp_path / ".pi-agent"
    pi_home.mkdir()
    prompt_path = pi_home / "SYSTEM.md"
    prompt_path.write_bytes(b"\xff")

    with pytest.raises(ResourceError, match="Could not read replacement system prompt file"):
        discover_system_prompt_resources(PiResourcePaths(root=pi_home, agents_root=None))


def test_selected_append_system_prompt_file_must_be_readable_utf8(tmp_path: Path) -> None:
    pi_home = tmp_path / ".pi-agent"
    pi_home.mkdir()
    (pi_home / "APPEND_SYSTEM.md").write_bytes(b"\xff")

    with pytest.raises(ResourceError, match="Could not read append system prompt file"):
        discover_system_prompt_resources(PiResourcePaths(root=pi_home, agents_root=None))


def test_parse_frontmatter_description() -> None:
    metadata, body = parse_markdown_resource(
        "---\ndescription: Write tests\n---\n# Testing\nUse pytest."
    )

    assert metadata == {"description": "Write tests"}
    assert body == "# Testing\nUse pytest."


def test_parse_frontmatter_normalizes_crlf_line_endings() -> None:
    metadata, body = parse_markdown_resource(
        "---\r\ndescription: Write tests\r\n---\r\n# Testing\r\nUse pytest."
    )

    assert metadata == {"description": "Write tests"}
    assert body == "# Testing\nUse pytest."


def test_derive_description_uses_first_heading_or_paragraph() -> None:
    assert derive_description("\n# Title\nBody") == "Title"
    assert derive_description("\nFirst paragraph\nMore") == "First paragraph"
