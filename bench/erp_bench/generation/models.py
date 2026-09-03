from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Difficulty = Literal["easy", "medium", "hard"]


def _non_negative_int_range(value: tuple[int, int]) -> tuple[int, int]:
    low, high = value
    if low < 0:
        raise ValueError("lower bound must be non-negative")
    if low > high:
        raise ValueError("lower bound must be <= upper bound")
    return value


def _positive_int_range(value: tuple[int, int]) -> tuple[int, int]:
    low, high = value
    if low < 1:
        raise ValueError("lower bound must be >= 1")
    if low > high:
        raise ValueError("lower bound must be <= upper bound")
    return value


NonNegativeIntRange = Annotated[tuple[int, int], AfterValidator(_non_negative_int_range)]
PositiveIntRange = Annotated[tuple[int, int], AfterValidator(_positive_int_range)]


class GenerationModel(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )


class GenerationCase(GenerationModel):
    scenario_number: int
    seed: int
    difficulty: Difficulty
    config: BaseModel
    task_pattern: str | None = None
    source_config_path: Path | None = None


class GenerationOptions(GenerationModel):
    num_search_workers: int = Field(default=8, ge=8)


class ProfileScenarioConfig(GenerationModel):
    category: str
    difficulty: Difficulty


class ProfileDatasetEntry(GenerationModel):
    config: Path | None = None
    scenario: ProfileScenarioConfig | None = None
    count: int = Field(gt=0)

    @model_validator(mode="after")
    def require_one_scenario_source(self) -> ProfileDatasetEntry:
        if (self.config is None) == (self.scenario is None):
            raise ValueError("dataset entry requires exactly one of config or scenario")
        return self


class ProfileDatasetConfig(GenerationModel):
    category: str
    kind: str = "profile_dataset"
    seed: int = 42
    start_number: int
    dataset_name: str | None = None
    dataset_version: str = "1.0"
    entries: tuple[ProfileDatasetEntry, ...] = Field(min_length=1)


class GenerationDefaults(GenerationModel):
    base_seed: int = 42
    start_number: int
    difficulty_mix: Mapping[Difficulty, int]


class CategoryMetadata(GenerationModel):
    key: str
    default_dataset_version: str = "1.0"
    defaults: GenerationDefaults
    patterns: tuple[str, ...] = ()
    pattern_defaults: Mapping[str, GenerationDefaults] = {}


class TaskMetadata(GenerationModel):
    scenario_number: int
    scenario_name: str
    difficulty: Difficulty
    tags: tuple[str, ...]
    seed: int | None = None
    category: str = "erp"
    objective_kind: str | None = None
    task_pattern: str | None = None
    build_timeout_sec: float = 600.0
    cpus: int = 3
    memory_mb: int = 4096
    storage_mb: int = 2048


class EnvironmentSpec(GenerationModel):
    setup_template: str
    template_context: Mapping[str, Any] = Field(default_factory=dict)
    runtime_db_name: str = "bench"
    install_native_xlsx_support: bool = False
    admin_user: str = "admin"
    admin_password: str = "pass"


class RenderedTemplateFile(GenerationModel):
    path: str
    template: str
    context: Mapping[str, Any]
    executable: bool = False


class JsonOutputFile(GenerationModel):
    path: str
    payload: Any


class TaskRenderSpec(GenerationModel):
    task_name: str
    scenario: BaseModel
    metadata: TaskMetadata
    environment: EnvironmentSpec
    files: tuple[RenderedTemplateFile, ...]
    json_files: tuple[JsonOutputFile, ...] = ()


class TaskCategory(Protocol):
    metadata: CategoryMetadata
    scenario_config_type: type[BaseModel]
    dataset_config_type: type[BaseModel]
    presets: Mapping[str, BaseModel]

    def build(self, case: GenerationCase, options: GenerationOptions) -> object: ...

    def render(self, case: GenerationCase, build: object) -> TaskRenderSpec: ...
