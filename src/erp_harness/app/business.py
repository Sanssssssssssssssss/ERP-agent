"""桌面业务目标契约。旧会话保留原来的默认值和有效组合。"""

BUSINESS_TARGETS = {
    "sale_invoice": ("read_only", "draft", "confirmed", "posted"),
    "purchase": ("read_only", "draft", "confirmed"),
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
COMPLETION_TARGETS = ("read_only", "draft", "confirmed", "posted", "done", "reconciled", "sent")
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
        "read_only": "Stop after factual reads; do not create or modify records.",
        "draft": "The completion target is draft documents; do not confirm or post them.",
        "posted": "Stop after the requested documents are posted and verified. Invoice delivery is a separate confirmed phase; do not send mail here.",
        "done": "Verify completed stock moves or production, source links and quantities, including partial deliveries and backorders required by the goal.",
        "reconciled": "Verify posted balanced entries, original documents, requested residuals and bank matching. A payment_state of paid alone does not prove bank reconciliation.",
        "sent": "Use mcp_odoo_execute_method on account.move.message_post with kwargs.ids=[invoice_id] and partner_ids=[recipient_id]. Runtime supplies the registered email and official PDF. Generate the PDF first if absent. Stop after the verified SMTP acceptance receipt; do not repeat a sent or uncertain mail action. This does not prove the recipient opened the email.",
    }.get(target, "Verify the requested final state before reporting completion.")
