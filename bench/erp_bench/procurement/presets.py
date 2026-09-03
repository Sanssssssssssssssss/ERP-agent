"""Editable procurement difficulty presets."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .config import (
    AvailableRoutes,
    CustomersConfig,
    DifficultyKey,
    ExportConfig,
    ManufactureConfig,
    OrderAcceptanceConfig,
    ProcurementScenarioConfig,
    SupplyConfig,
)


class DifficultyPreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: DifficultyKey
    label: str
    description: str
    config: ProcurementScenarioConfig


DIFFICULTY_PRESETS: dict[DifficultyKey, DifficultyPreset] = {
    "easy": DifficultyPreset(
        key="easy",
        label="Easy",
        description="Simple purchase-only fulfillment with no in-house manufacturing.",
        config=ProcurementScenarioConfig(
            difficulty="easy",
            available_routes=AvailableRoutes.buy_only,
            customers=CustomersConfig(
                count_range=(2, 4),
                deadline_range=(5, 9),
                demand_range=(5, 11),
            ),
            supply=SupplyConfig(
                stock_ratio_range=(0.75, 0.92),
                finished_vendor_target_range=(3, 3),
                finished_vendor_limit=4,
                include_components=False,
                component_count_range=None,
                component_vendor_category_count_range=(1, 1),
                vendor_capacity_primary_ratio_range=(0.4, 0.9),
                sampling_tightness=0.25,
            ),
            order_acceptance=OrderAcceptanceConfig(),
            manufacture=None,
            export=ExportConfig(),
        ),
    ),
    "medium": DifficultyPreset(
        key="medium",
        label="Medium",
        description="Constrained manufacture-or-buy scenarios with a single BOM and one in-house workcenter.",
        config=ProcurementScenarioConfig(
            difficulty="medium",
            available_routes=AvailableRoutes.buy_and_manufacture,
            customers=CustomersConfig(
                count_range=(8, 10),
                deadline_range=(8, 14),
                demand_range=(14, 25),
            ),
            supply=SupplyConfig(
                stock_ratio_range=(0.38, 0.52),
                finished_vendor_target_range=(4, 5),
                finished_vendor_limit=6,
                include_components=True,
                component_count_range=(5, 7),
                component_vendor_category_count_range=(3, 3),
                vendor_capacity_primary_ratio_range=(0.1, 0.36),
                sampling_tightness=0.55,
            ),
            order_acceptance=OrderAcceptanceConfig(),
            manufacture=ManufactureConfig(
                bom_structure="single_bom",
                workcenter_count=1,
                assembly_capacity_buffer_range=(0, 1),
                assembly_capacity_ratio_range=(0.24, 0.46),
            ),
            export=ExportConfig(),
        ),
    ),
    "hard": DifficultyPreset(
        key="hard",
        label="Hard",
        description="Tight high-pressure manufacture-or-buy scenarios with a single BOM and one constrained in-house workcenter.",
        config=ProcurementScenarioConfig(
            difficulty="hard",
            available_routes=AvailableRoutes.buy_and_manufacture,
            customers=CustomersConfig(
                count_range=(20, 32),
                deadline_range=(8, 13),
                demand_range=(18, 31),
            ),
            supply=SupplyConfig(
                stock_ratio_range=(0.28, 0.42),
                finished_vendor_target_range=(5, 6),
                finished_vendor_limit=8,
                include_components=True,
                component_count_range=(8, 10),
                component_vendor_category_count_range=(3, 3),
                vendor_capacity_primary_ratio_range=(0.07, 0.26),
                sampling_tightness=0.72,
            ),
            order_acceptance=OrderAcceptanceConfig(),
            manufacture=ManufactureConfig(
                bom_structure="single_bom",
                workcenter_count=1,
                assembly_capacity_buffer_range=(0, 1),
                assembly_capacity_ratio_range=(0.2, 0.34),
            ),
            export=ExportConfig(),
        ),
    ),
}
