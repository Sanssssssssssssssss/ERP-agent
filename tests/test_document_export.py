import base64
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workbench.document_export import csv_bytes, generate_document_export, result_payload, validate_document_bytes


class DocumentExportTest(unittest.TestCase):
    def test_csv_quotes_and_neutralizes_formula_cells(self):
        data = csv_bytes(
            "sale.order",
            {"name": " =HYPERLINK(\"https://evil.invalid\")", "state": "sale", "partner_id": [4, "Acme"], "amount_total": 10},
            [{"product_id": [8, "Widget"], "name": "a,b", "product_uom_qty": -10}],
        ).decode("utf-8")
        self.assertIn("' =HYPERLINK", data)
        self.assertIn('"a,b"', data)
        self.assertIn("'-10", data)

    def test_pdf_requires_real_signature_and_payload_is_bounded_json_safe(self):
        raw = b"%PDF-1.7\nreal report"
        self.assertEqual(validate_document_bytes(raw, "pdf"), raw)
        payload = result_payload("sale.order", 7, raw, "pdf")
        self.assertEqual(base64.b64decode(payload["data_base64"]), raw)
        self.assertEqual(payload["mime"], "application/pdf")
        with self.assertRaisesRegex(ValueError, "DOCUMENT_PDF_INVALID"):
            validate_document_bytes(b"not a pdf", "pdf")

    def test_model_and_format_boundaries(self):
        with self.assertRaisesRegex(ValueError, "DOCUMENT_MODEL_NOT_ALLOWED"):
            csv_bytes("res.partner", {}, [])
        with self.assertRaisesRegex(ValueError, "DOCUMENT_FORMAT_INVALID"):
            validate_document_bytes(b"x", "exe")

    def test_generate_pdf_uses_same_origin_portal_route_without_returning_token(self):
        seen: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                seen.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.end_headers()
                self.wfile.write(b"%PDF-1.7\nportal report")

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            class Client:
                url = f"http://127.0.0.1:{server.server_port}"
                timeout = 3

                def _json2_call_once(self, model, method, payload):
                    self.called = (model, method, payload)
                    return "/my/orders/9?access_token=opaque-token&report_type=pdf&download=true"

            class Reads:
                client = Client()

                def call(self, name, args):
                    self.assert_name = name
                    self.args = args
                    return {"success": True, "result": {"id": 9}}

            reads = Reads()
            payload = generate_document_export(reads, "sale.order", 9, "pdf")
            self.assertEqual(base64.b64decode(payload["data_base64"]), b"%PDF-1.7\nportal report")
            self.assertIn("report_type=pdf", seen[0])
            self.assertIn("download=true", seen[0])
            self.assertIn("access_token=opaque-token", seen[0])
            self.assertNotIn("opaque-token", str(payload))
            self.assertNotIn("access_token", reads.args["fields"])
            self.assertEqual(reads.client.called, ("sale.order", "get_portal_url", {"ids": [9], "report_type": "pdf", "download": True}))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_generate_csv_rereads_lines_and_never_exports_portal_token(self):
        class Client:
            url = "http://127.0.0.1:1"
            timeout = 1

        class Reads:
            client = Client()

            def __init__(self):
                self.calls = []

            def call(self, name, args):
                self.calls.append((name, args))
                if name == "read_record":
                    return {"success": True, "result": {"id": 3, "name": "PO3", "order_line": [8]}}
                return {"success": True, "result": [{"id": 8, "name": "Widget", "price_unit": 4}]}

        reads = Reads()
        payload = generate_document_export(reads, "purchase.order", 3, "csv")
        csv_text = base64.b64decode(payload["data_base64"]).decode()
        self.assertIn("Widget", csv_text)
        self.assertNotIn("do-not-export", csv_text)
        self.assertEqual([entry[0] for entry in reads.calls], ["read_record", "search_records"])

    def test_posted_customer_invoice_requires_official_generated_pdf(self):
        class Client:
            url = "http://127.0.0.1:1"
            timeout = 1

        class Reads:
            client = Client()

            def call(self, name, args):
                self.args = args
                return {"success": True, "result": {
                    "id": 4, "state": "posted", "move_type": "out_invoice",
                    "invoice_pdf_report_id": False,
                }}

        with self.assertRaisesRegex(ValueError, "DOCUMENT_INVOICE_PDF_NOT_GENERATED"):
            generate_document_export(Reads(), "account.move", 4, "pdf")


if __name__ == "__main__":
    unittest.main()
