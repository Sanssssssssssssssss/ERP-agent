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
}
BUSINESS_LABELS = {
    "sale_invoice": "销售与开票", "purchase": "采购", "sale_purchase_invoice": "销售、采购与开票",
    "inventory": "库存收发与退货", "manufacturing": "制造与补货", "payment": "收付款",
    "refund": "退款与贷项", "reconciliation": "银行与账务核销",
}
COMPLETION_TARGETS = ("read_only", "draft", "confirmed", "posted", "done", "reconciled")
ENTERPRISE_TYPES = frozenset({"inventory", "manufacturing", "payment", "refund", "reconciliation"})


def default_target(kind: str) -> str:
    return BUSINESS_TARGETS.get(kind, BUSINESS_TARGETS["sale_invoice"])[-1]


def valid_target(kind, target) -> bool:
    return isinstance(kind, str) and isinstance(target, str) and target in BUSINESS_TARGETS.get(kind, ())
