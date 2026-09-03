"""Public procurement config schema."""

from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from schemas.procurement_invoicing_validation import validate_procurement_invoicing_policy

from .procurement_objectives import ProcurementObjectiveKind

DifficultyKey = Literal["easy", "medium", "hard"]


class AvailableRoutes(StrEnum):
    buy_only = "buy_only"
    buy_and_manufacture = "buy_and_manufacture"
    manufacture_only_policy_forbidden = "manufacture_only_policy_forbidden"
    manufacture_only_no_buy_route = "manufacture_only_no_buy_route"
    manufacture_only_no_available_vendors = "manufacture_only_no_available_vendors"

    @property
    def requires_manufacture(self) -> bool:
        return self is not AvailableRoutes.buy_only


class OrderAcceptanceMode(StrEnum):
    accept_all_orders = "accept_all_orders"
    screen_orders_by_policy = "screen_orders_by_policy"


class SeededOrderCoverage(StrEnum):
    all_seeded_orders = "all_seeded_orders"
    mixed_seeded_and_prompt_only = "mixed_seeded_and_prompt_only"


class RejectRule(StrEnum):
    budget_below_list_price = "budget_below_list_price"
    quantity_outside_window = "quantity_outside_window"
    lead_time_below_minimum = "lead_time_below_minimum"


class InvoicingPaymentTerm(StrEnum):
    immediate = "immediate"
    net_30 = "net_30"


class DownpaymentMode(StrEnum):
    percentage = "percentage"
    fixed_amount = "fixed_amount"


class RepairScenarioKind(StrEnum):
    supplier_cancellation = "supplier_cancellation"
    workcenter_outage = "workcenter_outage"


class BomStructure(StrEnum):
    single_bom = "single_bom"
    restricted_subassembly = "restricted_subassembly"
    single_subassembly = "single_subassembly"
    parallel_subassemblies = "parallel_subassemblies"
    serial_subassemblies = "serial_subassemblies"
    shared_component_subassemblies = "shared_component_subassemblies"


class WorkcenterChoicePath(StrEnum):
    lowest_cost = "lowest_cost"
    qualified_workcenters = "qualified_workcenters"
    split_by_capacity = "split_by_capacity"
    shared_overflow_capacity = "shared_overflow_capacity"
    branch_assigned_workcenters = "branch_assigned_workcenters"


SUPPORTED_WORKCENTER_CHOICE_PATHS_BY_BOM_STRUCTURE: dict[
    BomStructure, tuple[WorkcenterChoicePath, ...]
] = {
    BomStructure.single_bom: (
        WorkcenterChoicePath.lowest_cost,
        WorkcenterChoicePath.split_by_capacity,
    ),
    BomStructure.restricted_subassembly: (WorkcenterChoicePath.qualified_workcenters,),
    BomStructure.single_subassembly: (
        WorkcenterChoicePath.lowest_cost,
        WorkcenterChoicePath.qualified_workcenters,
        WorkcenterChoicePath.shared_overflow_capacity,
    ),
    BomStructure.parallel_subassemblies: (WorkcenterChoicePath.branch_assigned_workcenters,),
    BomStructure.serial_subassemblies: (WorkcenterChoicePath.branch_assigned_workcenters,),
    BomStructure.shared_component_subassemblies: (
        WorkcenterChoicePath.branch_assigned_workcenters,
    ),
}


class ProcurementConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductDomainSelection(StrEnum):
    default = "default"
    sound_panels = "sound_panels"
    server_racks = "server_racks"
    conveyor_modules = "conveyor_modules"
    workstations = "workstations"
    lab_benches = "lab_benches"
    cable_assemblies = "cable_assemblies"
    hvac_units = "hvac_units"
    led_fixtures = "led_fixtures"
    packaging_machines = "packaging_machines"
    safety_enclosures = "safety_enclosures"
    solar_panels = "solar_panels"
    robotic_arms = "robotic_arms"
    water_filters = "water_filters"
    elevator_parts = "elevator_parts"
    battery_packs = "battery_packs"
    air_compressors = "air_compressors"
    fire_systems = "fire_systems"
    clean_rooms = "clean_rooms"
    dock_levelers = "dock_levelers"
    transformer_units = "transformer_units"


def _non_negative_int_range(v: tuple[int, int]) -> tuple[int, int]:
    low, high = v
    if low < 0:
        raise ValueError("lower bound must be non-negative")
    if low > high:
        raise ValueError("lower bound must be <= upper bound")
    return v


def _positive_int_range(v: tuple[int, int]) -> tuple[int, int]:
    low, high = v
    if low < 1:
        raise ValueError("lower bound must be >= 1")
    if low > high:
        raise ValueError("lower bound must be <= upper bound")
    return v


def _float_range(v: tuple[float, float]) -> tuple[float, float]:
    low, high = v
    if low > high:
        raise ValueError("lower bound must be <= upper bound")
    return v


NonNegativeIntRange = Annotated[tuple[int, int], AfterValidator(_non_negative_int_range)]
PositiveIntRange = Annotated[tuple[int, int], AfterValidator(_positive_int_range)]
FloatRange = Annotated[tuple[float, float], AfterValidator(_float_range)]


class CustomersConfig(ProcurementConfigModel):
    count_range: PositiveIntRange
    deadline_range: PositiveIntRange
    demand_range: PositiveIntRange


class SupplyConfig(ProcurementConfigModel):
    stock_ratio_range: FloatRange
    finished_vendor_target_range: PositiveIntRange | None = None
    finished_vendor_limit: int | None = None
    include_components: bool = True
    component_count_range: PositiveIntRange | None = None
    component_vendor_category_count_range: PositiveIntRange = (2, 4)
    vendor_capacity_primary_ratio_range: FloatRange = (0.12, 0.55)
    sampling_tightness: float = 0.5

    @model_validator(mode="after")
    def validate_ranges(self) -> SupplyConfig:
        if self.finished_vendor_limit is not None and self.finished_vendor_limit < 1:
            raise ValueError("finished_vendor_limit must be >= 1")
        if not 0 <= self.sampling_tightness <= 1:
            raise ValueError("sampling_tightness must be between 0 and 1")
        if not self.include_components and self.component_count_range is not None:
            raise ValueError(
                "component_count_range must be omitted when include_components is false"
            )
        return self


class OrderAcceptanceConfig(ProcurementConfigModel):
    mode: OrderAcceptanceMode = OrderAcceptanceMode.accept_all_orders
    seeded_order_coverage: SeededOrderCoverage | None = None
    reject_rules: tuple[RejectRule, ...] = ()

    @model_validator(mode="after")
    def validate_mode(self) -> OrderAcceptanceConfig:
        if self.mode is OrderAcceptanceMode.accept_all_orders:
            if self.seeded_order_coverage is not None or self.reject_rules:
                raise ValueError(
                    "seeded_order_coverage and reject_rules must be omitted when mode is accept_all_orders"
                )
            return self
        if self.seeded_order_coverage is None:
            raise ValueError(
                "seeded_order_coverage is required when mode is screen_orders_by_policy"
            )
        if not self.reject_rules:
            raise ValueError(
                "reject_rules must list at least one rule when mode is screen_orders_by_policy"
            )
        if len(set(self.reject_rules)) != len(self.reject_rules):
            raise ValueError("reject_rules must not contain duplicates")
        return self


class InvoicingDownpaymentConfig(ProcurementConfigModel):
    order_threshold_amount: float = Field(gt=0)
    mode: DownpaymentMode
    value: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_value(self) -> InvoicingDownpaymentConfig:
        if self.mode is DownpaymentMode.percentage and self.value > 100:
            raise ValueError("percentage downpayment value must be <= 100")
        return self


class InvoicingConfig(ProcurementConfigModel):
    required: bool = False
    payment_term: InvoicingPaymentTerm | None = None
    downpayment: InvoicingDownpaymentConfig | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> InvoicingConfig:
        validate_procurement_invoicing_policy(
            invoice_required=self.required,
            payment_term=self.payment_term,
            downpayment_required=self.downpayment is not None,
            downpayment_threshold_amount=(
                float(self.downpayment.order_threshold_amount)
                if self.downpayment is not None
                else None
            ),
            downpayment_mode=self.downpayment.mode if self.downpayment is not None else None,
            downpayment_value=(
                float(self.downpayment.value) if self.downpayment is not None else None
            ),
            invoice_required_label="invoicing.required",
            downpayment_required_label="invoicing.downpayment",
            downpayment_fields_name="downpayment",
            downpayment_threshold_amount_label="order_threshold_amount",
            percentage_downpayment_value_label="downpayment value",
        )
        return self


class ManufactureConfig(ProcurementConfigModel):
    bom_structure: BomStructure
    workcenter_count: int
    workcenter_choice_path: WorkcenterChoicePath | None = None
    assembly_capacity_buffer_range: NonNegativeIntRange | None = None
    assembly_capacity_ratio_range: FloatRange | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ManufactureConfig:
        if self.workcenter_count < 1:
            raise ValueError("workcenter_count must be >= 1")
        if self.workcenter_count == 1 and self.workcenter_choice_path is not None:
            raise ValueError("workcenter_choice_path must be omitted when workcenter_count is 1")
        if self.workcenter_count == 1 and self.bom_structure is not BomStructure.single_bom:
            raise ValueError(
                "single-workcenter scenarios currently require bom_structure='single_bom'"
            )
        if self.workcenter_count > 1 and self.workcenter_choice_path is None:
            raise ValueError(
                "workcenter_choice_path is required when workcenter_count is greater than 1"
            )
        if self.workcenter_choice_path is not None:
            allowed_paths = SUPPORTED_WORKCENTER_CHOICE_PATHS_BY_BOM_STRUCTURE[self.bom_structure]
            if self.workcenter_choice_path not in allowed_paths:
                raise ValueError(
                    f"workcenter_choice_path={self.workcenter_choice_path.value} is not supported for "
                    f"bom_structure={self.bom_structure.value}"
                )
        return self


class ExportConfig(ProcurementConfigModel):
    product_domain: ProductDomainSelection = ProductDomainSelection.default
    include_adjacent_data: bool = True


class RepairConfig(ProcurementConfigModel):
    kind: RepairScenarioKind


class ProcurementScenarioConfig(ProcurementConfigModel):
    category: Literal["procurement"] = "procurement"
    difficulty: DifficultyKey
    available_routes: AvailableRoutes
    objective_kind: ProcurementObjectiveKind = ProcurementObjectiveKind.min_new_spend
    customers: CustomersConfig
    supply: SupplyConfig
    order_acceptance: OrderAcceptanceConfig = OrderAcceptanceConfig()
    manufacture: ManufactureConfig | None = None
    invoicing: InvoicingConfig = InvoicingConfig()
    repair: RepairConfig | None = None
    export: ExportConfig = ExportConfig()

    @model_validator(mode="after")
    def validate_routes(self) -> ProcurementScenarioConfig:
        if self.available_routes.requires_manufacture:
            if self.manufacture is None:
                raise ValueError(
                    "manufacture section is required when available_routes allows manufacture"
                )
        elif self.manufacture is not None:
            raise ValueError(
                "manufacture section must be omitted when available_routes is buy_only"
            )
        if (
            self.objective_kind is ProcurementObjectiveKind.constraint_only
            and self.difficulty != "easy"
        ):
            raise ValueError("constraint_only objective requires difficulty='easy'")
        if (
            self.objective_kind is ProcurementObjectiveKind.capacity_preservation
            and self.manufacture is None
        ):
            raise ValueError("capacity_preservation objective requires a manufacture section")
        if self.objective_kind is ProcurementObjectiveKind.repair_plan and self.repair is None:
            raise ValueError("repair_plan objective requires a repair section")
        if self.repair is not None:
            if self.objective_kind is not ProcurementObjectiveKind.repair_plan:
                raise ValueError("repair scenarios require objective_kind='repair_plan'")
            if self.order_acceptance.mode is not OrderAcceptanceMode.accept_all_orders:
                raise ValueError(
                    "repair scenarios require order_acceptance.mode='accept_all_orders'"
                )
            if (
                self.repair.kind is RepairScenarioKind.workcenter_outage
                and self.manufacture is None
            ):
                raise ValueError("workcenter_outage repair scenarios require a manufacture section")
        return self


class ProcurementDatasetEntry(ProcurementConfigModel):
    count: int = Field(gt=0)
    config: Path | None = None
    scenario: ProcurementScenarioConfig | None = None

    @model_validator(mode="after")
    def validate_source(self) -> ProcurementDatasetEntry:
        if (self.config is None) == (self.scenario is None):
            raise ValueError("dataset entry requires exactly one of config or scenario")
        return self


class ProcurementDatasetConfig(ProcurementConfigModel):
    category: Literal["procurement"] = "procurement"
    kind: Literal["procurement_dataset"]
    seed: int
    start_number: int
    dataset_name: str | None = None
    dataset_version: str | None = None
    entries: tuple[ProcurementDatasetEntry, ...]

    @model_validator(mode="after")
    def validate_entries(self) -> ProcurementDatasetConfig:
        if not self.entries:
            raise ValueError("entries must contain at least one dataset entry")
        return self


def load_procurement_config(path: Path) -> ProcurementScenarioConfig:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return ProcurementScenarioConfig.model_validate(payload)


def load_procurement_dataset_config(path: Path) -> ProcurementDatasetConfig:
    dataset_path = path if path.is_absolute() else path.resolve()
    with dataset_path.open("rb") as handle:
        payload = tomllib.load(handle)
    config = ProcurementDatasetConfig.model_validate(payload)
    entries = tuple(
        entry
        if entry.config is None or entry.config.is_absolute()
        else entry.model_copy(update={"config": dataset_path.parent / entry.config})
        for entry in config.entries
    )
    return config.model_copy(update={"entries": entries})
