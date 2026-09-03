"""Harbor task export for ERP-Bench procurement scenarios."""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from pprint import pformat
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader

if TYPE_CHECKING:
    from .procurement.solver import PlanResult, ScenarioBlueprint, ScenarioBuild

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    return text.strip("_")


def _build_solver_plan(plan: PlanResult) -> dict[str, list[dict[str, object]]]:
    def require_days(value: int | None, *, label: str) -> int:
        if value is None:
            raise ValueError(f"{label} must be set when exporting the solver plan.")
        return value

    sales = [
        {
            "ref": allocation.ref,
            "action": allocation.action,
            "task_order_ref": allocation.task_order_ref,
            "demand": allocation.demand,
            "deadline": allocation.deadline,
            "price": allocation.price,
        }
        for allocation in plan.allocations
    ]

    manufacturing = []
    for order in sorted(
        plan.manufacturing_orders,
        key=lambda candidate: (
            -candidate.level,
            require_days(
                candidate.needed_by_days,
                label=f"manufacturing order {candidate.plan_ref} needed_by_days",
            ),
        ),
    ):
        manufacturing.append(
            {
                "plan_ref": order.plan_ref,
                "product_code": order.product_code,
                "quantity": order.quantity,
                "lead_time": order.lead_time,
                "needed_by_days": require_days(
                    order.needed_by_days,
                    label=f"manufacturing order {order.plan_ref} needed_by_days",
                ),
                "workcenter_code": order.workcenter_code,
                "level": order.level,
                "origin_customer_refs": list(order.origin_customer_refs),
                "origin_plan_refs": list(order.origin_plan_refs),
            }
        )

    purchases = []
    for order in plan.purchase_orders:
        if order.quantity <= 0:
            continue
        purchases.append(
            {
                "plan_ref": order.plan_ref,
                "vendor_ref": order.vendor_ref,
                "offer_key": order.offer_key,
                "product_code": order.product_code,
                "quantity": order.quantity,
                "unit_cost": order.unit_cost,
                "planned_arrival_days": require_days(
                    order.planned_arrival_days,
                    label=f"purchase order {order.plan_ref} planned_arrival_days",
                ),
                "supply_role": order.supply_role,
                "origin_customer_refs": list(order.origin_customer_refs),
                "origin_plan_refs": list(order.origin_plan_refs),
            }
        )

    return {
        "sales": sales,
        "manufacturing": manufacturing,
        "purchases": purchases,
    }


def _write_single_container_environment(
    jinja_env: Environment,
    env_dir: Path,
    scenario,
    setup_template_name: str,
    template_context: dict | None = None,
    runtime_db_name: str = "bench",
    install_native_xlsx_support: bool = False,
    admin_user: str = "admin",
    admin_password: str = "pass",
) -> None:
    """Render Dockerfile-only environment files for a task."""
    env_template_context = {
        "runtime_db_name": runtime_db_name,
        "install_native_xlsx_support": install_native_xlsx_support,
        "admin_user": admin_user,
        "admin_password": admin_password,
    }
    template = jinja_env.get_template("shared/Dockerfile.jinja2")
    (env_dir / "Dockerfile").write_text(template.render(**env_template_context))

    template = jinja_env.get_template("shared/entrypoint.sh.jinja2")
    entrypoint_path = env_dir / "entrypoint.sh"
    entrypoint_path.write_text(template.render(**env_template_context))
    entrypoint_path.chmod(0o755)

    template = jinja_env.get_template(setup_template_name)
    context = {"scenario": scenario, **env_template_context}
    if template_context:
        context.update(template_context)
    (env_dir / "setup_scenario.py").write_text(template.render(**context))

    odoo_conf = TEMPLATES_DIR / "shared" / "odoo.conf"
    if odoo_conf.exists():
        shutil.copy(odoo_conf, env_dir / "odoo.conf")

    scenario_data = scenario.model_dump(mode="json")
    (env_dir / "scenario_data.json").write_text(json.dumps(scenario_data, indent=2))


class HarborTaskExporter:
    """Exports ScenarioBuild to Harbor task directory format."""

    def __init__(
        self,
        output_dir: Path | str = "tasks",
        force: bool = False,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.force = force

        # Setup Jinja2 environment
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def export(
        self,
        build: ScenarioBuild,
        blueprint: ScenarioBlueprint,
        difficulty: str | None = None,
        name_suffix: str | None = None,
        task_pattern: str | None = None,
    ) -> Path | None:
        """Export a ScenarioBuild to a Harbor task directory.

        Args:
            build: The generated scenario build containing ScenarioData and PlanResult
            blueprint: The original blueprint used to generate the scenario
            difficulty: Optional difficulty label (easy/medium/hard). If None, auto-assessed.
            name_suffix: Optional explicit suffix appended to the task directory name
                after ``{number}_{difficulty}``.
            task_pattern: Optional procurement task pattern written into
                ``task.toml`` metadata. When ``name_suffix`` is omitted, this is
                also used as the task directory suffix.

        Returns:
            Path to the created task directory, or None if skipped
        """
        scenario = build.scenario
        plan = build.optimal_plan
        difficulty = difficulty or self._assess_difficulty(scenario)

        # Create task directory name: {number}_{difficulty}[_{suffix}]
        directory_suffix = name_suffix or task_pattern
        task_name = f"{scenario.scenario_number}_{difficulty}"
        if directory_suffix:
            task_name = f"{task_name}_{directory_suffix}"
        task_dir = self.output_dir / task_name

        if task_dir.exists():
            if not self.force:
                logger.warning("Skipping %s (already exists, use --force to overwrite)", task_name)
                return None
            shutil.rmtree(task_dir)

        # Create directory structure
        task_dir.mkdir(parents=True)
        (task_dir / "environment").mkdir()
        (task_dir / "tests").mkdir()
        (task_dir / "solution").mkdir()

        # Generate all files
        self._write_instruction(task_dir, scenario, blueprint)
        self._write_task_toml(
            task_dir,
            scenario,
            difficulty,
            blueprint,
            task_pattern=task_pattern,
        )
        self._write_environment(task_dir, scenario)
        self._write_tests(task_dir, scenario, plan, blueprint)
        self._write_solution(task_dir, scenario, plan, blueprint)

        logger.info("Exported Harbor task: %s", task_dir)
        return task_dir

    def _write_instruction(self, task_dir: Path, scenario, blueprint: ScenarioBlueprint) -> None:
        """Write instruction.md file."""
        template = self.jinja_env.get_template("procurement/supply_planning/instruction.md.jinja2")
        content = template.render(
            scenario_name=scenario.name,
            instruction=scenario.instruction,
            background_and_policy=scenario.background_and_policy or "",
        )
        (task_dir / "instruction.md").write_text(content)

    def _write_task_toml(
        self,
        task_dir: Path,
        scenario,
        difficulty: str,
        blueprint: ScenarioBlueprint,
        task_pattern: str | None = None,
    ) -> None:
        """Write task.toml file."""
        template = self.jinja_env.get_template("shared/task.toml.jinja2")
        tags = ["odoo", "erp-bench", "procurement"]
        if blueprint.unsat_demand:
            tags.append("unsat_demand")
        content = template.render(
            scenario_number=scenario.scenario_number,
            scenario_name=scenario.name,
            difficulty=difficulty,
            category="erp",
            objective_kind=blueprint.objective_kind.value,
            tags=tags,
            seed=scenario.seed,
            task_pattern=task_pattern,
        )
        (task_dir / "task.toml").write_text(content)

    def _assess_difficulty(self, scenario) -> str:
        """Assess scenario difficulty based on complexity."""
        # Count complexity factors
        complexity = 0
        complexity += len(scenario.customers) * 2
        complexity += len(scenario.vendors)
        complexity += len(scenario.products)
        complexity += len(scenario.boms) * 3
        complexity += len(scenario.workcenters) * 2

        if complexity < 15:
            return "easy"
        elif complexity < 30:
            return "medium"
        else:
            return "hard"

    def _write_environment(self, task_dir: Path, scenario) -> None:
        """Write environment files."""
        env_dir = task_dir / "environment"
        _write_single_container_environment(
            self.jinja_env,
            env_dir,
            scenario,
            "procurement/supply_planning/setup_scenario.py.jinja2",
        )

    def _write_tests(
        self, task_dir: Path, scenario, plan: PlanResult, blueprint: ScenarioBlueprint
    ) -> None:
        """Write test files (test.sh + checks.py)."""
        tests_dir = task_dir / "tests"
        ctx = self._build_template_context(scenario, plan, blueprint)

        test_sh_path = tests_dir / "test.sh"
        test_sh_path.write_text(
            self.jinja_env.get_template("procurement/supply_planning/test.sh.jinja2").render(**ctx)
        )
        test_sh_path.chmod(0o755)

        checks_path = tests_dir / "checks.py"
        checks_path.write_text(
            self.jinja_env.get_template("procurement/supply_planning/checks.py.jinja2").render(**ctx)
        )
        checks_path.chmod(0o755)

    @staticmethod
    def _build_template_context(scenario, plan: PlanResult, blueprint: ScenarioBlueprint) -> dict:
        """Build template context with computed values for verification and solution."""
        main_product = scenario.products[0] if scenario.products else None
        product_code = main_product.code if main_product else "UNKNOWN"
        list_price = main_product.list_price if main_product else 0.0
        required_new_spend_margin = blueprint.margin
        has_budgets = any(
            getattr(c, "budget_dollars", None) is not None for c in scenario.customers
        )
        stock_available = sum(
            sl.quantity for sl in scenario.stock_levels if sl.product_code == product_code
        )
        partial_acceptance_required = blueprint.unsat_demand
        customers_by_ref = {customer.ref: customer for customer in blueprint.customers}
        solver_plan = _build_solver_plan(plan)
        baseline_solver_plan = {"sales": [], "manufacturing": [], "purchases": []}
        repair_expectations = {"enabled": False}
        if blueprint.repair_context is not None:
            baseline_plan = blueprint.repair_context.baseline_plan
            if baseline_plan is not None:
                baseline_solver_plan = _build_solver_plan(baseline_plan)
            broken_purchase_order_refs = list(blueprint.repair_context.broken_purchase_order_refs)
            broken_manufacturing_order_refs = list(
                blueprint.repair_context.broken_manufacturing_order_refs
            )
            seeded_purchase_orders = [
                {
                    "ref": order.ref,
                    "plan_ref": order.plan_ref,
                    "vendor_ref": order.vendor_ref,
                    "product_code": order.product_code,
                    "quantity": order.quantity,
                    "planned_arrival_days": order.planned_arrival_days,
                    "origin_customer_refs": list(order.origin_customer_refs),
                    "origin_plan_refs": list(order.origin_plan_refs),
                    "supply_role": order.supply_role,
                }
                for order in blueprint.repair_context.seeded_purchase_orders
            ]
            seeded_manufacturing_orders = [
                {
                    "ref": order.ref,
                    "plan_ref": order.plan_ref,
                    "product_code": order.product_code,
                    "quantity": order.quantity,
                    "workcenter_code": order.workcenter_code,
                    "deadline_days": order.deadline_days,
                    "origin_customer_refs": list(order.origin_customer_refs),
                    "origin_plan_refs": list(order.origin_plan_refs),
                    "level": order.level,
                }
                for order in blueprint.repair_context.seeded_manufacturing_orders
            ]
            repair_expectations = {
                "enabled": True,
                "kind": blueprint.repair_context.kind,
                "broken_supplier_offer_key": blueprint.repair_context.broken_supplier_offer_key,
                "broken_supplier_vendor_ref": blueprint.repair_context.broken_supplier_vendor_ref,
                "broken_workcenter_code": blueprint.repair_context.broken_workcenter_code,
                "impacted_customer_refs": list(blueprint.repair_context.impacted_customer_refs),
                "seeded_sales_order_refs": [
                    customer.resolved_task_order_ref for customer in blueprint.customers
                ],
                "seeded_purchase_order_refs": [
                    order.ref for order in blueprint.repair_context.seeded_purchase_orders
                ],
                "seeded_manufacturing_order_refs": [
                    order.ref for order in blueprint.repair_context.seeded_manufacturing_orders
                ],
                "broken_purchase_order_refs": broken_purchase_order_refs,
                "broken_manufacturing_order_refs": broken_manufacturing_order_refs,
                "baseline_offer_quantities": {
                    row.offer_key: row.quantity
                    for row in blueprint.repair_context.baseline_offer_quantities
                },
                "baseline_manufacturing_quantities": {
                    f"{row.product_code}|{row.workcenter_code}": row.quantity
                    for row in blueprint.repair_context.baseline_manufacturing_quantities
                },
                "seeded_purchase_orders": seeded_purchase_orders,
                "seeded_manufacturing_orders": seeded_manufacturing_orders,
            }
        invoicing_policy = {
            "invoice_required": blueprint.invoicing_policy.invoice_required,
            "payment_term": blueprint.invoicing_policy.payment_term,
            "downpayment_required": blueprint.invoicing_policy.downpayment_required,
            "downpayment_threshold_amount": blueprint.invoicing_policy.downpayment_threshold_amount,
            "downpayment_mode": blueprint.invoicing_policy.downpayment_mode,
            "downpayment_value": blueprint.invoicing_policy.downpayment_value,
        }

        def append_unique(values: list[str], value: str) -> None:
            if value not in values:
                values.append(value)

        task_orders = []
        for allocation in plan.allocations:
            customer = customers_by_ref.get(allocation.ref)
            task_orders.append(
                {
                    "customer_ref": allocation.ref,
                    "customer_name": customer.name if customer is not None else allocation.ref,
                    "order_ref": allocation.task_order_ref or allocation.ref,
                    "source": allocation.task_order_source,
                    "seeded_order_state": allocation.seeded_order_state,
                    "action": allocation.action,
                    "accepted": allocation.accepted,
                    "reject_reasons": list(allocation.reject_reasons),
                    "reject_reason_text": allocation.reject_reason_text,
                    "demand": allocation.demand,
                    "fulfilled_demand": allocation.fulfilled_demand,
                    "deadline": allocation.deadline,
                    "price": allocation.price,
                    "revenue": allocation.revenue,
                    "sale_order_amount_untaxed": round(allocation.price * allocation.demand, 2),
                    "budget_dollars": customer.budget_dollars if customer is not None else None,
                }
            )

        verifier_orders = []
        for order in task_orders:
            if partial_acceptance_required and not order["accepted"]:
                continue
            verifier_orders.append(
                {
                    "customer_ref": order["customer_ref"],
                    "demand_target": (
                        order["fulfilled_demand"]
                        if partial_acceptance_required
                        else order["demand"]
                    ),
                    "demand_comparator": "equals" if partial_acceptance_required else "gte",
                    "deadline": order["deadline"],
                    "revenue": order["revenue"],
                    "budget_dollars": order["budget_dollars"],
                }
            )

        seeded_orders_to_confirm = [
            order
            for order in task_orders
            if order["source"] == "seeded" and not order["reject_reasons"]
        ]
        seeded_orders_to_cancel = [
            order
            for order in task_orders
            if order["source"] == "seeded" and order["reject_reasons"]
        ]
        prompt_only_orders_to_keep = [
            order
            for order in task_orders
            if order["source"] == "prompt_only" and not order["reject_reasons"]
        ]
        prompt_only_orders_to_skip = [
            order
            for order in task_orders
            if order["source"] == "prompt_only" and order["reject_reasons"]
        ]
        seeded_order_confirm_rule_lines = [
            f"seeded_order_confirmed {order['order_ref']} {order['customer_ref']} "
            f"{product_code} {order['demand']}"
            for order in seeded_orders_to_confirm
        ]
        seeded_order_cancel_rule_lines = [
            f"seeded_order_cancelled {order['order_ref']} {order['customer_ref']} "
            f"{product_code} {order['demand']}"
            for order in seeded_orders_to_cancel
        ]
        prompt_only_keep_rule_lines = [
            f"so_confirmed {order['customer_ref']} {product_code}"
            for order in prompt_only_orders_to_keep
        ]
        prompt_only_skip_rule_lines = [
            f"prompt_request_not_confirmed {order['customer_ref']} {product_code}"
            for order in prompt_only_orders_to_skip
        ]
        confirmed_so_rule_lines = (
            seeded_order_confirm_rule_lines
            if blueprint.repair_context is not None
            else [f"so_confirmed {order['customer_ref']} {product_code}" for order in task_orders]
        )
        invoice_requirements = []
        for order in task_orders:
            amount = float(order["sale_order_amount_untaxed"])
            retained = bool(order["accepted"])
            invoice_required = bool(invoicing_policy["invoice_required"]) and retained
            invoice_flow = (
                blueprint.invoicing_policy.invoice_flow_for(amount) if invoice_required else "none"
            )
            threshold_applies = (
                blueprint.invoicing_policy.threshold_applies(amount) if invoice_required else False
            )
            expected_downpayment_invoice_amount = (
                blueprint.invoicing_policy.downpayment_invoice_amount_for(amount)
                if invoice_required
                else 0.0
            )
            expected_regular_invoice_amount = (
                blueprint.invoicing_policy.regular_invoice_amount_for(amount)
                if invoice_required
                else 0.0
            )
            invoice_requirements.append(
                {
                    "customer_ref": order["customer_ref"],
                    "customer_name": order["customer_name"],
                    "order_client_ref": order["order_ref"],
                    "task_order_source": order["source"],
                    "action": order["action"],
                    "accepted": retained,
                    "invoice_required": invoice_required,
                    "invoice_flow": invoice_flow,
                    "payment_term": invoicing_policy["payment_term"],
                    "sale_order_amount_untaxed": amount,
                    "threshold_applies": threshold_applies,
                    "expected_posted_invoice_count": (
                        2 if invoice_flow == "downpayment_then_regular" else int(invoice_required)
                    ),
                    "downpayment_mode": (
                        invoicing_policy["downpayment_mode"] if threshold_applies else "none"
                    ),
                    "downpayment_value": (
                        invoicing_policy["downpayment_value"] if threshold_applies else 0.0
                    ),
                    "expected_downpayment_invoice_amount": round(
                        float(expected_downpayment_invoice_amount), 2
                    ),
                    "expected_regular_invoice_amount": round(
                        float(expected_regular_invoice_amount), 2
                    ),
                }
            )
        verifier_has_budget_rules = any(
            order["budget_dollars"] is not None for order in verifier_orders
        )

        task_customer_refs = [customer.ref for customer in blueprint.customers]

        relevant_codes: list[str] = []
        if product_code != "UNKNOWN":
            append_unique(relevant_codes, product_code)

        manufactured_product_codes: list[str] = []
        bom_rows = []
        for bom in scenario.boms:
            append_unique(relevant_codes, bom.product_code)
            append_unique(manufactured_product_codes, bom.product_code)
            component_rows = []
            for component in bom.components:
                append_unique(relevant_codes, component.component_code)
                component_rows.append(
                    {
                        "component_code": component.component_code,
                        "quantity": component.quantity,
                    }
                )
            bom_rows.append(
                {
                    "product_code": bom.product_code,
                    "components": component_rows,
                }
            )

        core_vendor_refs: list[str] = []
        for product in scenario.products:
            if product.code not in relevant_codes:
                continue
            for vendor_info in product.vendor_info:
                append_unique(core_vendor_refs, vendor_info.partner_ref)

        core_partner_refs = list(core_vendor_refs)
        for customer_ref in task_customer_refs:
            append_unique(core_partner_refs, customer_ref)

        adjacent_partner_refs: list[str] = []
        for customer in scenario.customers:
            if customer.ref not in task_customer_refs:
                append_unique(adjacent_partner_refs, customer.ref)
        for vendor in scenario.vendors:
            if vendor.ref not in core_vendor_refs:
                append_unique(adjacent_partner_refs, vendor.ref)

        adjacent_product_codes: list[str] = []
        for product in scenario.products:
            if product.code not in relevant_codes:
                append_unique(adjacent_product_codes, product.code)

        workcenter_cost_rows = []
        workcenter_time_rows = []
        product_lead_days: dict[str, float] = {}
        workcenter_capacity_minutes: dict[str, float] = {}
        for workcenter in scenario.workcenters:
            workcenter_cost_rows.append(
                {
                    "product_code": workcenter.product_code,
                    "workcenter_code": workcenter.code,
                    "unit_cost": round(workcenter.unit_cost, 2),
                }
            )
            workcenter_time_rows.append(
                {
                    "product_code": workcenter.product_code,
                    "workcenter_code": workcenter.code,
                    "minutes_per_unit": round(workcenter.time_per_unit_minutes, 6),
                }
            )
            product_lead_days.setdefault(
                workcenter.product_code,
                round(workcenter.lead_days or 0, 2),
            )
            rounded_capacity_minutes = round(workcenter.capacity_minutes, 2)
            existing_capacity_minutes = workcenter_capacity_minutes.get(workcenter.code)
            if existing_capacity_minutes is None:
                workcenter_capacity_minutes[workcenter.code] = rounded_capacity_minutes
            elif existing_capacity_minutes != rounded_capacity_minutes:
                raise RuntimeError(
                    "inconsistent capacity_minutes for workcenter "
                    f"{workcenter.code}: {existing_capacity_minutes} vs {rounded_capacity_minutes}"
                )

        product_lead_rows = [
            {"product_code": code, "lead_days": lead_days}
            for code, lead_days in product_lead_days.items()
        ]
        workcenter_capacity_rows = [
            {
                "workcenter_code": workcenter_code,
                "capacity_minutes": capacity_minutes,
            }
            for workcenter_code, capacity_minutes in workcenter_capacity_minutes.items()
        ]

        finished_vendor_max_qty_rows = []
        component_vendor_max_qty_rows = []
        supplier_offer_rows = []
        purchase_traceability_targets = []
        purchase_traceability_keys: set[tuple[str, str]] = set()
        for vendor in blueprint.vendors:
            target_key = (vendor.product_code, vendor.supply_role)
            if target_key not in purchase_traceability_keys:
                purchase_traceability_keys.add(target_key)
                purchase_traceability_targets.append(
                    {
                        "product_code": vendor.product_code,
                        "supply_role": vendor.supply_role,
                    }
                )
            if vendor.supply_role == "finished" and vendor.max_qty is not None:
                finished_vendor_max_qty_rows.append(
                    {
                        "vendor_ref": vendor.ref,
                        "max_qty": vendor.max_qty,
                    }
                )
            if vendor.supply_role == "component" and vendor.max_qty is not None:
                component_vendor_max_qty_rows.append(
                    {
                        "vendor_ref": vendor.ref,
                        "product_code": vendor.product_code,
                        "max_qty": vendor.max_qty,
                    }
                )
            if vendor.product_code in relevant_codes:
                supplier_offer_rows.append(
                    {
                        "vendor_ref": vendor.ref,
                        "product_code": vendor.product_code,
                        "min_qty": vendor.min_qty,
                        "price": round(vendor.price_dollars, 2),
                        "delay": vendor.lead_time,
                        "offer_key": (
                            f"{vendor.ref}|{vendor.product_code}|{vendor.min_qty}|"
                            f"{round(vendor.price_dollars, 2)}|{vendor.lead_time}"
                        ),
                    }
                )

        verifier_catalog = {
            "relevant_codes": relevant_codes,
            "core_partner_refs": core_partner_refs,
            "core_vendor_refs": core_vendor_refs,
            "adjacent_partner_refs": adjacent_partner_refs,
            "adjacent_product_codes": adjacent_product_codes,
            "manufactured_product_codes": manufactured_product_codes,
            "bom_rows": bom_rows,
            "workcenter_cost_rows": workcenter_cost_rows,
            "product_lead_rows": product_lead_rows,
            "workcenter_time_rows": workcenter_time_rows,
            "workcenter_capacity_rows": workcenter_capacity_rows,
            "finished_vendor_max_qty_rows": finished_vendor_max_qty_rows,
            "component_vendor_max_qty_rows": component_vendor_max_qty_rows,
            "supplier_offer_rows": supplier_offer_rows,
        }
        finished_goods_buying_allowed = not blueprint.manufacturing_only.enabled and any(
            vendor.supply_role == "finished" and vendor.product_code == product_code
            for vendor in blueprint.vendors
        )
        finished_manufacturing_allowed = product_code in manufactured_product_codes
        traceability_rule_lines = [
            f"po_origin_traceability {target['product_code']} {target['supply_role']}"
            for target in purchase_traceability_targets
        ]
        if finished_manufacturing_allowed:
            traceability_rule_lines.append(f"mrp_origin_traceability {product_code}")

        return {
            "scenario": scenario,
            "scenario_name": scenario.name,
            "scenario_number": scenario.scenario_number,
            "blueprint": blueprint,
            "plan": plan,
            "objective_kind": blueprint.objective_kind.value,
            "objective_value": round(plan.objective_value, 2),
            "solver_plan": solver_plan,
            "solver_plan_literal": pformat(solver_plan, sort_dicts=False, width=88),
            "baseline_solver_plan": baseline_solver_plan,
            "baseline_solver_plan_literal": pformat(
                baseline_solver_plan, sort_dicts=False, width=88
            ),
            "repair_expectations": repair_expectations,
            "repair_expectations_literal": pformat(repair_expectations, sort_dicts=False, width=88),
            "product_code": product_code,
            "list_price": list_price,
            "invoicing_policy": invoicing_policy,
            "invoicing_policy_literal": pformat(invoicing_policy, sort_dicts=False, width=88),
            "invoice_requirements": invoice_requirements,
            "invoice_requirements_literal": pformat(
                invoice_requirements, sort_dicts=False, width=88
            ),
            "required_new_spend_margin": required_new_spend_margin,
            "finished_goods_buying_allowed": finished_goods_buying_allowed,
            "finished_manufacturing_allowed": finished_manufacturing_allowed,
            "has_budgets": has_budgets,
            "stock_available": stock_available,
            "partial_acceptance_required": partial_acceptance_required,
            "task_customer_refs": [customer.ref for customer in blueprint.customers],
            "verifier_orders": verifier_orders,
            "verifier_has_budget_rules": verifier_has_budget_rules,
            "seeded_orders_to_confirm": seeded_orders_to_confirm,
            "seeded_orders_to_cancel": seeded_orders_to_cancel,
            "prompt_only_orders_to_keep": prompt_only_orders_to_keep,
            "prompt_only_orders_to_skip": prompt_only_orders_to_skip,
            "seeded_order_confirm_rule_lines": seeded_order_confirm_rule_lines,
            "seeded_order_cancel_rule_lines": seeded_order_cancel_rule_lines,
            "prompt_only_keep_rule_lines": prompt_only_keep_rule_lines,
            "prompt_only_skip_rule_lines": prompt_only_skip_rule_lines,
            "confirmed_so_rule_lines": confirmed_so_rule_lines,
            "purchase_traceability_targets": purchase_traceability_targets,
            "traceability_rule_lines": traceability_rule_lines,
            "verifier_catalog": verifier_catalog,
        }

    def _write_solution(
        self, task_dir: Path, scenario, plan: PlanResult, blueprint: ScenarioBlueprint
    ) -> None:
        """Write solution files (solve.sh, solver.py)."""
        solution_dir = task_dir / "solution"

        # Compute template context
        ctx = self._build_template_context(scenario, plan, blueprint)

        # Write solver.py (ORM-based solution executor)
        template = self.jinja_env.get_template("procurement/supply_planning/solver.py.jinja2")
        content = template.render(**ctx)
        (solution_dir / "solver.py").write_text(content)

        # Write solve.sh
        template = self.jinja_env.get_template("procurement/supply_planning/solve.sh.jinja2")
        content = template.render(**ctx)
        solve_sh_path = solution_dir / "solve.sh"
        solve_sh_path.write_text(content)
        solve_sh_path.chmod(0o755)

        # Write optimal_plan.json for reference
        plan_data = plan.model_dump(mode="json")
        (solution_dir / "optimal_plan.json").write_text(json.dumps(plan_data, indent=2))


def export_scenario_to_harbor(
    build: ScenarioBuild,
    blueprint: ScenarioBlueprint,
    output_dir: Path | str = "tasks",
) -> Path:
    """Convenience function to export a single scenario to Harbor format.

    Args:
        build: The generated scenario build
        blueprint: The original blueprint
        output_dir: Directory to create task in

    Returns:
        Path to the created task directory
    """
    exporter = HarborTaskExporter(output_dir=output_dir)
    return exporter.export(build, blueprint)


def export_scenarios_to_harbor(
    builds: list[tuple[ScenarioBuild, ScenarioBlueprint]],
    output_dir: Path | str = "tasks",
) -> list[Path]:
    """Export multiple scenarios to Harbor format.

    Args:
        builds: List of (ScenarioBuild, ScenarioBlueprint) tuples
        output_dir: Directory to create tasks in

    Returns:
        List of paths to created task directories
    """
    exporter = HarborTaskExporter(output_dir=output_dir)
    return [exporter.export(build, blueprint) for build, blueprint in builds]
