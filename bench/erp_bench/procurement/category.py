from __future__ import annotations

import logging
import time

from pydantic import BaseModel, ConfigDict

from ..generation.models import (
    CategoryMetadata,
    EnvironmentSpec,
    GenerationCase,
    GenerationDefaults,
    GenerationOptions,
    JsonOutputFile,
    RenderedTemplateFile,
    TaskMetadata,
    TaskRenderSpec,
)
from ..harbor import HarborTaskExporter
from .config import (
    ProcurementDatasetConfig,
    ProcurementScenarioConfig,
)
from .presets import DIFFICULTY_PRESETS
from .sampler import (
    DEFAULT_BASE_SEED,
    ProcurementSampler,
    _build_and_solve,
    sampler_settings_from_config,
)
from .solver import ScenarioBlueprint, ScenarioBuild

logger = logging.getLogger(__name__)


class ProcurementBuild(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    config: ProcurementScenarioConfig
    blueprint: ScenarioBlueprint
    scenario_build: ScenarioBuild
    seed: int


class ProcurementCategory:
    metadata = CategoryMetadata(
        key="procurement",
        default_dataset_version="1.0",
        defaults=GenerationDefaults(
            base_seed=DEFAULT_BASE_SEED,
            start_number=1000,
            difficulty_mix={"easy": 75, "medium": 120, "hard": 105},
        ),
    )
    scenario_config_type = ProcurementScenarioConfig
    dataset_config_type = ProcurementDatasetConfig
    presets = {key: preset.config for key, preset in DIFFICULTY_PRESETS.items()}

    def build(self, case: GenerationCase, options: GenerationOptions) -> ProcurementBuild:
        config = self._validated_config(case.config)
        settings = sampler_settings_from_config(config)
        sampler = ProcurementSampler(seed=case.seed, settings=settings)
        sample_started = time.perf_counter()
        blueprint = sampler.build_blueprint(case.scenario_number)
        logger.info(
            "Sampled procurement blueprint scenario=%s name=%s objective=%s in %.2fs",
            blueprint.scenario_number,
            blueprint.name,
            blueprint.objective_kind.value,
            time.perf_counter() - sample_started,
        )
        solve_started = time.perf_counter()
        logger.info(
            "Solving procurement blueprint scenario=%s seed=%s workers=%s",
            blueprint.scenario_number,
            case.seed,
            options.num_search_workers,
        )
        solved_blueprint, scenario_build, used_seed = _build_and_solve(
            blueprint=blueprint,
            seed=case.seed,
            num_search_workers=options.num_search_workers,
            settings=settings,
            include_adjacent_data=config.export.include_adjacent_data,
        )
        logger.info(
            "Solved procurement scenario=%s used_seed=%s objective=%.2f in %.2fs",
            solved_blueprint.scenario_number,
            used_seed,
            scenario_build.optimal_plan.objective_value,
            time.perf_counter() - solve_started,
        )
        return ProcurementBuild(
            config=config,
            blueprint=solved_blueprint,
            scenario_build=scenario_build,
            seed=used_seed,
        )

    def render(self, case: GenerationCase, build: object) -> TaskRenderSpec:
        procurement_build = self._validated_build(build)
        scenario = procurement_build.scenario_build.scenario
        plan = procurement_build.scenario_build.optimal_plan
        blueprint = procurement_build.blueprint
        difficulty = procurement_build.config.difficulty
        task_name = f"{scenario.scenario_number}_{difficulty}"
        if case.task_pattern is not None:
            task_name = f"{task_name}_{case.task_pattern}"

        tags = ["odoo", "erp-bench", "procurement"]
        if blueprint.unsat_demand:
            tags.append("unsat_demand")

        ctx = self._template_context(
            scenario=scenario,
            plan=plan,
            blueprint=blueprint,
        )
        return TaskRenderSpec(
            task_name=task_name,
            scenario=scenario,
            metadata=TaskMetadata(
                scenario_number=scenario.scenario_number,
                scenario_name=scenario.name,
                difficulty=difficulty,
                tags=tuple(tags),
                seed=scenario.seed,
                objective_kind=blueprint.objective_kind.value,
                task_pattern=case.task_pattern,
            ),
            environment=EnvironmentSpec(
                setup_template="procurement/supply_planning/setup_scenario.py.jinja2",
            ),
            files=(
                RenderedTemplateFile(
                    path="instruction.md",
                    template="procurement/supply_planning/instruction.md.jinja2",
                    context={
                        "scenario_name": scenario.name,
                        "instruction": scenario.instruction,
                        "background_and_policy": scenario.background_and_policy or "",
                    },
                ),
                RenderedTemplateFile(
                    path="tests/test.sh",
                    template="procurement/supply_planning/test.sh.jinja2",
                    context=ctx,
                    executable=True,
                ),
                RenderedTemplateFile(
                    path="tests/checks.py",
                    template="procurement/supply_planning/checks.py.jinja2",
                    context=ctx,
                    executable=True,
                ),
                RenderedTemplateFile(
                    path="solution/solver.py",
                    template="procurement/supply_planning/solver.py.jinja2",
                    context=ctx,
                ),
                RenderedTemplateFile(
                    path="solution/solve.sh",
                    template="procurement/supply_planning/solve.sh.jinja2",
                    context=ctx,
                    executable=True,
                ),
            ),
            json_files=(
                JsonOutputFile(
                    path="solution/optimal_plan.json",
                    payload=plan,
                ),
            ),
        )

    def dataset_template(self) -> ProcurementDatasetConfig:
        return ProcurementDatasetConfig(
            kind="procurement_dataset",
            seed=DEFAULT_BASE_SEED,
            start_number=self.metadata.defaults.start_number,
            entries=(
                {"count": 2, "scenario": DIFFICULTY_PRESETS["easy"].config},
                {"count": 1, "scenario": DIFFICULTY_PRESETS["medium"].config},
            ),
        )

    def list_topology_combinations(self) -> list[str]:
        from .config import SUPPORTED_WORKCENTER_CHOICE_PATHS_BY_BOM_STRUCTURE

        rows = ["single_bom + workcenter_count=1"]
        for bom_structure, paths in SUPPORTED_WORKCENTER_CHOICE_PATHS_BY_BOM_STRUCTURE.items():
            for path in paths:
                rows.append(f"{bom_structure.value} + workcenter_count>1 + {path.value}")
        return rows

    @staticmethod
    def _validated_config(config: BaseModel) -> ProcurementScenarioConfig:
        if not isinstance(config, ProcurementScenarioConfig):
            raise TypeError(f"Expected ProcurementScenarioConfig, got {type(config).__name__}")
        return config

    @staticmethod
    def _validated_build(build: object) -> ProcurementBuild:
        if not isinstance(build, ProcurementBuild):
            raise TypeError(f"Expected ProcurementBuild, got {type(build).__name__}")
        return build

    @staticmethod
    def _template_context(*, scenario, plan, blueprint) -> dict:
        return HarborTaskExporter._build_template_context(
            scenario,
            plan,
            blueprint,
        )
