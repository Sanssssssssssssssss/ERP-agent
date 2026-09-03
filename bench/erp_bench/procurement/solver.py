from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta
from graphlib import TopologicalSorter
from typing import Iterator, Literal, NamedTuple, cast

from ortools.sat.python import cp_model
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from schemas.models import (
    BOMComponentData,
    BOMData,
    CustomerData,
    ExistingManufacturingOrderData,
    ExistingPurchaseOrderData,
    ExistingSalesOrderData,
    ProcurementInvoicingPolicyData,
    ProcurementRepairData,
    ProductData,
    ScenarioData,
    StockLevelData,
    SystemParameterData,
    VendorData,
    VendorInfoData,
    WorkcenterData,
)
from schemas.procurement_invoicing_validation import validate_procurement_invoicing_policy

from .procurement_objectives import ProcurementObjectiveKind

BoolVar = cp_model.IntVar
RouteLiteral = Literal["buy", "mto", "manufacture", "dropship"]
TaskOrderSource = Literal["seeded", "prompt_only"]
TaskOrderState = Literal["draft", "sent", "sale"]
RejectReason = Literal["budget", "min_qty", "max_qty", "min_lead_time"]
RepairScenarioKind = Literal["supplier_cancellation", "workcenter_outage"]
InvoicePaymentTerm = Literal["immediate", "net_30"]
DownpaymentMode = Literal["percentage", "fixed_amount"]
InvoiceFlow = Literal["regular", "downpayment_then_regular", "none"]
TaskOrderAction = Literal[
    "confirm_seeded",
    "cancel_seeded",
    "create_confirm_prompt",
    "skip_prompt",
]

logger = logging.getLogger(__name__)


class ProcurementSolverModelMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_number: int
    objective_kind: str
    fixed_spend_cents: int | None
    variables: int
    boolean_variables: int
    integer_variables: int
    constraints: int
    linear_constraints: int
    bool_or_constraints: int
    tie_break_variables: int
    customers: int
    vendors: int
    manufacturing_products: int
    workcenters: int


class ProcurementSolverCallMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    status: str
    time_limit_seconds: float
    elapsed_seconds: float
    solver_wall_seconds: float


class ProcurementSolverTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ProcurementSolverModelMetrics] = Field(default_factory=list)
    solve_calls: list[ProcurementSolverCallMetrics] = Field(default_factory=list)


_solver_trace: ContextVar[ProcurementSolverTrace | None] = ContextVar(
    "procurement_solver_trace",
    default=None,
)


@contextmanager
def capture_procurement_solver_trace() -> Iterator[ProcurementSolverTrace]:
    trace = ProcurementSolverTrace()
    token = _solver_trace.set(trace)
    try:
        yield trace
    finally:
        _solver_trace.reset(token)


def format_margin_percent(margin: float) -> str:
    return f"{margin * 100:.1f}".rstrip("0").rstrip(".")


class VendorSpec(BaseModel):
    name: str
    ref: str
    supplier_rank: int
    product_code: str
    price_dollars: float
    lead_time: int
    min_qty: int
    max_qty: int | None = None
    description: str | None = None
    supply_role: Literal["finished", "component"] = "finished"

    @property
    def price_cents(self) -> int:
        return int(round(self.price_dollars * 100))

    @property
    def offer_key(self) -> str:
        return (
            f"{self.ref}|{self.product_code}|{self.min_qty}|"
            f"{round(self.price_dollars, 2)}|{self.lead_time}"
        )


class ProductSpec(BaseModel):
    name: str
    code: str
    category: str = "Industrial Equipment"
    list_price_dollars: float
    standard_price_dollars: float
    type: Literal["consu", "product", "service"] = "consu"
    is_storable: bool = True
    routes: tuple[str, ...] = Field(default=("buy",))


class ComponentSpec(BaseModel):
    product: ProductSpec
    per_unit: int = 1


class CustomerSpec(BaseModel):
    name: str
    ref: str
    email: str
    demand: int
    deadline: int
    budget_dollars: float | None = None
    task_order_source: TaskOrderSource = "prompt_only"
    task_order_ref: str | None = None
    seeded_order_state: TaskOrderState | None = None
    reject_reasons: tuple[RejectReason, ...] = Field(default_factory=tuple)
    reject_reason_text: str | None = None

    @property
    def budget_cents(self) -> int | None:
        if self.budget_dollars is None:
            return None
        return int(round(self.budget_dollars * 100))

    @property
    def resolved_task_order_ref(self) -> str:
        return self.task_order_ref or self.ref

    @property
    def is_rejected_upfront(self) -> bool:
        return bool(self.reject_reasons)


class WorkcenterSpec(BaseModel):
    code: str
    name: str
    capacity_minutes: int
    alternative_workcenter_codes: tuple[str, ...] = Field(default_factory=tuple)
    time_efficiency: float = 100.0
    time_start: float = 0.0
    time_stop: float = 0.0
    oee_target: float = 1.0
    note: str | None = None


class WorkcenterChoiceSpec(BaseModel):
    workcenter_code: str
    unit_cost_dollars: float
    lead_time_days: int
    time_per_unit_minutes: float
    operation_name: str = "Assembly"

    @property
    def unit_cost_cents(self) -> int:
        return int(round(self.unit_cost_dollars * 100))


class ManufacturingProductSpec(BaseModel):
    product: ProductSpec
    components: tuple[ComponentSpec, ...] = Field(default_factory=tuple)
    workcenter_choices: tuple[WorkcenterChoiceSpec, ...]
    primary_workcenter_code: str
    operation_name: str = "Assembly"

    @model_validator(mode="after")
    def validate_unique_workcenter_choices(self) -> ManufacturingProductSpec:
        seen: set[str] = set()
        duplicates: list[str] = []
        for choice in self.workcenter_choices:
            if choice.workcenter_code in seen and choice.workcenter_code not in duplicates:
                duplicates.append(choice.workcenter_code)
            seen.add(choice.workcenter_code)
        if duplicates:
            raise ValueError(
                f"Manufacturing product {self.product.code} has duplicate workcenter choices "
                f"for {duplicates}. Each product/workcenter pair must be unique."
            )
        return self

    @property
    def code(self) -> str:
        return self.product.code

    @property
    def min_lead_days(self) -> int:
        return min(choice.lead_time_days for choice in self.workcenter_choices)

    @property
    def lead_days(self) -> int:
        leads = {choice.lead_time_days for choice in self.workcenter_choices}
        if len(leads) != 1:
            raise ValueError(
                f"Manufacturing product {self.code} has non-uniform workcenter lead times {sorted(leads)}; "
                "lead time is a product-level property (mrp.bom.produce_delay) and must be the same across all workcenter choices."
            )
        return next(iter(leads))


ALL_REJECT_RULES: frozenset[str] = frozenset(
    {"budget_below_list_price", "quantity_outside_window", "lead_time_below_minimum"}
)


class UnsatAcceptancePolicy(BaseModel):
    min_order_quantity: int
    max_order_quantity: int
    min_lead_time_days: int
    reject_rules: frozenset[str] = Field(default_factory=lambda: ALL_REJECT_RULES)

    @model_validator(mode="after")
    def validate_rules(self) -> UnsatAcceptancePolicy:
        unknown = self.reject_rules - ALL_REJECT_RULES
        if unknown:
            raise ValueError(f"unknown reject rules: {sorted(unknown)}")
        if not self.reject_rules:
            raise ValueError("UnsatAcceptancePolicy requires at least one reject rule")
        return self

    def reject_reasons_for(
        self,
        *,
        customer: CustomerSpec,
        product: ProductSpec,
    ) -> tuple[RejectReason, ...]:
        reject_reasons: list[RejectReason] = []
        list_price_total = round(product.list_price_dollars * customer.demand, 2)
        if (
            "budget_below_list_price" in self.reject_rules
            and customer.budget_dollars is not None
            and customer.budget_dollars + 0.01 < list_price_total
        ):
            reject_reasons.append("budget")
        if "quantity_outside_window" in self.reject_rules:
            if customer.demand < self.min_order_quantity:
                reject_reasons.append("min_qty")
            if customer.demand > self.max_order_quantity:
                reject_reasons.append("max_qty")
        if (
            "lead_time_below_minimum" in self.reject_rules
            and customer.deadline < self.min_lead_time_days
        ):
            reject_reasons.append("min_lead_time")
        return tuple(reject_reasons)

    def reject_reason_text_for(
        self,
        *,
        customer: CustomerSpec,
        product: ProductSpec,
    ) -> str | None:
        fragments: list[str] = []
        list_price_total = round(product.list_price_dollars * customer.demand, 2)
        if (
            "budget_below_list_price" in self.reject_rules
            and customer.budget_dollars is not None
            and customer.budget_dollars + 0.01 < list_price_total
        ):
            fragments.append(
                f"budget ${customer.budget_dollars:,.0f} is below the list-price total ${list_price_total:,.0f}"
            )
        if "quantity_outside_window" in self.reject_rules:
            if customer.demand < self.min_order_quantity:
                fragments.append(
                    f"requested quantity {customer.demand} is below the {self.min_order_quantity}-unit minimum"
                )
            if customer.demand > self.max_order_quantity:
                fragments.append(
                    f"requested quantity {customer.demand} exceeds the {self.max_order_quantity}-unit maximum"
                )
        if (
            "lead_time_below_minimum" in self.reject_rules
            and customer.deadline < self.min_lead_time_days
        ):
            fragments.append(
                f"requested lead time of {customer.deadline} days is below the {self.min_lead_time_days}-day minimum"
            )
        return "; ".join(fragments) if fragments else None


class ManufacturingOnlyConfig(BaseModel):
    family: Literal["policy_forbidden"] | None = None
    no_buy_route: bool = False
    no_available_vendors: bool = False

    @property
    def enabled(self) -> bool:
        return self.family is not None or self.no_buy_route or self.no_available_vendors


class RepairOfferQuantity(BaseModel):
    offer_key: str
    quantity: int


class RepairManufacturingQuantity(BaseModel):
    product_code: str
    workcenter_code: str
    quantity: int


class ProcurementRepairContext(BaseModel):
    kind: RepairScenarioKind
    broken_supplier_offer_key: str | None = None
    broken_supplier_vendor_ref: str | None = None
    broken_workcenter_code: str | None = None
    impacted_customer_refs: tuple[str, ...] = Field(default_factory=tuple)
    baseline_offer_quantities: tuple[RepairOfferQuantity, ...] = Field(default_factory=tuple)
    baseline_manufacturing_quantities: tuple[RepairManufacturingQuantity, ...] = Field(
        default_factory=tuple
    )
    seeded_purchase_orders: tuple[ExistingPurchaseOrderData, ...] = Field(default_factory=tuple)
    seeded_manufacturing_orders: tuple[ExistingManufacturingOrderData, ...] = Field(
        default_factory=tuple
    )
    broken_purchase_order_refs: tuple[str, ...] = Field(default_factory=tuple)
    broken_manufacturing_order_refs: tuple[str, ...] = Field(default_factory=tuple)
    baseline_plan: PlanResult | None = None


class ProcurementInvoicingPolicy(BaseModel):
    invoice_required: bool = False
    payment_term: InvoicePaymentTerm | None = None
    downpayment_required: bool = False
    downpayment_threshold_amount: float | None = None
    downpayment_mode: DownpaymentMode | None = None
    downpayment_value: float | None = None

    @model_validator(mode="after")
    def validate_policy(self) -> ProcurementInvoicingPolicy:
        validate_procurement_invoicing_policy(
            invoice_required=self.invoice_required,
            payment_term=self.payment_term,
            downpayment_required=self.downpayment_required,
            downpayment_threshold_amount=self.downpayment_threshold_amount,
            downpayment_mode=self.downpayment_mode,
            downpayment_value=self.downpayment_value,
        )
        return self

    def threshold_applies(self, sale_order_amount_untaxed: float) -> bool:
        if (
            not self.invoice_required
            or not self.downpayment_required
            or self.downpayment_threshold_amount is None
        ):
            return False
        return sale_order_amount_untaxed + 0.01 >= self.downpayment_threshold_amount

    def invoice_flow_for(self, sale_order_amount_untaxed: float) -> InvoiceFlow:
        if not self.invoice_required:
            return "none"
        if self.threshold_applies(sale_order_amount_untaxed):
            return "downpayment_then_regular"
        return "regular"

    def downpayment_invoice_amount_for(self, sale_order_amount_untaxed: float) -> float:
        if not self.threshold_applies(sale_order_amount_untaxed):
            return 0.0
        if self.downpayment_mode == "percentage":
            return round(sale_order_amount_untaxed * float(self.downpayment_value or 0) / 100, 2)
        return round(float(self.downpayment_value or 0), 2)

    def regular_invoice_amount_for(self, sale_order_amount_untaxed: float) -> float:
        regular_amount = round(sale_order_amount_untaxed, 2)
        if not self.threshold_applies(sale_order_amount_untaxed):
            return regular_amount
        remaining_amount = round(
            regular_amount - self.downpayment_invoice_amount_for(sale_order_amount_untaxed),
            2,
        )
        if remaining_amount <= 0:
            raise RuntimeError(
                "Downpayment scenarios must leave a positive remaining balance for the final invoice"
            )
        return remaining_amount


class ScenarioBlueprint(BaseModel):
    scenario_number: int
    name: str
    instruction: str
    background_and_policy: str
    prompt_variant_seed: int | None = None
    objective_kind: ProcurementObjectiveKind = ProcurementObjectiveKind.min_new_spend
    margin: float
    customers: tuple[CustomerSpec, ...]
    vendors: tuple[VendorSpec, ...]
    product: ProductSpec
    manufactured_products: tuple[ManufacturingProductSpec, ...] = Field(default_factory=tuple)
    workcenters: tuple[WorkcenterSpec, ...] = Field(default_factory=tuple)
    system_parameters: tuple[SystemParameterData, ...] = Field(default_factory=tuple)
    unsat_demand: bool = False
    unsat_acceptance_policy: UnsatAcceptancePolicy | None = None
    manufacturing_only: ManufacturingOnlyConfig = Field(default_factory=ManufacturingOnlyConfig)
    invoicing_policy: ProcurementInvoicingPolicy = Field(default_factory=ProcurementInvoicingPolicy)
    repair_context: ProcurementRepairContext | None = None
    product_stock_units: dict[str, int] = Field(default_factory=dict)
    product_stock_cost_dollars: dict[str, float] = Field(default_factory=dict)

    @property
    def target_product_stock_units(self) -> int:
        return self.product_stock_units.get(self.product.code, 0)

    @property
    def target_product_stock_cost_dollars(self) -> float:
        return self.product_stock_cost_dollars.get(self.product.code, 0.0)

    @property
    def target_product_stock_cost_cents(self) -> int:
        return int(round(self.target_product_stock_cost_dollars * 100))

    @property
    def margin_bps(self) -> int:
        return int(round(self.margin * 10_000))

    @property
    def manufacturing_by_code(self) -> dict[str, ManufacturingProductSpec]:
        return {product.code: product for product in self.manufactured_products}

    @property
    def workcenters_by_code(self) -> dict[str, WorkcenterSpec]:
        return {workcenter.code: workcenter for workcenter in self.workcenters}

    @property
    def manufactured_product_codes(self) -> set[str]:
        return set(self.manufacturing_by_code)

    @property
    def leaf_component_codes(self) -> tuple[str, ...]:
        seen: list[str] = []
        manufactured = self.manufactured_product_codes
        for product in self.manufactured_products:
            for component in product.components:
                code = component.product.code
                if code in manufactured:
                    continue
                if code not in seen:
                    seen.append(code)
        return tuple(seen)

    def manufacturing_product(self, code: str) -> ManufacturingProductSpec:
        return self.manufacturing_by_code[code]


class PlanPurchaseOrder(BaseModel):
    plan_ref: str
    vendor_ref: str
    offer_key: str | None = None
    quantity: int
    unit_cost: float
    total_cost: float
    lead_time: int
    planned_arrival_days: int | None = None
    description: str | None = None
    product_code: str
    supply_role: Literal["finished", "component"]
    origin_customer_refs: tuple[str, ...] = Field(default_factory=tuple)
    origin_plan_refs: tuple[str, ...] = Field(default_factory=tuple)
    consumer_product_codes: tuple[str, ...] = Field(default_factory=tuple)


class PlanCustomer(BaseModel):
    ref: str
    accepted: bool = True
    action: TaskOrderAction = "create_confirm_prompt"
    task_order_source: TaskOrderSource = "prompt_only"
    task_order_ref: str = ""
    seeded_order_state: TaskOrderState | None = None
    reject_reasons: tuple[RejectReason, ...] = ()
    reject_reason_text: str | None = None
    stock_allocated: int
    demand: int
    fulfilled_demand: int = 0
    deadline: int
    price: float
    purchase_units: int
    assembly_units: int
    requested_revenue: float = 0.0
    revenue: float
    variable_cost: float
    component_cost: float
    assembly_cost: float
    margin: float


class PlanManufacturingOrder(BaseModel):
    plan_ref: str
    product_code: str
    quantity: int
    workcenter_code: str
    unit_cost: float
    total_cost: float
    lead_time: int
    time_per_unit_minutes: float
    operation_name: str = "Assembly"
    origin_customer_refs: tuple[str, ...] = Field(default_factory=tuple)
    origin_plan_refs: tuple[str, ...] = Field(default_factory=tuple)
    consumer_product_codes: tuple[str, ...] = Field(default_factory=tuple)
    level: int = 0
    needed_by_days: int | None = None


class PlanResult(BaseModel):
    objective_kind: ProcurementObjectiveKind = ProcurementObjectiveKind.min_new_spend
    objective_value: float = Field(
        description="Optimal value for the task's declared procurement objective."
    )
    stock_cost: float
    component_cost: float
    assembly_cost: float
    allocations: tuple[PlanCustomer, ...]
    purchase_orders: tuple[PlanPurchaseOrder, ...]
    manufacturing_orders: tuple[PlanManufacturingOrder, ...] = Field(default_factory=tuple)
    stock_report: tuple[StockUsage, ...] = Field(default_factory=tuple)

    @computed_field
    def optimal_new_spend(self) -> float:
        return round(self.expected_new_spend, 2)

    @computed_field
    def distinct_vendors_used(self) -> int:
        return len(
            {
                order.vendor_ref
                for order in self.purchase_orders
                if order.quantity > 0 and order.vendor_ref
            }
        )

    @computed_field
    def total_scheduled_minutes(self) -> float:
        return round(
            sum(
                order.quantity * order.time_per_unit_minutes for order in self.manufacturing_orders
            ),
            2,
        )

    @computed_field
    def finished_purchase_spend(self) -> float:
        return sum(
            order.total_cost for order in self.purchase_orders if order.supply_role == "finished"
        )

    @computed_field
    def component_purchase_spend(self) -> float:
        return sum(
            order.total_cost for order in self.purchase_orders if order.supply_role == "component"
        )

    @computed_field
    def expected_new_spend(self) -> float:
        return self.finished_purchase_spend + self.component_purchase_spend + self.assembly_cost

    @computed_field
    def new_spend_units(self) -> int:
        return sum(
            customer.purchase_units + customer.assembly_units for customer in self.allocations
        )

    @computed_field
    def revenue_from_new_spend(self) -> float:
        return sum(
            customer.price * (customer.purchase_units + customer.assembly_units)
            for customer in self.allocations
        )

    @computed_field
    def new_spend_coverage_ratio(self) -> float | None:
        new_spend = self.expected_new_spend
        if new_spend <= 0:
            return None
        return self.revenue_from_new_spend / new_spend

    @computed_field
    def new_spend_margin(self) -> float | None:
        revenue = self.revenue_from_new_spend
        if revenue <= 0 or self.expected_new_spend <= 0:
            return None
        return (revenue - self.expected_new_spend) / revenue

    @computed_field
    def total_revenue(self) -> float:
        return sum(customer.revenue for customer in self.allocations)

    @computed_field
    def assembly_units(self) -> int:
        return sum(customer.assembly_units for customer in self.allocations)

    @computed_field
    def total_cost(self) -> float:
        return (
            self.stock_cost
            + self.finished_purchase_spend
            + self.component_cost
            + self.assembly_cost
        )

    @computed_field
    def margin(self) -> float:
        revenue = self.total_revenue
        return (revenue - self.total_cost) / revenue if revenue else 0.0


class StockUsage(BaseModel):
    code: str
    label: str | None
    available: int
    used: int
    remaining: int


class ScenarioBuild(BaseModel):
    scenario: ScenarioData
    optimal_plan: PlanResult


class _LeafVendorVars(NamedTuple):
    activation: BoolVar
    quantity: cp_model.IntVar


class _ModelVars(NamedTuple):
    stock_to_due_day: dict[int, cp_model.IntVar]
    finished_vendor_quantity_by_due_day: dict[tuple[int, int], cp_model.IntVar]
    finished_vendor_activation: tuple[BoolVar, ...]
    final_product_workcenter_choice_quantity_by_due_day: dict[tuple[int, int], cp_model.IntVar]
    manufactured_choice_quantity_by_day: dict[tuple[str, int, int], cp_model.IntVar]
    stock_use_by_code: dict[str, cp_model.IntVar]
    component_vendor_vars: dict[tuple[str, str], _LeafVendorVars]


class _ModelManufacturingContext(NamedTuple):
    finished_vendors: tuple[VendorSpec, ...]
    final_product: ManufacturingProductSpec | None
    final_product_workcenter_choices: tuple[WorkcenterChoiceSpec, ...]
    total_demand: int
    leaf_requirements: dict[str, int]
    event_days: tuple[int, ...]
    due_days_by_code: dict[str, tuple[int, ...]]
    customer_indices_by_deadline: dict[int, tuple[int, ...]]


class _FinalDemandVars(NamedTuple):
    stock_to_due_day: dict[int, cp_model.IntVar]
    finished_vendor_quantity_by_due_day: dict[tuple[int, int], cp_model.IntVar]
    finished_vendor_activation: tuple[BoolVar, ...]
    final_product_workcenter_choice_quantity_by_due_day: dict[tuple[int, int], cp_model.IntVar]


class _ProductionSupportVars(NamedTuple):
    manufactured_choice_quantity_by_day: dict[tuple[str, int, int], cp_model.IntVar]
    stock_use_by_code: dict[str, cp_model.IntVar]
    component_vendor_vars: dict[tuple[str, str], _LeafVendorVars]


class ScenarioGenerator:
    def __init__(
        self,
        blueprint: ScenarioBlueprint,
        *,
        solve_time_limit: float = 5.0,
        log_level: int = logging.INFO,
        num_search_workers: int = 1,
        seed: int | None = None,
        randomize_search: bool = False,
    ):
        self.blueprint = blueprint
        self.scale = 100
        self.solve_time_limit = solve_time_limit
        self.num_search_workers = max(1, int(num_search_workers))
        self._seed = None if seed is None else int(seed)
        self._solver_seed = 0 if self._seed is None else (self._seed % (2**31 - 1))
        self.randomize_search = bool(randomize_search)
        logger.setLevel(log_level)

    def generate(self) -> ScenarioBuild:
        optimal_plan = self._solve(self.blueprint)
        scenario = self._build_scenario_data(optimal_plan)
        return ScenarioBuild(scenario=scenario, optimal_plan=optimal_plan)

    def solve_plan_for_objective(
        self,
        objective_kind: ProcurementObjectiveKind | None = None,
        *,
        fixed_spend_cents: int | None = None,
    ) -> PlanResult:
        return self._solve(
            self.blueprint,
            objective_kind=objective_kind,
            fixed_spend_cents=fixed_spend_cents,
        )

    def _solve(
        self,
        scenario: ScenarioBlueprint,
        *,
        objective_kind: ProcurementObjectiveKind | None = None,
        fixed_spend_cents: int | None = None,
    ) -> PlanResult:
        selected_objective = objective_kind or scenario.objective_kind
        solve_label = (
            f"scenario={scenario.scenario_number} objective={selected_objective.value} "
            f"fixed_spend={fixed_spend_cents}"
        )
        model_started = time.perf_counter()
        model, vars_bundle = self._build_model(scenario)
        logger.info(
            "Built CP-SAT model %s in %.2fs",
            solve_label,
            time.perf_counter() - model_started,
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.solve_time_limit
        solver.parameters.num_search_workers = self.num_search_workers
        solver.parameters.random_seed = self._solver_seed
        solver.parameters.randomize_search = self.randomize_search

        total_spend, tie_break_vars = self._build_objectives(scenario, vars_bundle)
        if fixed_spend_cents is not None:
            model.Add(total_spend == fixed_spend_cents)

        primary_objective, objective_scale = self._build_primary_objective(
            model,
            scenario,
            vars_bundle,
            objective_kind=selected_objective,
        )
        self._record_model_metrics(
            model=model,
            scenario=scenario,
            objective_kind=selected_objective,
            fixed_spend_cents=fixed_spend_cents,
            tie_break_variables=len(tie_break_vars),
        )
        objective_value = 0.0
        if primary_objective is None:
            logger.info("CP-SAT phase selected %s feasibility-only", solve_label)
            status = self._solve_with_log(solver, model, f"{solve_label} feasibility")
            if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                solver.parameters.max_time_in_seconds = self.solve_time_limit * 3
                status = self._solve_with_log(
                    solver,
                    model,
                    f"{solve_label} feasibility retry",
                )
            if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                raise RuntimeError(
                    "Solver failed to find a feasible canonical plan "
                    f"(status={solver.StatusName(status)})"
                )
        else:
            logger.info("CP-SAT phase selected %s primary objective", solve_label)
            model.Minimize(primary_objective)
            status = self._solve_with_log(solver, model, f"{solve_label} primary")
            if status != cp_model.OPTIMAL:
                solver.parameters.max_time_in_seconds = self.solve_time_limit * 3
                status = self._solve_with_log(solver, model, f"{solve_label} primary retry")
            if status != cp_model.OPTIMAL:
                raise RuntimeError(
                    f"Solver failed to certify optimality (status={solver.StatusName(status)})"
                )
            objective_target = int(round(solver.ObjectiveValue()))
            model.Add(primary_objective == objective_target)
            objective_value = round(objective_target / objective_scale, 2)
            if self._uses_spend_as_secondary_tiebreak(selected_objective):
                logger.info("CP-SAT phase selected %s spend secondary tie-break", solve_label)
                model.Minimize(total_spend)
                status = self._solve_with_log(
                    solver,
                    model,
                    f"{solve_label} spend secondary",
                )
                if status != cp_model.OPTIMAL:
                    solver.parameters.max_time_in_seconds = self.solve_time_limit * 3
                    status = self._solve_with_log(
                        solver,
                        model,
                        f"{solve_label} spend secondary retry",
                    )
                if status != cp_model.OPTIMAL:
                    raise RuntimeError(
                        "Solver failed to certify spend secondary tie-break "
                        f"(status={solver.StatusName(status)})"
                    )
                model.Add(total_spend == int(round(solver.ObjectiveValue())))
        logger.info("CP-SAT phase selected %s lexicographic tie-break", solve_label)
        self._solve_lexicographic_phase(model, solver, tie_break_vars)
        logger.info("CP-SAT extract plan start %s", solve_label)
        extract_started = time.perf_counter()
        plan = self._extract_plan(
            scenario,
            solver,
            vars_bundle,
            objective_kind=selected_objective,
            objective_value=objective_value,
        )
        logger.info(
            "CP-SAT extract plan done %s elapsed=%.2fs",
            solve_label,
            time.perf_counter() - extract_started,
        )
        return plan

    def _solve_with_log(
        self,
        solver: cp_model.CpSolver,
        model: cp_model.CpModel,
        phase: str,
    ) -> int:
        time_limit = solver.parameters.max_time_in_seconds
        logger.info("CP-SAT start %s time_limit=%.1fs", phase, time_limit)
        started = time.perf_counter()
        status = solver.Solve(model)
        elapsed_seconds = time.perf_counter() - started
        logger.info(
            "CP-SAT done %s status=%s elapsed=%.2fs solver_wall=%.2fs",
            phase,
            solver.StatusName(status),
            elapsed_seconds,
            solver.WallTime(),
        )
        trace = _solver_trace.get()
        if trace is not None:
            trace.solve_calls.append(
                ProcurementSolverCallMetrics(
                    phase=phase,
                    status=solver.StatusName(status),
                    time_limit_seconds=float(time_limit),
                    elapsed_seconds=elapsed_seconds,
                    solver_wall_seconds=solver.WallTime(),
                )
            )
        return status

    def _record_model_metrics(
        self,
        *,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        objective_kind: ProcurementObjectiveKind,
        fixed_spend_cents: int | None,
        tie_break_variables: int,
    ) -> None:
        trace = _solver_trace.get()
        if trace is None:
            return
        proto = model.Proto()
        boolean_variables = sum(
            1 for variable in proto.variables if list(variable.domain) == [0, 1]
        )
        linear_constraints = 0
        bool_or_constraints = 0
        for constraint in proto.constraints:
            if constraint.has_linear():
                linear_constraints += 1
            elif constraint.has_bool_or():
                bool_or_constraints += 1
        trace.models.append(
            ProcurementSolverModelMetrics(
                scenario_number=scenario.scenario_number,
                objective_kind=objective_kind.value,
                fixed_spend_cents=fixed_spend_cents,
                variables=len(proto.variables),
                boolean_variables=boolean_variables,
                integer_variables=len(proto.variables) - boolean_variables,
                constraints=len(proto.constraints),
                linear_constraints=linear_constraints,
                bool_or_constraints=bool_or_constraints,
                tie_break_variables=tie_break_variables,
                customers=len(scenario.customers),
                vendors=len(scenario.vendors),
                manufacturing_products=len(scenario.manufacturing_by_code),
                workcenters=len(scenario.workcenters),
            )
        )

    def _build_model(self, scenario: ScenarioBlueprint) -> tuple[cp_model.CpModel, _ModelVars]:
        """Build the CP-SAT feasibility model for one scenario blueprint."""
        started = time.perf_counter()
        logger.info(
            "CP-SAT model build start scenario=%s customers=%s vendors=%s manufacturing_products=%s",
            scenario.scenario_number,
            len(scenario.customers),
            len(scenario.vendors),
            len(scenario.manufacturing_by_code),
        )
        model = cp_model.CpModel()
        step_started = time.perf_counter()
        context = self._build_model_context(scenario)
        logger.info(
            "CP-SAT model context built scenario=%s event_days=%s leaf_requirements=%s elapsed=%.2fs",
            scenario.scenario_number,
            len(context.event_days),
            len(context.leaf_requirements),
            time.perf_counter() - step_started,
        )
        step_started = time.perf_counter()
        final_demand_vars = self._build_final_demand_vars(model, scenario, context)
        logger.info(
            "CP-SAT final demand vars built scenario=%s finished_vendor_vars=%s workcenter_choice_vars=%s elapsed=%.2fs",
            scenario.scenario_number,
            len(final_demand_vars.finished_vendor_quantity_by_due_day),
            len(final_demand_vars.final_product_workcenter_choice_quantity_by_due_day),
            time.perf_counter() - step_started,
        )
        step_started = time.perf_counter()
        production_support_vars = self._build_production_support_vars(model, scenario, context)
        logger.info(
            "CP-SAT production vars built scenario=%s manufactured_vars=%s stock_vars=%s component_vendor_vars=%s elapsed=%.2fs",
            scenario.scenario_number,
            len(production_support_vars.manufactured_choice_quantity_by_day),
            len(production_support_vars.stock_use_by_code),
            len(production_support_vars.component_vendor_vars),
            time.perf_counter() - step_started,
        )
        vars_bundle = _ModelVars(
            stock_to_due_day=final_demand_vars.stock_to_due_day,
            finished_vendor_quantity_by_due_day=(
                final_demand_vars.finished_vendor_quantity_by_due_day
            ),
            finished_vendor_activation=final_demand_vars.finished_vendor_activation,
            final_product_workcenter_choice_quantity_by_due_day=(
                final_demand_vars.final_product_workcenter_choice_quantity_by_due_day
            ),
            manufactured_choice_quantity_by_day=(
                production_support_vars.manufactured_choice_quantity_by_day
            ),
            stock_use_by_code=production_support_vars.stock_use_by_code,
            component_vendor_vars=production_support_vars.component_vendor_vars,
        )
        logger.info(
            "CP-SAT variable summary scenario=%s tie_break_candidates=%s",
            scenario.scenario_number,
            len(final_demand_vars.finished_vendor_activation)
            + len(final_demand_vars.final_product_workcenter_choice_quantity_by_due_day)
            + len(production_support_vars.manufactured_choice_quantity_by_day)
            + len(production_support_vars.stock_use_by_code)
            + (2 * len(production_support_vars.component_vendor_vars)),
        )
        step_started = time.perf_counter()
        self._add_finished_vendor_purchase_constraints(model, vars_bundle, context)
        logger.info(
            "CP-SAT finished vendor constraints added scenario=%s elapsed=%.2fs",
            scenario.scenario_number,
            time.perf_counter() - step_started,
        )
        step_started = time.perf_counter()
        self._add_customer_fulfillment_constraints(model, scenario, vars_bundle, context)
        logger.info(
            "CP-SAT customer constraints added scenario=%s elapsed=%.2fs",
            scenario.scenario_number,
            time.perf_counter() - step_started,
        )
        step_started = time.perf_counter()
        self._add_event_time_component_balance_constraints(model, scenario, vars_bundle, context)
        logger.info(
            "CP-SAT component balance constraints added scenario=%s elapsed=%.2fs",
            scenario.scenario_number,
            time.perf_counter() - step_started,
        )
        step_started = time.perf_counter()
        self._add_workcenter_capacity_constraints(
            model,
            scenario,
            vars_bundle,
            self._final_product_choice_totals(vars_bundle, context),
            context.final_product_workcenter_choices,
            context,
        )
        logger.info(
            "CP-SAT workcenter constraints added scenario=%s elapsed=%.2fs",
            scenario.scenario_number,
            time.perf_counter() - step_started,
        )
        logger.info(
            "CP-SAT model build done scenario=%s elapsed=%.2fs",
            scenario.scenario_number,
            time.perf_counter() - started,
        )
        return model, vars_bundle

    def _build_model_context(self, scenario: ScenarioBlueprint) -> _ModelManufacturingContext:
        """Collect derived scenario values reused across model-building helpers."""
        final_product = scenario.manufacturing_by_code.get(scenario.product.code)
        final_product_workcenter_choices = (
            final_product.workcenter_choices if final_product is not None else ()
        )
        due_days_by_code = self._due_days_by_code(scenario)
        customer_indices_by_deadline: dict[int, tuple[int, ...]] = {}
        grouped_customer_indices: dict[int, list[int]] = defaultdict(list)
        for customer_index, customer in enumerate(scenario.customers):
            grouped_customer_indices[customer.deadline].append(customer_index)
        for deadline, indices in grouped_customer_indices.items():
            customer_indices_by_deadline[deadline] = tuple(indices)
        return _ModelManufacturingContext(
            finished_vendors=self._finished_product_vendors(scenario),
            final_product=final_product,
            final_product_workcenter_choices=final_product_workcenter_choices,
            total_demand=sum(
                customer.demand
                for customer in scenario.customers
                if not customer.is_rejected_upfront
            ),
            leaf_requirements=self._leaf_requirements_for_product(scenario, scenario.product.code),
            event_days=self._event_days(scenario, due_days_by_code),
            due_days_by_code=due_days_by_code,
            customer_indices_by_deadline=customer_indices_by_deadline,
        )

    def _due_days_by_code(self, scenario: ScenarioBlueprint) -> dict[str, tuple[int, ...]]:
        """Return completion checkpoints for each manufactured product code.

        Final-product due days come directly from customer deadlines. Manufactured
        child products inherit due checkpoints from the start cutoffs of every
        parent route that could consume them.
        """
        due_days_by_code: dict[str, set[int]] = {
            scenario.product.code: {customer.deadline for customer in scenario.customers}
        }
        manufactured_codes = scenario.manufactured_product_codes
        changed = True
        while changed:
            changed = False
            for parent in scenario.manufactured_products:
                parent_due_days = tuple(due_days_by_code.get(parent.code, ()))
                if not parent_due_days:
                    continue
                for due_day in parent_due_days:
                    for choice in parent.workcenter_choices:
                        child_due_day = due_day - choice.lead_time_days
                        if child_due_day < 0:
                            continue
                        for component in parent.components:
                            code = component.product.code
                            if code not in manufactured_codes:
                                continue
                            code_due_days = due_days_by_code.setdefault(code, set())
                            if child_due_day not in code_due_days:
                                code_due_days.add(child_due_day)
                                changed = True
        return {code: tuple(sorted(days)) for code, days in due_days_by_code.items()}

    def _event_days(
        self,
        scenario: ScenarioBlueprint,
        due_days_by_code: dict[str, tuple[int, ...]],
    ) -> tuple[int, ...]:
        """Return the compact event horizon used for cumulative timing checks."""
        event_days: set[int] = set()
        for due_days in due_days_by_code.values():
            event_days.update(due_days)
        for parent in scenario.manufactured_products:
            for due_day in due_days_by_code.get(parent.code, ()):
                for choice in parent.workcenter_choices:
                    cutoff_day = due_day - choice.lead_time_days
                    if cutoff_day >= 0:
                        event_days.add(cutoff_day)
        return tuple(sorted(event_days))

    def _demand_by_due_day(
        self, scenario: ScenarioBlueprint, context: _ModelManufacturingContext
    ) -> dict[int, int]:
        """Return total customer demand grouped by due day."""
        return {
            due_day: sum(
                scenario.customers[customer_index].demand
                for customer_index in customer_indices
                if not scenario.customers[customer_index].is_rejected_upfront
            )
            for due_day, customer_indices in context.customer_indices_by_deadline.items()
        }

    def _build_final_demand_vars(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        context: _ModelManufacturingContext,
    ) -> _FinalDemandVars:
        """Create fully bucketed top-level fulfillment variables."""
        demand_by_due_day = self._demand_by_due_day(scenario, context)
        stock_to_due_day = {
            due_day: model.NewIntVar(
                0,
                min(demand, scenario.target_product_stock_units),
                f"stock_due_{due_day}",
            )
            for due_day, demand in demand_by_due_day.items()
        }
        model.Add(
            cp_model.LinearExpr.Sum(list(stock_to_due_day.values()))
            <= scenario.target_product_stock_units
        )
        finished_vendor_quantity_by_due_day: dict[tuple[int, int], cp_model.IntVar] = {}
        for vendor_index, vendor in enumerate(context.finished_vendors):
            for due_day, demand in demand_by_due_day.items():
                upper_bound = (
                    demand
                    if (not scenario.manufacturing_only.enabled and vendor.lead_time <= due_day)
                    else 0
                )
                finished_vendor_quantity_by_due_day[(vendor_index, due_day)] = model.NewIntVar(
                    0,
                    upper_bound,
                    f"fin_qty_{vendor.ref}_{due_day}",
                )
        finished_vendor_activation: list[BoolVar] = []
        for vendor in context.finished_vendors:
            activation = model.NewBoolVar(f"fin_use_{vendor.ref}")
            finished_vendor_activation.append(activation)
        final_product_workcenter_choice_quantity_by_due_day: dict[
            tuple[int, int], cp_model.IntVar
        ] = {}
        for choice_index, choice in enumerate(context.final_product_workcenter_choices):
            for due_day, demand in demand_by_due_day.items():
                upper_bound = demand if choice.lead_time_days <= due_day else 0
                final_product_workcenter_choice_quantity_by_due_day[(choice_index, due_day)] = (
                    model.NewIntVar(
                        0,
                        upper_bound,
                        f"fin_mfg_{scenario.product.code}_{choice.workcenter_code}_{due_day}",
                    )
                )
        return _FinalDemandVars(
            stock_to_due_day=stock_to_due_day,
            finished_vendor_quantity_by_due_day=finished_vendor_quantity_by_due_day,
            finished_vendor_activation=tuple(finished_vendor_activation),
            final_product_workcenter_choice_quantity_by_due_day=(
                final_product_workcenter_choice_quantity_by_due_day
            ),
        )

    def _add_finished_vendor_purchase_constraints(
        self,
        model: cp_model.CpModel,
        vars_bundle: _ModelVars,
        context: _ModelManufacturingContext,
    ) -> None:
        """Link finished-goods vendor totals to min/max order decisions."""
        repair = self.blueprint.repair_context
        for vendor_index, vendor in enumerate(context.finished_vendors):
            total_vendor_flow = cp_model.LinearExpr.Sum(
                [
                    qty_var
                    for (
                        idx,
                        _due_day,
                    ), qty_var in vars_bundle.finished_vendor_quantity_by_due_day.items()
                    if idx == vendor_index
                ]
            )
            upper_bound = vendor.max_qty if vendor.max_qty is not None else context.total_demand
            activation = vars_bundle.finished_vendor_activation[vendor_index]
            model.Add(total_vendor_flow >= vendor.min_qty * activation)
            model.Add(total_vendor_flow <= upper_bound * activation)
            if (
                repair is not None
                and repair.kind == "supplier_cancellation"
                and repair.broken_supplier_offer_key == vendor.offer_key
            ):
                model.Add(total_vendor_flow == 0)

    def _build_production_support_vars(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        context: _ModelManufacturingContext,
    ) -> _ProductionSupportVars:
        """Create subassembly, component stock, and component purchasing variables."""
        manufactured_choice_quantity_by_day: dict[tuple[str, int, int], cp_model.IntVar] = {}
        for manufacturing_product in scenario.manufactured_products:
            if manufacturing_product.code == scenario.product.code:
                continue
            for due_day in context.due_days_by_code.get(manufacturing_product.code, ()):
                for choice_index, choice in enumerate(manufacturing_product.workcenter_choices):
                    upper_bound = (
                        context.total_demand * 8 if choice.lead_time_days <= due_day else 0
                    )
                    manufactured_choice_quantity_by_day[
                        (manufacturing_product.code, choice_index, due_day)
                    ] = model.NewIntVar(
                        0,
                        upper_bound,
                        f"mfg_{manufacturing_product.code}_{choice_index}_{due_day}",
                    )
        stock_use_by_code: dict[str, cp_model.IntVar] = {}
        for code, qty in scenario.product_stock_units.items():
            if code == scenario.product.code:
                continue
            if qty <= 0:
                continue
            stock_use_by_code[code] = model.NewIntVar(0, qty, f"stock_use_{code}")
        component_vendor_vars: dict[tuple[str, str], _LeafVendorVars] = {}
        max_event_day = max(context.event_days, default=0)
        for code in scenario.leaf_component_codes:
            vendors = self._component_vendors(scenario, code)
            per_final_unit = max(context.leaf_requirements.get(code, 1), 1)
            for vendor in vendors:
                activation = model.NewBoolVar(f"comp_use_{code}_{vendor.ref}")
                implied_need = max(context.total_demand * per_final_unit, 0)
                if vendor.lead_time > max_event_day:
                    upper_bound = 0
                elif vendor.max_qty is not None:
                    upper_bound = vendor.max_qty
                else:
                    upper_bound = max(implied_need, vendor.min_qty)
                quantity = model.NewIntVar(0, upper_bound, f"comp_qty_{code}_{vendor.ref}")
                component_vendor_vars[(code, vendor.ref)] = _LeafVendorVars(activation, quantity)
                model.Add(quantity >= vendor.min_qty * activation)
                model.Add(quantity <= upper_bound * activation)
        return _ProductionSupportVars(
            manufactured_choice_quantity_by_day=manufactured_choice_quantity_by_day,
            stock_use_by_code=stock_use_by_code,
            component_vendor_vars=component_vendor_vars,
        )

    def _add_customer_fulfillment_constraints(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
        context: _ModelManufacturingContext,
    ) -> None:
        """Ensure demand is met, budgets hold, and new spend clears the floor."""
        unit_revenue_cents = int(round(scenario.product.list_price_dollars * 100))
        demand_by_due_day = self._demand_by_due_day(scenario, context)
        for customer in scenario.customers:
            if customer.is_rejected_upfront:
                continue
            if customer.budget_cents is not None:
                model.Add(unit_revenue_cents * customer.demand <= customer.budget_cents)
        for due_day, demand in demand_by_due_day.items():
            model.Add(
                vars_bundle.stock_to_due_day[due_day]
                + cp_model.LinearExpr.Sum(
                    [
                        vars_bundle.finished_vendor_quantity_by_due_day[(vendor_index, due_day)]
                        for vendor_index in range(len(context.finished_vendors))
                    ]
                )
                + cp_model.LinearExpr.Sum(
                    [
                        vars_bundle.final_product_workcenter_choice_quantity_by_due_day[
                            (choice_index, due_day)
                        ]
                        for choice_index in range(len(context.final_product_workcenter_choices))
                    ]
                )
                == demand
            )
        if not scenario.margin:
            return
        revenue_from_new_spend = unit_revenue_cents * self._new_spend_backed_finished_units_expr(
            vars_bundle
        )
        new_spend = cp_model.LinearExpr.Sum(self._new_spend_terms(scenario, vars_bundle))
        model.Add(new_spend * 10_000 <= revenue_from_new_spend * (10_000 - scenario.margin_bps))

    def _new_spend_backed_finished_units_expr(self, vars_bundle: _ModelVars) -> cp_model.LinearExpr:
        """Return finished units fulfilled via buying or manufacturing, not stock."""
        return cp_model.LinearExpr.Sum(
            [
                *vars_bundle.finished_vendor_quantity_by_due_day.values(),
                *vars_bundle.final_product_workcenter_choice_quantity_by_due_day.values(),
            ]
        )

    def _new_spend_terms(
        self,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
    ) -> list[cp_model.LinearExpr]:
        """Return all modeled spend terms that count as fresh spend."""
        finished_vendors = self._finished_product_vendors(scenario)
        final_product = scenario.manufacturing_by_code.get(scenario.product.code)
        spend_terms: list[cp_model.LinearExpr] = []
        for (
            vendor_index,
            _due_day,
        ), flow in vars_bundle.finished_vendor_quantity_by_due_day.items():
            spend_terms.append(finished_vendors[vendor_index].price_cents * flow)
        if final_product is not None:
            for (
                choice_index,
                _due_day,
            ), flow in vars_bundle.final_product_workcenter_choice_quantity_by_due_day.items():
                choice = final_product.workcenter_choices[choice_index]
                spend_terms.append(choice.unit_cost_cents * flow)
        for (
            code,
            choice_index,
            _due_day,
        ), qty_var in vars_bundle.manufactured_choice_quantity_by_day.items():
            choice = scenario.manufacturing_product(code).workcenter_choices[choice_index]
            spend_terms.append(choice.unit_cost_cents * qty_var)
        for (code, vendor_ref), vendor_vars in vars_bundle.component_vendor_vars.items():
            vendor = next(
                vendor
                for vendor in scenario.vendors
                if vendor.product_code == code and vendor.ref == vendor_ref
            )
            spend_terms.append(vendor.price_cents * vendor_vars.quantity)
        return spend_terms

    def _scheduled_minutes_terms(
        self,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
    ) -> list[cp_model.LinearExpr]:
        minute_terms: list[cp_model.LinearExpr] = []
        final_product = scenario.manufacturing_by_code.get(scenario.product.code)
        if final_product is not None:
            for (
                choice_index,
                _due_day,
            ), flow in vars_bundle.final_product_workcenter_choice_quantity_by_due_day.items():
                choice = final_product.workcenter_choices[choice_index]
                minute_terms.append(int(round(choice.time_per_unit_minutes * 1000)) * flow)
        for (
            code,
            choice_index,
            _due_day,
        ), qty_var in vars_bundle.manufactured_choice_quantity_by_day.items():
            choice = scenario.manufacturing_product(code).workcenter_choices[choice_index]
            minute_terms.append(int(round(choice.time_per_unit_minutes * 1000)) * qty_var)
        return minute_terms

    def _vendor_usage_expr(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
    ) -> cp_model.LinearExpr:
        activations_by_ref: dict[str, list[BoolVar]] = defaultdict(list)
        finished_vendors = self._finished_product_vendors(scenario)
        for vendor_index, vendor in enumerate(finished_vendors):
            activations_by_ref[vendor.ref].append(
                vars_bundle.finished_vendor_activation[vendor_index]
            )
        for (_code, vendor_ref), vendor_vars in vars_bundle.component_vendor_vars.items():
            activations_by_ref[vendor_ref].append(vendor_vars.activation)

        vendor_usage_vars: list[BoolVar] = []
        for vendor_ref, activations in sorted(activations_by_ref.items()):
            if len(activations) == 1:
                vendor_usage_vars.append(activations[0])
                continue
            grouped_use = model.NewBoolVar(f"use_vendor_{vendor_ref.replace('-', '_')}")
            model.AddMaxEquality(grouped_use, activations)
            vendor_usage_vars.append(grouped_use)
        return cp_model.LinearExpr.Sum(vendor_usage_vars)

    def _repair_penalty_expr(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
    ) -> cp_model.LinearExpr:
        repair = scenario.repair_context
        if repair is None:
            raise RuntimeError("repair_plan objective requires repair_context")
        context = self._build_model_context(scenario)
        total_demand = max(context.total_demand, 0)
        repair_upper_bound = max(
            total_demand * 8,
            max((int(row.quantity) for row in repair.baseline_offer_quantities), default=0),
            max(
                (int(row.quantity) for row in repair.baseline_manufacturing_quantities),
                default=0,
            ),
            1,
        )
        penalty_terms: list[cp_model.LinearExpr] = []

        baseline_offer_quantities = {
            row.offer_key: int(row.quantity) for row in repair.baseline_offer_quantities
        }
        finished_vendors = self._finished_product_vendors(scenario)
        for vendor_index, vendor in enumerate(finished_vendors):
            total_vendor_flow = cp_model.LinearExpr.Sum(
                [
                    qty_var
                    for (
                        idx,
                        _due_day,
                    ), qty_var in vars_bundle.finished_vendor_quantity_by_due_day.items()
                    if idx == vendor_index
                ]
            )
            if (
                repair.kind == "supplier_cancellation"
                and repair.broken_supplier_offer_key == vendor.offer_key
            ):
                model.Add(total_vendor_flow == 0)
            diff = model.NewIntVar(
                0,
                repair_upper_bound,
                f"repair_offer_diff_{vendor_index}",
            )
            baseline_qty = baseline_offer_quantities.get(vendor.offer_key, 0)
            model.Add(total_vendor_flow - baseline_qty <= diff)
            model.Add(baseline_qty - total_vendor_flow <= diff)
            penalty_terms.append(diff)

        baseline_manufacturing_quantities = {
            (row.product_code, row.workcenter_code): int(row.quantity)
            for row in repair.baseline_manufacturing_quantities
        }
        for choice_index, choice in enumerate(context.final_product_workcenter_choices):
            total_choice_flow = cp_model.LinearExpr.Sum(
                [
                    qty_var
                    for (
                        idx,
                        _due_day,
                    ), qty_var in vars_bundle.final_product_workcenter_choice_quantity_by_due_day.items()
                    if idx == choice_index
                ]
            )
            if (
                repair.kind == "workcenter_outage"
                and repair.broken_workcenter_code == choice.workcenter_code
            ):
                model.Add(total_choice_flow == 0)
            diff = model.NewIntVar(
                0,
                repair_upper_bound,
                f"repair_workcenter_diff_{choice_index}",
            )
            baseline_qty = baseline_manufacturing_quantities.get(
                (scenario.product.code, choice.workcenter_code),
                0,
            )
            model.Add(total_choice_flow - baseline_qty <= diff)
            model.Add(baseline_qty - total_choice_flow <= diff)
            penalty_terms.append(diff)

        manufactured_choice_totals: dict[tuple[str, str], list[cp_model.IntVar]] = defaultdict(
            list
        )
        for (product_code, choice_index, _due_day), qty_var in (
            vars_bundle.manufactured_choice_quantity_by_day.items()
        ):
            choice = scenario.manufacturing_product(product_code).workcenter_choices[choice_index]
            manufactured_choice_totals[(product_code, choice.workcenter_code)].append(qty_var)
        for manufacturing_index, ((product_code, workcenter_code), qty_vars) in enumerate(
            sorted(manufactured_choice_totals.items())
        ):
            total_choice_flow = cp_model.LinearExpr.Sum(qty_vars)
            if (
                repair.kind == "workcenter_outage"
                and repair.broken_workcenter_code == workcenter_code
            ):
                model.Add(total_choice_flow == 0)
            diff = model.NewIntVar(
                0,
                repair_upper_bound,
                f"repair_mfg_workcenter_diff_{manufacturing_index}",
            )
            baseline_qty = baseline_manufacturing_quantities.get((product_code, workcenter_code), 0)
            model.Add(total_choice_flow - baseline_qty <= diff)
            model.Add(baseline_qty - total_choice_flow <= diff)
            penalty_terms.append(diff)

        return cp_model.LinearExpr.Sum(penalty_terms)

    def _build_primary_objective(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
        *,
        objective_kind: ProcurementObjectiveKind,
    ) -> tuple[cp_model.LinearExpr | None, int]:
        if objective_kind is ProcurementObjectiveKind.constraint_only:
            return None, 1
        if objective_kind is ProcurementObjectiveKind.min_new_spend:
            return cp_model.LinearExpr.Sum(self._new_spend_terms(scenario, vars_bundle)), self.scale
        if objective_kind is ProcurementObjectiveKind.vendor_consolidation:
            return self._vendor_usage_expr(model, scenario, vars_bundle), 1
        if objective_kind is ProcurementObjectiveKind.capacity_preservation:
            return cp_model.LinearExpr.Sum(
                self._scheduled_minutes_terms(scenario, vars_bundle)
            ), 1000
        if objective_kind is ProcurementObjectiveKind.repair_plan:
            return self._repair_penalty_expr(model, scenario, vars_bundle), 1
        raise RuntimeError(f"Unsupported procurement objective_kind={objective_kind}")

    def _uses_spend_as_secondary_tiebreak(
        self,
        objective_kind: ProcurementObjectiveKind,
    ) -> bool:
        return objective_kind in {
            ProcurementObjectiveKind.vendor_consolidation,
            ProcurementObjectiveKind.capacity_preservation,
            ProcurementObjectiveKind.repair_plan,
        }

    def _final_product_choice_totals(
        self,
        vars_bundle: _ModelVars,
        context: _ModelManufacturingContext,
    ) -> list[cp_model.LinearExpr]:
        """Aggregate final-product builds by workcenter choice across due buckets."""
        return [
            cp_model.LinearExpr.Sum(
                [
                    qty_var
                    for (
                        idx,
                        _due_day,
                    ), qty_var in vars_bundle.final_product_workcenter_choice_quantity_by_due_day.items()
                    if idx == choice_index
                ]
            )
            for choice_index in range(len(context.final_product_workcenter_choices))
        ]

    def _completion_expr(
        self,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
        product_code: str,
        choice_index: int,
        due_day: int,
    ) -> cp_model.LinearExpr:
        """Return the completion expression for one route at one due checkpoint."""
        if product_code == scenario.product.code:
            return vars_bundle.final_product_workcenter_choice_quantity_by_due_day[
                (choice_index, due_day)
            ]
        return vars_bundle.manufactured_choice_quantity_by_day[
            (product_code, choice_index, due_day)
        ]

    def _available_component_supply_by_event_day(
        self,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
        context: _ModelManufacturingContext,
        code: str,
        event_day: int,
    ) -> cp_model.LinearExpr:
        """Return cumulative supply available for one component code by one checkpoint.

        Manufactured child codes are modeled as internal arrival streams: their
        completed quantities become available to parent products once the child
        manufacturing order finishes in or before the checkpoint.
        """
        supply_terms: list[cp_model.LinearExpr] = []
        stock_var = vars_bundle.stock_use_by_code.get(code)
        if stock_var is not None:
            supply_terms.append(stock_var)
        if code in scenario.manufactured_product_codes:
            manufacturing_product = scenario.manufacturing_product(code)
            for due_day in context.due_days_by_code.get(code, ()):
                if due_day > event_day:
                    continue
                for choice_index in range(len(manufacturing_product.workcenter_choices)):
                    supply_terms.append(
                        vars_bundle.manufactured_choice_quantity_by_day[
                            (code, choice_index, due_day)
                        ]
                    )
            return cp_model.LinearExpr.Sum(supply_terms)
        for vendor in self._component_vendors(scenario, code):
            if vendor.lead_time > event_day:
                continue
            supply_terms.append(vars_bundle.component_vendor_vars[(code, vendor.ref)].quantity)
        return cp_model.LinearExpr.Sum(supply_terms)

    def _add_event_time_component_balance_constraints(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
        context: _ModelManufacturingContext,
    ) -> None:
        """Require cumulative child supply to cover every parent-start checkpoint."""
        required_terms_by_component_and_day: dict[tuple[str, int], list[cp_model.LinearExpr]] = (
            defaultdict(list)
        )
        for parent in scenario.manufactured_products:
            parent_due_days = context.due_days_by_code.get(parent.code, ())
            for due_day in parent_due_days:
                for choice_index, choice in enumerate(parent.workcenter_choices):
                    cutoff_day = due_day - choice.lead_time_days
                    if cutoff_day < 0:
                        continue
                    completion_expr = self._completion_expr(
                        scenario,
                        vars_bundle,
                        parent.code,
                        choice_index,
                        due_day,
                    )
                    for component in parent.components:
                        term = int(component.per_unit) * completion_expr
                        for event_day in context.event_days:
                            if event_day < cutoff_day:
                                continue
                            required_terms_by_component_and_day[
                                (component.product.code, event_day)
                            ].append(term)
        for (code, event_day), required_terms in required_terms_by_component_and_day.items():
            model.Add(
                self._available_component_supply_by_event_day(
                    scenario, vars_bundle, context, code, event_day
                )
                >= cp_model.LinearExpr.Sum(required_terms)
            )

    def _add_workcenter_capacity_constraints(
        self,
        model: cp_model.CpModel,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
        final_product_choice_totals: list[cp_model.LinearExpr],
        final_product_workcenter_choices: tuple[WorkcenterChoiceSpec, ...],
        context: _ModelManufacturingContext,
    ) -> None:
        """Cap total scheduled minutes on each workcenter across all production."""
        repair = scenario.repair_context
        for workcenter in scenario.workcenters:
            relevant_terms: list[cp_model.LinearExpr] = []
            for choice_index, choice in enumerate(final_product_workcenter_choices):
                if choice.workcenter_code != workcenter.code:
                    continue
                relevant_terms.append(
                    int(round(choice.time_per_unit_minutes * 1000))
                    * final_product_choice_totals[choice_index]
                )
            for manufacturing_product in scenario.manufactured_products:
                if manufacturing_product.code == scenario.product.code:
                    continue
                for due_day in context.due_days_by_code.get(manufacturing_product.code, ()):
                    for choice_index, choice in enumerate(manufacturing_product.workcenter_choices):
                        if choice.workcenter_code != workcenter.code:
                            continue
                        relevant_terms.append(
                            int(round(choice.time_per_unit_minutes * 1000))
                            * vars_bundle.manufactured_choice_quantity_by_day[
                                (manufacturing_product.code, choice_index, due_day)
                            ]
                        )
            if not relevant_terms:
                continue
            capacity_minutes = workcenter.capacity_minutes
            if (
                repair is not None
                and repair.kind == "workcenter_outage"
                and repair.broken_workcenter_code == workcenter.code
            ):
                capacity_minutes = 0
            model.Add(
                cp_model.LinearExpr.Sum(relevant_terms) <= int(round(capacity_minutes * 1000))
            )

    def _build_objectives(
        self,
        scenario: ScenarioBlueprint,
        vars_bundle: _ModelVars,
    ) -> tuple[cp_model.LinearExpr, tuple[cp_model.IntVar, ...]]:
        spend_terms = self._new_spend_terms(scenario, vars_bundle)
        tie_break_vars: list[cp_model.IntVar] = []

        for due_day in sorted(vars_bundle.stock_to_due_day):
            tie_break_vars.append(vars_bundle.stock_to_due_day[due_day])

        for key in sorted(vars_bundle.finished_vendor_quantity_by_due_day):
            tie_break_vars.append(vars_bundle.finished_vendor_quantity_by_due_day[key])
        tie_break_vars.extend(vars_bundle.finished_vendor_activation)

        for key in sorted(vars_bundle.final_product_workcenter_choice_quantity_by_due_day):
            tie_break_vars.append(
                vars_bundle.final_product_workcenter_choice_quantity_by_due_day[key]
            )

        for key in sorted(vars_bundle.manufactured_choice_quantity_by_day):
            tie_break_vars.append(vars_bundle.manufactured_choice_quantity_by_day[key])

        for code in sorted(vars_bundle.stock_use_by_code):
            tie_break_vars.append(vars_bundle.stock_use_by_code[code])

        for key in sorted(vars_bundle.component_vendor_vars):
            vendor_vars = vars_bundle.component_vendor_vars[key]
            tie_break_vars.append(vendor_vars.activation)
            tie_break_vars.append(vendor_vars.quantity)

        return cp_model.LinearExpr.Sum(spend_terms), tuple(tie_break_vars)

    def _solve_lexicographic_phase(
        self,
        model: cp_model.CpModel,
        solver: cp_model.CpSolver,
        tie_break_vars: tuple[cp_model.IntVar, ...],
    ) -> None:
        solver.parameters.num_search_workers = 1
        solver.parameters.randomize_search = False
        solver.parameters.search_branching = cp_model.FIXED_SEARCH
        solver.parameters.stop_after_first_solution = True
        # The legacy weighted sum gave later variables larger coefficients.
        priority_vars = tuple(reversed(tie_break_vars))
        total_priority_vars = len(priority_vars)
        logger.info("CP-SAT lexicographic fixed-search start vars=%s", total_priority_vars)
        model.ClearObjective()
        model.AddDecisionStrategy(
            priority_vars,
            cp_model.CHOOSE_FIRST,
            cp_model.SELECT_MIN_VALUE,
        )
        status = self._solve_with_log(solver, model, "lexicographic fixed-search")
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            solver.parameters.max_time_in_seconds = self.solve_time_limit * 3
            status = self._solve_with_log(solver, model, "lexicographic fixed-search retry")
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            raise RuntimeError(
                "Solver failed to find lexicographic canonical solution "
                f"(status={solver.StatusName(status)})"
            )

    def _allocate_bucket_quantity(
        self,
        *,
        customer_indices: tuple[int, ...],
        remaining_by_customer: dict[int, int],
        quantity: int,
    ) -> dict[int, int]:
        """Allocate one due-bucket quantity across customers in a stable order."""
        allocated_by_customer: dict[int, int] = {}
        remaining_quantity = quantity
        for customer_index in customer_indices:
            if remaining_quantity <= 0:
                break
            alloc = min(remaining_by_customer.get(customer_index, 0), remaining_quantity)
            if alloc <= 0:
                continue
            allocated_by_customer[customer_index] = alloc
            remaining_by_customer[customer_index] -= alloc
            remaining_quantity -= alloc
        if remaining_quantity:
            raise RuntimeError("Bucket allocation overflow while reconstructing customer plan.")
        return allocated_by_customer

    @staticmethod
    def _append_unique_ref(refs: list[str], ref: str) -> None:
        if ref not in refs:
            refs.append(ref)

    @staticmethod
    def _purchase_order_plan_ref(vendor: VendorSpec) -> str:
        return (
            "po:"
            f"{vendor.supply_role}:"
            f"{vendor.ref}:"
            f"{vendor.product_code}:"
            f"{vendor.min_qty}:"
            f"{round(vendor.price_dollars, 2)}:"
            f"{vendor.lead_time}"
        )

    @staticmethod
    def _manufacturing_order_plan_ref(
        product_code: str,
        *,
        choice_index: int,
        due_day: int,
    ) -> str:
        return f"mo:{product_code}:{choice_index}:{due_day}"

    @staticmethod
    def _manufacturing_order_start_day(order: PlanManufacturingOrder) -> int:
        if order.needed_by_days is None:
            raise RuntimeError(
                f"Manufacturing order {order.plan_ref} is missing needed_by_days for origin allocation."
            )
        return order.needed_by_days - order.lead_time

    def _assign_exact_supply_origins(
        self,
        *,
        scenario: ScenarioBlueprint,
        manufacturing_orders: list[PlanManufacturingOrder],
        purchase_orders: list[PlanPurchaseOrder],
        component_stock_use_counter: dict[str, int],
    ) -> tuple[list[PlanManufacturingOrder], list[PlanPurchaseOrder]]:
        supply_by_code: dict[str, list[dict[str, int | str | None]]] = defaultdict(list)
        origin_plan_refs_by_supply_ref: dict[str, list[str]] = {}

        for code, used_qty in sorted(component_stock_use_counter.items()):
            if used_qty <= 0:
                continue
            supply_by_code[code].append(
                {
                    "plan_ref": None,
                    "available_day": 0,
                    "remaining": int(used_qty),
                    "priority": 0,
                    "sort_ref": code,
                }
            )

        for order in purchase_orders:
            if order.supply_role != "component":
                continue
            if order.planned_arrival_days is None:
                raise RuntimeError(
                    f"Component purchase order {order.plan_ref} is missing planned_arrival_days."
                )
            origin_plan_refs_by_supply_ref[order.plan_ref] = []
            supply_by_code[order.product_code].append(
                {
                    "plan_ref": order.plan_ref,
                    "available_day": int(order.planned_arrival_days),
                    "remaining": int(order.quantity),
                    "priority": 1,
                    "sort_ref": order.plan_ref,
                }
            )

        for order in manufacturing_orders:
            if order.product_code == scenario.product.code:
                continue
            if order.needed_by_days is None:
                raise RuntimeError(
                    f"Manufacturing order {order.plan_ref} is missing needed_by_days."
                )
            origin_plan_refs_by_supply_ref[order.plan_ref] = []
            supply_by_code[order.product_code].append(
                {
                    "plan_ref": order.plan_ref,
                    "available_day": int(order.needed_by_days),
                    "remaining": int(order.quantity),
                    "priority": 2,
                    "sort_ref": order.plan_ref,
                }
            )

        demand_orders = sorted(
            manufacturing_orders,
            key=lambda order: (
                self._manufacturing_order_start_day(order),
                -order.level,
                order.plan_ref,
            ),
        )
        for demand_order in demand_orders:
            manufacturing_product = scenario.manufacturing_by_code.get(demand_order.product_code)
            if manufacturing_product is None:
                continue
            demand_day = self._manufacturing_order_start_day(demand_order)
            for component in manufacturing_product.components:
                remaining = int(component.per_unit) * int(demand_order.quantity)
                available_supplies = [
                    supply
                    for supply in supply_by_code.get(component.product.code, [])
                    if int(supply["remaining"]) > 0 and int(supply["available_day"]) <= demand_day
                ]
                available_supplies.sort(
                    key=lambda supply: (
                        int(supply["available_day"]),
                        int(supply["priority"]),
                        str(supply["sort_ref"]),
                    )
                )
                for supply in available_supplies:
                    take = min(remaining, int(supply["remaining"]))
                    if take <= 0:
                        continue
                    plan_ref = supply["plan_ref"]
                    if isinstance(plan_ref, str):
                        self._append_unique_ref(
                            origin_plan_refs_by_supply_ref[plan_ref],
                            demand_order.plan_ref,
                        )
                    supply["remaining"] = int(supply["remaining"]) - take
                    remaining -= take
                    if remaining == 0:
                        break
                if remaining:
                    raise RuntimeError(
                        f"Unable to allocate exact supply lineage for {component.product.code} "
                        f"into {demand_order.plan_ref}."
                    )

        updated_manufacturing_orders: list[PlanManufacturingOrder] = []
        for order in manufacturing_orders:
            if order.product_code == scenario.product.code:
                updated_manufacturing_orders.append(order)
                continue
            updated_manufacturing_orders.append(
                order.model_copy(
                    update={
                        "origin_plan_refs": tuple(origin_plan_refs_by_supply_ref[order.plan_ref]),
                    }
                )
            )

        updated_purchase_orders: list[PlanPurchaseOrder] = []
        for order in purchase_orders:
            if order.supply_role != "component":
                updated_purchase_orders.append(order)
                continue
            updated_purchase_orders.append(
                order.model_copy(
                    update={
                        "origin_plan_refs": tuple(origin_plan_refs_by_supply_ref[order.plan_ref]),
                    }
                )
            )
        return updated_manufacturing_orders, updated_purchase_orders

    def _extract_plan(
        self,
        scenario: ScenarioBlueprint,
        solver: cp_model.CpSolver,
        vars_bundle: _ModelVars,
        *,
        objective_kind: ProcurementObjectiveKind,
        objective_value: float,
    ) -> PlanResult:
        finished_vendors = self._finished_product_vendors(scenario)
        final_product = scenario.manufacturing_by_code.get(scenario.product.code)
        context = self._build_model_context(scenario)
        product_levels = self._product_levels(scenario)
        consumer_codes = self._consumer_codes(scenario)

        stock_used = 0
        stock_cost = 0.0
        finished_purchase_cost = 0.0
        final_production_direct_cost = 0.0
        component_cost_total = 0.0
        assembly_cost_total = 0.0

        manufacturing_orders: list[PlanManufacturingOrder] = []
        allocations: list[PlanCustomer] = []
        purchase_orders: list[PlanPurchaseOrder] = []
        purchase_index: dict[tuple[str, str], PlanPurchaseOrder] = {}

        finished_units_built = 0
        component_stock_use_counter: dict[str, int] = {}
        stock_allocated_by_customer: dict[int, int] = defaultdict(int)
        purchase_units_by_customer: dict[int, int] = defaultdict(int)
        purchase_cost_by_customer: dict[int, float] = defaultdict(float)
        assembly_units_by_customer: dict[int, int] = defaultdict(int)
        direct_build_cost_by_customer: dict[int, float] = defaultdict(float)
        finished_po_origin_customer_refs: dict[int, list[str]] = defaultdict(list)
        final_order_origin_refs: dict[tuple[int, int], tuple[str, ...]] = {}

        # Global component spend.
        for code, stock_var in vars_bundle.stock_use_by_code.items():
            used = solver.Value(stock_var)
            if not used:
                continue
            component_stock_use_counter[code] = component_stock_use_counter.get(code, 0) + used
            if code == scenario.product.code:
                continue
            stock_unit_cost = scenario.product_stock_cost_dollars.get(code, 0.0)
            component_cost_total += used * stock_unit_cost

        consumer_product_codes_by_component: dict[str, tuple[str, ...]] = {}
        for mp in scenario.manufactured_products:
            for comp in mp.components:
                existing = consumer_product_codes_by_component.get(comp.product.code, ())
                if mp.product.code not in existing:
                    consumer_product_codes_by_component[comp.product.code] = (
                        *existing,
                        mp.product.code,
                    )

        for (code, vendor_ref), vendor_vars in vars_bundle.component_vendor_vars.items():
            quantity = solver.Value(vendor_vars.quantity)
            if not quantity:
                continue
            vendor = next(
                vendor
                for vendor in scenario.vendors
                if vendor.product_code == code and vendor.ref == vendor_ref
            )
            unit_cost = vendor.price_cents / self.scale
            component_cost_total += quantity * unit_cost
            order = PlanPurchaseOrder(
                plan_ref=self._purchase_order_plan_ref(vendor),
                vendor_ref=vendor.ref,
                offer_key=vendor.offer_key,
                quantity=quantity,
                unit_cost=unit_cost,
                total_cost=quantity * unit_cost,
                lead_time=vendor.lead_time,
                planned_arrival_days=vendor.lead_time,
                description=vendor.description,
                product_code=vendor.product_code,
                supply_role=vendor.supply_role,
                origin_customer_refs=(),
                origin_plan_refs=(),
                consumer_product_codes=consumer_product_codes_by_component.get(
                    vendor.product_code, ()
                ),
            )
            purchase_index[(vendor.ref, vendor.product_code)] = order
            purchase_orders.append(order)

        # Reconstruct customer allocations from fully bucketed top-level decisions.
        for due_day, customer_indices in sorted(context.customer_indices_by_deadline.items()):
            remaining_by_customer = {
                customer_index: (
                    0
                    if scenario.customers[customer_index].is_rejected_upfront
                    else scenario.customers[customer_index].demand
                )
                for customer_index in customer_indices
            }
            stock_qty = solver.Value(vars_bundle.stock_to_due_day[due_day])
            for customer_index, qty in self._allocate_bucket_quantity(
                customer_indices=customer_indices,
                remaining_by_customer=remaining_by_customer,
                quantity=stock_qty,
            ).items():
                stock_allocated_by_customer[customer_index] += qty
            for vendor_index, vendor in enumerate(finished_vendors):
                qty = solver.Value(
                    vars_bundle.finished_vendor_quantity_by_due_day[(vendor_index, due_day)]
                )
                if not qty:
                    continue
                allocated = self._allocate_bucket_quantity(
                    customer_indices=customer_indices,
                    remaining_by_customer=remaining_by_customer,
                    quantity=qty,
                )
                for customer_index, customer_qty in allocated.items():
                    purchase_units_by_customer[customer_index] += customer_qty
                    purchase_cost_by_customer[customer_index] += customer_qty * vendor.price_dollars
                    if customer_qty > 0:
                        self._append_unique_ref(
                            finished_po_origin_customer_refs[vendor_index],
                            scenario.customers[customer_index].ref,
                        )
            if final_product is not None:
                for choice_index, choice in enumerate(final_product.workcenter_choices):
                    qty = solver.Value(
                        vars_bundle.final_product_workcenter_choice_quantity_by_due_day[
                            (choice_index, due_day)
                        ]
                    )
                    if not qty:
                        continue
                    allocated = self._allocate_bucket_quantity(
                        customer_indices=customer_indices,
                        remaining_by_customer=remaining_by_customer,
                        quantity=qty,
                    )
                    final_order_origin_refs[(choice_index, due_day)] = tuple(
                        scenario.customers[customer_index].ref
                        for customer_index in customer_indices
                        if allocated.get(customer_index, 0) > 0
                    )
                    direct_cost = qty * choice.unit_cost_dollars
                    final_production_direct_cost += direct_cost
                    finished_units_built += qty
                    for customer_index, customer_qty in allocated.items():
                        assembly_units_by_customer[customer_index] += customer_qty
                        direct_build_cost_by_customer[customer_index] += (
                            customer_qty * choice.unit_cost_dollars
                        )
            if any(remaining_by_customer.values()):
                raise RuntimeError(
                    f"Unable to reconstruct customer allocations for due day {due_day}."
                )

        for customer_index, customer in enumerate(scenario.customers):
            accepted = not customer.is_rejected_upfront
            stock_alloc = stock_allocated_by_customer.get(customer_index, 0)
            stock_used += stock_alloc
            stock_cost_value = stock_alloc * scenario.target_product_stock_cost_dollars
            stock_cost += stock_cost_value
            customer_build_units = assembly_units_by_customer.get(customer_index, 0)
            customer_direct_build_cost = round(
                direct_build_cost_by_customer.get(customer_index, 0.0),
                2,
            )
            purchase_units = purchase_units_by_customer.get(customer_index, 0)
            requested_revenue = round(scenario.product.list_price_dollars * customer.demand, 2)
            revenue = requested_revenue if accepted else 0.0
            fulfilled_demand = customer.demand if accepted else 0
            if customer.task_order_source == "seeded":
                action: TaskOrderAction = "confirm_seeded" if accepted else "cancel_seeded"
            else:
                action = "create_confirm_prompt" if accepted else "skip_prompt"
            allocations.append(
                PlanCustomer(
                    ref=customer.ref,
                    accepted=accepted,
                    action=action,
                    task_order_source=customer.task_order_source,
                    task_order_ref=customer.resolved_task_order_ref,
                    seeded_order_state=customer.seeded_order_state,
                    reject_reasons=customer.reject_reasons,
                    reject_reason_text=customer.reject_reason_text,
                    stock_allocated=stock_alloc,
                    demand=customer.demand,
                    fulfilled_demand=fulfilled_demand,
                    deadline=customer.deadline,
                    price=round(scenario.product.list_price_dollars, 2),
                    purchase_units=purchase_units,
                    assembly_units=customer_build_units,
                    requested_revenue=requested_revenue,
                    revenue=revenue,
                    variable_cost=0.0,
                    component_cost=0.0,
                    assembly_cost=customer_direct_build_cost,
                    margin=0.0,
                )
            )

        # Finished purchase totals.
        for vendor_index, vendor in enumerate(finished_vendors):
            quantity = sum(
                solver.Value(
                    vars_bundle.finished_vendor_quantity_by_due_day[(vendor_index, due_day)]
                )
                for due_day in context.customer_indices_by_deadline
            )
            if not quantity:
                continue
            needed_by_days = min(
                due_day
                for due_day in context.customer_indices_by_deadline
                if solver.Value(
                    vars_bundle.finished_vendor_quantity_by_due_day[(vendor_index, due_day)]
                )
                > 0
            )
            unit_cost = vendor.price_cents / self.scale
            order = PlanPurchaseOrder(
                plan_ref=self._purchase_order_plan_ref(vendor),
                vendor_ref=vendor.ref,
                offer_key=vendor.offer_key,
                quantity=quantity,
                unit_cost=unit_cost,
                total_cost=quantity * unit_cost,
                lead_time=vendor.lead_time,
                planned_arrival_days=needed_by_days,
                description=vendor.description,
                product_code=vendor.product_code,
                supply_role=vendor.supply_role,
                origin_customer_refs=tuple(finished_po_origin_customer_refs[vendor_index]),
                origin_plan_refs=(),
            )
            purchase_index[(vendor.ref, vendor.product_code)] = order
            purchase_orders.append(order)
            finished_purchase_cost += order.total_cost

        # Final-product production grouped by due day and workcenter choice.
        if final_product is not None:
            for due_day, customer_indices in sorted(context.customer_indices_by_deadline.items()):
                for choice_index, choice in enumerate(final_product.workcenter_choices):
                    quantity = solver.Value(
                        vars_bundle.final_product_workcenter_choice_quantity_by_due_day[
                            (choice_index, due_day)
                        ]
                    )
                    if not quantity:
                        continue
                    origin_customer_refs = final_order_origin_refs.get(
                        (choice_index, due_day),
                        tuple(
                            scenario.customers[customer_index].ref
                            for customer_index in customer_indices
                        ),
                    )
                    manufacturing_orders.append(
                        PlanManufacturingOrder(
                            plan_ref=self._manufacturing_order_plan_ref(
                                scenario.product.code,
                                choice_index=choice_index,
                                due_day=due_day,
                            ),
                            product_code=scenario.product.code,
                            quantity=quantity,
                            workcenter_code=choice.workcenter_code,
                            unit_cost=choice.unit_cost_dollars,
                            total_cost=quantity * choice.unit_cost_dollars,
                            lead_time=choice.lead_time_days,
                            time_per_unit_minutes=choice.time_per_unit_minutes,
                            operation_name=choice.operation_name,
                            origin_customer_refs=origin_customer_refs,
                            origin_plan_refs=(),
                            level=product_levels.get(scenario.product.code, 0),
                            needed_by_days=due_day,
                        )
                    )

        # Subassembly production is emitted as internal arrivals at explicit due checkpoints.
        for manufacturing_product in scenario.manufactured_products:
            if manufacturing_product.code == scenario.product.code:
                continue
            for due_day in context.due_days_by_code.get(manufacturing_product.code, ()):
                for choice_index, choice in enumerate(manufacturing_product.workcenter_choices):
                    qty = solver.Value(
                        vars_bundle.manufactured_choice_quantity_by_day[
                            (manufacturing_product.code, choice_index, due_day)
                        ]
                    )
                    if not qty:
                        continue
                    total_cost = qty * choice.unit_cost_dollars
                    assembly_cost_total += total_cost
                    manufacturing_orders.append(
                        PlanManufacturingOrder(
                            plan_ref=self._manufacturing_order_plan_ref(
                                manufacturing_product.code,
                                choice_index=choice_index,
                                due_day=due_day,
                            ),
                            product_code=manufacturing_product.code,
                            quantity=qty,
                            workcenter_code=choice.workcenter_code,
                            unit_cost=choice.unit_cost_dollars,
                            total_cost=total_cost,
                            lead_time=choice.lead_time_days,
                            time_per_unit_minutes=choice.time_per_unit_minutes,
                            operation_name=choice.operation_name,
                            origin_customer_refs=(),
                            origin_plan_refs=(),
                            consumer_product_codes=tuple(
                                sorted(consumer_codes.get(manufacturing_product.code, ()))
                            ),
                            level=product_levels[manufacturing_product.code],
                            needed_by_days=due_day,
                        )
                    )
        assembly_cost_total += final_production_direct_cost
        manufacturing_orders, purchase_orders = self._assign_exact_supply_origins(
            scenario=scenario,
            manufacturing_orders=manufacturing_orders,
            purchase_orders=purchase_orders,
            component_stock_use_counter=component_stock_use_counter,
        )

        # Allocate indirect manufacturing/component costs across customers by final built units.
        indirect_mfg_cost = assembly_cost_total - final_production_direct_cost
        for index, allocation in enumerate(allocations):
            share = (
                allocation.assembly_units / finished_units_built
                if finished_units_built > 0 and allocation.assembly_units > 0
                else 0.0
            )
            component_share = round(component_cost_total * share, 2)
            indirect_share = round(indirect_mfg_cost * share, 2)
            finished_purchase_share = round(purchase_cost_by_customer.get(index, 0.0), 2)
            stock_share = allocation.stock_allocated * scenario.target_product_stock_cost_dollars
            assembly_share = round(allocation.assembly_cost + indirect_share, 2)
            variable_cost = round(
                stock_share + finished_purchase_share + component_share + assembly_share, 2
            )
            revenue = allocation.revenue
            margin = (revenue - variable_cost) / revenue if revenue else 0.0
            allocations[index] = allocation.model_copy(
                update={
                    "component_cost": component_share,
                    "assembly_cost": assembly_share,
                    "variable_cost": variable_cost,
                    "margin": margin,
                }
            )

        stock_report = tuple(
            self._build_stock_report(
                scenario=scenario,
                finished_stock_used=stock_used,
                non_finished_stock_use=component_stock_use_counter,
            )
        )
        return PlanResult(
            objective_kind=objective_kind,
            objective_value=objective_value,
            stock_cost=round(stock_cost, 2),
            component_cost=round(component_cost_total, 2),
            assembly_cost=round(assembly_cost_total, 2),
            allocations=tuple(allocations),
            purchase_orders=tuple(purchase_orders),
            manufacturing_orders=tuple(manufacturing_orders),
            stock_report=stock_report,
        )

    def _record_purchase_order(
        self,
        order_index: dict[tuple[str, str], PlanPurchaseOrder],
        purchase_orders: list[PlanPurchaseOrder],
        vendor: VendorSpec,
        quantity: int,
        *,
        planned_arrival_days: int | None = None,
    ) -> None:
        if not quantity:
            return
        if self.blueprint.manufacturing_only.enabled and vendor.supply_role == "finished":
            raise RuntimeError(
                "Manufacturing-only scenario produced a finished-goods purchase order."
            )
        key = (vendor.ref, vendor.product_code)
        if key in order_index:
            return
        unit_cost = vendor.price_cents / self.scale
        order = PlanPurchaseOrder(
            plan_ref=self._purchase_order_plan_ref(vendor),
            vendor_ref=vendor.ref,
            offer_key=vendor.offer_key,
            quantity=quantity,
            unit_cost=unit_cost,
            total_cost=quantity * unit_cost,
            lead_time=vendor.lead_time,
            planned_arrival_days=planned_arrival_days,
            description=vendor.description,
            product_code=vendor.product_code,
            supply_role=vendor.supply_role,
            origin_customer_refs=(),
            origin_plan_refs=(),
        )
        order_index[key] = order
        purchase_orders.append(order)

    def _build_stock_report(
        self,
        *,
        scenario: ScenarioBlueprint,
        finished_stock_used: int,
        non_finished_stock_use: dict[str, int],
    ) -> list[StockUsage]:
        specs: dict[str, ProductSpec] = {scenario.product.code: scenario.product}
        for product in scenario.manufactured_products:
            specs.setdefault(product.code, product.product)
            for component in product.components:
                specs.setdefault(component.product.code, component.product)
        report = [
            StockUsage(
                code=scenario.product.code,
                label=scenario.product.name,
                available=scenario.target_product_stock_units,
                used=finished_stock_used,
                remaining=max(scenario.target_product_stock_units - finished_stock_used, 0),
            )
        ]
        for code, quantity in scenario.product_stock_units.items():
            if code == scenario.product.code:
                continue
            used = non_finished_stock_use.get(code, 0)
            if quantity <= 0 and used <= 0:
                continue
            report.append(
                StockUsage(
                    code=code,
                    label=specs.get(code).name if code in specs else None,
                    available=quantity,
                    used=used,
                    remaining=max(quantity - used, 0),
                )
            )
        return report

    def _finished_product_vendors(self, scenario: ScenarioBlueprint) -> tuple[VendorSpec, ...]:
        if scenario.manufacturing_only.enabled:
            return ()
        return tuple(vendor for vendor in scenario.vendors if vendor.supply_role == "finished")

    def _component_vendors(
        self, scenario: ScenarioBlueprint, product_code: str
    ) -> tuple[VendorSpec, ...]:
        return tuple(
            vendor
            for vendor in scenario.vendors
            if vendor.supply_role == "component" and vendor.product_code == product_code
        )

    def _leaf_requirements_for_product(
        self,
        scenario: ScenarioBlueprint,
        product_code: str,
        cache: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, int]:
        """Return leaf-component demand for one unit of ``product_code``.

        This recursively flattens the product's manufacturing tree into a map
        of ``leaf_product_code -> quantity``. A leaf is any product that is not
        manufactured in-house, so the base case returns ``{product_code: 1}``.
        For manufactured products, each child requirement is multiplied by that
        component's per-unit quantity and accumulated across the full BOM.

        The solver uses this to convert final-product demand into implied
        component demand when sizing component stock/purchase decisions. The
        optional ``cache`` memoizes shared subassemblies so repeated lookups do
        not recompute the same requirement tree.
        """
        memo = cache if cache is not None else {}
        if product_code in memo:
            return memo[product_code]
        manufacturing_product = scenario.manufacturing_by_code.get(product_code)
        if manufacturing_product is None:
            memo[product_code] = {product_code: 1}
            return memo[product_code]
        requirements: dict[str, int] = defaultdict(int)
        for component in manufacturing_product.components:
            child_requirements = self._leaf_requirements_for_product(
                scenario,
                component.product.code,
                memo,
            )
            for code, qty in child_requirements.items():
                requirements[code] += int(component.per_unit) * qty
        memo[product_code] = dict(requirements)
        return memo[product_code]

    def _consumer_codes(self, scenario: ScenarioBlueprint) -> dict[str, set[str]]:
        consumers: dict[str, set[str]] = defaultdict(set)
        for parent in scenario.manufactured_products:
            for component in parent.components:
                if component.product.code in scenario.manufactured_product_codes:
                    consumers[component.product.code].add(parent.code)
        return consumers

    def _product_levels(self, scenario: ScenarioBlueprint) -> dict[str, int]:
        sorter = TopologicalSorter()
        for product in scenario.manufactured_products:
            deps = [
                component.product.code
                for component in product.components
                if component.product.code in scenario.manufactured_product_codes
            ]
            sorter.add(product.code, *deps)
        order = tuple(sorter.static_order())
        levels: dict[str, int] = {}
        for code in order:
            deps = [
                component.product.code
                for component in scenario.manufacturing_product(code).components
                if component.product.code in scenario.manufactured_product_codes
            ]
            levels[code] = 0 if not deps else max(levels[dep] for dep in deps) + 1
        return levels

    def _build_product_boms(self) -> list[BOMData]:
        boms: list[BOMData] = []
        for manufacturing_product in sorted(
            self.blueprint.manufactured_products,
            key=lambda product: self._product_levels(self.blueprint).get(product.code, 0),
        ):
            if not manufacturing_product.components:
                continue
            boms.append(
                BOMData(
                    product_code=manufacturing_product.code,
                    quantity=1.0,
                    components=[
                        BOMComponentData(
                            component_code=component.product.code,
                            quantity=float(component.per_unit),
                        )
                        for component in manufacturing_product.components
                    ],
                    warehouse_code=None,
                )
            )
        return boms

    def _build_workcenters(self) -> list[WorkcenterData]:
        rows: list[WorkcenterData] = []
        repair = self.blueprint.repair_context
        for manufacturing_product in self.blueprint.manufactured_products:
            allowed_codes = tuple(
                choice.workcenter_code for choice in manufacturing_product.workcenter_choices
            )
            for choice in manufacturing_product.workcenter_choices:
                pool = self.blueprint.workcenters_by_code[choice.workcenter_code]
                repair_note = None
                if (
                    repair is not None
                    and repair.kind == "workcenter_outage"
                    and repair.broken_workcenter_code == pool.code
                ):
                    repair_note = (
                        "Repair scenario disruption: this workcenter is down for the task-critical "
                        "manufacturing path and should not be used for replacement work."
                    )
                note = pool.note
                if repair_note:
                    note = f"{note}\n{repair_note}" if note else repair_note
                capacity_minutes = float(pool.capacity_minutes)
                if (
                    repair is not None
                    and repair.kind == "workcenter_outage"
                    and repair.broken_workcenter_code == pool.code
                ):
                    capacity_minutes = 0.0
                rows.append(
                    WorkcenterData(
                        product_code=manufacturing_product.code,
                        code=pool.code,
                        name=pool.name,
                        unit_cost=choice.unit_cost_dollars,
                        costs_hour=round(
                            choice.unit_cost_dollars
                            * 60.0
                            / max(choice.time_per_unit_minutes, 1e-6),
                            2,
                        ),
                        time_per_unit_minutes=round(choice.time_per_unit_minutes, 6),
                        time_efficiency=pool.time_efficiency,
                        time_start=pool.time_start,
                        time_stop=pool.time_stop,
                        oee_target=pool.oee_target,
                        note=note,
                        capacity_minutes=capacity_minutes,
                        lead_days=float(choice.lead_time_days),
                        operation_name=manufacturing_product.operation_name,
                        sequence=100,
                        is_primary=choice.workcenter_code
                        == manufacturing_product.primary_workcenter_code,
                        alternative_workcenter_codes=[
                            code
                            for code in allowed_codes
                            if code != manufacturing_product.primary_workcenter_code
                        ]
                        if choice.workcenter_code == manufacturing_product.primary_workcenter_code
                        else [],
                    )
                )
        return rows

    def _build_scenario_data(self, plan: PlanResult | None = None) -> ScenarioData:
        repair = self.blueprint.repair_context
        specs_by_code: dict[str, ProductSpec] = {
            self.blueprint.product.code: self.blueprint.product
        }
        for manufacturing_product in self.blueprint.manufactured_products:
            specs_by_code[manufacturing_product.code] = manufacturing_product.product
            for component in manufacturing_product.components:
                specs_by_code.setdefault(component.product.code, component.product)

        vendor_info_map: dict[str, list[VendorInfoData]] = defaultdict(list)
        seen_vendor_infos: set[tuple[str, str, str | None]] = set()
        for vendor in self.blueprint.vendors:
            key = (vendor.ref, vendor.product_code, vendor.description)
            if key in seen_vendor_infos:
                raise ValueError(
                    f"Duplicate vendor offer detected for {vendor.ref}/{vendor.product_code}/{vendor.description}"
                )
            seen_vendor_infos.add(key)
            vendor_info_map[vendor.product_code].append(
                VendorInfoData(
                    partner_ref=vendor.ref,
                    delay=vendor.lead_time,
                    min_qty=vendor.min_qty,
                    max_qty=vendor.max_qty,
                    price=vendor.price_dollars,
                    name=vendor.description,
                )
            )

        manufactured_codes = self.blueprint.manufactured_product_codes
        products: list[ProductData] = []
        for code, spec in specs_by_code.items():
            routes: list[RouteLiteral] = [cast(RouteLiteral, route) for route in spec.routes]
            if code in manufactured_codes and "manufacture" not in routes:
                routes.append(cast(RouteLiteral, "manufacture"))
            if (
                code == self.blueprint.product.code
                and self.blueprint.manufacturing_only.no_buy_route
            ):
                routes = [route for route in routes if route != "buy"]
            products.append(
                ProductData(
                    name=spec.name,
                    code=spec.code,
                    category=spec.category,
                    list_price=spec.list_price_dollars,
                    standard_price=spec.standard_price_dollars,
                    type="consu" if spec.type == "product" else spec.type,
                    routes=routes,
                    vendor_info=vendor_info_map.get(code, []),
                )
            )

        system_parameters = list(self.blueprint.system_parameters)
        margin_param = SystemParameterData(
            key="erp_bench.min_margin_percent",
            value=format_margin_percent(self.blueprint.margin),
        )
        if all(parameter.key != margin_param.key for parameter in system_parameters):
            system_parameters.append(margin_param)

        stock_levels = [
            StockLevelData(product_code=code, warehouse_code=None, quantity=quantity)
            for code, quantity in sorted(self.blueprint.product_stock_units.items())
            if quantity > 0
        ]

        existing_sales_orders = [
            ExistingSalesOrderData(
                ref=customer.resolved_task_order_ref,
                customer_ref=customer.ref,
                product_code=self.blueprint.product.code,
                quantity=float(customer.demand),
                order_days_ago=0,
                commitment_days=customer.deadline,
                warehouse_code=None,
                state=customer.seeded_order_state or "draft",
                price_unit=round(self.blueprint.product.list_price_dollars, 2),
                note=(
                    "Preloaded task order for keep/cancel traceability."
                    if self.blueprint.unsat_demand
                    else None
                ),
            )
            for customer in self.blueprint.customers
            if customer.task_order_source == "seeded"
        ]
        existing_purchase_orders = list(repair.seeded_purchase_orders) if repair is not None else []
        existing_manufacturing_orders = (
            list(repair.seeded_manufacturing_orders) if repair is not None else []
        )
        repair_context = (
            ProcurementRepairData(
                kind=repair.kind,
                broken_supplier_offer_key=repair.broken_supplier_offer_key,
                broken_supplier_vendor_ref=repair.broken_supplier_vendor_ref,
                broken_workcenter_code=repair.broken_workcenter_code,
                impacted_customer_refs=list(repair.impacted_customer_refs),
                seeded_sales_order_refs=[order.ref for order in existing_sales_orders],
                seeded_purchase_order_refs=[
                    order.ref for order in repair.seeded_purchase_orders
                ],
                seeded_manufacturing_order_refs=[
                    order.ref for order in repair.seeded_manufacturing_orders
                ],
                broken_purchase_order_refs=list(repair.broken_purchase_order_refs),
                broken_manufacturing_order_refs=list(repair.broken_manufacturing_order_refs),
            )
            if repair is not None
            else None
        )
        invoicing_policy = ProcurementInvoicingPolicyData(
            invoice_required=self.blueprint.invoicing_policy.invoice_required,
            payment_term=self.blueprint.invoicing_policy.payment_term,
            downpayment_required=self.blueprint.invoicing_policy.downpayment_required,
            downpayment_threshold_amount=self.blueprint.invoicing_policy.downpayment_threshold_amount,
            downpayment_mode=self.blueprint.invoicing_policy.downpayment_mode,
            downpayment_value=self.blueprint.invoicing_policy.downpayment_value,
        )
        return ScenarioData(
            scenario_number=self.blueprint.scenario_number,
            name=self.blueprint.name,
            instruction=self.blueprint.instruction,
            seed=self._seed,
            background_and_policy=self.blueprint.background_and_policy,
            invoicing_policy=invoicing_policy,
            warehouses=[],
            vendors=[
                VendorData(
                    name=vendor.name,
                    ref=vendor.ref,
                    supplier_rank=vendor.supplier_rank,
                    comment=(
                        "Existing confirmed procurement from this supplier is no longer reliable "
                        "for the task-critical order because the supplier canceled the commitment."
                        if repair is not None
                        and repair.kind == "supplier_cancellation"
                        and vendor.ref == repair.broken_supplier_vendor_ref
                        else None
                    ),
                )
                for vendor in self.blueprint.vendors
            ],
            customers=[
                CustomerData(
                    name=customer.name,
                    ref=customer.ref,
                    email=customer.email,
                    budget_dollars=customer.budget_dollars,
                )
                for customer in self.blueprint.customers
            ],
            products=products,
            boms=self._build_product_boms(),
            workcenters=self._build_workcenters(),
            stock_levels=stock_levels,
            existing_purchase_orders=existing_purchase_orders,
            existing_manufacturing_orders=existing_manufacturing_orders,
            existing_sales_orders=existing_sales_orders,
            repair_context=repair_context,
            system_parameters=system_parameters,
        )


def log_plan(label: str, plan: PlanResult, *, log: logging.Logger = logger) -> None:
    finished_po_spend = sum(
        order.total_cost for order in plan.purchase_orders if order.supply_role == "finished"
    )
    console = Console(record=True, force_terminal=False, width=120, soft_wrap=False)
    with console.capture() as capture:
        console.rule(f"[bold cyan]{escape(label.capitalize())} Plan Summary")
        summary = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold white")
        summary.add_column("Metric")
        summary.add_column("Value", justify="right")
        summary.add_row("Total Cost", f"${plan.total_cost:,.2f}")
        summary.add_row("Stock Cost", f"${plan.stock_cost:,.2f}")
        summary.add_row("Finished POs", f"${finished_po_spend:,.2f}")
        summary.add_row("Component Cost", f"${plan.component_cost:,.2f}")
        summary.add_row("Manufacturing Cost", f"${plan.assembly_cost:,.2f}")
        summary.add_row("Accounting Margin", f"{plan.margin * 100:.2f}%")
        summary.add_row("Spend-Backed Revenue", f"${plan.revenue_from_new_spend:,.2f}")
        summary.add_row("New Spend", f"${plan.expected_new_spend:,.2f}")
        summary.add_row(
            "New-Spend Margin",
            (f"{plan.new_spend_margin * 100:.2f}%" if plan.new_spend_margin is not None else "n/a"),
        )
        summary.add_row("Manufacturing Units", f"{plan.assembly_units}")
        console.print(summary)

        if plan.manufacturing_orders:
            console.print()
            console.rule("[bold cyan]Manufacturing Orders")
            production_table = Table(
                box=box.SIMPLE_HEAVY, show_header=True, header_style="bold white"
            )
            production_table.add_column("Product")
            production_table.add_column("Qty", justify="right")
            production_table.add_column("Workcenter")
            production_table.add_column("Lead (d)", justify="right")
            production_table.add_column("Unit Cost", justify="right")
            production_table.add_column("Total Cost", justify="right")
            for order in plan.manufacturing_orders:
                production_table.add_row(
                    escape(order.product_code),
                    str(order.quantity),
                    escape(order.workcenter_code),
                    str(order.lead_time),
                    f"${order.unit_cost:,.2f}",
                    f"${order.total_cost:,.2f}",
                )
            console.print(production_table)

    log.info("\n%s", capture.get())


def format_plan_guidance(
    plan: PlanResult,
    blueprint: ScenarioBlueprint,
    *,
    anchor_date: date,
) -> str:
    lines = ["Sales Orders:"]
    for customer in blueprint.customers:
        allocation = next(
            allocation for allocation in plan.allocations if allocation.ref == customer.ref
        )
        due_date = anchor_date + timedelta(days=customer.deadline)
        segments: list[str] = []
        if allocation.stock_allocated:
            segments.append(f"stock {allocation.stock_allocated}")
        if allocation.purchase_units:
            segments.append(f"buy {allocation.purchase_units}")
        if allocation.assembly_units:
            segments.append(f"build {allocation.assembly_units}")
        lines.append(
            f"- {customer.name} ({customer.ref}) qty={customer.demand} due={due_date.isoformat()} via {', '.join(segments) or 'none'}"
        )

    if plan.manufacturing_orders:
        lines.append("")
        lines.append("Manufacturing Orders:")
        for order in plan.manufacturing_orders:
            needed_by_days = order.needed_by_days if order.needed_by_days is not None else 0
            due_date = anchor_date + timedelta(days=needed_by_days)
            start_date = due_date - timedelta(days=order.lead_time)
            origins = ", ".join(
                order.origin_customer_refs
                or order.origin_plan_refs
                or order.consumer_product_codes
                or ("(internal)",)
            )
            lines.append(
                f"- {order.product_code} qty={order.quantity} workcenter={order.workcenter_code} start={start_date.isoformat()} due={due_date.isoformat()} origins={origins}"
            )

    if plan.purchase_orders:
        vendor_names = {vendor.ref: vendor.name for vendor in blueprint.vendors}
        lines.append("")
        lines.append("Purchase Orders:")
        for order in plan.purchase_orders:
            arrival_days = (
                order.planned_arrival_days
                if order.planned_arrival_days is not None
                else order.lead_time
            )
            arrival_date = anchor_date + timedelta(days=arrival_days)
            lines.append(
                f"- {vendor_names.get(order.vendor_ref, order.vendor_ref)} ({order.vendor_ref}) {order.quantity} x {order.product_code} arrive={arrival_date.isoformat()}"
            )
    return "\n".join(lines)


def log_plan_guidance(
    plan: PlanResult,
    blueprint: ScenarioBlueprint,
    *,
    anchor_date: date | None = None,
    log: logging.Logger = logger,
) -> None:
    anchor = anchor_date or date.today()
    log.info("\n%s", format_plan_guidance(plan, blueprint, anchor_date=anchor))
