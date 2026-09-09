"""Bounded host-side exports using authenticated Odoo JSON-2 and portal metadata."""

from __future__ import annotations

import base64
import csv
import io
import urllib.error
import urllib.parse
import urllib.request
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _same_origin(base_url: str, candidate: str) -> bool:
    base, target = urllib.parse.urlsplit(base_url), urllib.parse.urlsplit(candidate)
    return (target.scheme, target.hostname, target.port) == (base.scheme, base.hostname, base.port)


def _has_relation(value: object) -> bool:
    """Accept the JSON-2 shapes used for a non-empty many2one value."""
    if value in (None, False, 0, "", [], ()):
        return False
    if isinstance(value, (list, tuple)):
        return bool(value and value[0])
    return bool(value)


def _portal_pdf(client: object, model: str, record_id: int) -> bytes:
    base_url = str(getattr(client, "url", "")).rstrip("/")
    if not base_url or not hasattr(client, "_json2_call_once"):
        raise ValueError("DOCUMENT_PORTAL_ACCESS_UNAVAILABLE")
    try:
        # This is the official public model method. Odoo may generate portal
        # metadata/token here; it remains inside this host request and is never
        # copied into the returned artifact metadata.
        url = client._json2_call_once(model, "get_portal_url", {"ids": [record_id], "report_type": "pdf", "download": True})
    except Exception as exc:
        if getattr(exc, "status_code", None) in {401, 403}:
            raise ValueError("DOCUMENT_PORTAL_PERMISSION_DENIED") from exc
        raise ValueError("DOCUMENT_PORTAL_ACCESS_UNAVAILABLE") from exc
    if not isinstance(url, str) or not url.startswith(("/", "http://", "https://")):
        raise ValueError("DOCUMENT_PORTAL_ACCESS_UNAVAILABLE")
    url = urllib.parse.urljoin(base_url + "/", url.lstrip("/")) if url.startswith("/") else url
    if not _same_origin(base_url, url):
        raise ValueError("DOCUMENT_PORTAL_ORIGIN_INVALID")
    timeout = float(getattr(client, "timeout", 10) or 10)
    request = urllib.request.Request(url, headers={"Accept": "application/pdf"}, method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=min(max(timeout, 1), 30)) as response:
            if not _same_origin(base_url, response.geturl()):
                raise ValueError("DOCUMENT_PORTAL_ORIGIN_INVALID")
            content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
            data = response.read(MAX_DOCUMENT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ValueError("DOCUMENT_PORTAL_PERMISSION_DENIED") from exc
        raise ValueError("DOCUMENT_REPORT_UNAVAILABLE") from exc
    except urllib.error.URLError as exc:
        raise ValueError("DOCUMENT_REPORT_UNAVAILABLE") from exc
    if content_type not in {"application/pdf", "application/octet-stream"}:
        raise ValueError("DOCUMENT_REPORT_CONTENT_TYPE_INVALID")
    return validate_document_bytes(data, "pdf")


def generate_document_export(native_reads: object, model: str, record_id: int, fmt: str) -> dict[str, str | int]:
    """Fresh-read and export one observed Odoo document using the host credential.

    PDF uses the model's standard portal route and never returns its access token.
    CSV rereads the document and its line model before serialization.  The caller
    remains responsible for business/session scope and artifact registration.
    """
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
        data = _portal_pdf(native_reads.client, model, record_id)
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
