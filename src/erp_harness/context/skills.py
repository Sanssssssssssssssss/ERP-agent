"""Pi-compatible markdown skill discovery and expansion."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pathspec import GitIgnoreSpec

from erp_harness.context.resources import (
    ResourcePaths,
    ResourceDiagnostic,
    ResourceError,
    parse_markdown_resource,
)

_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_IGNORE_FILE_NAMES = (".gitignore", ".ignore", ".fdignore")


@dataclass(frozen=True, slots=True)
class Skill:
    """A markdown skill resource."""

    name: str
    path: Path
    content: str
    description: str | None = None
    disable_model_invocation: bool = False


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    """Parsed expanded skill invocation message."""

    name: str
    location: str
    content: str
    additional_instructions: str | None = None


def is_skill_candidate(path: Path) -> bool:
    """Return whether a path is a Pi-loadable skill file candidate."""
    return path.suffix == ".md" and (path.is_file() or path.is_symlink())


def load_skills(paths: ResourcePaths | None = None) -> list[Skill]:
    """Load skills, keeping the first resource for each name as Pi does."""
    return load_skills_with_diagnostics(paths)[0]


def load_skills_with_diagnostics(
    paths: ResourcePaths | None = None,
) -> tuple[list[Skill], list[ResourceDiagnostic]]:
    """Load skills and return Pi-compatible validation/collision diagnostics."""
    resource_paths = paths or ResourcePaths()
    skills_by_name: dict[str, Skill] = {}
    real_paths: set[Path] = set()
    diagnostics: list[ResourceDiagnostic] = []

    for skills_dir in resource_paths.skills_dirs:
        skills, directory_diagnostics = _load_skills_from_dir_with_diagnostics(skills_dir)
        diagnostics.extend(directory_diagnostics)
        for skill in skills:
            try:
                real_path = skill.path.resolve()
            except OSError:
                real_path = skill.path.absolute()
            if real_path in real_paths:
                continue
            previous = skills_by_name.get(skill.name)
            if previous is not None:
                diagnostics.append(
                    ResourceDiagnostic(
                        kind="skill",
                        name=skill.name,
                        path=skill.path,
                        message=(
                            f'name "{skill.name}" collision; keeping {previous.path} '
                            f"and ignoring {skill.path}"
                        ),
                    )
                )
                continue
            skills_by_name[skill.name] = skill
            real_paths.add(real_path)

    return list(skills_by_name.values()), diagnostics


def expand_skill_command(text: str, skills: Sequence[Skill]) -> str | None:
    """Expand Pi's exact `/skill:name args` command shape."""
    if not text.startswith("/skill:"):
        return None
    space_index = text.find(" ")
    name = text[7:] if space_index == -1 else text[7:space_index]
    arguments = "" if space_index == -1 else text[space_index + 1 :].strip()
    skill = next((candidate for candidate in skills if candidate.name == name), None)
    if skill is None:
        return text
    try:
        _metadata, body = parse_markdown_resource(skill.path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ResourceError):
        return text
    return format_skill_invocation(skill, arguments or None, content=body)


def format_skill_invocation(
    skill: Skill,
    additional_instructions: str | None = None,
    *,
    content: str | None = None,
) -> str:
    """Format Pi's full skill invocation prompt."""
    skill_block = (
        f'<skill name="{skill.name}" location="{skill.path}">\n'
        f"References are relative to {skill.path.parent}.\n\n"
        f"{(skill.content if content is None else content).strip()}\n"
        "</skill>"
    )
    if additional_instructions and additional_instructions.strip():
        return f"{skill_block}\n\n{additional_instructions.strip()}"
    return skill_block


def parse_skill_invocation(text: str) -> SkillInvocation | None:
    """Parse Pi's expanded skill invocation message format."""
    match = re.match(
        r'^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n</skill>'
        r"(?:\n\n([\s\S]+))?$",
        text,
    )
    if match is None:
        return None
    name, location, content, additional_instructions = match.groups()
    return SkillInvocation(
        name=name,
        location=location,
        content=content,
        additional_instructions=additional_instructions,
    )


def build_skill_index(skills: Sequence[Skill]) -> str:
    """Build the concise skill list used by Pi's presentation layer."""
    visible = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible:
        return "Available skills: none"
    return "\n".join(
        ["Available skills:", *(f"- {skill.name}: {skill.description}" for skill in visible)]
    )


def _load_skills_from_dir(skills_dir: Path) -> list[Skill]:
    return _load_skills_from_dir_with_diagnostics(skills_dir)[0]


def _load_skills_from_dir_with_diagnostics(
    skills_dir: Path,
) -> tuple[list[Skill], list[ResourceDiagnostic]]:
    return _scan_skills(
        skills_dir.expanduser(),
        root=skills_dir.expanduser(),
        include_root_files=True,
        ignore_patterns=[],
    )


def _scan_skills(
    directory: Path,
    *,
    root: Path,
    include_root_files: bool,
    ignore_patterns: list[str],
) -> tuple[list[Skill], list[ResourceDiagnostic]]:
    skills: list[Skill] = []
    diagnostics: list[ResourceDiagnostic] = []
    if not directory.exists() or not directory.is_dir():
        return skills, diagnostics

    patterns = [*ignore_patterns, *_read_ignore_patterns(directory, root)]
    try:
        entries = list(directory.iterdir())
    except OSError:
        return skills, diagnostics

    for entry in entries:
        if entry.name != "SKILL.md" or not _is_file(entry) or _is_ignored(entry, root, patterns):
            continue
        skill, load_diagnostics = _load_skill(entry)
        diagnostics.extend(load_diagnostics)
        if skill is not None:
            skills.append(skill)
        return skills, diagnostics

    for entry in entries:
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue
        is_directory = _is_directory(entry)
        if not is_directory and not _is_file(entry):
            continue
        if _is_ignored(entry, root, patterns, directory=is_directory):
            continue
        if is_directory:
            nested_skills, nested_diagnostics = _scan_skills(
                entry,
                root=root,
                include_root_files=False,
                ignore_patterns=patterns,
            )
            skills.extend(nested_skills)
            diagnostics.extend(nested_diagnostics)
        elif include_root_files and entry.name.endswith(".md"):
            skill, load_diagnostics = _load_skill(entry)
            diagnostics.extend(load_diagnostics)
            if skill is not None:
                skills.append(skill)
    return skills, diagnostics


def _load_skill(path: Path) -> tuple[Skill | None, list[ResourceDiagnostic]]:
    diagnostics: list[ResourceDiagnostic] = []
    try:
        raw = path.read_text(encoding="utf-8")
        metadata, content = parse_markdown_resource(raw)
    except (OSError, UnicodeError, ResourceError) as exc:
        return None, [
            ResourceDiagnostic(kind="skill", path=path, message=str(exc), severity="warning")
        ]

    description_value = metadata.get("description")
    description = description_value if isinstance(description_value, str) else None
    if not description or not description.strip():
        diagnostics.append(
            ResourceDiagnostic(kind="skill", path=path, message="description is required")
        )
    elif len(description) > _MAX_DESCRIPTION_LENGTH:
        diagnostics.append(
            ResourceDiagnostic(
                kind="skill",
                path=path,
                message=(
                    f"description exceeds {_MAX_DESCRIPTION_LENGTH} characters ({len(description)})"
                ),
            )
        )

    name_value = metadata.get("name")
    name = name_value if isinstance(name_value, str) and name_value else path.parent.name
    diagnostics.extend(_name_diagnostics(name, path))
    if not description or not description.strip():
        return None, diagnostics
    return (
        Skill(
            name=name,
            path=path,
            content=content,
            description=description,
            disable_model_invocation=metadata.get("disable-model-invocation") is True,
        ),
        diagnostics,
    )


def _name_diagnostics(name: str, path: Path) -> list[ResourceDiagnostic]:
    messages: list[str] = []
    if len(name) > _MAX_NAME_LENGTH:
        messages.append(f"name exceeds {_MAX_NAME_LENGTH} characters ({len(name)})")
    if re.fullmatch(r"[a-z0-9-]+", name) is None:
        messages.append(
            "name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)"
        )
    if name.startswith("-") or name.endswith("-"):
        messages.append("name must not start or end with a hyphen")
    if "--" in name:
        messages.append("name must not contain consecutive hyphens")
    return [ResourceDiagnostic(kind="skill", name=name, path=path, message=msg) for msg in messages]


def _read_ignore_patterns(directory: Path, root: Path) -> list[str]:
    try:
        prefix_path = directory.relative_to(root)
    except ValueError:
        prefix_path = Path()
    prefix = f"{prefix_path.as_posix()}/" if prefix_path.parts else ""
    patterns: list[str] = []
    for filename in _IGNORE_FILE_NAMES:
        try:
            lines = (directory / filename).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            pattern = _prefix_ignore_pattern(line, prefix)
            if pattern is not None:
                patterns.append(pattern)
    return patterns


def _prefix_ignore_pattern(line: str, prefix: str) -> str | None:
    stripped = line.strip()
    if not stripped or (stripped.startswith("#") and not stripped.startswith(r"\#")):
        return None
    negated = line.startswith("!")
    pattern = line[1:] if negated else line
    if pattern.startswith(r"\!"):
        pattern = pattern[1:]
    if pattern.startswith("/"):
        pattern = pattern[1:]
    prefixed = f"{prefix}{pattern}" if prefix else pattern
    return f"!{prefixed}" if negated else prefixed


def _is_ignored(
    path: Path, root: Path, patterns: Sequence[str], *, directory: bool = False
) -> bool:
    if not patterns:
        return False
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return False
    return GitIgnoreSpec.from_lines(patterns).match_file(f"{relative}/" if directory else relative)


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _is_directory(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False
