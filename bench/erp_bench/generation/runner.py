from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .models import Difficulty, GenerationCase, GenerationOptions, TaskCategory
from .render import HarborRenderer

logger = logging.getLogger(__name__)


class TaskGenerationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_number: int
    task_name: str
    difficulty: str
    task_pattern: str | None
    initial_seed: int
    used_seed: int | None
    solve_seconds: float
    pre_solver_discards: int
    solver_discards: int
    discarded_samples: int
    discard_messages: list[str]
    solver_models: list[dict[str, Any]] = Field(default_factory=list)
    solver_calls: list[dict[str, Any]] = Field(default_factory=list)
    model_builds: int = 0
    max_model_variables: int | None = None
    max_tie_break_variables: int | None = None
    total_solve_calls: int = 0
    total_solver_wall_seconds: float = 0.0
    solve_status_counts: dict[str, int] = Field(default_factory=dict)


class DatasetGenerationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: str
    dataset_name: str
    dataset_version: str
    category: str
    total_tasks: int
    generated_tasks: int
    pre_solver_discards: int
    solver_discards: int
    discarded_samples: int
    resampled_tasks: int
    average_solve_seconds: float | None
    total_solve_seconds: float
    wall_seconds: float
    started_at: str
    finished_at: str | None
    tasks: list[TaskGenerationMetrics] = Field(default_factory=list)


class GenerationDiscardEvents(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pre_solver_messages: list[str] = Field(default_factory=list)
    solver_messages: list[str] = Field(default_factory=list)


@contextmanager
def _capture_discard_events() -> Iterator[GenerationDiscardEvents]:
    events = GenerationDiscardEvents()

    class ResampleWarningHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = record.getMessage()
            if message.startswith("Resampling scenario="):
                events.pre_solver_messages.append(message)
            if " rejected " in message and "resampling" in message:
                events.solver_messages.append(message)

    handler = ResampleWarningHandler(level=logging.INFO)
    root_logger = logging.getLogger("erp_bench")
    root_logger.addHandler(handler)
    try:
        yield events
    finally:
        root_logger.removeHandler(handler)


def _write_generation_metrics(
    metrics_output: Path,
    *,
    output_dir: Path,
    dataset_name: str,
    dataset_version: str,
    category: str,
    total_tasks: int,
    started_at: str,
    started_perf: float,
    finished_at: str | None,
    task_metrics: list[TaskGenerationMetrics],
) -> None:
    total_solve_seconds = sum(metric.solve_seconds for metric in task_metrics)
    average_solve_seconds = (
        total_solve_seconds / len(task_metrics) if task_metrics else None
    )
    payload = DatasetGenerationMetrics(
        output_dir=str(output_dir),
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        category=category,
        total_tasks=total_tasks,
        generated_tasks=len(task_metrics),
        pre_solver_discards=sum(metric.pre_solver_discards for metric in task_metrics),
        solver_discards=sum(metric.solver_discards for metric in task_metrics),
        discarded_samples=sum(metric.discarded_samples for metric in task_metrics),
        resampled_tasks=sum(1 for metric in task_metrics if metric.discarded_samples),
        average_solve_seconds=average_solve_seconds,
        total_solve_seconds=total_solve_seconds,
        wall_seconds=time.perf_counter() - started_perf,
        started_at=started_at,
        finished_at=finished_at,
        tasks=task_metrics,
    )
    metrics_output.parent.mkdir(parents=True, exist_ok=True)
    metrics_output.write_text(json.dumps(payload.model_dump(mode="json"), indent=2) + "\n")


def _solver_trace_payload(trace: object | None) -> dict[str, Any]:
    if trace is None:
        return {
            "solver_models": [],
            "solver_calls": [],
            "model_builds": 0,
            "max_model_variables": None,
            "max_tie_break_variables": None,
            "total_solve_calls": 0,
            "total_solver_wall_seconds": 0.0,
            "solve_status_counts": {},
        }
    payload = trace.model_dump(mode="json")  # type: ignore[attr-defined]
    solver_models = payload["models"]
    solver_calls = payload["solve_calls"]
    status_counts: dict[str, int] = {}
    for call in solver_calls:
        status = call["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "solver_models": solver_models,
        "solver_calls": solver_calls,
        "model_builds": len(solver_models),
        "max_model_variables": (
            max(model["variables"] for model in solver_models) if solver_models else None
        ),
        "max_tie_break_variables": (
            max(model["tie_break_variables"] for model in solver_models)
            if solver_models
            else None
        ),
        "total_solve_calls": len(solver_calls),
        "total_solver_wall_seconds": sum(
            call["solver_wall_seconds"] for call in solver_calls
        ),
        "solve_status_counts": status_counts,
    }


def derive_seeds(base_seed: int, count: int) -> list[int]:
    rng = np.random.default_rng(base_seed)
    return [int(rng.integers(0, 2**63)) for _ in range(count)]


def expand_scenario_config(
    *,
    config: BaseModel,
    count: int,
    base_seed: int | None,
    start_number: int,
) -> list[GenerationCase]:
    if count <= 0:
        raise ValueError("count must be greater than 0")
    seeds = (
        [secrets.randbits(63) for _ in range(count)]
        if base_seed is None
        else derive_seeds(base_seed, count)
    )
    difficulty = cast(Difficulty, config.difficulty)  # type: ignore[attr-defined]
    return [
        GenerationCase(
            scenario_number=start_number + idx,
            seed=seed,
            difficulty=difficulty,
            config=config,
        )
        for idx, seed in enumerate(seeds)
    ]


def expand_difficulty_mix(
    *,
    presets: dict[str, BaseModel],
    difficulty_counts: dict[str, int],
    base_seed: int | None,
    start_number: int,
) -> list[GenerationCase]:
    total = sum(difficulty_counts.values())
    if total <= 0:
        raise ValueError("difficulty_counts must produce at least one task")
    seeds = (
        [secrets.randbits(63) for _ in range(total)]
        if base_seed is None
        else derive_seeds(base_seed, total)
    )
    cases: list[GenerationCase] = []
    scenario_number = start_number
    seed_idx = 0
    for difficulty, count in difficulty_counts.items():
        if difficulty not in presets:
            raise ValueError(f"Unknown difficulty profile: {difficulty}")
        if count <= 0:
            raise ValueError("difficulty counts must be greater than 0")
        config = presets[difficulty]
        for _ in range(count):
            cases.append(
                GenerationCase(
                    scenario_number=scenario_number,
                    seed=seeds[seed_idx],
                    difficulty=cast(Difficulty, difficulty),
                    config=config,
                )
            )
            scenario_number += 1
            seed_idx += 1
    return cases


def expand_dataset_config(
    *,
    dataset_config: BaseModel,
    scenario_config_type: type[BaseModel],
) -> list[GenerationCase]:
    entries = tuple(dataset_config.entries)  # type: ignore[attr-defined]
    if not entries:
        raise ValueError("entries must contain at least one dataset entry")
    total = sum(entry.count for entry in entries)
    seeds = derive_seeds(dataset_config.seed, total)  # type: ignore[attr-defined]
    cases: list[GenerationCase] = []
    scenario_number = dataset_config.start_number  # type: ignore[attr-defined]
    seed_idx = 0
    for entry in entries:
        count = entry.count
        if count <= 0:
            raise ValueError("entry count must be greater than 0")
        source_config_path: Path | None = entry.config
        if source_config_path is not None:
            config = load_config(source_config_path, scenario_config_type)
            task_pattern = source_config_path.stem
        else:
            config = entry.scenario
            if config is None:
                raise ValueError("dataset entry requires exactly one of config or scenario")
            task_pattern = None
        difficulty = cast(Difficulty, config.difficulty)  # type: ignore[attr-defined]
        for _ in range(count):
            cases.append(
                GenerationCase(
                    scenario_number=scenario_number,
                    seed=seeds[seed_idx],
                    difficulty=difficulty,
                    config=config,
                    task_pattern=task_pattern,
                    source_config_path=source_config_path,
                )
            )
            scenario_number += 1
            seed_idx += 1
    return cases


def generate_cases(
    *,
    category: TaskCategory,
    cases: list[GenerationCase],
    output_dir: Path,
    dataset_name: str,
    dataset_version: str,
    force: bool,
    options: GenerationOptions,
    metrics_output: Path | None = None,
) -> list[Path]:
    logger.info(
        "Generating %s %s task(s) in %s (dataset=%s version=%s solver_workers=%s)",
        len(cases),
        category.metadata.key,
        output_dir,
        dataset_name,
        dataset_version,
        options.num_search_workers,
    )
    renderer = HarborRenderer(
        output_dir=output_dir,
        force=force,
    )
    exported: list[Path] = []
    task_metrics: list[TaskGenerationMetrics] = []
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    for index, case in enumerate(cases, start=1):
        pattern = f" pattern={case.task_pattern}" if case.task_pattern is not None else ""
        logger.info(
            "Solving %s/%s scenario=%s difficulty=%s seed=%s%s",
            index,
            len(cases),
            case.scenario_number,
            case.difficulty,
            case.seed,
            pattern,
        )
        case_started = time.perf_counter()
        with ExitStack() as stack:
            discard_events = stack.enter_context(_capture_discard_events())
            solver_trace = None
            if category.metadata.key == "procurement":
                from ..procurement.solver import capture_procurement_solver_trace

                solver_trace = stack.enter_context(capture_procurement_solver_trace())
            build = category.build(case, options)
        solve_seconds = time.perf_counter() - case_started
        spec = category.render(case, build)
        task_dir = renderer.write(spec)
        if task_dir is not None:
            exported.append(task_dir)
            logger.info(
                "Exported %s in %.2fs",
                task_dir.name,
                solve_seconds,
            )
        else:
            logger.info(
                "Skipped scenario=%s in %.2fs",
                case.scenario_number,
                solve_seconds,
            )
        if metrics_output is not None:
            solver_metrics = _solver_trace_payload(solver_trace)
            task_metrics.append(
                TaskGenerationMetrics(
                    scenario_number=case.scenario_number,
                    task_name=spec.task_name,
                    difficulty=case.difficulty,
                    task_pattern=case.task_pattern,
                    initial_seed=case.seed,
                    used_seed=getattr(build, "seed", None),
                    solve_seconds=solve_seconds,
                    pre_solver_discards=len(discard_events.pre_solver_messages),
                    solver_discards=len(discard_events.solver_messages),
                    discarded_samples=(
                        len(discard_events.pre_solver_messages)
                        + len(discard_events.solver_messages)
                    ),
                    discard_messages=[
                        *discard_events.pre_solver_messages,
                        *discard_events.solver_messages,
                    ],
                    **solver_metrics,
                )
            )
            _write_generation_metrics(
                metrics_output,
                output_dir=output_dir,
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                category=category.metadata.key,
                total_tasks=len(cases),
                started_at=started_at,
                started_perf=started,
                finished_at=None,
                task_metrics=task_metrics,
            )
    if metrics_output is not None:
        _write_generation_metrics(
            metrics_output,
            output_dir=output_dir,
            dataset_name=dataset_name,
            dataset_version=dataset_version,
            category=category.metadata.key,
            total_tasks=len(cases),
            started_at=started_at,
            started_perf=started,
            finished_at=datetime.now(UTC).isoformat(),
            task_metrics=task_metrics,
        )
        logger.info("Wrote generation metrics to %s", metrics_output)
    logger.info(
        "Generated %s/%s %s task(s) in %.2fs",
        len(exported),
        len(cases),
        category.metadata.key,
        time.perf_counter() - started,
    )
    return exported
