"""System prompt assembly for Pi coding sessions."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from erp_harness.runtime.tools import AgentTool
from erp_harness.context.self_docs import pi_docs_path, pi_examples_path, pi_readme_path
from erp_harness.context.skills import Skill


@dataclass(frozen=True, slots=True)
class ProjectContextFile:
    """A project instruction file included in the system prompt."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class PromptSection:
    """A free-form section appended to the system prompt."""

    title: str | None
    body: str


@dataclass(frozen=True, slots=True)
class BuildSystemPromptOptions:
    """Options used to build Pi's system prompt."""

    cwd: Path
    tools: Sequence[AgentTool] = ()
    skills: Sequence[Skill] = ()
    custom_prompt: str | None = None
    append_system_prompt: str | None = None
    context_files: Sequence[ProjectContextFile] = ()
    extra_guidelines: Sequence[str] = field(default_factory=tuple)
    extra_sections: Sequence[PromptSection] = field(default_factory=tuple)


def build_system_prompt(options: BuildSystemPromptOptions) -> str:
    """Build a deterministic Pi-style system prompt for Pi."""
    cwd = _format_path(options.cwd)
    append_parts = [options.append_system_prompt] if options.append_system_prompt else []
    append_parts.extend(format_prompt_section(section) for section in options.extra_sections)
    append_section = "".join(f"\n\n{part}" for part in append_parts)

    if options.custom_prompt:
        prompt = options.custom_prompt
        prompt += append_section
        prompt += format_project_context(options.context_files)
        if _has_tool(options.tools, "read"):
            prompt += format_skills_for_prompt(options.skills)
        prompt += f"\nCurrent working directory: {cwd}"
        return prompt

    prompt = (
        "You are an expert coding assistant operating inside Pi Agent for Python, "
        "an unofficial coding-agent harness. "
        "You help users by reading files, executing commands, editing code, and writing new files."
        f"\n\nAvailable tools:\n{format_available_tools(options.tools)}"
        "\n\nIn addition to the tools above, you may have access to other custom tools "
        "depending on the project."
        f"\n\nGuidelines:\n{format_guidelines(options.tools, options.extra_guidelines)}"
        f"\n\n{format_pi_documentation()}"
    )

    prompt += append_section
    prompt += format_project_context(options.context_files)
    if _has_tool(options.tools, "read"):
        prompt += format_skills_for_prompt(options.skills)
    prompt += f"\nCurrent working directory: {cwd}"
    return prompt


def format_prompt_section(section: PromptSection) -> str:
    """Render one optional-title free-form prompt section."""
    if section.title is None:
        return section.body
    return f"## {section.title}\n\n{section.body}"


def format_pi_documentation() -> str:
    """Format Pi-style routing hints to Pi's installed reference material."""
    readme_path = _format_path(pi_readme_path())
    docs_path = _format_path(pi_docs_path())
    examples_path = _format_path(pi_examples_path())
    return (
        "Pi Agent documentation (read only when the user asks about Pi Agent itself, its SDK, "
        "extensions, skills, providers, models, commands, or TUI):\n"
        f"- Main documentation: {readme_path}\n"
        f"- Additional docs: {docs_path}\n"
        f"- Examples: {examples_path} (extensions and custom tools)\n"
        "- When reading Pi docs or examples, resolve docs/... under Additional docs and "
        "examples/... under Examples, not the current working directory\n"
        "- When asked about: creating or modifying extensions (docs/extensions.md, "
        "examples/extensions/), skills and prompt templates (docs/skills.md), custom "
        "providers or adding built-in providers/models (docs/models.md), CLI and slash "
        "commands (docs/cli.md), TUI usage "
        "(docs/tui.md), Pi Agent architecture and packages (docs/architecture.md)\n"
        "- When working on Pi Agent topics, read the docs and examples, and follow .md "
        "cross-references before implementing\n"
        "- Always read relevant Pi Agent .md files completely and follow links to related docs"
    )


def format_available_tools(tools: Sequence[AgentTool]) -> str:
    """Format visible tools using prompt snippets."""
    lines = [f"- {tool.name}: {tool.prompt_snippet}" for tool in tools if tool.prompt_snippet]
    return "\n".join(lines) if lines else "(none)"


def collect_prompt_guidelines(
    tools: Sequence[AgentTool], extra_guidelines: Sequence[str] = ()
) -> list[str]:
    """Collect and de-duplicate system prompt guidelines."""
    names = {tool.name for tool in tools}
    guidelines: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        normalized = value.strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        guidelines.append(normalized)

    has_bash = "bash" in names
    if has_bash and not ({"grep", "find", "ls"} & names):
        add("Use bash for file operations like ls, rg, find")

    for tool in tools:
        for guideline in tool.prompt_guidelines:
            add(guideline)
    for guideline in extra_guidelines:
        add(guideline)

    add("Be concise in your responses")
    add("Show file paths clearly when working with files")
    return guidelines


def format_guidelines(tools: Sequence[AgentTool], extra_guidelines: Sequence[str] = ()) -> str:
    """Format prompt guidelines as markdown bullets."""
    return "\n".join(
        f"- {guideline}" for guideline in collect_prompt_guidelines(tools, extra_guidelines)
    )


def format_project_context(context_files: Sequence[ProjectContextFile]) -> str:
    """Format project context files using Pi's XML-like wrapper."""
    if not context_files:
        return ""

    lines = [
        "\n\n<project_context>",
        "",
        "Project-specific instructions and guidelines:",
        "",
    ]
    for context_file in context_files:
        lines.append(f'<project_instructions path="{_escape_xml(context_file.path)}">')
        lines.append(context_file.content)
        lines.append("</project_instructions>")
        lines.append("")
    lines.append("</project_context>")
    return "\n".join(lines)


def format_skills_for_prompt(skills: Sequence[Skill]) -> str:
    """Format skills for inclusion in a system prompt using Pi's XML style.

    Skills with ``disable_model_invocation`` set are excluded from the prompt;
    they remain invocable explicitly via ``/skill:<name>``.
    """
    visible_skills = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible_skills:
        return ""

    lines = [
        "\n\nThe following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory "
        "(parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]
    for skill in visible_skills:
        description = skill.description or ""
        lines.extend(
            [
                "  <skill>",
                f"    <name>{_escape_xml(skill.name)}</name>",
                f"    <description>{_escape_xml(description)}</description>",
                f"    <location>{_escape_xml(str(skill.path))}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def _has_tool(tools: Sequence[AgentTool], name: str) -> bool:
    return any(tool.name == name for tool in tools)


def _format_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _escape_xml(value: str) -> str:
    return escape(value, {'"': "&quot;", "'": "&apos;"})
