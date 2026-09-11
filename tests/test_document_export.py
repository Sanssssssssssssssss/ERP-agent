import base64
import unittest

import workbench.document_export as document_export
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

    def test_generate_pdf_without_deterministic_attachment_is_unavailable(self):
        class Reads:
            def __init__(self):
                self.calls = []

            def call(self, name, args):
                self.calls.append((name, args))
                if name == "read_record":
                    return {"success": True, "result": {"id": 9}}
                raise AssertionError("sale.order PDF must not select an arbitrary attachment")

        with self.assertRaisesRegex(ValueError, "DOCUMENT_PDF_UNAVAILABLE"):
            generate_document_export(Reads(), "sale.order", 9, "pdf")

    def test_invoice_attachment_relation_and_cross_record_check(self):
        class Reads:
            def __init__(self, attachment):
                self.attachment = attachment
                self.calls = []

            def call(self, name, args):
                self.calls.append((name, args))
                if name == "read_record":
                    return {"success": True, "result": {"id": 4, "state": "posted", "move_type": "out_invoice", "invoice_pdf_report_id": [41, "invoice.pdf"]}}
                return {"success": True, "attachment": self.attachment, "data_included": True, "data_base64": base64.b64encode(b"%PDF-1.7\ninvoice").decode()}

        reads = Reads({"id": 41, "mimetype": "application/pdf", "res_model": "account.move", "res_id": 4})
        payload = generate_document_export(reads, "account.move", 4, "pdf")
        self.assertEqual(payload["size"], len(b"%PDF-1.7\ninvoice"))
        self.assertEqual([name for name, _ in reads.calls], ["read_record", "read_attachment"])
        self.assertEqual(reads.calls[1][1], {"attachment_id": 41, "include_data": True})

        invalid = [
            ({"id": 42, "mimetype": "application/pdf", "res_model": "account.move", "res_id": 4}, "wrong id"),
            ({"id": 41, "mimetype": "application/pdf", "res_model": "sale.order", "res_id": 4}, "wrong model"),
            ({"id": 41, "mimetype": "text/plain", "res_model": "account.move", "res_id": 4}, "wrong mime"),
        ]
        for attachment, label in invalid:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "DOCUMENT_PDF_UNAVAILABLE"):
                generate_document_export(Reads(attachment), "account.move", 4, "pdf")

        class InvalidDataReads(Reads):
            def __init__(self, result):
                super().__init__({"id": 41, "mimetype": "application/pdf", "res_model": "account.move", "res_id": 4})
                self.result = result

            def call(self, name, args):
                if name == "read_record":
                    return super().call(name, args)
                return {"success": True, "attachment": self.attachment, **self.result}

        invalid_data = [
            ({"data_included": False, "data_base64": ""}, "data omitted"),
            ({"data_included": True, "data_base64": "%%%"}, "invalid base64"),
            ({"data_included": True, "data_base64": base64.b64encode(b"plain").decode()}, "not pdf"),
        ]
        for result, label in invalid_data:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "DOCUMENT_PDF_UNAVAILABLE"):
                generate_document_export(InvalidDataReads(result), "account.move", 4, "pdf")

        old_cap = document_export.MAX_DOCUMENT_BYTES
        document_export.MAX_DOCUMENT_BYTES = 4
        try:
            with self.assertRaisesRegex(ValueError, "DOCUMENT_PDF_UNAVAILABLE"):
                generate_document_export(InvalidDataReads({"data_included": True, "data_base64": base64.b64encode(b"%PDF-1.7").decode()}), "account.move", 4, "pdf")
        finally:
            document_export.MAX_DOCUMENT_BYTES = old_cap

    def test_attachment_permission_failure_does_not_fallback_to_portal(self):
        class Reads:
            def call(self, name, args):
                if name == "read_record":
                    return {"success": True, "result": {"id": 4, "state": "posted", "move_type": "out_invoice", "invoice_pdf_report_id": [41, "invoice.pdf"]}}
                raise PermissionError("attachment denied")

        with self.assertRaisesRegex(ValueError, "DOCUMENT_PDF_UNAVAILABLE"):
            generate_document_export(Reads(), "account.move", 4, "pdf")

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
