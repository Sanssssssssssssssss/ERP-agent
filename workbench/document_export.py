"""Bounded host-side exports using authenticated, read-only Odoo data."""

from __future__ import annotations

import base64
import csv
import io
from collections.abc import Iterable, Mapping

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
_MODEL_NAMES = {"sale.order": "sale-order", "purchase.order": "purchase-order", "account.move": "account-move"}
_FORMULA_PREFIX = "'"
_DOCUMENT_FIELDS = {
    "sale.order": ["id", "name", "date_order", "state", "partner_id", "amount_total", "currency_id", "order_line", "access_url", "access_token"],
    "purchase.order": ["id", "name", "date_order", "state", "partner_id", "amount_total", "currency_id", "order_line", "access_url", "access_token"],
    "account.move": ["id", "name", "invoice_date", "state", "move_type", "partner_id", "amount_total", "currency_id", "invoice_line_ids", "invoice_pdf_report_id", "access_url", "access_token"],
}
_LINE_FIELDS = {
    "sale.order": ("sale.order.line", ["id", "order_id", "product_id", "name", "product_uom_qty", "price_unit", "price_subtotal"]),
    "purchase.order": ("purchase.order.line", ["id", "order_id", "product_id", "name", "product_qty", "price_unit", "price_subtotal"]),
    "account.move": ("account.move.line", ["id", "move_id", "product_id", "name", "quantity", "price_unit", "price_subtotal"]),
}


def _cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return _FORMULA_PREFIX + text
    return text


def csv_bytes(model: str, record: Mapping[str, object], lines: Iterable[Mapping[str, object]]) -> bytes:
    """Create an Excel-friendly one-row-per-document-line CSV."""
    if model not in _MODEL_NAMES:
        raise ValueError("DOCUMENT_MODEL_NOT_ALLOWED")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["单据号", "类型", "状态", "客户/供应商", "日期", "币种", "商品", "说明", "数量", "单价", "税前小计", "单据合计"])
    partner = record.get("partner_id")
    currency = record.get("currency_id")
    partner_name = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else partner
    currency_name = currency[1] if isinstance(currency, (list, tuple)) and len(currency) > 1 else currency
    qty_field = "product_uom_qty" if model == "sale.order" else "product_qty" if model == "purchase.order" else "quantity"
    date = record.get("date_order") if model != "account.move" else record.get("invoice_date")
    line_rows = list(lines)
    if len(line_rows) > 100:
        raise ValueError("DOCUMENT_LINES_TOO_MANY")
    rows = line_rows or [{}]
    for line in rows:
        product = line.get("product_id")
        product_name = product[1] if isinstance(product, (list, tuple)) and len(product) > 1 else product
        writer.writerow([_cell(record.get("name")), _cell(model), _cell(record.get("state")), _cell(partner_name),
                         _cell(date), _cell(currency_name), _cell(product_name), _cell(line.get("name")),
                         _cell(line.get(qty_field)), _cell(line.get("price_unit")), _cell(line.get("price_subtotal")),
                         _cell(record.get("amount_total"))])
    return output.getvalue().encode("utf-8-sig")


def validate_document_bytes(data: bytes, fmt: str) -> bytes:
    """Validate bounded bytes returned by the authenticated host export."""
    if fmt not in {"pdf", "csv"}:
        raise ValueError("DOCUMENT_FORMAT_INVALID")
    if not isinstance(data, bytes) or not data or len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError("DOCUMENT_EXPORT_INVALID")
    if fmt == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("DOCUMENT_PDF_INVALID")
    else:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("DOCUMENT_CSV_INVALID") from exc
    return data


def result_payload(model: str, record_id: int, data: bytes, fmt: str) -> dict[str, str | int]:
    """Return the bounded JSON-safe payload sent from host to desktop main."""
    if type(record_id) is not int or record_id < 1:
        raise ValueError("DOCUMENT_RECORD_INVALID")
    data = validate_document_bytes(data, fmt)
    extension = ".pdf" if fmt == "pdf" else ".csv"
    mime = "application/pdf" if fmt == "pdf" else "text/csv"
    stem = _MODEL_NAMES.get(model)
    if stem is None:
        raise ValueError("DOCUMENT_MODEL_NOT_ALLOWED")
    return {
        "data_base64": base64.b64encode(data).decode("ascii"),
        "name": f"odoo-{stem}-{record_id}{extension}",
        "mime": mime,
        "size": len(data),
    }


def _has_relation(value: object) -> bool:
    """Accept the JSON-2 shapes used for a non-empty many2one value."""
    if value in (None, False, 0, "", [], ()):
        return False
    if isinstance(value, (list, tuple)):
        return bool(value and value[0])
    return bool(value)


def _read_pdf_attachment(native_reads: object, model: str, record_id: int, attachment_id: int | None = None) -> bytes:
    if attachment_id is None:
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE")
    try:
        result = native_reads.call("read_attachment", {"attachment_id": attachment_id, "include_data": True})
    except Exception as exc:
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE") from exc
    if not isinstance(result, Mapping) or result.get("success") is not True:
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE")
    attachment = result.get("attachment")
    if not isinstance(attachment, Mapping) or attachment.get("id") != attachment_id or attachment.get("res_model") != model or attachment.get("res_id") != record_id:
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE")
    if attachment.get("mimetype") != "application/pdf" or result.get("data_included") is not True:
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE")
    encoded = result.get("data_base64")
    if not isinstance(encoded, str):
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE")
    try:
        data = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE") from exc
    try:
        return validate_document_bytes(data, "pdf")
    except ValueError as exc:
        # Do not expose attachment validation details through the public export
        # contract; an unreadable attachment is simply unavailable.
        raise ValueError("DOCUMENT_PDF_UNAVAILABLE") from exc


def generate_document_export(native_reads: object, model: str, record_id: int, fmt: str) -> dict[str, str | int]:
    """Fresh-read and export one observed Odoo document using read-only host calls."""
    if model not in _MODEL_NAMES:
        raise ValueError("DOCUMENT_MODEL_NOT_ALLOWED")
    if type(record_id) is not int or record_id < 1:
        raise ValueError("DOCUMENT_RECORD_INVALID")
    if fmt not in {"pdf", "csv"}:
        raise ValueError("DOCUMENT_FORMAT_INVALID")
    read_fields = [field for field in _DOCUMENT_FIELDS[model] if field not in {"access_url", "access_token"}]
    read = native_reads.call("read_record", {"model": model, "record_id": record_id, "fields": read_fields})
    if not isinstance(read, Mapping) or read.get("success") is not True or not isinstance(read.get("result"), Mapping):
        raise ValueError("DOCUMENT_RECORD_NOT_READ")
    record = dict(read["result"])
    if int(record.get("id") or 0) != record_id:
        raise ValueError("DOCUMENT_RECORD_NOT_READ")
    if fmt == "pdf":
        if (
            model == "account.move"
            and record.get("state") == "posted"
            and record.get("move_type") == "out_invoice"
            and not _has_relation(record.get("invoice_pdf_report_id"))
        ):
            raise ValueError("DOCUMENT_INVOICE_PDF_NOT_GENERATED")
        attachment_id = None
        if model == "account.move":
            relation = record.get("invoice_pdf_report_id")
            if isinstance(relation, (list, tuple)) and relation and type(relation[0]) is int and relation[0] > 0:
                attachment_id = relation[0]
            elif type(relation) is int and relation > 0:
                attachment_id = relation
        data = _read_pdf_attachment(native_reads, model, record_id, attachment_id)
        return result_payload(model, record_id, data, fmt)
    line_model, line_fields = _LINE_FIELDS[model]
    relation = "order_line" if model != "account.move" else "invoice_line_ids"
    line_ids = record.get(relation) or []
    if not isinstance(line_ids, list) or len(line_ids) > 100 or any(type(item) is not int or item < 1 for item in line_ids):
        raise ValueError("DOCUMENT_LINES_NOT_READ")
    lines: list[Mapping[str, object]] = []
    if line_ids:
        result = native_reads.call("search_records", {"model": line_model, "domain": [["id", "in", line_ids]], "fields": line_fields, "limit": 101})
        if not isinstance(result, Mapping) or result.get("success") is not True or not isinstance(result.get("result"), list):
            raise ValueError("DOCUMENT_LINES_NOT_READ")
        lines = [row for row in result["result"] if isinstance(row, Mapping)]
        if len(lines) != len(line_ids):
            raise ValueError("DOCUMENT_LINES_NOT_READ")
    public_record = {key: value for key, value in record.items() if key not in {"access_token", "access_url"}}
    return result_payload(model, record_id, csv_bytes(model, public_record, lines), fmt)
