from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ..harbor import HarborTaskExporter
from .prompts import build_background_and_policy, build_instruction
from .solver import (
    ComponentSpec,
    CustomerSpec,
    ManufacturingProductSpec,
    ProductSpec,
    ScenarioBlueprint,
    ScenarioBuild,
    ScenarioGenerator,
    VendorSpec,
    WorkcenterChoiceSpec,
    WorkcenterSpec,
)


class ProcurementValidationTaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    matrix_ids: tuple[str, ...]
    difficulty: str
    scenario_number: int
    description: str
    name_suffix: str


def single_route_manufacturing(
    *,
    product: ProductSpec,
    components: tuple[ComponentSpec, ...],
    unit_cost: float,
    lead_days: int,
    capacity_units: int,
    workcenter_code: str,
    workcenter_name: str,
    operation_name: str,
) -> tuple[ManufacturingProductSpec, WorkcenterSpec]:
    time_per_unit = 24 * 60 * max(float(lead_days), 1e-6) / max(float(capacity_units), 1.0)
    product_routes = tuple(dict.fromkeys((*product.routes, "manufacture")))
    return (
        ManufacturingProductSpec(
            product=product.model_copy(update={"routes": product_routes}),
            components=components,
            workcenter_choices=(
                WorkcenterChoiceSpec(
                    workcenter_code=workcenter_code,
                    unit_cost_dollars=unit_cost,
                    lead_time_days=lead_days,
                    time_per_unit_minutes=time_per_unit,
                    operation_name=operation_name,
                ),
            ),
            primary_workcenter_code=workcenter_code,
            operation_name=operation_name,
        ),
        WorkcenterSpec(
            code=workcenter_code,
            name=workcenter_name,
            capacity_minutes=int(round(max(capacity_units, 0) * time_per_unit)),
        ),
    )


def _single_customer(
    *,
    ref: str,
    demand: int,
    deadline: int,
    name: str,
) -> tuple[CustomerSpec, ...]:
    return (
        CustomerSpec(
            name=name,
            ref=ref,
            email=f"{ref.lower()}@example.com",
            demand=demand,
            deadline=deadline,
        ),
    )


def simple_validation_blueprint(*, scenario_number: int = 9450) -> ScenarioBlueprint:
    finished_product = ProductSpec(
        name="QA Compact Console",
        code="QA-SIMPLE-FIN-001",
        list_price_dollars=180.0,
        standard_price_dollars=85.0,
        type="product",
        routes=("buy",),
    )
    component_product = ProductSpec(
        name="QA Control Board",
        code="QA-SIMPLE-COMP-001",
        list_price_dollars=32.0,
        standard_price_dollars=12.0,
        type="product",
        routes=("buy",),
    )
    customers = (
        CustomerSpec(
            name="Simple Alpha",
            ref="QA-SIMPLE-CUST-A",
            email="simple-alpha@example.com",
            demand=1,
            deadline=6,
        ),
        CustomerSpec(
            name="Simple Beta",
            ref="QA-SIMPLE-CUST-B",
            email="simple-beta@example.com",
            demand=1,
            deadline=7,
        ),
        CustomerSpec(
            name="Simple Gamma",
            ref="QA-SIMPLE-CUST-C",
            email="simple-gamma@example.com",
            demand=1,
            deadline=8,
        ),
    )
    manufacturing_product, workcenter = single_route_manufacturing(
        product=finished_product,
        components=(ComponentSpec(product=component_product, per_unit=1),),
        unit_cost=10.0,
        lead_days=1,
        capacity_units=6,
        workcenter_code="QA-SIMPLE-WC01",
        workcenter_name="QA Simple Assembly",
        operation_name="QA Simple Assembly",
    )
    vendors = (
        VendorSpec(
            name="QA Finished Backup",
            ref="QA-SIMPLE-VEND-FIN",
            supplier_rank=1,
            product_code=finished_product.code,
            price_dollars=110.0,
            lead_time=2,
            min_qty=1,
            max_qty=10,
            supply_role="finished",
        ),
        VendorSpec(
            name="QA Component Source",
            ref="QA-SIMPLE-VEND-COMP",
            supplier_rank=2,
            product_code=component_product.code,
            price_dollars=14.0,
            lead_time=1,
            min_qty=1,
            max_qty=10,
            supply_role="component",
        ),
    )
    return ScenarioBlueprint(
        scenario_number=scenario_number,
        name="QA Simple Validation Manufacturing",
        instruction="Validate baseline single-level manufacturing with stock, component procurement, and assembly.",
        background_and_policy="",
        margin=0.12,
        customers=customers,
        vendors=vendors,
        product=finished_product,
        manufactured_products=(manufacturing_product,),
        workcenters=(workcenter,),
        system_parameters=(),
        product_stock_units={
            finished_product.code: 1,
            component_product.code: 0,
        },
        product_stock_cost_dollars={
            finished_product.code: finished_product.standard_price_dollars,
            component_product.code: component_product.standard_price_dollars,
        },
    )


def multilevel_chain_blueprint(
    *,
    scenario_number: int = 9451,
    demand: int = 2,
    deadline: int = 6,
    leaf_vendor_lead: int = 1,
    final_capacity_units: int | None = None,
    subassembly_capacity_units: int | None = None,
) -> ScenarioBlueprint:
    final_product = ProductSpec(
        name="QA Chain Device",
        code="QA-CHAIN-FIN-001",
        list_price_dollars=240.0,
        standard_price_dollars=110.0,
        type="product",
        routes=("buy",),
    )
    subassembly_product = ProductSpec(
        name="QA Chain Module",
        code="QA-CHAIN-SUB-001",
        list_price_dollars=90.0,
        standard_price_dollars=42.0,
        type="product",
        routes=("manufacture",),
    )
    raw_a_product = ProductSpec(
        name="QA Chain Core",
        code="QA-CHAIN-RAW-A",
        list_price_dollars=18.0,
        standard_price_dollars=7.0,
        type="product",
        routes=("buy",),
    )
    raw_b_product = ProductSpec(
        name="QA Chain Shield",
        code="QA-CHAIN-RAW-B",
        list_price_dollars=16.0,
        standard_price_dollars=6.0,
        type="product",
        routes=("buy",),
    )
    customers = _single_customer(
        ref="QA-CHAIN-CUST",
        demand=demand,
        deadline=deadline,
        name="QA Chain Customer",
    )
    final_mfg, final_workcenter = single_route_manufacturing(
        product=final_product,
        components=(ComponentSpec(product=subassembly_product, per_unit=1),),
        unit_cost=12.0,
        lead_days=1,
        capacity_units=final_capacity_units or demand,
        workcenter_code="QA-CHAIN-WC-FIN",
        workcenter_name="QA Chain Final Assembly",
        operation_name="QA Chain Final Assembly",
    )
    sub_mfg, sub_workcenter = single_route_manufacturing(
        product=subassembly_product,
        components=(
            ComponentSpec(product=raw_a_product, per_unit=1),
            ComponentSpec(product=raw_b_product, per_unit=1),
        ),
        unit_cost=7.0,
        lead_days=2,
        capacity_units=subassembly_capacity_units or demand,
        workcenter_code="QA-CHAIN-WC-SUB",
        workcenter_name="QA Chain Module Build",
        operation_name="QA Chain Module Build",
    )
    vendors = (
        VendorSpec(
            name="QA Chain Core Supply",
            ref="QA-CHAIN-VEND-A",
            supplier_rank=1,
            product_code=raw_a_product.code,
            price_dollars=8.0,
            lead_time=leaf_vendor_lead,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Chain Shield Supply",
            ref="QA-CHAIN-VEND-B",
            supplier_rank=2,
            product_code=raw_b_product.code,
            price_dollars=7.0,
            lead_time=leaf_vendor_lead,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
    )
    return ScenarioBlueprint(
        scenario_number=scenario_number,
        name="QA Multilevel Chain",
        instruction="Validate a two-tier chain BOM task.",
        background_and_policy="",
        margin=0.15,
        customers=customers,
        vendors=vendors,
        product=final_product,
        manufactured_products=(final_mfg, sub_mfg),
        workcenters=(final_workcenter, sub_workcenter),
        system_parameters=(),
        product_stock_units={
            final_product.code: 0,
            subassembly_product.code: 0,
            raw_a_product.code: 0,
            raw_b_product.code: 0,
        },
        product_stock_cost_dollars={
            final_product.code: final_product.standard_price_dollars,
            subassembly_product.code: subassembly_product.standard_price_dollars,
            raw_a_product.code: raw_a_product.standard_price_dollars,
            raw_b_product.code: raw_b_product.standard_price_dollars,
        },
    )


def parallel_subassemblies_blueprint(
    *,
    scenario_number: int = 9452,
    demand: int = 3,
    deadline: int = 7,
    final_capacity_units: int | None = None,
    sub_a_capacity_units: int | None = None,
    sub_b_capacity_units: int | None = None,
) -> ScenarioBlueprint:
    final_product = ProductSpec(
        name="QA Parallel Rig",
        code="QA-PAR-FIN-001",
        list_price_dollars=310.0,
        standard_price_dollars=140.0,
        type="product",
        routes=("buy",),
    )
    sub_a_product = ProductSpec(
        name="QA Parallel Frame",
        code="QA-PAR-SUB-A",
        list_price_dollars=85.0,
        standard_price_dollars=36.0,
        type="product",
        routes=("manufacture",),
    )
    sub_b_product = ProductSpec(
        name="QA Parallel Harness",
        code="QA-PAR-SUB-B",
        list_price_dollars=78.0,
        standard_price_dollars=30.0,
        type="product",
        routes=("manufacture",),
    )
    raw_a_product = ProductSpec(
        name="QA Parallel Beam",
        code="QA-PAR-RAW-A",
        list_price_dollars=20.0,
        standard_price_dollars=8.0,
        type="product",
        routes=("buy",),
    )
    raw_b_product = ProductSpec(
        name="QA Parallel Joint",
        code="QA-PAR-RAW-B",
        list_price_dollars=14.0,
        standard_price_dollars=5.0,
        type="product",
        routes=("buy",),
    )
    raw_c_product = ProductSpec(
        name="QA Parallel Cable",
        code="QA-PAR-RAW-C",
        list_price_dollars=12.0,
        standard_price_dollars=4.0,
        type="product",
        routes=("buy",),
    )
    customers = _single_customer(
        ref="QA-PAR-CUST",
        demand=demand,
        deadline=deadline,
        name="QA Parallel Customer",
    )
    final_mfg, final_workcenter = single_route_manufacturing(
        product=final_product,
        components=(
            ComponentSpec(product=sub_a_product, per_unit=1),
            ComponentSpec(product=sub_b_product, per_unit=1),
        ),
        unit_cost=16.0,
        lead_days=1,
        capacity_units=final_capacity_units or demand,
        workcenter_code="QA-PAR-WC-FIN",
        workcenter_name="QA Parallel Final Assembly",
        operation_name="QA Parallel Final Assembly",
    )
    sub_a_mfg, sub_a_workcenter = single_route_manufacturing(
        product=sub_a_product,
        components=(
            ComponentSpec(product=raw_a_product, per_unit=1),
            ComponentSpec(product=raw_b_product, per_unit=1),
        ),
        unit_cost=8.0,
        lead_days=2,
        capacity_units=sub_a_capacity_units or demand,
        workcenter_code="QA-PAR-WC-SUB-A",
        workcenter_name="QA Parallel Frame Build",
        operation_name="QA Parallel Frame Build",
    )
    sub_b_mfg, sub_b_workcenter = single_route_manufacturing(
        product=sub_b_product,
        components=(ComponentSpec(product=raw_c_product, per_unit=2),),
        unit_cost=7.0,
        lead_days=2,
        capacity_units=sub_b_capacity_units or demand,
        workcenter_code="QA-PAR-WC-SUB-B",
        workcenter_name="QA Parallel Harness Build",
        operation_name="QA Parallel Harness Build",
    )
    vendors = (
        VendorSpec(
            name="QA Parallel Beam Supply",
            ref="QA-PAR-VEND-A",
            supplier_rank=1,
            product_code=raw_a_product.code,
            price_dollars=9.0,
            lead_time=1,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Parallel Joint Supply",
            ref="QA-PAR-VEND-B",
            supplier_rank=2,
            product_code=raw_b_product.code,
            price_dollars=6.0,
            lead_time=1,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Parallel Cable Supply",
            ref="QA-PAR-VEND-C",
            supplier_rank=3,
            product_code=raw_c_product.code,
            price_dollars=5.0,
            lead_time=1,
            min_qty=1,
            max_qty=40,
            supply_role="component",
        ),
    )
    return ScenarioBlueprint(
        scenario_number=scenario_number,
        name="QA Parallel Subassemblies",
        instruction="Validate parallel same-level subassemblies.",
        background_and_policy="",
        margin=0.15,
        customers=customers,
        vendors=vendors,
        product=final_product,
        manufactured_products=(final_mfg, sub_a_mfg, sub_b_mfg),
        workcenters=(final_workcenter, sub_a_workcenter, sub_b_workcenter),
        system_parameters=(),
        product_stock_units={
            final_product.code: 0,
            sub_a_product.code: 0,
            sub_b_product.code: 0,
            raw_a_product.code: 0,
            raw_b_product.code: 0,
            raw_c_product.code: 0,
        },
        product_stock_cost_dollars={
            final_product.code: final_product.standard_price_dollars,
            sub_a_product.code: sub_a_product.standard_price_dollars,
            sub_b_product.code: sub_b_product.standard_price_dollars,
            raw_a_product.code: raw_a_product.standard_price_dollars,
            raw_b_product.code: raw_b_product.standard_price_dollars,
            raw_c_product.code: raw_c_product.standard_price_dollars,
        },
    )


def deep_mixed_blueprint(
    *,
    scenario_number: int = 9453,
    demand: int = 2,
    deadline: int = 6,
    raw_x_lead: int = 1,
    raw_y_lead: int = 1,
    raw_z_lead: int = 1,
    final_capacity_units: int | None = None,
    sub_a_capacity_units: int | None = None,
    sub_b_capacity_units: int | None = None,
) -> ScenarioBlueprint:
    final_product = ProductSpec(
        name="QA Deep Mixed Rig",
        code="QA-DEEP-FIN-001",
        list_price_dollars=360.0,
        standard_price_dollars=160.0,
        type="product",
        routes=("buy",),
    )
    sub_a_product = ProductSpec(
        name="QA Deep Carrier",
        code="QA-DEEP-SUB-A",
        list_price_dollars=110.0,
        standard_price_dollars=48.0,
        type="product",
        routes=("manufacture",),
    )
    sub_b_product = ProductSpec(
        name="QA Deep Power Cage",
        code="QA-DEEP-SUB-B",
        list_price_dollars=82.0,
        standard_price_dollars=34.0,
        type="product",
        routes=("manufacture",),
    )
    raw_x_product = ProductSpec(
        name="QA Deep Rail",
        code="QA-DEEP-RAW-X",
        list_price_dollars=22.0,
        standard_price_dollars=8.0,
        type="product",
        routes=("buy",),
    )
    raw_y_product = ProductSpec(
        name="QA Deep Cell",
        code="QA-DEEP-RAW-Y",
        list_price_dollars=18.0,
        standard_price_dollars=7.0,
        type="product",
        routes=("buy",),
    )
    raw_z_product = ProductSpec(
        name="QA Deep Mesh",
        code="QA-DEEP-RAW-Z",
        list_price_dollars=16.0,
        standard_price_dollars=6.0,
        type="product",
        routes=("buy",),
    )
    customers = _single_customer(
        ref="QA-DEEP-CUST",
        demand=demand,
        deadline=deadline,
        name="QA Deep Customer",
    )
    final_mfg, final_workcenter = single_route_manufacturing(
        product=final_product,
        components=(
            ComponentSpec(product=sub_a_product, per_unit=1),
            ComponentSpec(product=raw_x_product, per_unit=1),
        ),
        unit_cost=18.0,
        lead_days=1,
        capacity_units=final_capacity_units or demand,
        workcenter_code="QA-DEEP-WC-FIN",
        workcenter_name="QA Deep Final Assembly",
        operation_name="QA Deep Final Assembly",
    )
    sub_a_mfg, sub_a_workcenter = single_route_manufacturing(
        product=sub_a_product,
        components=(ComponentSpec(product=sub_b_product, per_unit=1),),
        unit_cost=9.0,
        lead_days=1,
        capacity_units=sub_a_capacity_units or demand,
        workcenter_code="QA-DEEP-WC-SUB-A",
        workcenter_name="QA Deep Carrier Build",
        operation_name="QA Deep Carrier Build",
    )
    sub_b_mfg, sub_b_workcenter = single_route_manufacturing(
        product=sub_b_product,
        components=(
            ComponentSpec(product=raw_y_product, per_unit=1),
            ComponentSpec(product=raw_z_product, per_unit=1),
        ),
        unit_cost=8.0,
        lead_days=2,
        capacity_units=sub_b_capacity_units or demand,
        workcenter_code="QA-DEEP-WC-SUB-B",
        workcenter_name="QA Deep Power Build",
        operation_name="QA Deep Power Build",
    )
    vendors = (
        VendorSpec(
            name="QA Deep Rail Supply",
            ref="QA-DEEP-VEND-X",
            supplier_rank=1,
            product_code=raw_x_product.code,
            price_dollars=9.0,
            lead_time=raw_x_lead,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Deep Cell Supply",
            ref="QA-DEEP-VEND-Y",
            supplier_rank=2,
            product_code=raw_y_product.code,
            price_dollars=8.0,
            lead_time=raw_y_lead,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Deep Mesh Supply",
            ref="QA-DEEP-VEND-Z",
            supplier_rank=3,
            product_code=raw_z_product.code,
            price_dollars=7.0,
            lead_time=raw_z_lead,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
    )
    return ScenarioBlueprint(
        scenario_number=scenario_number,
        name="QA Deep Mixed BOM",
        instruction="Validate deep mixed raw and manufactured dependencies.",
        background_and_policy="",
        margin=0.15,
        customers=customers,
        vendors=vendors,
        product=final_product,
        manufactured_products=(final_mfg, sub_a_mfg, sub_b_mfg),
        workcenters=(final_workcenter, sub_a_workcenter, sub_b_workcenter),
        system_parameters=(),
        product_stock_units={
            final_product.code: 0,
            sub_a_product.code: 0,
            sub_b_product.code: 0,
            raw_x_product.code: 0,
            raw_y_product.code: 0,
            raw_z_product.code: 0,
        },
        product_stock_cost_dollars={
            final_product.code: final_product.standard_price_dollars,
            sub_a_product.code: sub_a_product.standard_price_dollars,
            sub_b_product.code: sub_b_product.standard_price_dollars,
            raw_x_product.code: raw_x_product.standard_price_dollars,
            raw_y_product.code: raw_y_product.standard_price_dollars,
            raw_z_product.code: raw_z_product.standard_price_dollars,
        },
    )


def shared_raw_leaf_blueprint(
    *,
    scenario_number: int = 9454,
    demand: int = 2,
    deadline: int = 7,
    final_capacity_units: int | None = None,
    sub_a_capacity_units: int | None = None,
    sub_b_capacity_units: int | None = None,
) -> ScenarioBlueprint:
    final_product = ProductSpec(
        name="QA Shared Leaf Array",
        code="QA-SHARED-FIN-001",
        list_price_dollars=340.0,
        standard_price_dollars=155.0,
        type="product",
        routes=("buy",),
    )
    sub_a_product = ProductSpec(
        name="QA Shared Left Module",
        code="QA-SHARED-SUB-A",
        list_price_dollars=88.0,
        standard_price_dollars=34.0,
        type="product",
        routes=("manufacture",),
    )
    sub_b_product = ProductSpec(
        name="QA Shared Right Module",
        code="QA-SHARED-SUB-B",
        list_price_dollars=84.0,
        standard_price_dollars=33.0,
        type="product",
        routes=("manufacture",),
    )
    shared_raw_product = ProductSpec(
        name="QA Shared Cell",
        code="QA-SHARED-RAW-X",
        list_price_dollars=19.0,
        standard_price_dollars=7.0,
        type="product",
        routes=("buy",),
    )
    raw_y_product = ProductSpec(
        name="QA Shared Bracket",
        code="QA-SHARED-RAW-Y",
        list_price_dollars=15.0,
        standard_price_dollars=5.0,
        type="product",
        routes=("buy",),
    )
    raw_z_product = ProductSpec(
        name="QA Shared Fastener",
        code="QA-SHARED-RAW-Z",
        list_price_dollars=13.0,
        standard_price_dollars=4.0,
        type="product",
        routes=("buy",),
    )
    customers = _single_customer(
        ref="QA-SHARED-CUST",
        demand=demand,
        deadline=deadline,
        name="QA Shared Customer",
    )
    final_mfg, final_workcenter = single_route_manufacturing(
        product=final_product,
        components=(
            ComponentSpec(product=sub_a_product, per_unit=1),
            ComponentSpec(product=sub_b_product, per_unit=1),
        ),
        unit_cost=17.0,
        lead_days=1,
        capacity_units=final_capacity_units or demand,
        workcenter_code="QA-SHARED-WC-FIN",
        workcenter_name="QA Shared Final Assembly",
        operation_name="QA Shared Final Assembly",
    )
    sub_a_mfg, sub_a_workcenter = single_route_manufacturing(
        product=sub_a_product,
        components=(
            ComponentSpec(product=shared_raw_product, per_unit=1),
            ComponentSpec(product=raw_y_product, per_unit=1),
        ),
        unit_cost=8.0,
        lead_days=2,
        capacity_units=sub_a_capacity_units or demand,
        workcenter_code="QA-SHARED-WC-SUB-A",
        workcenter_name="QA Shared Left Build",
        operation_name="QA Shared Left Build",
    )
    sub_b_mfg, sub_b_workcenter = single_route_manufacturing(
        product=sub_b_product,
        components=(
            ComponentSpec(product=shared_raw_product, per_unit=1),
            ComponentSpec(product=raw_z_product, per_unit=1),
        ),
        unit_cost=8.0,
        lead_days=2,
        capacity_units=sub_b_capacity_units or demand,
        workcenter_code="QA-SHARED-WC-SUB-B",
        workcenter_name="QA Shared Right Build",
        operation_name="QA Shared Right Build",
    )
    vendors = (
        VendorSpec(
            name="QA Shared Cell Supply",
            ref="QA-SHARED-VEND-X",
            supplier_rank=1,
            product_code=shared_raw_product.code,
            price_dollars=8.0,
            lead_time=1,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Shared Bracket Supply",
            ref="QA-SHARED-VEND-Y",
            supplier_rank=2,
            product_code=raw_y_product.code,
            price_dollars=6.0,
            lead_time=1,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
        VendorSpec(
            name="QA Shared Fastener Supply",
            ref="QA-SHARED-VEND-Z",
            supplier_rank=3,
            product_code=raw_z_product.code,
            price_dollars=5.0,
            lead_time=1,
            min_qty=1,
            max_qty=20,
            supply_role="component",
        ),
    )
    return ScenarioBlueprint(
        scenario_number=scenario_number,
        name="QA Shared Raw Leaf",
        instruction="Validate shared raw-leaf aggregation across branches.",
        background_and_policy="",
        margin=0.15,
        customers=customers,
        vendors=vendors,
        product=final_product,
        manufactured_products=(final_mfg, sub_a_mfg, sub_b_mfg),
        workcenters=(final_workcenter, sub_a_workcenter, sub_b_workcenter),
        system_parameters=(),
        product_stock_units={
            final_product.code: 0,
            sub_a_product.code: 0,
            sub_b_product.code: 0,
            shared_raw_product.code: 0,
            raw_y_product.code: 0,
            raw_z_product.code: 0,
        },
        product_stock_cost_dollars={
            final_product.code: final_product.standard_price_dollars,
            sub_a_product.code: sub_a_product.standard_price_dollars,
            sub_b_product.code: sub_b_product.standard_price_dollars,
            shared_raw_product.code: shared_raw_product.standard_price_dollars,
            raw_y_product.code: raw_y_product.standard_price_dollars,
            raw_z_product.code: raw_z_product.standard_price_dollars,
        },
    )


def validation_task_specs() -> tuple[ProcurementValidationTaskSpec, ...]:
    return (
        ProcurementValidationTaskSpec(
            key="sm-simple",
            matrix_ids=("SM-01", "SM-02"),
            difficulty="medium",
            scenario_number=9450,
            description="Baseline single-level manufacturing with stock, component procurement, and assembly.",
            name_suffix="procurement_bom_single_level",
        ),
        ProcurementValidationTaskSpec(
            key="bom-chain",
            matrix_ids=("BOM-02",),
            difficulty="medium",
            scenario_number=9451,
            description="Two-tier multilevel chain.",
            name_suffix="procurement_bom_two_tier_chain",
        ),
        ProcurementValidationTaskSpec(
            key="bom-parallel",
            matrix_ids=("BOM-03",),
            difficulty="hard",
            scenario_number=9452,
            description="Parallel same-level subassemblies.",
            name_suffix="procurement_bom_parallel_subassemblies",
        ),
        ProcurementValidationTaskSpec(
            key="bom-deep-mixed",
            matrix_ids=("BOM-04",),
            difficulty="hard",
            scenario_number=9453,
            description="Deep mixed raw and manufactured dependencies.",
            name_suffix="procurement_bom_deep_mixed_chain",
        ),
        ProcurementValidationTaskSpec(
            key="bom-shared-raw",
            matrix_ids=("BOM-07",),
            difficulty="hard",
            scenario_number=9454,
            description="Shared raw leaf across branches.",
            name_suffix="procurement_bom_shared_raw_leaf",
        ),
    )


def _populate_prompt(blueprint: ScenarioBlueprint) -> ScenarioBlueprint:
    """Replace the hand-coded QA instruction/background with the canonical procurement prompt."""
    instruction = build_instruction(blueprint)
    instruction = (
        f"{instruction}\n\nFulfill the listed customer demand while following the task policy."
    )
    customer_segments = "; ".join(
        f"{customer.demand} units in {customer.deadline} days for {customer.name}"
        for customer in blueprint.customers
    )
    if customer_segments:
        instruction = f"{instruction}\n\nCustomer demand summary: {customer_segments}."
    return blueprint.model_copy(
        update={
            "instruction": instruction,
            "background_and_policy": build_background_and_policy(blueprint),
        }
    )


def build_validation_blueprint(key: str) -> ScenarioBlueprint:
    if key == "sm-simple":
        blueprint = simple_validation_blueprint()
    elif key == "bom-chain":
        blueprint = multilevel_chain_blueprint(
            demand=1,
            deadline=6,
            final_capacity_units=8,
            subassembly_capacity_units=16,
        )
    elif key == "bom-parallel":
        blueprint = parallel_subassemblies_blueprint(
            demand=1,
            deadline=7,
            final_capacity_units=8,
            sub_a_capacity_units=8,
            sub_b_capacity_units=8,
        )
    elif key == "bom-deep-mixed":
        blueprint = deep_mixed_blueprint(
            demand=1,
            deadline=6,
            final_capacity_units=8,
            sub_a_capacity_units=8,
            sub_b_capacity_units=16,
        )
    elif key == "bom-shared-raw":
        blueprint = shared_raw_leaf_blueprint(
            demand=1,
            deadline=7,
            final_capacity_units=8,
            sub_a_capacity_units=8,
            sub_b_capacity_units=8,
        )
    else:
        raise ValueError(f"Unknown validation scenario key: {key}")
    return _populate_prompt(blueprint)


def build_validation_scenario(
    spec: ProcurementValidationTaskSpec,
) -> tuple[ScenarioBlueprint, ScenarioBuild]:
    blueprint = build_validation_blueprint(spec.key)
    build = ScenarioGenerator(
        blueprint,
        solve_time_limit=4.0,
        num_search_workers=8,
        seed=spec.scenario_number,
    ).generate()
    return blueprint, build


def export_validation_tasks(
    output_dir: Path | str,
    *,
    force: bool = True,
) -> list[Path]:
    exporter = HarborTaskExporter(
        output_dir=output_dir,
        force=force,
    )
    exported: list[Path] = []
    for spec in validation_task_specs():
        blueprint, build = build_validation_scenario(spec)
        task_dir = exporter.export(
            build,
            blueprint,
            difficulty=spec.difficulty,
            name_suffix=spec.name_suffix,
        )
        if task_dir is not None:
            exported.append(task_dir)
    return exported
