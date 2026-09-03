from __future__ import annotations

import json
import tomllib
from pathlib import Path

from pydantic import BaseModel

CONFIG_CATEGORIES = {"procurement"}


def load_toml_payload(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_config[T: BaseModel](path: Path, model_type: type[T]) -> T:
    payload = load_toml_payload(path)
    return model_type.model_validate(payload)


def load_dataset_config[T: BaseModel](path: Path, model_type: type[T]) -> T:
    dataset_path = path if path.is_absolute() else path.resolve()
    payload = load_toml_payload(dataset_path)
    config = model_type.model_validate(payload)
    entries = []
    for entry in config.entries:  # type: ignore[attr-defined]
        config_path = getattr(entry, "config", None)
        if config_path is None or config_path.is_absolute():
            entries.append(entry)
        else:
            entries.append(entry.model_copy(update={"config": dataset_path.parent / config_path}))
    return config.model_copy(update={"entries": tuple(entries)})


def detect_category(path: Path, *, dataset: bool) -> str:
    payload = load_toml_payload(path)
    category = payload.get("category")
    if isinstance(category, str):
        if category not in CONFIG_CATEGORIES:
            raise ValueError(f"Unknown category: {category}")
        return category

    kind = payload.get("kind")
    if dataset and isinstance(kind, str) and kind.endswith("_dataset"):
        category_from_kind = kind.removesuffix("_dataset")
        if category_from_kind in CONFIG_CATEGORIES:
            return category_from_kind

    if not dataset:
        if "available_routes" in payload:
            return "procurement"

    raise ValueError(f"{path} must define category")


def _render_toml_value(value) -> str:
    if isinstance(value, Path):
        return json.dumps(str(value))
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_render_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def _render_toml_table(lines: list[str], prefix: str, table: dict) -> None:
    scalar_items: list[tuple[str, object]] = []
    nested_items: list[tuple[str, dict]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested_items.append((key, value))
        else:
            scalar_items.append((key, value))
    if prefix:
        lines.append(f"[{prefix}]")
    for key, value in scalar_items:
        lines.append(f"{key} = {_render_toml_value(value)}")
    if prefix and (scalar_items or nested_items):
        lines.append("")
    for idx, (key, value) in enumerate(nested_items):
        child_prefix = f"{prefix}.{key}" if prefix else key
        _render_toml_table(lines, child_prefix, value)
        if idx != len(nested_items) - 1:
            lines.append("")


def render_config_toml(config: BaseModel) -> str:
    payload = config.model_dump(mode="json", exclude_none=True)
    lines: list[str] = []
    _render_toml_table(lines, "", payload)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def render_dataset_config_toml(config: BaseModel) -> str:
    payload = config.model_dump(mode="json", exclude_none=True)
    entries = payload.pop("entries")
    lines: list[str] = []
    _render_toml_table(lines, "", payload)
    if lines and lines[-1]:
        lines.append("")
    for idx, entry in enumerate(entries):
        lines.append("[[entries]]")
        if "config" in entry:
            lines.append(f"config = {_render_toml_value(entry['config'])}")
        lines.append(f"count = {_render_toml_value(entry['count'])}")
        scenario = entry.get("scenario")
        if scenario is not None:
            lines.append("")
            _render_toml_table(lines, "entries.scenario", scenario)
        if idx != len(entries) - 1:
            lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def parse_difficulty_mix(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for chunk in raw.split(","):
        key, value = chunk.strip().split(":")
        result[key.strip()] = int(value.strip())
    return result
