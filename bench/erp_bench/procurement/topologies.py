"""Procurement manufacturing topology builders."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from typing import TYPE_CHECKING

from .solver import (
    ComponentSpec,
    ManufacturingOnlyConfig,
    ManufacturingProductSpec,
    ProductSpec,
    WorkcenterChoiceSpec,
    WorkcenterSpec,
)

if TYPE_CHECKING:
    from .sampler import ProcurementSampler


def build_manufacturing_graph(
    sampler: ProcurementSampler,
    *,
    scenario_number: int,
    product: ProductSpec,
    components: Sequence[ComponentSpec],
    pattern: str | None,
    total_demand: int,
    stock_units: int,
    deadlines: Sequence[int],
    manufacturing_only: ManufacturingOnlyConfig,
) -> tuple[
    tuple[ComponentSpec, ...], tuple[ManufacturingProductSpec, ...], tuple[WorkcenterSpec, ...]
]:
    """Build the manufacturing graph for a sampled procurement scenario."""
    self = sampler
    if pattern is None:
        return tuple(components), (), ()
    workcenter_count = self._sample_workcenter_count()
    pools = self._build_workcenter_pools(
        scenario_number=scenario_number,
        count=workcenter_count,
        category=product.category,
    )
    if not pools:
        return tuple(components), (), ()

    ratio_capacity = math.ceil(
        total_demand * self._sample_ratio_in_range(self.settings.assembly_capacity_ratio_range)
    )
    buffer_low, buffer_high = self.settings.assembly_capacity_buffer_range
    build_capacity_units = ratio_capacity + self._randint(buffer_low, buffer_high)
    if total_demand <= 1:
        build_capacity_units = 0
    else:
        build_capacity_units = max(1, min(build_capacity_units, total_demand - 1))

    lead_anchor = int(round(statistics.median(deadlines))) if deadlines else 5
    base_lead_days = max(2, min(lead_anchor // 2, 5))
    if manufacturing_only.enabled:
        build_capacity_units = max(build_capacity_units, max(total_demand - stock_units, 0))
        base_lead_days = 1
    if pattern in {
        "capability_restricted",
        "multilevel_basic",
        "multilevel_capability",
        "multilevel_shared_capacity",
        "multilevel_multi_branch",
        "multilevel_deep_mixed",
        "multilevel_shared_leaf",
    }:
        if self.settings.difficulty_key == "medium":
            base_lead_days = 1
            build_capacity_units = max(build_capacity_units, math.ceil(total_demand * 0.28))
        elif self.settings.difficulty_key == "hard":
            base_lead_days = 1
            build_capacity_units = max(build_capacity_units, math.ceil(total_demand * 0.3))

    build_units = max(build_capacity_units, max(total_demand - 1, 1))
    manufactured_units = max(total_demand - stock_units, 1)
    base_time = max(45.0, min(180.0, float(base_lead_days * 24 * 60) / max(build_units, 1)))
    alternatives_by_code: dict[str, tuple[str, ...]] = {}

    def set_capacity(pool_index: int, unit_cap: int, time_per_unit: float) -> None:
        pools[pool_index] = pools[pool_index].model_copy(
            update={
                "capacity_minutes": int(max(1.0, unit_cap * max(time_per_unit, 1.0))),
            }
        )

    direct_components: list[ComponentSpec] = list(components)
    manufacturing_products: list[ManufacturingProductSpec] = []

    final_choices: list[WorkcenterChoiceSpec]
    if pattern == "cost_driven":
        final_choices = [
            self._choice_spec(
                workcenter=pools[idx],
                unit_cost=28.0 + (idx * 9.0),
                lead_days=max(2, base_lead_days + 1),
                time_per_unit_minutes=base_time + (idx * 8.0),
                operation_name=f"{product.code} Assembly",
            )
            for idx in range(min(len(pools), 3 if self.settings.difficulty_key == "hard" else 2))
        ]
        for idx, choice in enumerate(final_choices):
            set_capacity(idx, build_units + 2, choice.time_per_unit_minutes)
        alternatives_by_code[final_choices[0].workcenter_code] = tuple(
            choice.workcenter_code for choice in final_choices[1:]
        )
    elif pattern == "capability_restricted":
        component_list = list(components)
        restricted_parts = component_list[:2]
        remaining = component_list[2:]
        if not restricted_parts:
            raise ValueError(
                "capability_restricted pattern requires at least one component for the pinned subassembly"
            )
        restricted_product = self._subassembly_product(
            scenario_number=scenario_number,
            product=product,
            label="Restricted Module",
            components=restricted_parts,
        )
        direct_components = [
            ComponentSpec(product=restricted_product, per_unit=1),
            *remaining[:2],
        ]
        restricted_choices = [
            self._choice_spec(
                workcenter=pools[0],
                unit_cost=18.0,
                lead_days=max(2, base_lead_days),
                time_per_unit_minutes=max(35.0, base_time - 15.0),
                operation_name=f"{restricted_product.code} Build",
            )
        ]
        manufacturing_products.append(
            ManufacturingProductSpec(
                product=restricted_product,
                components=tuple(restricted_parts),
                workcenter_choices=tuple(restricted_choices),
                primary_workcenter_code=restricted_choices[0].workcenter_code,
                operation_name=f"{restricted_product.code} Build",
            )
        )
        final_choices = [
            self._choice_spec(
                workcenter=pools[idx],
                unit_cost=31.0 + (idx * 6.0),
                lead_days=max(2, base_lead_days + 1),
                time_per_unit_minutes=base_time + (idx * 8.0),
                operation_name=f"{product.code} Assembly",
            )
            for idx in range(min(len(pools), 3 if self.settings.difficulty_key == "hard" else 2))
        ]
        restricted_minutes = manufactured_units * restricted_choices[0].time_per_unit_minutes
        pool0_final_units = max(1, math.floor(manufactured_units * 0.3))
        pools[0] = pools[0].model_copy(
            update={
                "capacity_minutes": int(
                    restricted_minutes + pool0_final_units * final_choices[0].time_per_unit_minutes
                ),
            }
        )
        for idx, choice in enumerate(final_choices[1:], start=1):
            set_capacity(
                idx,
                max(1, math.ceil(manufactured_units * 0.8)),
                choice.time_per_unit_minutes,
            )
        alternatives_by_code[final_choices[0].workcenter_code] = tuple(
            choice.workcenter_code for choice in final_choices[1:]
        )
    elif pattern == "split_by_capacity":
        final_choices = [
            self._choice_spec(
                workcenter=pools[0],
                unit_cost=31.0,
                lead_days=max(2, base_lead_days + 1),
                time_per_unit_minutes=base_time,
                operation_name=f"{product.code} Assembly",
            ),
        ]
        if len(pools) > 1:
            final_choices.append(
                self._choice_spec(
                    workcenter=pools[1],
                    unit_cost=36.0,
                    lead_days=max(2, base_lead_days + 1),
                    time_per_unit_minutes=base_time + 10.0,
                    operation_name=f"{product.code} Assembly",
                )
            )
        if self.settings.difficulty_key == "hard" and len(pools) > 2:
            final_choices.append(
                self._choice_spec(
                    workcenter=pools[2],
                    unit_cost=42.0,
                    lead_days=max(2, base_lead_days + 1),
                    time_per_unit_minutes=base_time + 18.0,
                    operation_name=f"{product.code} Assembly",
                )
            )
        set_capacity(
            0,
            max(1, math.floor(manufactured_units * 0.55)),
            final_choices[0].time_per_unit_minutes,
        )
        if len(final_choices) > 1:
            set_capacity(
                1,
                max(1, math.ceil(manufactured_units * 0.60)),
                final_choices[1].time_per_unit_minutes,
            )
        if len(final_choices) > 2:
            set_capacity(
                2,
                max(1, math.ceil(manufactured_units * 0.35)),
                final_choices[2].time_per_unit_minutes,
            )
        alternatives_by_code[final_choices[0].workcenter_code] = tuple(
            choice.workcenter_code for choice in final_choices[1:]
        )
    else:
        component_list = list(components)
        primary_leaf_count = min(len(component_list), 2)
        if primary_leaf_count == 0:
            return tuple(components), (), ()
        if pattern == "multilevel_deep_mixed":
            raw_x_part = component_list[0]
            raw_y_part = component_list[1] if len(component_list) > 1 else component_list[0]
            raw_z_part = component_list[2] if len(component_list) > 2 else raw_y_part
            sub_b_parts = [raw_y_part, raw_z_part]
            sub_b_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly B",
                components=sub_b_parts,
            )
            sub_a_parts = [ComponentSpec(product=sub_b_product, per_unit=1)]
            sub_a_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly A",
                components=sub_a_parts,
            )
            direct_components = [
                ComponentSpec(product=sub_a_product, per_unit=1),
                raw_x_part,
            ]
            final_pool_index = 2 if len(pools) > 2 else (1 if len(pools) > 1 else 0)
            sub_a_pool_index = 1 if len(pools) > 1 else 0
            sub_b_pool_index = 0
            final_choices = [
                self._choice_spec(
                    workcenter=pools[final_pool_index],
                    unit_cost=36.0,
                    lead_days=max(2, base_lead_days + 1),
                    time_per_unit_minutes=base_time,
                    operation_name=f"{product.code} Final Assembly",
                )
            ]
            sub_a_choices = [
                self._choice_spec(
                    workcenter=pools[sub_a_pool_index],
                    unit_cost=22.0,
                    lead_days=max(1, base_lead_days),
                    time_per_unit_minutes=max(30.0, base_time - 15.0),
                    operation_name=f"{sub_a_product.code} Build",
                )
            ]
            sub_b_choices = [
                self._choice_spec(
                    workcenter=pools[sub_b_pool_index],
                    unit_cost=18.0,
                    lead_days=max(2, base_lead_days),
                    time_per_unit_minutes=max(35.0, base_time - 20.0),
                    operation_name=f"{sub_b_product.code} Build",
                )
            ]
            set_capacity(final_pool_index, build_units + 2, final_choices[0].time_per_unit_minutes)
            set_capacity(sub_a_pool_index, build_units + 2, sub_a_choices[0].time_per_unit_minutes)
            set_capacity(sub_b_pool_index, build_units + 2, sub_b_choices[0].time_per_unit_minutes)
            manufacturing_products.extend(
                [
                    ManufacturingProductSpec(
                        product=sub_a_product,
                        components=tuple(sub_a_parts),
                        workcenter_choices=tuple(sub_a_choices),
                        primary_workcenter_code=sub_a_choices[0].workcenter_code,
                        operation_name=f"{sub_a_product.code} Build",
                    ),
                    ManufacturingProductSpec(
                        product=sub_b_product,
                        components=tuple(sub_b_parts),
                        workcenter_choices=tuple(sub_b_choices),
                        primary_workcenter_code=sub_b_choices[0].workcenter_code,
                        operation_name=f"{sub_b_product.code} Build",
                    ),
                ]
            )
        elif pattern == "multilevel_shared_leaf":
            shared_part = component_list[0]
            sub_a_extra_part = component_list[1] if len(component_list) > 1 else component_list[0]
            sub_b_extra_part = component_list[2] if len(component_list) > 2 else sub_a_extra_part
            sub_a_parts = [shared_part, sub_a_extra_part]
            sub_b_parts = [shared_part.model_copy(), sub_b_extra_part]
            sub_a_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly A",
                components=sub_a_parts,
            )
            sub_b_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly B",
                components=sub_b_parts,
            )
            direct_components = [
                ComponentSpec(product=sub_a_product, per_unit=1),
                ComponentSpec(product=sub_b_product, per_unit=1),
            ]
            final_choices = [
                self._choice_spec(
                    workcenter=pools[0],
                    unit_cost=35.0,
                    lead_days=max(2, base_lead_days + 1),
                    time_per_unit_minutes=base_time,
                    operation_name=f"{product.code} Final Assembly",
                )
            ]
            sub_a_choices = [
                self._choice_spec(
                    workcenter=pools[1 if len(pools) > 1 else 0],
                    unit_cost=18.0,
                    lead_days=max(2, base_lead_days),
                    time_per_unit_minutes=max(35.0, base_time - 20.0),
                    operation_name=f"{sub_a_product.code} Build",
                )
            ]
            sub_b_choices = [
                self._choice_spec(
                    workcenter=pools[2 if len(pools) > 2 else (1 if len(pools) > 1 else 0)],
                    unit_cost=20.0,
                    lead_days=max(2, base_lead_days),
                    time_per_unit_minutes=max(40.0, base_time - 15.0),
                    operation_name=f"{sub_b_product.code} Build",
                )
            ]
            set_capacity(0, build_units + 2, final_choices[0].time_per_unit_minutes)
            set_capacity(
                1 if len(pools) > 1 else 0,
                build_units + 2,
                sub_a_choices[0].time_per_unit_minutes,
            )
            set_capacity(
                2 if len(pools) > 2 else (1 if len(pools) > 1 else 0),
                build_units + 2,
                sub_b_choices[0].time_per_unit_minutes,
            )
            manufacturing_products.extend(
                [
                    ManufacturingProductSpec(
                        product=sub_a_product,
                        components=tuple(sub_a_parts),
                        workcenter_choices=tuple(sub_a_choices),
                        primary_workcenter_code=sub_a_choices[0].workcenter_code,
                        operation_name=f"{sub_a_product.code} Build",
                    ),
                    ManufacturingProductSpec(
                        product=sub_b_product,
                        components=tuple(sub_b_parts),
                        workcenter_choices=tuple(sub_b_choices),
                        primary_workcenter_code=sub_b_choices[0].workcenter_code,
                        operation_name=f"{sub_b_product.code} Build",
                    ),
                ]
            )
        elif pattern == "multilevel_multi_branch":
            sub_a_parts = component_list[:2]
            sub_b_parts = component_list[2:4] if len(component_list) >= 4 else component_list[:2]
            remaining = component_list[4:]
            sub_a_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly A",
                components=sub_a_parts,
            )
            sub_b_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly B",
                components=sub_b_parts,
            )
            direct_components = [
                ComponentSpec(product=sub_a_product, per_unit=1),
                ComponentSpec(product=sub_b_product, per_unit=1),
                *remaining[:1],
            ]
            final_choices = [
                self._choice_spec(
                    workcenter=pools[0],
                    unit_cost=35.0,
                    lead_days=max(2, base_lead_days + 1),
                    time_per_unit_minutes=base_time,
                    operation_name=f"{product.code} Final Assembly",
                )
            ]
            sub_a_choices = [
                self._choice_spec(
                    workcenter=pools[1],
                    unit_cost=18.0,
                    lead_days=max(2, base_lead_days),
                    time_per_unit_minutes=max(35.0, base_time - 20.0),
                    operation_name=f"{sub_a_product.code} Build",
                )
            ]
            sub_b_choices = [
                self._choice_spec(
                    workcenter=pools[2 if len(pools) > 2 else 1],
                    unit_cost=20.0,
                    lead_days=max(2, base_lead_days),
                    time_per_unit_minutes=max(40.0, base_time - 15.0),
                    operation_name=f"{sub_b_product.code} Build",
                )
            ]
            set_capacity(0, build_units + 2, final_choices[0].time_per_unit_minutes)
            set_capacity(1, build_units + 2, sub_a_choices[0].time_per_unit_minutes)
            set_capacity(
                2 if len(pools) > 2 else 1,
                build_units + 2,
                sub_b_choices[0].time_per_unit_minutes,
            )
            manufacturing_products.extend(
                [
                    ManufacturingProductSpec(
                        product=sub_a_product,
                        components=tuple(sub_a_parts),
                        workcenter_choices=tuple(sub_a_choices),
                        primary_workcenter_code=sub_a_choices[0].workcenter_code,
                        operation_name=f"{sub_a_product.code} Build",
                    ),
                    ManufacturingProductSpec(
                        product=sub_b_product,
                        components=tuple(sub_b_parts),
                        workcenter_choices=tuple(sub_b_choices),
                        primary_workcenter_code=sub_b_choices[0].workcenter_code,
                        operation_name=f"{sub_b_product.code} Build",
                    ),
                ]
            )
        else:
            sub_parts = component_list[:2]
            remaining = component_list[2:]
            sub_product = self._subassembly_product(
                scenario_number=scenario_number,
                product=product,
                label="Subassembly",
                components=sub_parts,
            )
            direct_components = [ComponentSpec(product=sub_product, per_unit=1), *remaining[:2]]
            final_choices = [
                self._choice_spec(
                    workcenter=pools[1 if len(pools) > 1 else 0],
                    unit_cost=34.0 if pattern != "capability_restricted" else 39.0,
                    lead_days=max(2, base_lead_days + 1),
                    time_per_unit_minutes=base_time,
                    operation_name=f"{product.code} Final Assembly",
                )
            ]
            if pattern in {"capability_restricted", "multilevel_capability"} and len(pools) > 2:
                final_choices.append(
                    self._choice_spec(
                        workcenter=pools[2],
                        unit_cost=41.0,
                        lead_days=max(2, base_lead_days + 1),
                        time_per_unit_minutes=base_time + 10.0,
                        operation_name=f"{product.code} Final Assembly",
                    )
                )
            if pattern == "multilevel_shared_capacity" and len(pools) > 2:
                final_choices.append(
                    self._choice_spec(
                        workcenter=pools[2],
                        unit_cost=40.0,
                        lead_days=max(2, base_lead_days + 1),
                        time_per_unit_minutes=base_time + 15.0,
                        operation_name=f"{product.code} Final Assembly",
                    )
                )
            if pattern == "multilevel_basic":
                sub_choices = [
                    self._choice_spec(
                        workcenter=pools[0],
                        unit_cost=18.0,
                        lead_days=max(2, base_lead_days),
                        time_per_unit_minutes=max(35.0, base_time - 20.0),
                        operation_name=f"{sub_product.code} Build",
                    ),
                    self._choice_spec(
                        workcenter=pools[1 if len(pools) > 1 else 0],
                        unit_cost=22.0,
                        lead_days=max(2, base_lead_days + 1),
                        time_per_unit_minutes=max(45.0, base_time - 10.0),
                        operation_name=f"{sub_product.code} Build",
                    ),
                ]
            elif pattern == "multilevel_capability":
                sub_choices = [
                    self._choice_spec(
                        workcenter=pools[0],
                        unit_cost=19.0,
                        lead_days=max(2, base_lead_days),
                        time_per_unit_minutes=max(35.0, base_time - 20.0),
                        operation_name=f"{sub_product.code} Build",
                    )
                ]
            elif pattern == "multilevel_shared_capacity":
                sub_choices = [
                    self._choice_spec(
                        workcenter=pools[0],
                        unit_cost=20.0,
                        lead_days=max(2, base_lead_days),
                        time_per_unit_minutes=max(40.0, base_time - 15.0),
                        operation_name=f"{sub_product.code} Build",
                    ),
                    self._choice_spec(
                        workcenter=pools[2 if len(pools) > 2 else 1],
                        unit_cost=24.0,
                        lead_days=max(2, base_lead_days + 1),
                        time_per_unit_minutes=max(50.0, base_time - 5.0),
                        operation_name=f"{sub_product.code} Build",
                    ),
                ]
            else:
                sub_choices = [
                    self._choice_spec(
                        workcenter=pools[0],
                        unit_cost=18.0,
                        lead_days=max(2, base_lead_days),
                        time_per_unit_minutes=max(35.0, base_time - 20.0),
                        operation_name=f"{sub_product.code} Build",
                    )
                ]
            set_capacity(0, build_units + 2, sub_choices[0].time_per_unit_minutes)
            if len(sub_choices) > 1:
                alt_pool_index = (
                    2 if pattern == "multilevel_shared_capacity" and len(pools) > 2 else 1
                )
                set_capacity(alt_pool_index, build_units + 2, sub_choices[1].time_per_unit_minutes)
            set_capacity(
                1 if len(pools) > 1 else 0,
                build_units + 2,
                final_choices[0].time_per_unit_minutes,
            )
            if pattern == "multilevel_shared_capacity":
                set_capacity(
                    0,
                    max(1, math.floor(build_units * 0.7)),
                    sub_choices[0].time_per_unit_minutes,
                )
            manufacturing_products.append(
                ManufacturingProductSpec(
                    product=sub_product,
                    components=tuple(sub_parts),
                    workcenter_choices=tuple(sub_choices),
                    primary_workcenter_code=sub_choices[0].workcenter_code,
                    operation_name=f"{sub_product.code} Build",
                )
            )
            alternatives_by_code[sub_choices[0].workcenter_code] = tuple(
                choice.workcenter_code for choice in sub_choices[1:]
            )

    manufacturing_products.insert(
        0,
        ManufacturingProductSpec(
            product=product.model_copy(update={"routes": ("buy", "manufacture")}),
            components=tuple(direct_components),
            workcenter_choices=tuple(final_choices),
            primary_workcenter_code=final_choices[0].workcenter_code,
            operation_name=f"{product.code} Assembly",
        ),
    )
    pools = self._assign_pool_alternatives(
        pools=pools,
        alternatives_by_code=alternatives_by_code,
    )
    return tuple(direct_components), tuple(manufacturing_products), tuple(pools)
