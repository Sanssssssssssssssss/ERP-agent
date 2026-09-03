"""Skill contracts translated from Pi 0.84.1 skills.test.ts."""

from pathlib import Path

from pi_coding import (
    PiResourcePaths,
    Skill,
    build_skill_index,
    expand_skill_command,
    format_skill_invocation,
    load_skills,
    load_skills_with_diagnostics,
    parse_skill_invocation,
)


def _write_skill(
    path: Path,
    *,
    description: str | None = "A test skill.",
    name: str | None = None,
    extra: str = "",
    body: str = "Instructions.",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = ["---"]
    if name is not None:
        frontmatter.append(f"name: {name}")
    if description is not None:
        frontmatter.append(f"description: {description}")
    if extra:
        frontmatter.append(extra)
    frontmatter.append("---")
    path.write_text("\n".join([*frontmatter, body]), encoding="utf-8")
    return path


def _load(root: Path):  # noqa: ANN202
    return load_skills_with_diagnostics(PiResourcePaths(root=root, agents_root=None))


def test_loads_valid_skill_and_uses_parent_name(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path / "skills" / "valid-skill" / "SKILL.md",
        description="A valid skill for testing purposes.",
    )

    skills, diagnostics = _load(tmp_path)

    assert [(skill.name, skill.description, skill.path) for skill in skills] == [
        ("valid-skill", "A valid skill for testing purposes.", path)
    ]
    assert diagnostics == []


def test_frontmatter_name_may_differ_from_parent(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "parent" / "SKILL.md",
        name="different-name",
    )

    skills, diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == ["different-name"]
    assert diagnostics == []


def test_invalid_names_warn_but_still_load(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills" / "invalid" / "SKILL.md", name="Bad--Name")

    skills, diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == ["Bad--Name"]
    assert any("invalid characters" in diagnostic.message for diagnostic in diagnostics)
    assert any("consecutive hyphens" in diagnostic.message for diagnostic in diagnostics)


def test_long_name_warns_but_still_loads(tmp_path: Path) -> None:
    name = "a" * 65
    _write_skill(tmp_path / "skills" / "long" / "SKILL.md", name=name)

    skills, diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == [name]
    assert any("exceeds 64 characters" in diagnostic.message for diagnostic in diagnostics)


def test_missing_description_warns_and_skips(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills" / "missing" / "SKILL.md", description=None)

    skills, diagnostics = _load(tmp_path)

    assert skills == []
    assert any("description is required" in diagnostic.message for diagnostic in diagnostics)


def test_unknown_frontmatter_fields_are_ignored(tmp_path: Path) -> None:
    _write_skill(
        tmp_path / "skills" / "unknown" / "SKILL.md",
        extra="unknown-field: anything",
    )

    skills, diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == ["unknown"]
    assert diagnostics == []


def test_nested_skills_are_discovered_recursively(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills" / "group" / "child" / "SKILL.md", name="child")

    skills, diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == ["child"]
    assert diagnostics == []


def test_root_skill_is_preferred_over_nested_skills(tmp_path: Path) -> None:
    root = tmp_path / "skills" / "root"
    _write_skill(root / "SKILL.md", name="root", description="Root wins.")
    _write_skill(root / "nested" / "SKILL.md", name="nested")

    skills, diagnostics = _load(tmp_path)

    assert [(skill.name, skill.description) for skill in skills] == [("root", "Root wins.")]
    assert diagnostics == []


def test_invalid_yaml_warns_and_skips(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "invalid-yaml" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\ndescription: [broken\n---\nBody", encoding="utf-8")

    skills, diagnostics = _load(tmp_path)

    assert skills == []
    assert diagnostics
    assert "line" in diagnostics[0].message


def test_multiline_yaml_description_is_preserved(tmp_path: Path) -> None:
    path = tmp_path / "skills" / "multiline" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: multiline\ndescription: |\n  This is a multiline description.\n"
        "  It has two lines.\n---\nBody",
        encoding="utf-8",
    )

    skills, diagnostics = _load(tmp_path)

    assert "\n" in (skills[0].description or "")
    assert "This is a multiline description." in (skills[0].description or "")
    assert diagnostics == []


def test_root_markdown_files_are_backward_compatible_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path / "skills" / "legacy.md", name="legacy")

    skills, diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == ["legacy"]
    assert diagnostics == []


def test_gitignore_rules_exclude_discovered_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir / "kept" / "SKILL.md", name="kept")
    _write_skill(skills_dir / "ignored" / "SKILL.md", name="ignored")
    (skills_dir / ".gitignore").write_text("ignored/\n", encoding="utf-8")

    skills, _diagnostics = _load(tmp_path)

    assert [skill.name for skill in skills] == ["kept"]


def test_disable_model_invocation_uses_yaml_boolean_only(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    _write_skill(
        skills_dir / "hidden" / "SKILL.md",
        name="hidden",
        extra="disable-model-invocation: true",
    )
    _write_skill(
        skills_dir / "yes-is-a-string" / "SKILL.md",
        name="yes-is-a-string",
        extra="disable-model-invocation: yes",
    )

    skills = load_skills(PiResourcePaths(root=tmp_path, agents_root=None))
    by_name = {skill.name: skill for skill in skills}

    assert by_name["hidden"].disable_model_invocation is True
    assert by_name["yes-is-a-string"].disable_model_invocation is False


def test_name_collision_keeps_first_skill(tmp_path: Path) -> None:
    pi_home = tmp_path / "home" / ".pi-agent"
    agents_home = tmp_path / "home" / ".agents"
    first = _write_skill(pi_home / "skills" / "first" / "SKILL.md", name="review")
    second = _write_skill(agents_home / "skills" / "second" / "SKILL.md", name="review")

    skills, diagnostics = load_skills_with_diagnostics(
        PiResourcePaths(root=pi_home, agents_root=agents_home)
    )

    assert [(skill.name, skill.path) for skill in skills] == [("review", first)]
    assert any(
        diagnostic.path == second and "collision" in diagnostic.message
        for diagnostic in diagnostics
    )


def test_explicit_invocation_rereads_skill_file(tmp_path: Path) -> None:
    path = _write_skill(
        tmp_path / "skills" / "testing" / "SKILL.md",
        name="testing",
        body="# Testing\nRun pytest.",
    )
    skills = load_skills(PiResourcePaths(root=tmp_path, agents_root=None))
    path.write_text(
        "---\nname: testing\ndescription: A test skill.\n---\n# Testing\nRun updated tests.",
        encoding="utf-8",
    )

    expanded = expand_skill_command("/skill:testing add parser tests", skills)

    assert expanded is not None
    assert f'<skill name="testing" location="{path}">' in expanded
    assert "Run updated tests." in expanded
    assert expanded.endswith("</skill>\n\nadd parser tests")


def test_unknown_or_multiline_skill_command_passes_through() -> None:
    unknown = "/skill:missing"
    multiline = "/skill:testing\n\ninstructions"

    assert expand_skill_command(unknown, []) == unknown
    assert expand_skill_command(multiline, []) == multiline


def test_format_and_parse_skill_invocation(tmp_path: Path) -> None:
    skill = Skill(
        name="testing",
        path=tmp_path / "skills" / "testing" / "SKILL.md",
        content="# Testing\nRun pytest.",
        description="Test code",
    )

    formatted = format_skill_invocation(skill, "add parser tests")
    parsed = parse_skill_invocation(formatted)

    assert parsed is not None
    assert parsed.name == "testing"
    assert parsed.location == str(skill.path)
    assert "# Testing" in parsed.content
    assert parsed.additional_instructions == "add parser tests"


def test_skill_index_excludes_disabled_skills(tmp_path: Path) -> None:
    visible = Skill("visible", tmp_path / "visible" / "SKILL.md", "", "Visible")
    hidden = Skill("hidden", tmp_path / "hidden" / "SKILL.md", "", "Hidden", True)

    assert build_skill_index([visible, hidden]) == "Available skills:\n- visible: Visible"
    assert build_skill_index([hidden]) == "Available skills: none"
