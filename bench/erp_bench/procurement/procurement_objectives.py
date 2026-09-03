"""Shared procurement objective definitions."""

from __future__ import annotations

from enum import StrEnum


class ProcurementObjectiveKind(StrEnum):
    min_new_spend = "min_new_spend"
    vendor_consolidation = "vendor_consolidation"
    capacity_preservation = "capacity_preservation"
    repair_plan = "repair_plan"
    constraint_only = "constraint_only"


OPTIMIZING_PROCUREMENT_OBJECTIVES: tuple[ProcurementObjectiveKind, ...] = (
    ProcurementObjectiveKind.vendor_consolidation,
    ProcurementObjectiveKind.capacity_preservation,
    ProcurementObjectiveKind.repair_plan,
)
