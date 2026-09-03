"""Shared task generation primitives."""

from .models import (
    CategoryMetadata,
    EnvironmentSpec,
    GenerationCase,
    GenerationDefaults,
    GenerationOptions,
    JsonOutputFile,
    RenderedTemplateFile,
    TaskCategory,
    TaskMetadata,
    TaskRenderSpec,
)
from .runner import (
    derive_seeds,
    expand_dataset_config,
    expand_difficulty_mix,
    expand_scenario_config,
    generate_cases,
)

__all__ = [
    "CategoryMetadata",
    "EnvironmentSpec",
    "GenerationDefaults",
    "GenerationCase",
    "GenerationOptions",
    "JsonOutputFile",
    "RenderedTemplateFile",
    "TaskCategory",
    "TaskMetadata",
    "TaskRenderSpec",
    "derive_seeds",
    "expand_dataset_config",
    "expand_difficulty_mix",
    "expand_scenario_config",
    "generate_cases",
]
