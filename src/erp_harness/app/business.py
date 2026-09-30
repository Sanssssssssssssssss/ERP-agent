"""桌面业务目标契约。旧会话保留原来的默认值和有效组合。"""

BUSINESS_TARGETS = {
    "sale_invoice": ("read_only", "draft", "confirmed", "posted"),
    "purchase": ("read_only", "draft", "cancelled", "confirmed"),
    "sale_purchase_invoice": ("read_only", "posted"),
    "inventory": ("read_only", "draft", "confirmed", "done"),
    "manufacturing": ("read_only", "draft", "confirmed", "done"),
    "payment": ("read_only", "draft", "posted", "reconciled"),
    "refund": ("read_only", "draft", "posted", "reconciled"),
    "reconciliation": ("read_only", "reconciled"),
    "invoice_delivery": ("sent",),
}
BUSINESS_LABELS = {
    "sale_invoice": "销售与开票", "purchase": "采购", "sale_purchase_invoice": "销售、采购与开票",
    "inventory": "库存收发与退货", "manufacturing": "制造与补货", "payment": "收付款",
    "refund": "退款与贷项", "reconciliation": "银行与账务核销",
    "invoice_delivery": "发票发送",
}
COMPLETION_TARGETS = ("read_only", "draft", "confirmed", "cancelled", "posted", "done", "reconciled", "sent")
BUSINESS_COMMUNICATION = (
    "面向用户的进度和最终回复一律用简体中文。只说完成事项、单据名称、数量金额、未完成事项及需要用户决定的下一步。"
    "Keep tool names, action IDs, hashes, API steps and internal verification terminology in receipts, not in the customer-facing answer. "
    "Posted invoices are accounting entries, not received money. An ended run is not a completed business. "
    "Matching products/quantities do not prove a source relation; no manufacturing orders does not prove no BOM or manufacturing ability. "
    "Historical purchase prices/dates are not current supplier offers or delivery promises. Recheck copied dates against the user's current request and the business calendar. "
    "Unreserved quantity is not a stock shortage: distinguish on-hand stock, reservation, incoming supply and manufacturing requirements. "
    "Planned arrival is not physical receipt. Ask the user to confirm actual received quantities before registering stock receipt. "
    "Missing published fields are not proof of permission denial. State precisely what could not be read."
)
ENTERPRISE_TYPES = frozenset({"inventory", "manufacturing", "payment", "refund", "reconciliation"})


def default_target(kind: str) -> str:
    return BUSINESS_TARGETS.get(kind, BUSINESS_TARGETS["sale_invoice"])[-1]


def valid_target(kind, target) -> bool:
    return isinstance(kind, str) and isinstance(target, str) and target in BUSINESS_TARGETS.get(kind, ())


def completion_target_instruction(kind: str, target: str) -> str:
    """One phase description for first launch and approval resumption."""
    if target == "confirmed":
        return ("Stop at confirmed records. For sales confirmation do not invoice or deliver goods."
                if kind == "sale_invoice" else "Stop at confirmed records; verify the confirmed state.")
    return {
        "cancelled": "Cancel only the explicitly requested purchase orders through the reviewed button_cancel method and verify cancellation and linked transfers. Received goods or bills require a separate business choice; never write state directly.",
        "read_only": "Stop after factual reads; do not create or modify records.",
        "draft": "The completion target is draft documents; do not confirm or post them.",
        "posted": "Stop after the requested documents are posted and verified. Invoice delivery is a separate confirmed phase; do not send mail here.",
        "done": "Verify completed stock moves or production, source links and quantities, including partial deliveries and backorders required by the goal.",
        "reconciled": "Verify posted balanced entries, original documents, requested residuals and bank matching. A payment_state of paid alone does not prove bank reconciliation.",
        "sent": "Read get_odoo_sop safe_write_review with model=account.move, operation=message_post for the mail drafting format. Use mcp_odoo_execute_method on account.move.message_post with kwargs.ids=[invoice_id], partner_ids=[recipient_id] and your plain-text subject/body reflecting the user's latest requirements. Runtime supplies the registered email and official PDF; the actual draft needs human approval. Generate the PDF first if absent. Stop after the verified SMTP acceptance receipt; do not repeat a sent or uncertain mail action. This does not prove the recipient opened the email.",
    }.get(target, "Verify the requested final state before reporting completion.")
