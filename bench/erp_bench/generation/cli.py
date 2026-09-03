from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import BaseModel

from .config import (
    detect_category,
    load_config,
    load_dataset_config,
    parse_difficulty_mix,
    render_config_toml,
    render_dataset_config_toml,
)
from .models import GenerationDefaults, GenerationOptions, TaskCategory
from .runner import (
    expand_dataset_config,
    expand_difficulty_mix,
    expand_scenario_config,
    generate_cases,
)

logger = logging.getLogger(__name__)


def _load_procurement_category() -> TaskCategory:
    from ..procurement.category import ProcurementCategory

    return ProcurementCategory()


_CATEGORY_LOADERS = {
    "procurement": _load_procurement_category,
}


def _argv_mentions_option(argv: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(f"{option}=") for arg in argv)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SaaS-Bench tasks")
    parser.add_argument("--category", choices=sorted(_CATEGORY_LOADERS), default=None)
    parser.add_argument("--output", type=Path, default=Path("tasks"))
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--dataset-config", type=Path, default=None)
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--start-number", type=int, default=None)
    parser.add_argument("--dataset-name", type=str, default=None)
    parser.add_argument("--dataset-version", type=str, default=None)
    parser.add_argument("--difficulty-mix", type=str, default=None)
    parser.add_argument("--pattern", type=str, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--num-search-workers", type=int, default=8)
    parser.add_argument("--metrics-output", type=Path, default=None)
    parser.add_argument("--difficulty", choices=["easy", "medium", "hard"], default=None)
    parser.add_argument("--print-config-schema", action="store_true")
    parser.add_argument("--print-config-template", action="store_true")
    parser.add_argument("--print-dataset-config-template", action="store_true")
    parser.add_argument("--print-difficulty-presets", action="store_true")
    parser.add_argument("--list-topology-combinations", action="store_true")
    return parser


def _resolve_category_key(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    config_category: str | None = None
    config_path: Path | None = None
    if args.config is not None:
        config_path = args.config
        try:
            config_category = detect_category(args.config, dataset=False)
        except ValueError as exc:
            parser.error(str(exc))
    if args.dataset_config is not None:
        config_path = args.dataset_config
        try:
            config_category = detect_category(args.dataset_config, dataset=True)
        except ValueError as exc:
            parser.error(str(exc))

    if args.category is not None and config_category is not None and args.category != config_category:
        parser.error(f"--category {args.category} does not match {config_path}: {config_category}")
    if config_category is not None and config_category not in _CATEGORY_LOADERS:
        parser.error(f"{config_path} declares unsupported category {config_category!r}; only procurement is available")
    if config_category is not None:
        return config_category
    if args.category is not None:
        return args.category
    parser.error("--category is required unless --config or --dataset-config is provided")
    raise AssertionError("unreachable")


def _presets_for_pattern(category: TaskCategory, pattern: str | None) -> dict[str, BaseModel]:
    if pattern is None:
        return dict(category.presets)
    if not hasattr(category, "presets_for_pattern"):
        raise SystemExit(f"{category.metadata.key} does not define patterns")
    return dict(category.presets_for_pattern(pattern))  # type: ignore[attr-defined]


def _generation_defaults(category: TaskCategory, pattern: str | None) -> GenerationDefaults:
    if pattern is None:
        return category.metadata.defaults
    defaults = category.metadata.pattern_defaults.get(pattern)
    if defaults is None:
        raise SystemExit(f"{category.metadata.key} does not define defaults for {pattern}")
    return defaults


def _handle_prints(args: argparse.Namespace, category: TaskCategory) -> bool:
    if args.print_config_schema:
        print(json.dumps(category.scenario_config_type.model_json_schema(), indent=2))
        return True
    if args.print_config_template:
        if args.difficulty is None:
            raise SystemExit("--print-config-template requires --difficulty")
        if category.metadata.patterns and args.pattern is None:
            raise SystemExit(f"--print-config-template for {category.metadata.key} requires --pattern")
        config = _presets_for_pattern(category, args.pattern)[args.difficulty]
        print(render_config_toml(config), end="")
        return True
    if args.print_dataset_config_template:
        if not hasattr(category, "dataset_template"):
            raise SystemExit(f"{category.metadata.key} does not define a dataset template")
        dataset = category.dataset_template()  # type: ignore[attr-defined]
        print(render_dataset_config_toml(dataset), end="")
        return True
    if args.print_difficulty_presets:
        presets = _presets_for_pattern(category, args.pattern)
        for idx, (key, preset) in enumerate(presets.items()):
            if idx:
                print()
            print(f"# {key}")
            print(render_config_toml(preset), end="")
        return True
    if args.list_topology_combinations:
        if not hasattr(category, "list_topology_combinations"):
            raise SystemExit(f"{category.metadata.key} does not define topology combinations")
        for row in category.list_topology_combinations():  # type: ignore[attr-defined]
            print(row)
        return True
    return False


def _dataset_version(
    dataset_config: BaseModel | None, category: TaskCategory, explicit: str | None
) -> str:
    if explicit is not None:
        return explicit
    if dataset_config is not None:
        version = getattr(dataset_config, "dataset_version", None)
        if version is not None:
            return version
    return category.metadata.default_dataset_version


def _dataset_name(dataset_config: BaseModel | None, explicit: str | None) -> str:
    if explicit is not None:
        return explicit
    if dataset_config is not None:
        name = getattr(dataset_config, "dataset_name", None)
        if name is not None:
            return name
    return "erp-bench"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = _build_parser()
    raw_argv = sys.argv[1:]
    args = parser.parse_args()
    category_key = _resolve_category_key(args, parser)

    category = _CATEGORY_LOADERS[category_key]()

    if args.pattern is not None and not category.metadata.patterns:
        parser.error(f"--category {category.metadata.key} does not define patterns")
    if args.pattern is not None and args.pattern not in category.metadata.patterns:
        parser.error(
            f"--pattern must be one of {', '.join(category.metadata.patterns)} "
            f"for --category {category.metadata.key}"
        )

    if _handle_prints(args, category):
        return

    if args.config is not None and args.dataset_config is not None:
        parser.error("--config cannot be combined with --dataset-config")
    if args.pattern is not None and args.config is not None:
        parser.error("--pattern cannot be combined with --config")
    if args.pattern is not None and args.dataset_config is not None:
        parser.error("--pattern cannot be combined with --dataset-config")

    if args.dataset_config is not None:
        conflicts: list[str] = []
        for option in (
            "--count",
            "--seed",
            "--start-number",
            "--difficulty-mix",
            "--difficulty",
            "--pattern",
        ):
            if _argv_mentions_option(raw_argv, option):
                conflicts.append(option)
        if conflicts:
            parser.error(f"--dataset-config cannot be combined with {', '.join(conflicts)}")
        dataset_config = load_dataset_config(args.dataset_config, category.dataset_config_type)
        cases = expand_dataset_config(
            dataset_config=dataset_config,
            scenario_config_type=category.scenario_config_type,
        )
    elif args.config is not None:
        if args.difficulty_mix is not None:
            parser.error("--config cannot be combined with --difficulty-mix")
        if args.difficulty is not None:
            parser.error("--config cannot be combined with --difficulty")
        config = load_config(args.config, category.scenario_config_type)
        cases = expand_scenario_config(
            config=config,
            count=args.count or 1,
            base_seed=(
                args.seed
                if args.seed is not None
                else _generation_defaults(category, getattr(config, "pattern", None)).base_seed
            ),
            start_number=(
                args.start_number
                if args.start_number is not None
                else _generation_defaults(category, getattr(config, "pattern", None)).start_number
            ),
        )
        dataset_config = None
    else:
        if category.metadata.patterns and args.pattern is None:
            parser.error(
                f"--category {category.metadata.key} requires --pattern when generating without "
                "--config or --dataset-config"
            )
        if args.difficulty_mix is not None and args.count is not None:
            parser.error("--difficulty-mix cannot be combined with --count")
        if args.difficulty_mix is not None and args.difficulty is not None:
            parser.error("--difficulty-mix cannot be combined with --difficulty")
        difficulty_counts = (
            parse_difficulty_mix(args.difficulty_mix)
            if args.difficulty_mix is not None
            else dict(_generation_defaults(category, args.pattern).difficulty_mix)
        )
        if args.difficulty is not None:
            difficulty_counts = {args.difficulty: args.count or 1}
        elif args.count is not None:
            ordered = list(_presets_for_pattern(category, args.pattern))
            difficulty_counts = dict.fromkeys(ordered, 0)
            for idx in range(args.count):
                difficulty_counts[ordered[idx % len(ordered)]] += 1
            difficulty_counts = {key: value for key, value in difficulty_counts.items() if value}
        cases = expand_difficulty_mix(
            presets=_presets_for_pattern(category, args.pattern),
            difficulty_counts=difficulty_counts,
            base_seed=(
                args.seed
                if args.seed is not None
                else _generation_defaults(category, args.pattern).base_seed
            ),
            start_number=(
                args.start_number
                if args.start_number is not None
                else _generation_defaults(category, args.pattern).start_number
            ),
        )
        dataset_config = None

    exported = generate_cases(
        category=category,
        cases=cases,
        output_dir=args.output,
        dataset_name=_dataset_name(dataset_config, args.dataset_name),
        dataset_version=_dataset_version(dataset_config, category, args.dataset_version),
        force=args.force,
        options=GenerationOptions(num_search_workers=args.num_search_workers),
        metrics_output=args.metrics_output,
    )
    logger.info(
        "Generated %d %s task(s) in %s",
        len(exported),
        category.metadata.key,
        args.output.resolve(),
    )


if __name__ == "__main__":
    main()
