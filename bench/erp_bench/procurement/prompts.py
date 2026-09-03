"""Procurement prompt rendering helpers."""

from __future__ import annotations

from hashlib import sha256

from .procurement_objectives import ProcurementObjectiveKind
from .solver import CustomerSpec, ScenarioBlueprint, format_margin_percent

PAYMENT_TERM_LABELS = {
    "immediate": "Immediate Payment",
    "net_30": "30 Days",
}

SEEDED_INSTRUCTION_INTRO_CHOICES = (
    "Review the open order requests for {product_name}.",
    "Work through the current order queue for {product_name}.",
    "Start with the live customer requests for {product_name}.",
    "Fill the pending customer orders for {product_name}.",
    "Cover all outstanding {product_name} order requests.",
    "Address the current order backlog for {product_name}.",
    "Process the incoming customer orders for {product_name}.",
    "Handle the list of live {product_name} order demands.",
    "Handle the open {product_name} order queue.",
    "Confirm supply status for the latest {product_name} requests.",
)
SINGLE_ORDER_INSTRUCTION_INTRO_CHOICES = (
    "This customer order for {product_name} still needs supply coverage:",
    "We need a release plan for this {product_name} order:",
    "This {product_name} order is waiting on a supply decision:",
)
MULTI_ORDER_INSTRUCTION_INTRO_CHOICES = (
    "The following customer orders for {product_name} require confirmed supply to proceed with fulfillment:",
    "Plan fulfillment for these outstanding {product_name} orders to ensure timely deliveries:",
    "Ensure all these {product_name} customer orders are supplied on schedule:",
    "Each customer order below requires a fulfillment-ready supply solution before its delivery due date:",
    "The following {product_name} customer orders are firm and must be supplied on schedule:",
    "All orders below for {product_name} need confirmed supply coverage and scheduling.",
    "Supply plans must be created to fulfill each outstanding {product_name} customer order listed.",
    "Prepare a fulfillment strategy for all listed {product_name} orders to meet due dates and avoid shortages.",
)
ZERO_STOCK_SUMMARY_CHOICES = (
    "There is no available stock on hand.",
    "Current finished-goods stock is zero.",
    "We do not have any finished units in stock.",
)
STOCK_SUMMARY_CHOICES = (
    "We have {stock_units} finished {unit_label} on hand.",
    "Current finished-goods stock is {stock_units} {unit_label}.",
    "On-hand finished stock covers {stock_units} {unit_label}.",
)
MIXED_ROUTE_SUMMARY_CHOICES = (
    "If stock is not enough, you can cover the gap by buying finished goods, building in-house, or using a mix of both.",
    "If stock runs short, you can close the gap with finished-goods purchasing, in-house manufacturing, or a combination of the two.",
    "Any shortfall can be handled with finished-goods buying, in-house manufacturing, or a mix that still satisfies policy.",
)
POLICY_FORBIDDEN_ROUTE_SUMMARY_CHOICES = (
    "Finished-goods buying is off the table for this cycle, so any shortfall has to be manufactured in-house.",
    "Leadership has ruled out finished-goods buying for this cycle, so any gap beyond stock has to be built in-house.",
    "Do not buy the finished product for this run; any shortfall has to be manufactured internally.",
)
MANUFACTURE_ONLY_ROUTE_SUMMARY_CHOICES = (
    "There is no finished-goods buying route available for this product right now, so any shortfall has to be manufactured in-house.",
    "Buying the finished product is not available here, so any gap beyond stock has to be built in-house.",
    "Any shortfall has to be manufactured in-house because there is no usable finished-goods vendor route.",
)
BUY_ONLY_ROUTE_SUMMARY_CHOICES = (
    "There is no in-house manufacturing route for the finished product, so any shortfall has to come from vendors.",
    "You cannot build the finished product internally, so any gap beyond stock has to be covered by purchasing.",
    "The finished product is not manufactured in-house, so any shortfall has to be sourced from vendors.",
)
OBJECTIVE_BULLET_VENDOR_CONSOLIDATION_CHOICES = (
    "Fulfill all customer orders while consolidating this cycle's purchasing across as few vendors as practical. If more than one feasible plan uses the same number of vendors, keep new purchasing and manufacturing spend as low as possible.",
    "Cover every customer order while keeping this cycle's purchasing concentrated with as few vendors as practical. If multiple feasible plans use the same number of vendors, keep new purchasing and manufacturing spend as low as possible.",
    "Get every customer order covered while limiting this cycle's purchasing to as few vendors as practical. Keep new purchasing and manufacturing spend as low as possible when choosing between equivalent vendor counts.",
)
OBJECTIVE_BULLET_CAPACITY_PRESERVATION_CHOICES = (
    "Fulfill all customer orders while preserving as much shared workcenter capacity as possible for other scheduled work. If more than one feasible plan uses the same amount of workcenter capacity, keep new purchasing and manufacturing spend as low as possible.",
    "Cover every customer order while using as little shared workcenter capacity as practical. If multiple feasible plans use the same amount of workcenter capacity, keep new purchasing and manufacturing spend as low as possible.",
    "Get every customer order covered while keeping as much shared workcenter capacity open as possible. Keep new purchasing and manufacturing spend as low as possible when workcenter-capacity use ties.",
)
OBJECTIVE_BULLET_CONSTRAINT_ONLY_CHOICES = (
    "Fulfill all customer orders while respecting all stated policies and constraints.",
    "Cover every customer order while following all stated policies and constraints.",
    "Get every customer order covered without violating any stated policy or constraint.",
)
MARGIN_POLICY_BULLET_CHOICES = (
    "Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above {margin_text}% at selling price.",
    "The combined units covered through new purchasing or manufacturing must clear at least {margin_text}% portfolio-level new-spend margin at selling price.",
    "Any units you cover through new buying or manufacturing count toward one combined portfolio that must still meet a minimum {margin_text}% new-spend margin at selling price.",
)
ZERO_STOCK_POLICY_BULLET_CONSTRAINT_ONLY_CHOICES = (
    "There is no finished stock available at the start.",
    "No finished stock is available at the start of the task.",
    "You are starting with zero finished stock on hand.",
)
ZERO_STOCK_POLICY_BULLET_CHOICES = (
    "There is no finished stock to use upfront, so all coverage has to come from new supply.",
    "No finished stock is available at the start, so plan the required new supply accordingly.",
    "You are starting with zero finished stock, so the full requirement has to be covered through new supply.",
)
STOCK_POLICY_BULLET_VENDOR_CONSOLIDATION_CHOICES = (
    "Use available finished stock where it helps avoid opening additional purchasing paths.",
    "Use the finished stock on hand where it helps keep purchasing concentrated with fewer vendors.",
    "Available finished stock can be used where it helps avoid adding another supplier to the plan.",
)
STOCK_POLICY_BULLET_CAPACITY_PRESERVATION_CHOICES = (
    "Use available finished stock where it helps preserve shared workcenter capacity.",
    "Available finished stock can be used where it helps keep shared workcenter capacity open.",
    "Use the finished stock on hand where it helps reduce demand on shared workcenters.",
)
STOCK_POLICY_BULLET_CONSTRAINT_ONLY_CHOICES = (
    "Finished stock is available and may be used where it helps satisfy the stated constraints.",
    "Use the finished stock on hand where it helps satisfy the stated policies and constraints.",
    "Available finished stock may be used wherever it helps achieve a constraint-compliant plan.",
)
STOCK_POLICY_BULLET_CHOICES = (
    "Accounting treats the existing stock as sunk cost.",
    "Finance considers the existing stock as sunk cost.",
    "Existing stock is a sunk cost and should not be treated as new spend.",
)
ACCEPTED_SET_BULLET_VENDOR_CONSOLIDATION_CHOICES = (
    "For every order that passes acceptance, provide coverage while consolidating this cycle's purchasing across as few vendors as practical. If more than one feasible plan uses the same number of vendors, keep new purchasing and manufacturing spend as low as possible.",
    "For all accepted orders, arrange fulfillment while keeping this cycle's purchasing concentrated with as few vendors as practical. If vendor counts tie, keep new purchasing and manufacturing spend as low as possible.",
    "Once orders are accepted, cover them while limiting this cycle's purchasing to as few vendors as practical. When vendor counts tie, keep new purchasing and manufacturing spend as low as possible.",
)
ACCEPTED_SET_BULLET_CAPACITY_PRESERVATION_CHOICES = (
    "For every order that passes acceptance, provide coverage while preserving as much shared workcenter capacity as possible for other scheduled work. If more than one feasible plan uses the same amount of workcenter capacity, keep new purchasing and manufacturing spend as low as possible.",
    "For all accepted orders, arrange fulfillment while using as little shared workcenter capacity as practical. If workcenter-capacity use ties, keep new purchasing and manufacturing spend as low as possible.",
    "Once orders are accepted, cover them while keeping as much shared workcenter capacity open as possible. When workcenter-capacity use ties, keep new purchasing and manufacturing spend as low as possible.",
)
ACCEPTED_SET_BULLET_CONSTRAINT_ONLY_CHOICES = (
    "For every order that passes acceptance, provide coverage while respecting all stated policies and constraints.",
    "For all accepted orders, arrange fulfillment while following all stated policies and constraints.",
    "Once orders are accepted, cover them without violating any stated policy or constraint.",
)
ACCEPTED_SET_BULLET_CHOICES = (
    "For every order that passes acceptance, provide coverage while spending as little as possible.",
    "For all accepted orders, arrange fulfillment at the lowest possible new purchase or manufacturing cost.",
    "Once orders are accepted, minimize new procurement or production spend while covering them.",
    "Approved orders should be satisfied with the minimum necessary new spend.",
    "After acceptance, cover each qualifying order while achieving the lowest possible new outlay.",
    "For every accepted order, fulfill it while keeping additional procurement or manufacturing outflow to a minimum.",
    "Deliver eligible orders but focus on reducing fresh procurement or manufacturing expenses.",
    "For every accepted order, provide supply coverage with minimal new spend.",
    "Arrange supply for eligible orders, ensuring the least new investment.",
    "For qualifying orders, ensure fulfillment while keeping new costs down.",
)
ACCEPTANCE_INTRO_BULLET_CHOICES = (
    "Our criteria for accepting incoming customer orders are as follows.",
    "These are the rules for customer order acceptance.",
    "Order acceptance must follow these rules.",
    "Customer order acceptance will be determined as follows.",
    "The rules for which orders can be accepted are outlined below.",
    "Customer order acceptance is based on the following criteria.",
)
NOTES_BULLET_CHOICES = (
    "Check Internal Notes/comments on {target_text} before you release anything.",
    "Before releasing anything, read the Internal Notes/comments on {target_text}.",
    "Review Internal Notes/comments on {target_text} before you confirm entries in the ERP.",
)
MANUFACTURING_ONLY_POLICY_BULLET_CHOICES = (
    "Finished-goods procurement is not allowed in this planning cycle because finance has frozen budget for direct replenishment; rely on in-house manufacturing instead.",
    "Finished-goods purchasing is not allowed right now because our vendors are experiencing a supply chain disruption; rely on in-house manufacturing instead.",
    "Finished-goods procurement is not allowed for these orders because leadership has directed us to move off of external vendors; rely on in-house manufacturing instead.",
)
OBJECTIVE_BULLET_CHOICES = (
    "Fulfill all orders while minimizing new spending on procurement and manufacturing.",
)
FINISHED_MO_BULLET = (
    "For finished goods MOs, put the Sales Order reference (e.g. S00030) into the origin "
    "field ('Source' in the UI)."
)
SUBASSEMBLY_MO_BULLET = (
    "For subassembly or intermediate MOs, put the immediate parent MO reference(s) that the "
    "subassembly feeds (e.g. WH/MO/00020 or WH/MO/00020, WH/MO/00021) into the origin field "
    "('Source' in the UI)."
)
FINISHED_PO_ORIGIN_BULLET = (
    "For finished goods POs, put the SO reference(s) (e.g. S00030 or S00030, S00031) into the "
    "origin field ('Source' in the UI)."
)
COMPONENT_PO_ORIGIN_BULLET = (
    "For component POs, put the MO reference(s) (e.g. WH/MO/00010 or WH/MO/00010, WH/MO/00011) "
    "into the origin field."
)
LIST_PRICE_BULLET = "You must sell this product at List Price."
MANUFACTURING_ONLY_COVERAGE_BULLET = (
    "Use in-house manufacturing to cover finished-goods shortfalls; purchase orders are only for "
    "components."
)
MANUFACTURE_COMPONENTS_IF_CHOSEN_BULLET = (
    "If you choose to manufacture, you must procure the components that are not in stock."
)
PROCURE_COMPONENTS_ONLY_BULLET = "Procure only the components that are not in stock."
PURCHASE_ORDER_DELIVERY_DATE_BULLET = "On purchase orders, you must set the delivery date."


def _payment_term_label(payment_term: str) -> str:
    if payment_term not in PAYMENT_TERM_LABELS:
        raise RuntimeError(f"Unsupported payment term: {payment_term}")
    return PAYMENT_TERM_LABELS[payment_term]


def _downpayment_rule_text(blueprint: ScenarioBlueprint) -> str:
    policy = blueprint.invoicing_policy
    threshold = policy.downpayment_threshold_amount
    if threshold is None or policy.downpayment_mode is None or policy.downpayment_value is None:
        raise RuntimeError("Downpayment policy must define threshold, mode, and value")
    if policy.downpayment_mode == "percentage":
        value_text = f"{policy.downpayment_value:g}%"
    else:
        value_text = f"${policy.downpayment_value:,.2f}"
    return (
        f"For retained orders with untaxed totals of ${threshold:,.2f} or more, first create and post "
        f"a down payment invoice for {value_text}; then create and post a regular invoice for the "
        "remaining amount. Below that threshold, create and post one regular invoice."
    )


def _invoicing_policy_bullets(blueprint: ScenarioBlueprint) -> list[str]:
    policy = blueprint.invoicing_policy
    if not policy.invoice_required:
        return []
    if policy.payment_term is None:
        raise RuntimeError("Invoice-required scenarios must define a payment term")
    bullets = [
        f"Use {_payment_term_label(policy.payment_term)} terms on retained sales orders and all linked customer invoices."
    ]
    if policy.downpayment_required:
        bullets.append(_downpayment_rule_text(blueprint))
    else:
        bullets.insert(
            0,
            "After confirming each retained sales order, create and post exactly one linked customer invoice.",
        )
    if blueprint.unsat_demand:
        bullets.append("Rejected, cancelled, or skipped orders must not be invoiced.")
    return bullets


def _variant_index(blueprint: ScenarioBlueprint, *, key: str, size: int) -> int:
    if size <= 0:
        raise RuntimeError("Prompt variant list cannot be empty")
    digest = sha256(f"{blueprint.scenario_number}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % size


def _pick_variant(
    blueprint: ScenarioBlueprint,
    *,
    key: str,
    choices: tuple[str, ...],
) -> str:
    return choices[_variant_index(blueprint, key=key, size=len(choices))]


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else plural or f"{singular}s"


def _natural_join(parts: tuple[str, ...]) -> str:
    if not parts:
        raise RuntimeError("Natural join requires at least one part")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _has_finished_goods_buying(blueprint: ScenarioBlueprint) -> bool:
    return any(
        vendor.supply_role == "finished" and vendor.product_code == blueprint.product.code
        for vendor in blueprint.vendors
    )


def _has_finished_goods_manufacturing(blueprint: ScenarioBlueprint) -> bool:
    return blueprint.product.code in blueprint.manufacturing_by_code


def _route_mode(blueprint: ScenarioBlueprint) -> str:
    has_manufacturing = _has_finished_goods_manufacturing(blueprint)
    has_finished_buying = _has_finished_goods_buying(blueprint)
    if blueprint.manufacturing_only.enabled:
        if not has_manufacturing:
            raise RuntimeError(
                "Manufacturing-only procurement prompts require in-house finished-goods manufacturing"
            )
        return "manufacture_only"
    if has_manufacturing and has_finished_buying:
        return "mixed"
    if has_manufacturing:
        return "manufacture_only"
    if has_finished_buying:
        return "buy_only"
    raise RuntimeError("Procurement prompt requires either a buying route or a manufacturing route")


def _format_customer_line(customer: CustomerSpec) -> str:
    unit_label = _plural(customer.demand, "unit")
    day_label = _plural(customer.deadline, "day")
    line = (
        f"- {customer.name}: {customer.demand} {unit_label} due in {customer.deadline} {day_label}"
    )
    if customer.budget_dollars is not None:
        line += f" (pretax budget cap ${customer.budget_dollars:,.0f})"
    return line


def _instruction_intro(blueprint: ScenarioBlueprint) -> str:
    product_name = blueprint.product.name
    seeded_orders = any(customer.task_order_source == "seeded" for customer in blueprint.customers)
    if seeded_orders:
        return _pick_variant(
            blueprint,
            key="seeded-instruction-intro",
            choices=tuple(
                choice.format(product_name=product_name)
                for choice in SEEDED_INSTRUCTION_INTRO_CHOICES
            ),
        )
    if len(blueprint.customers) == 1:
        return _pick_variant(
            blueprint,
            key="single-order-instruction-intro",
            choices=tuple(
                choice.format(product_name=product_name)
                for choice in SINGLE_ORDER_INSTRUCTION_INTRO_CHOICES
            ),
        )
    return _pick_variant(
        blueprint,
        key="multi-order-instruction-intro",
        choices=tuple(
            choice.format(product_name=product_name)
            for choice in MULTI_ORDER_INSTRUCTION_INTRO_CHOICES
        ),
    )


def _stock_summary(blueprint: ScenarioBlueprint) -> str:
    stock_units = blueprint.target_product_stock_units
    if stock_units <= 0:
        return _pick_variant(
            blueprint,
            key="zero-stock-summary",
            choices=ZERO_STOCK_SUMMARY_CHOICES,
        )
    unit_label = _plural(stock_units, "unit")
    return _pick_variant(
        blueprint,
        key="stock-summary",
        choices=tuple(
            choice.format(stock_units=stock_units, unit_label=unit_label)
            for choice in STOCK_SUMMARY_CHOICES
        ),
    )


def _route_summary(blueprint: ScenarioBlueprint) -> str:
    route_mode = _route_mode(blueprint)
    if route_mode == "mixed":
        return _pick_variant(
            blueprint,
            key="mixed-route-summary",
            choices=MIXED_ROUTE_SUMMARY_CHOICES,
        )
    if route_mode == "manufacture_only":
        if blueprint.manufacturing_only.family == "policy_forbidden":
            return _pick_variant(
                blueprint,
                key="policy-forbidden-route-summary",
                choices=POLICY_FORBIDDEN_ROUTE_SUMMARY_CHOICES,
            )
        return _pick_variant(
            blueprint,
            key="manufacture-only-route-summary",
            choices=MANUFACTURE_ONLY_ROUTE_SUMMARY_CHOICES,
        )
    if route_mode == "buy_only":
        return _pick_variant(
            blueprint,
            key="buy-only-route-summary",
            choices=BUY_ONLY_ROUTE_SUMMARY_CHOICES,
        )
    raise RuntimeError(f"Unsupported procurement route mode: {route_mode}")


def _instruction_policy_summary(blueprint: ScenarioBlueprint) -> str:
    policy = blueprint.invoicing_policy
    margin_text = format_margin_percent(blueprint.margin)
    parts = [
        f"Across all units covered through new purchasing or manufacturing, keep portfolio-level new-spend margin at or above {margin_text}% at selling price."
    ]
    if not policy.invoice_required:
        return " ".join(parts)
    parts.append("After fulfillment, create and post the required linked customer invoices.")
    if policy.payment_term is not None:
        parts.append(
            f"Use {_payment_term_label(policy.payment_term)} terms on retained sales orders and all linked customer invoices."
        )
    if policy.downpayment_required:
        parts.append(_downpayment_rule_text(blueprint))
    return " ".join(parts)


def _repair_customer_text(blueprint: ScenarioBlueprint) -> str:
    repair = blueprint.repair_context
    if repair is None or not repair.impacted_customer_refs:
        return "the affected customer commitments"
    names = tuple(
        customer.name
        for customer in blueprint.customers
        if customer.ref in set(repair.impacted_customer_refs)
    )
    if not names:
        return "the affected customer commitments"
    return _natural_join(names)


def _repair_instruction_intro(blueprint: ScenarioBlueprint) -> str:
    repair = blueprint.repair_context
    if repair is None:
        raise RuntimeError("Repair instruction intro requires repair_context")
    product_name = blueprint.product.name
    customer_text = _repair_customer_text(blueprint)
    if repair.kind == "supplier_cancellation":
        broken_order = next(
            order
            for order in repair.seeded_purchase_orders
            if order.ref in set(repair.broken_purchase_order_refs)
        )
        vendor_name = next(
            vendor.name for vendor in blueprint.vendors if vendor.ref == broken_order.vendor_ref
        )
        return (
            f"Existing confirmed sales orders and current supply commitments for {product_name} are "
            "already in Odoo. "
            f"{vendor_name} has canceled on us, so that confirmed purchase order can no longer be "
            "relied on. Reuse the existing sales orders, review the commitments already in place, "
            "cancel that purchase order, keep unaffected work where it still makes sense, and "
            f"adjust the plan for {customer_text} "
            "using the best feasible alternative source or fulfillment route."
        )
    workcenter = blueprint.workcenters_by_code[repair.broken_workcenter_code]
    return (
        f"Existing confirmed sales orders and current manufacturing commitments for {product_name} are "
        "already in Odoo. "
        f"{workcenter.name} is experiencing an equipment outage, and confirmed manufacturing work is "
        "already scheduled on that line. Reuse the existing sales orders, review the commitments "
        "already in place, cancel the affected manufacturing orders, keep unaffected work where it "
        f"still makes sense, and adjust the plan for {customer_text} "
        "using the best feasible alternative fulfillment route."
    )


def build_instruction(blueprint: ScenarioBlueprint) -> str:
    """Render the customer-facing instruction block for a procurement blueprint."""
    route_mode = _route_mode(blueprint)
    context_lines = [_stock_summary(blueprint)]
    if route_mode != "manufacture_only":
        context_lines.append(_route_summary(blueprint))
    policy_summary = _instruction_policy_summary(blueprint)
    sections = [
        _repair_instruction_intro(blueprint)
        if blueprint.repair_context is not None
        else _instruction_intro(blueprint),
        "\n".join(_format_customer_line(customer) for customer in blueprint.customers),
        " ".join(context_lines),
        policy_summary,
    ]
    return "\n\n".join(section for section in sections if section)


def _objective_bullet(blueprint: ScenarioBlueprint) -> str:
    if blueprint.objective_kind is ProcurementObjectiveKind.vendor_consolidation:
        return _pick_variant(
            blueprint,
            key="objective-bullet-vendor-consolidation",
            choices=OBJECTIVE_BULLET_VENDOR_CONSOLIDATION_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.capacity_preservation:
        return _pick_variant(
            blueprint,
            key="objective-bullet-capacity-preservation",
            choices=OBJECTIVE_BULLET_CAPACITY_PRESERVATION_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.repair_plan:
        return _pick_variant(
            blueprint,
            key="objective-bullet-repair-plan",
            choices=(
                "Keep the committed orders on track while changing as little of the current plan as practical. If more than one feasible option preserves the same amount of prior work, keep new purchasing and manufacturing spend as low as possible.",
                "Work through the disruption with the smallest practical change to the commitments already in place. If multiple feasible options preserve the same amount of prior work, keep new purchasing and manufacturing spend as low as possible.",
                "Adjust the existing supply plan with the least disruption to what is already committed. When more than one feasible option changes the plan by the same amount, keep new purchasing and manufacturing spend as low as possible.",
            ),
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.constraint_only:
        return _pick_variant(
            blueprint,
            key="objective-bullet-constraint-only",
            choices=OBJECTIVE_BULLET_CONSTRAINT_ONLY_CHOICES,
        )
    return _pick_variant(
        blueprint,
        key="objective-bullet",
        choices=OBJECTIVE_BULLET_CHOICES,
    )


def _margin_policy_bullet(blueprint: ScenarioBlueprint) -> str:
    margin_text = format_margin_percent(blueprint.margin)
    return _pick_variant(
        blueprint,
        key="margin-policy-bullet",
        choices=tuple(
            choice.format(margin_text=margin_text) for choice in MARGIN_POLICY_BULLET_CHOICES
        ),
    )


def _stock_policy_bullet(blueprint: ScenarioBlueprint) -> str:
    if blueprint.target_product_stock_units <= 0:
        if blueprint.objective_kind is ProcurementObjectiveKind.constraint_only:
            return _pick_variant(
                blueprint,
                key="zero-stock-policy-bullet-constraint-only",
                choices=ZERO_STOCK_POLICY_BULLET_CONSTRAINT_ONLY_CHOICES,
            )
        return _pick_variant(
            blueprint,
            key="zero-stock-policy-bullet",
            choices=ZERO_STOCK_POLICY_BULLET_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.vendor_consolidation:
        return _pick_variant(
            blueprint,
            key="stock-policy-bullet-vendor-consolidation",
            choices=STOCK_POLICY_BULLET_VENDOR_CONSOLIDATION_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.capacity_preservation:
        return _pick_variant(
            blueprint,
            key="stock-policy-bullet-capacity-preservation",
            choices=STOCK_POLICY_BULLET_CAPACITY_PRESERVATION_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.constraint_only:
        return _pick_variant(
            blueprint,
            key="stock-policy-bullet-constraint-only",
            choices=STOCK_POLICY_BULLET_CONSTRAINT_ONLY_CHOICES,
        )
    return _pick_variant(
        blueprint,
        key="stock-policy-bullet",
        choices=STOCK_POLICY_BULLET_CHOICES,
    )


def _accepted_set_bullet(blueprint: ScenarioBlueprint) -> str:
    if blueprint.objective_kind is ProcurementObjectiveKind.vendor_consolidation:
        return _pick_variant(
            blueprint,
            key="accepted-set-bullet-vendor-consolidation",
            choices=ACCEPTED_SET_BULLET_VENDOR_CONSOLIDATION_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.capacity_preservation:
        return _pick_variant(
            blueprint,
            key="accepted-set-bullet-capacity-preservation",
            choices=ACCEPTED_SET_BULLET_CAPACITY_PRESERVATION_CHOICES,
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.repair_plan:
        return _pick_variant(
            blueprint,
            key="accepted-set-bullet-repair-plan",
            choices=(
                "For every committed order you keep, keep as much of the current plan in place as practical and only change what the disruption forces you to change. If multiple feasible options preserve the same amount of prior work, keep new purchasing and manufacturing spend as low as possible.",
                "For the retained order set, make the smallest practical change to the commitments already in place. If multiple feasible options change the plan by the same amount, keep new purchasing and manufacturing spend as low as possible.",
                "For every retained order, adjust the existing supply plan with the least disruption to what is already committed. When multiple feasible options preserve the same amount of prior work, keep new purchasing and manufacturing spend as low as possible.",
            ),
        )
    if blueprint.objective_kind is ProcurementObjectiveKind.constraint_only:
        return _pick_variant(
            blueprint,
            key="accepted-set-bullet-constraint-only",
            choices=ACCEPTED_SET_BULLET_CONSTRAINT_ONLY_CHOICES,
        )
    return _pick_variant(
        blueprint,
        key="accepted-set-bullet",
        choices=ACCEPTED_SET_BULLET_CHOICES,
    )


def _acceptance_intro_bullet(blueprint: ScenarioBlueprint) -> str:
    return _pick_variant(
        blueprint,
        key="acceptance-intro-bullet",
        choices=ACCEPTANCE_INTRO_BULLET_CHOICES,
    )


def _notes_bullet(blueprint: ScenarioBlueprint) -> str:
    note_targets = ["stock", "customers", "vendors"]
    if blueprint.workcenters:
        note_targets.append("workcenters")
    target_text = _natural_join(tuple(note_targets))
    return _pick_variant(
        blueprint,
        key="notes-bullet",
        choices=tuple(choice.format(target_text=target_text) for choice in NOTES_BULLET_CHOICES),
    )


def _sales_order_commitment_bullet(*, retained: bool) -> str:
    if retained:
        return "On every accepted Sales Order, set the commitment date."
    return "On sales orders, set the commitment date."


def _manufacturing_schedule_bullet() -> str:
    return "On manufacturing orders, you must set the start date and the due date."


def _traceability_bullet(*, retained: bool, route_mode: str) -> str:
    if route_mode == "buy_only":
        if retained:
            return (
                "Link every accepted Sales Order to the related Purchase Orders for traceability."
            )
        return "Link Sales Orders to the related Purchase Orders for traceability."
    if retained:
        return (
            "Link every accepted Sales Order to the related Manufacturing Orders and Purchase Orders "
            "for traceability."
        )
    return "Link Sales Orders to the related Manufacturing Orders and Purchase Orders for traceability."


def _lineage_bullet(
    *,
    route_mode: str,
    has_subassemblies: bool,
    retained: bool,
) -> str:
    suffix = " for every incoming customer order you accept." if retained else "."
    if route_mode == "buy_only":
        return f"In the end, the lineage must be SO -> PO{suffix}"
    if route_mode == "manufacture_only":
        return f"In the end, the lineage must be SO -> MO -> PO{suffix}"
    if route_mode == "mixed":
        optional_subassembly_segment = " -> (Subassembly MO if needed)" if has_subassemblies else ""
        return (
            "In the end, the lineage must be SO -> MO"
            + optional_subassembly_segment
            + " -> PO or SO -> PO"
            + suffix
        )
    raise RuntimeError(f"Unsupported procurement route mode: {route_mode}")


def _dedupe_bullets(bullets: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for bullet in bullets:
        if bullet in seen:
            continue
        deduped.append(bullet)
        seen.add(bullet)
    return deduped


def _manufacturing_only_policy_bullet(blueprint: ScenarioBlueprint) -> str | None:
    if blueprint.manufacturing_only.family != "policy_forbidden":
        return None
    return _pick_variant(
        blueprint,
        key="manufacturing-only-policy-bullet",
        choices=MANUFACTURING_ONLY_POLICY_BULLET_CHOICES,
    )


def _capacity_policy_section(
    blueprint: ScenarioBlueprint,
    *,
    route_mode: str,
) -> str:
    repair = blueprint.repair_context
    has_finished_goods_procurement = route_mode in {"buy_only", "mixed"} and any(
        vendor.supply_role == "finished" and vendor.product_code == blueprint.product.code
        for vendor in blueprint.vendors
    )
    has_component_procurement = route_mode in {"manufacture_only", "mixed"} and any(
        vendor.supply_role == "component" for vendor in blueprint.vendors
    )
    lines = [
        (
            "Fulfillment constraints for accepted orders:"
            if blueprint.unsat_demand
            else "Capacity constraints:"
        ),
    ]
    if not blueprint.manufactured_products:
        lines.append("- No in-house manufacturing capacity is available.")
    else:
        lines.append(
            "- After an order is accepted, treat workcenter capacity as a hard horizon-wide limit across all products that share the center."
            if blueprint.unsat_demand
            else "- Treat workcenter capacity as a hard horizon-wide limit across all products that share the center."
        )
        lines.append(
            "- Check each workcenter's Internal Notes in Odoo for the exact horizon-wide minute limit."
        )
        lines.append("- Assign workcenter on each manufacturing work order.")
        if repair is not None and repair.kind == "workcenter_outage":
            workcenter_name = blueprint.workcenters_by_code[repair.broken_workcenter_code].name
            lines.append(
                f"- {workcenter_name} is experiencing an equipment outage and must not be used."
            )
    if has_finished_goods_procurement:
        lines.append(
            "- For each finished-goods supplier offer used to fulfill accepted orders, respect min/max quantities as horizon-wide totals."
            if blueprint.unsat_demand
            else "- For each finished-goods supplier offer, respect min/max quantities as horizon-wide totals."
        )
        if repair is not None and repair.kind == "supplier_cancellation":
            lines.append(
                "- That supplier commitment has fallen through and must not be reused."
            )
    if has_component_procurement:
        lines.append(
            "- For each component supplier offer used to fulfill accepted orders, respect min/max quantities as horizon-wide totals."
            if blueprint.unsat_demand
            else "- For each component supplier offer, respect min/max quantities as horizon-wide totals."
        )
    if has_finished_goods_procurement or has_component_procurement:
        lines.append(
            "- Use one consolidated PO per supplier offer when fulfilling accepted orders (do not split a single offer across multiple POs)."
            if blueprint.unsat_demand
            else "- Use one consolidated PO per supplier offer (do not split a single offer across multiple POs)."
        )
        lines.append(
            "- Check each vendor's Internal Notes in Odoo for fulfillment-side maximum order quantity limits."
            if blueprint.unsat_demand
            else "- Check each vendor's Internal Notes in Odoo for maximum order quantity limits."
        )
    return "\n".join(lines)


def _seeded_policy_bullets(blueprint: ScenarioBlueprint, *, route_mode: str) -> list[str]:
    if not any(customer.budget_dollars is not None for customer in blueprint.customers):
        raise RuntimeError("Seeded-order procurement scenarios require customer budgets")
    acceptance_policy = blueprint.unsat_acceptance_policy
    if acceptance_policy is None:
        raise RuntimeError("Seeded-order scenarios require an acceptance policy")
    rules = acceptance_policy.reject_rules
    bullets = [_acceptance_intro_bullet(blueprint)]
    if "budget_below_list_price" in rules:
        bullets.append("Only accept orders whose budgets cover the full list-price total.")
    window_fragments: list[str] = []
    if "quantity_outside_window" in rules:
        window_fragments.append(
            f"request between {acceptance_policy.min_order_quantity} and {acceptance_policy.max_order_quantity} units"
        )
    if "lead_time_below_minimum" in rules:
        window_fragments.append(
            f"allow at least {acceptance_policy.min_lead_time_days} days of lead time"
        )
    if window_fragments:
        bullets.append(f"Accepted orders must {' and '.join(window_fragments)}.")
    bullets.extend(
        [
            "Reject or cancel any order that fails those acceptance rules.",
            _accepted_set_bullet(blueprint),
            _margin_policy_bullet(blueprint),
            _stock_policy_bullet(blueprint),
            "Some sales documents may already exist in draft; review existing documents before creating new ones.",
            "Cancel any already-drafted sales order that fails those acceptance rules.",
            "Customer budgets are pre-tax amounts.",
            _traceability_bullet(retained=True, route_mode=route_mode),
        ]
    )
    return bullets


def _repair_policy_bullets(
    blueprint: ScenarioBlueprint,
    *,
    route_mode: str,
) -> list[str]:
    repair = blueprint.repair_context
    if repair is None:
        raise RuntimeError("Repair policy bullets require repair_context")
    bullets = [
        _objective_bullet(blueprint),
        _margin_policy_bullet(blueprint),
        _stock_policy_bullet(blueprint),
        "The listed customer sales orders are already confirmed in Odoo; work from those orders and do not create duplicates.",
        "Review the current purchase orders, manufacturing orders, and supplier or workcenter notes before making changes.",
        "Keep commitments that still work; only rework the part of the plan affected by the disruption.",
    ]
    if repair.kind == "supplier_cancellation":
        broken_order = next(
            order
            for order in repair.seeded_purchase_orders
            if order.ref in set(repair.broken_purchase_order_refs)
        )
        vendor_name = next(
            vendor.name for vendor in blueprint.vendors if vendor.ref == broken_order.vendor_ref
        )
        bullets.extend(
            [
                f"A confirmed purchase order from {vendor_name} is already in Odoo, but the supplier has canceled on us.",
                "Cancel that purchase order and cover the gap with the best feasible alternative source or fulfillment route without sending the same demand back down that supplier path.",
            ]
        )
    else:
        workcenter = blueprint.workcenters_by_code[repair.broken_workcenter_code]
        bullets.extend(
            [
                f"Confirmed manufacturing work is already scheduled on {workcenter.name}, but that workcenter is experiencing an equipment outage.",
                "Cancel the affected manufacturing orders and cover the gap with the best feasible alternative fulfillment route without using that workcenter.",
            ]
        )
    bullets.append(_traceability_bullet(retained=True, route_mode=route_mode))
    return bullets


def _standard_policy_bullets(blueprint: ScenarioBlueprint) -> list[str]:
    return [
        _objective_bullet(blueprint),
        _margin_policy_bullet(blueprint),
        _stock_policy_bullet(blueprint),
    ]


def _standard_route_intro_bullets(
    blueprint: ScenarioBlueprint,
    *,
    route_mode: str,
) -> list[str]:
    if route_mode == "manufacture_only":
        bullets = [
            "You must create and confirm the necessary sales orders, manufacturing orders, and any component purchase orders needed for assembly."
        ]
    elif route_mode == "mixed":
        bullets = [
            "You must create and confirm the necessary sales orders, purchase orders, and/or manufacturing orders."
        ]
    elif route_mode == "buy_only":
        bullets = ["You must create and confirm the necessary sales orders and purchase orders."]
    else:
        raise RuntimeError(f"Unsupported procurement route mode: {route_mode}")
    if any(customer.budget_dollars is not None for customer in blueprint.customers):
        bullets.append("Customer budgets are pre-tax amounts.")
    bullets.append(_traceability_bullet(retained=False, route_mode=route_mode))
    return bullets


def _manufacture_only_route_bullets(
    *,
    retained: bool,
    has_subassemblies: bool,
    notes_bullet: str,
) -> list[str]:
    return [
        MANUFACTURING_ONLY_COVERAGE_BULLET,
        COMPONENT_PO_ORIGIN_BULLET,
        FINISHED_MO_BULLET,
        _lineage_bullet(
            route_mode="manufacture_only",
            has_subassemblies=has_subassemblies,
            retained=retained,
        ),
        LIST_PRICE_BULLET,
        _sales_order_commitment_bullet(retained=retained),
        _manufacturing_schedule_bullet(),
        PROCURE_COMPONENTS_ONLY_BULLET,
        PURCHASE_ORDER_DELIVERY_DATE_BULLET,
        notes_bullet,
    ]


def _mixed_route_bullets(
    *,
    retained: bool,
    has_subassemblies: bool,
    notes_bullet: str,
) -> list[str]:
    return [
        FINISHED_PO_ORIGIN_BULLET,
        COMPONENT_PO_ORIGIN_BULLET,
        FINISHED_MO_BULLET,
        _lineage_bullet(route_mode="mixed", has_subassemblies=has_subassemblies, retained=retained),
        LIST_PRICE_BULLET,
        _sales_order_commitment_bullet(retained=retained),
        _manufacturing_schedule_bullet(),
        MANUFACTURE_COMPONENTS_IF_CHOSEN_BULLET,
        PURCHASE_ORDER_DELIVERY_DATE_BULLET,
        notes_bullet,
    ]


def _buy_only_route_bullets(
    *,
    retained: bool,
    has_subassemblies: bool,
    notes_bullet: str,
) -> list[str]:
    return [
        FINISHED_PO_ORIGIN_BULLET,
        _lineage_bullet(
            route_mode="buy_only",
            has_subassemblies=has_subassemblies,
            retained=retained,
        ),
        LIST_PRICE_BULLET,
        _sales_order_commitment_bullet(retained=retained),
        PURCHASE_ORDER_DELIVERY_DATE_BULLET,
        notes_bullet,
    ]


def _route_policy_bullets(
    *,
    route_mode: str,
    retained: bool,
    has_subassemblies: bool,
    notes_bullet: str,
) -> list[str]:
    if route_mode == "manufacture_only":
        return _manufacture_only_route_bullets(
            retained=retained,
            has_subassemblies=has_subassemblies,
            notes_bullet=notes_bullet,
        )
    if route_mode == "mixed":
        return _mixed_route_bullets(
            retained=retained,
            has_subassemblies=has_subassemblies,
            notes_bullet=notes_bullet,
        )
    if route_mode == "buy_only":
        return _buy_only_route_bullets(
            retained=retained,
            has_subassemblies=has_subassemblies,
            notes_bullet=notes_bullet,
        )
    raise RuntimeError(f"Unsupported procurement route mode: {route_mode}")


def build_background_and_policy(
    blueprint: ScenarioBlueprint,
) -> str:
    """Render the canonical Background & Policy block for a procurement blueprint."""
    route_mode = _route_mode(blueprint)
    policy_bullet = _manufacturing_only_policy_bullet(blueprint)
    has_subassemblies = any(
        mp.code != blueprint.product.code for mp in blueprint.manufactured_products
    )
    if blueprint.repair_context is not None:
        bullets = _repair_policy_bullets(blueprint, route_mode=route_mode)
        bullets.extend(_invoicing_policy_bullets(blueprint))
        if route_mode == "manufacture_only" and policy_bullet:
            bullets.append(policy_bullet)
        bullets.extend(
            _route_policy_bullets(
                route_mode=route_mode,
                retained=True,
                has_subassemblies=has_subassemblies,
                notes_bullet=_notes_bullet(blueprint),
            )
        )
        if has_subassemblies and FINISHED_MO_BULLET in bullets:
            bullets.insert(bullets.index(FINISHED_MO_BULLET) + 1, SUBASSEMBLY_MO_BULLET)
        deduped_bullets = _dedupe_bullets(bullets)
        background = "".join(f"* {bullet}\n" for bullet in deduped_bullets)
        capacity_section = _capacity_policy_section(
            blueprint,
            route_mode=route_mode,
        )
        return f"{background.rstrip()}\n\n{capacity_section}" if background else capacity_section
    seeded_orders = any(customer.task_order_source == "seeded" for customer in blueprint.customers)
    if seeded_orders:
        bullets = _seeded_policy_bullets(blueprint, route_mode=route_mode)
    else:
        bullets = _standard_policy_bullets(blueprint)
    bullets.extend(_invoicing_policy_bullets(blueprint))
    if route_mode == "manufacture_only" and policy_bullet:
        bullets.append(policy_bullet)
    if not seeded_orders:
        bullets.extend(_standard_route_intro_bullets(blueprint, route_mode=route_mode))
    bullets.extend(
        _route_policy_bullets(
            route_mode=route_mode,
            retained=seeded_orders,
            has_subassemblies=has_subassemblies,
            notes_bullet=_notes_bullet(blueprint),
        )
    )
    if has_subassemblies:
        if FINISHED_MO_BULLET in bullets:
            bullets.insert(bullets.index(FINISHED_MO_BULLET) + 1, SUBASSEMBLY_MO_BULLET)
    deduped_bullets = _dedupe_bullets(bullets)
    background = "".join(f"* {bullet}\n" for bullet in deduped_bullets)
    capacity_section = _capacity_policy_section(
        blueprint,
        route_mode=route_mode,
    )
    return f"{background.rstrip()}\n\n{capacity_section}" if background else capacity_section
