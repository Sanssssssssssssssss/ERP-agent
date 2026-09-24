"""Project instruction discovery for Pi coding sessions."""

from __future__ import annotations

from pathlib import Path

from erp_harness.context.resources import ResourcePaths, ResourceDiagnostic
from erp_harness.context.system_prompt import ProjectContextFile

CONTEXT_FILENAMES = (
    "AGENTS.override.md",
    "AGENTS.md",
    "AGENTS.MD",
    "CLAUDE.md",
    "CLAUDE.MD",
)


def discover_project_context(
    paths: ResourcePaths | None = None,
) -> tuple[ProjectContextFile, ...]:
    """Discover project instruction files for system prompt context."""
    context_files, _diagnostics = discover_project_context_with_diagnostics(paths)
    return context_files


def discover_project_context_with_diagnostics(
    paths: ResourcePaths | None = None,
) -> tuple[tuple[ProjectContextFile, ...], tuple[ResourceDiagnostic, ...]]:
    """Discover project instruction files and return non-fatal diagnostics."""
    resource_paths = paths or ResourcePaths()
    context_files: list[ProjectContextFile] = []
    diagnostics: list[ResourceDiagnostic] = []
    for path in _context_file_candidates(resource_paths):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            diagnostics.append(
                ResourceDiagnostic(
                    kind="context",
                    path=path,
                    message=f"could not read context file: {exc}",
                )
            )
            continue
        context_files.append(ProjectContextFile(path=str(path), content=content))
    return tuple(context_files), tuple(diagnostics)


def _context_file_candidates(paths: ResourcePaths) -> tuple[Path, ...]:
    candidates: list[Path] = []
    global_context = _context_file_from_dir(paths.root)
    if global_context is not None:
        candidates.append(global_context)
    if paths.agents_root is not None:
        agents_context = _context_file_from_dir(paths.agents_root)
        if agents_context is not None:
            candidates.append(agents_context)

    if paths.cwd is not None and paths.project_resources_enabled:
        cwd = paths.cwd.expanduser().resolve()
        candidates.extend(_ancestor_context_files(cwd))
        pi_paths = paths._paths()
        for directory in (
            pi_paths.project_pi_agent_dir(cwd),
            pi_paths.project_agents_dir(cwd),
        ):
            context_file = _context_file_from_dir(directory)
            if context_file is not None:
                candidates.append(context_file)

    existing = [path for path in candidates if path.is_file()]
    return tuple(_dedupe_resolved_paths(existing))


def _context_file_from_dir(directory: Path) -> Path | None:
    for filename in CONTEXT_FILENAMES:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _ancestor_context_files(cwd: Path) -> list[Path]:
    contexts: list[Path] = []
    for directory in reversed((cwd, *cwd.parents)):
        context_file = _context_file_from_dir(directory)
        if context_file is not None:
            contexts.append(context_file)
    return contexts


def _dedupe_resolved_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped
