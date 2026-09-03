"""Markdown resource path and frontmatter helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from yaml import SafeLoader, YAMLError, load  # type: ignore[import-untyped]

from pi_agent.types import JSONValue
from pi_coding.paths import PiPaths


class ResourceError(ValueError):
    """Raised when Pi resources are invalid or cannot be expanded."""


@dataclass(frozen=True, slots=True)
class SystemPromptResources:
    """Discovered Pi-native system-prompt file contents and sources."""

    custom_prompt: str | None = None
    custom_prompt_path: Path | None = None
    append_prompt: str | None = None
    append_prompt_paths: tuple[Path, ...] = ()
    diagnostics: tuple[ResourceDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class ResourceDiagnostic:
    """A non-fatal resource discovery problem or precedence note."""

    kind: str
    message: str
    path: Path | None = None
    name: str | None = None
    severity: str = "warning"

    def format(self) -> str:
        """Return a concise human-readable diagnostic line."""
        parts = [self.severity, self.kind]
        if self.name is not None:
            parts.append(self.name)
        label = " ".join(parts)
        if self.path is None:
            return f"{label}: {self.message}"
        return f"{label}: {self.message} ({self.path})"


@dataclass(frozen=True, slots=True)
class PiResourcePaths:
    """Filesystem locations for Pi markdown resources.

    By default Pi loads both Pi-native resources and `.agents` resources from
    the user home directory. When a cwd is provided, project-local `.pi-agent` and
    `.agents` resources are loaded automatically as well.
    """

    root: Path = field(default_factory=lambda: Path.home() / ".pi-agent")
    cwd: Path | None = None
    agents_root: Path | None = field(default_factory=lambda: Path.home() / ".agents")
    paths: PiPaths | None = None
    project_resources_enabled: bool = True
    extra_skills_dirs: tuple[Path, ...] = ()
    extra_prompts_dirs: tuple[Path, ...] = ()
    extra_themes_dirs: tuple[Path, ...] = ()

    @property
    def skills_dir(self) -> Path:
        """Return the primary Pi skills directory."""
        return self.root / "skills"

    @property
    def prompts_dir(self) -> Path:
        """Return the primary Pi prompt templates directory."""
        return self.root / "prompts"

    @property
    def system_prompt_path(self) -> Path:
        """Return the user-level replacement system-prompt file."""
        return self.root / "SYSTEM.md"

    @property
    def append_system_prompt_path(self) -> Path:
        """Return the user-level appended system-prompt file."""
        return self.root / "APPEND_SYSTEM.md"

    @property
    def skills_dirs(self) -> tuple[Path, ...]:
        """Return skill directories in increasing precedence order.

        Only the ``skills`` subdirectory of an ``.agents`` root is scanned,
        never the root ``.agents`` directory itself (which may contain
        ``README.md``, ``AGENTS.md``, etc.).
        """
        paths = self._paths()
        dirs = [self.skills_dir]
        if self.agents_root is not None:
            dirs.append(self.agents_root / "skills")
        if self.cwd is not None and self.project_resources_enabled:
            dirs.extend(
                [
                    paths.project_skills_dir(self.cwd),
                    paths.project_agents_skills_dir(self.cwd),
                ]
            )
        dirs.extend(self.extra_skills_dirs)
        return tuple(_dedupe_paths(dirs))

    @property
    def themes_dirs(self) -> tuple[Path, ...]:
        """Return TUI theme directories in increasing precedence order.

        Themes are Pi-specific, so unlike skills and prompts no ``.agents``
        directories are scanned.
        """
        paths = self._paths()
        dirs = [self.root / "themes"]
        if self.cwd is not None and self.project_resources_enabled:
            dirs.append(paths.project_themes_dir(self.cwd))
        dirs.extend(self.extra_themes_dirs)
        return tuple(_dedupe_paths(dirs))

    @property
    def prompts_dirs(self) -> tuple[Path, ...]:
        """Return prompt template directories in increasing precedence order."""
        paths = self._paths()
        dirs = [self.prompts_dir]
        if self.agents_root is not None:
            dirs.append(self.agents_root / "prompts")
        if self.cwd is not None and self.project_resources_enabled:
            dirs.extend(
                [
                    paths.project_prompts_dir(self.cwd),
                    paths.project_agents_prompts_dir(self.cwd),
                ]
            )
        dirs.extend(self.extra_prompts_dirs)
        return tuple(_dedupe_paths(dirs))

    def _paths(self) -> PiPaths:
        agents_home = self.agents_root or Path.home() / ".agents"
        return self.paths or PiPaths(home=self.root, agents_home=agents_home)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def discover_system_prompt_resources(
    paths: PiResourcePaths,
    *,
    custom_prompt_explicit: bool = False,
    enabled: bool = True,
) -> SystemPromptResources:
    """Discover a precedence-selected base and cumulative append prompt files."""
    if not enabled:
        return SystemPromptResources()

    diagnostics: list[ResourceDiagnostic] = []
    custom_prompt, custom_path = _discover_system_prompt_file(
        paths,
        filename="SYSTEM.md",
        label="replacement",
        explicit=custom_prompt_explicit,
        diagnostics=diagnostics,
    )
    append_prompt, append_paths = _discover_append_system_prompt_files(
        paths,
        diagnostics=diagnostics,
    )
    return SystemPromptResources(
        custom_prompt=custom_prompt,
        custom_prompt_path=custom_path,
        append_prompt=append_prompt,
        append_prompt_paths=append_paths,
        diagnostics=tuple(diagnostics),
    )


def _discover_system_prompt_file(
    paths: PiResourcePaths,
    *,
    filename: str,
    label: str,
    explicit: bool,
    diagnostics: list[ResourceDiagnostic],
) -> tuple[str | None, Path | None]:
    candidates: list[tuple[str, Path]] = []
    if paths.cwd is not None and paths.project_resources_enabled:
        candidates.append(("project", paths.cwd / ".pi-agent" / filename))
    candidates.append(("user", paths.root / filename))

    existing: list[tuple[str, Path]] = []
    for scope, path in candidates:
        try:
            if path.exists():
                existing.append((scope, path))
        except OSError as exc:
            raise ResourceError(
                f"Could not inspect {label} system prompt file {path}: {exc}"
            ) from exc

    if explicit:
        for _scope, path in existing:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="system-prompt",
                    name=label,
                    path=path,
                    severity="info",
                    message="ignored because an explicit startup value takes precedence",
                )
            )
        return None, None
    if not existing:
        return None, None

    selected_scope, selected_path = existing[0]
    try:
        content = selected_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceError(
            f"Could not read {label} system prompt file {selected_path} as UTF-8: {exc}"
        ) from exc

    diagnostics.append(
        ResourceDiagnostic(
            kind="system-prompt",
            name=label,
            path=selected_path,
            severity="info",
            message=f"selected {selected_scope} system prompt file",
        )
    )
    for _scope, path in existing[1:]:
        diagnostics.append(
            ResourceDiagnostic(
                kind="system-prompt",
                name=label,
                path=path,
                message=f"shadowed by higher-precedence file {selected_path}",
            )
        )
    return content, selected_path


def _discover_append_system_prompt_files(
    paths: PiResourcePaths,
    *,
    diagnostics: list[ResourceDiagnostic],
) -> tuple[str | None, tuple[Path, ...]]:
    """Select the trusted project append file, otherwise the user file."""
    candidates: list[tuple[str, Path]] = []
    if paths.cwd is not None and paths.project_resources_enabled:
        candidates.append(("project", paths.cwd / ".pi-agent" / "APPEND_SYSTEM.md"))
    candidates.append(("user", paths.root / "APPEND_SYSTEM.md"))

    existing: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for scope, path in candidates:
        resolved = path.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            exists = resolved.exists()
        except OSError as exc:
            raise ResourceError(
                f"Could not inspect append system prompt file {resolved}: {exc}"
            ) from exc
        if exists:
            existing.append((scope, resolved))

    if not existing:
        return None, ()

    selected_scope, selected_path = existing[0]
    try:
        content = selected_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ResourceError(
            f"Could not read append system prompt file {selected_path} as UTF-8: {exc}"
        ) from exc

    diagnostics.append(
        ResourceDiagnostic(
            kind="system-prompt",
            name="append",
            path=selected_path,
            severity="info",
            message=f"selected {selected_scope} system prompt file",
        )
    )
    for _scope, path in existing[1:]:
        diagnostics.append(
            ResourceDiagnostic(
                kind="system-prompt",
                name="append",
                path=path,
                message=f"shadowed by higher-precedence file {selected_path}",
            )
        )
    return content, (selected_path,)


def resource_paths_with_cwd(
    paths: PiResourcePaths | None,
    cwd: Path,
) -> PiResourcePaths:
    """Return resource paths with a cwd available for project-local discovery."""
    if paths is None:
        return PiResourcePaths(cwd=cwd)
    # A resource plan is destination-bound. Replacement/resume callers may
    # supply a plan created for the source session, but only its user/global
    # roots and feature flags are reusable.
    return PiResourcePaths(
        root=paths.root,
        cwd=cwd,
        agents_root=paths.agents_root,
        paths=paths.paths,
        project_resources_enabled=paths.project_resources_enabled,
        extra_skills_dirs=paths.extra_skills_dirs,
        extra_prompts_dirs=paths.extra_prompts_dirs,
        extra_themes_dirs=paths.extra_themes_dirs,
    )


def resource_paths_with_project_trust(
    paths: PiResourcePaths,
    *,
    trusted: bool,
) -> PiResourcePaths:
    """Return a coherent global-only or global-plus-project resource plan."""
    return PiResourcePaths(
        root=paths.root,
        cwd=paths.cwd,
        agents_root=paths.agents_root,
        paths=paths.paths,
        project_resources_enabled=trusted,
        extra_skills_dirs=paths.extra_skills_dirs,
        extra_prompts_dirs=paths.extra_prompts_dirs,
        extra_themes_dirs=paths.extra_themes_dirs,
    )


def resource_paths_with_extensions(
    paths: PiResourcePaths,
    *,
    skill_paths: tuple[Path, ...] = (),
    prompt_paths: tuple[Path, ...] = (),
    theme_paths: tuple[Path, ...] = (),
) -> PiResourcePaths:
    """Append paths returned by Pi's ``resources_discover`` event."""
    return PiResourcePaths(
        root=paths.root,
        cwd=paths.cwd,
        agents_root=paths.agents_root,
        paths=paths.paths,
        project_resources_enabled=paths.project_resources_enabled,
        extra_skills_dirs=(*paths.extra_skills_dirs, *skill_paths),
        extra_prompts_dirs=(*paths.extra_prompts_dirs, *prompt_paths),
        extra_themes_dirs=(*paths.extra_themes_dirs, *theme_paths),
    )


class _Yaml12SafeLoader(SafeLoader):  # type: ignore[misc]
    """Safe loader with YAML 1.2 boolean rules, matching Pi's `yaml` package."""


_Yaml12SafeLoader.yaml_implicit_resolvers = {
    key: [rule for rule in rules if rule[0] != "tag:yaml.org,2002:bool"]
    for key, rules in SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12SafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def parse_markdown_resource(text: str) -> tuple[dict[str, JSONValue], str]:
    """Parse Pi-compatible YAML frontmatter from a markdown resource."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---"):
        return {}, normalized

    end = normalized.find("\n---", 3)
    if end == -1:
        return {}, normalized

    raw_frontmatter = normalized[4:end]
    body = normalized[end + 4 :].strip()
    if not raw_frontmatter:
        return {}, body
    try:
        parsed = load(raw_frontmatter, Loader=_Yaml12SafeLoader)
    except YAMLError as exc:
        raise ResourceError(str(exc)) from exc
    if parsed is None:
        return {}, body
    if not isinstance(parsed, dict) or not all(isinstance(key, str) for key in parsed):
        raise ResourceError("frontmatter must be a mapping")
    return parsed, body


def derive_description(content: str) -> str | None:
    """Derive a short description from markdown content."""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
        return stripped
    return None


def metadata_to_json(metadata: dict[str, JSONValue]) -> dict[str, JSONValue]:
    """Convert string metadata into JSON-like values."""
    return dict(metadata)
