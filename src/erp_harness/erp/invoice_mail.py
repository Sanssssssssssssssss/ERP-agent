"""Single-invoice mail contract. Odoo delivers; the ledger binds the evidence."""
from __future__ import annotations

from email.utils import parseaddr, formataddr
import html
import re

from .business_operations import _Evidence
from .write_guards import _id

METHOD = ("account.move", "message_post")
INVOICE_FIELDS = ("name", "state", "move_type", "company_id", "partner_id", "commercial_partner_id",
                  "currency_id", "amount_total", "invoice_pdf_report_id")
CONTACT_FIELDS = ("name", "email", "active", "company_id", "parent_id", "commercial_partner_id", "type", "function")


def parties(g, invoice_id, recipient_id):
    invoice = g.read("account.move", invoice_id, INVOICE_FIELDS)
    recipient = g.read("res.partner", recipient_id, CONTACT_FIELDS)
    validate_parties(invoice, recipient)
    return invoice, recipient


def validate_parties(invoice, recipient):
    if invoice["state"] != "posted" or invoice["move_type"] != "out_invoice":
        raise ValueError("invoice delivery requires a posted customer invoice")
    if not recipient["active"] or not _id(invoice["commercial_partner_id"]) or _id(recipient["commercial_partner_id"]) != _id(invoice["commercial_partner_id"]):
        raise ValueError("recipient must be an active contact of this invoice's commercial customer")
    if _id(recipient["company_id"]) not in {None, _id(invoice["company_id"])}:
        raise ValueError("recipient belongs to a different ERP company")
    if recipient["id"] != _id(invoice["partner_id"]) and recipient["type"] != "invoice":
        raise ValueError("the selected child contact is not a billing contact (type=invoice)")
    email = recipient["email"]
    if not isinstance(email, str) or parseaddr(email)[1] != email or any(c in email for c in "\r\n,; ") or email.count("@") != 1 or not all(email.split("@")):
        raise ValueError("billing contact must have exactly one valid registered email address")


def requested(references):
    invoices = [r for r in references if r["model"] == "account.move" and r.get("purpose", "target") == "target"]
    recipients = [r for r in references if r["model"] == "res.partner" and r.get("purpose") == "recipient"]
    companies = [r for r in references if r["model"] == "res.company" and r.get("purpose", "target") == "target"]
    if len(invoices) != 1 or len(recipients) != 1 or len(companies) != 1:
        raise ValueError("invoice delivery requires exactly one invoice, company and recipient reference")
    invoice, recipient = invoices[0]["fields"], recipients[0]["fields"]
    validate_parties(invoice, recipient)
    if companies[0]["id"] != _id(invoice["company_id"]):
        raise ValueError("invoice company differs from the user-requested company")
    for ref in references:
        if ref["model"] == "res.partner" and ref.get("purpose", "target") == "target" and ref["id"] not in {_id(invoice["partner_id"]), _id(invoice["commercial_partner_id"])}:
            raise ValueError("invoice customer differs from the user-requested customer")
    return invoice["id"], recipient["id"]


def prestate(runtime, payload):
    kwargs = payload.get("kwargs", {})
    if set(kwargs) != {"ids", "partner_ids"} or any(
        not isinstance(kwargs[k], list) or len(kwargs[k]) != 1 or _id(kwargs[k][0]) is None
        for k in ("ids", "partner_ids")
    ):
        raise ValueError("invoice mail accepts only kwargs.ids=[invoice_id] and partner_ids=[billing_contact_id]; runtime supplies email, content and PDF")
    g = _Evidence(runtime, payload)
    invoice, recipient = parties(g, kwargs["ids"][0], kwargs["partner_ids"][0])
    pdf_id = _id(invoice["invoice_pdf_report_id"])
    if pdf_id is None:
        raise ValueError("generate the official invoice PDF first using the approved send wizard with empty sending_methods")
    pdf = g.read("ir.attachment", pdf_id, ("name", "mimetype", "checksum", "file_size", "res_model", "res_id"))
    if pdf["mimetype"] != "application/pdf" or pdf["file_size"] <= 0 or not pdf["checksum"] or pdf["res_model"] != "account.move" or pdf["res_id"] != invoice["id"]:
        raise ValueError("attachment must be this invoice's nonempty official PDF")
    currency = g.read("res.currency", _id(invoice["currency_id"]), ("name",))
    company = g.read("res.company", _id(invoice["company_id"]), ("name", "email"))
    sender = company["email"]
    if not isinstance(sender, str) or parseaddr(sender)[1] != sender or any(c in sender for c in "\r\n,; ") or sender.count("@") != 1:
        raise ValueError("configure one registered sender email on the invoice company before delivery")
    messages = g.find("mail.message", [["model", "=", "account.move"], ["res_id", "=", invoice["id"]]], ("message_type",))
    subject = f"{company['name']} · {invoice['name']}"
    body = f"您好，附件为 {invoice['name']}，金额 {invoice['amount_total']:,.2f} {currency['name']}。请查收。"
    return {"invoice": invoice, "recipient": recipient, "attachment": pdf, "company": company,
            "subject": subject, "body": body, "email_to": recipient["email"], "email_from": formataddr((company["name"], sender)),
            "last_message_id": max((r["id"] for r in messages), default=0)}


def execution_kwargs(payload, evidence):
    # 地址是审批时冻结的登记邮箱；不让 Odoo 再按 partner_id 取一个可能变更的地址。
    return {"ids": payload["kwargs"]["ids"], "partner_ids": [], "outgoing_email_to": evidence["email_to"],
            "subject": evidence["subject"], "body": evidence["body"], "email_from": evidence["email_from"], "message_type": "comment",
            "attachment_ids": [evidence["attachment"]["id"]], "notify_skip_followers": True,
            "mail_auto_delete": False, "force_send": True, "send_after_commit": True,
            "context": {"mail_post_autofollow": False, "mail_notify_force_send": True}}


def verify(runtime, payload, evidence, *, historical=False):
    g = _Evidence(runtime, payload)
    messages = g.find("mail.message", [["model", "=", "account.move"], ["res_id", "=", evidence["invoice"]["id"]],
                      ["id", ">", 0 if historical else evidence["last_message_id"]]],
                     ("subject", "body", "outgoing_email_to", "partner_ids", "attachment_ids", "notification_ids"))
    matches = []
    for msg in messages:
        if msg["subject"] != evidence["subject"] or msg["outgoing_email_to"] != evidence["email_to"] or msg["partner_ids"]:
            continue
        if html.unescape(re.sub(r"<[^>]*>", "", msg["body"])).strip() != evidence["body"]:
            continue
        attachments = [g.read("ir.attachment", i, ("checksum", "file_size", "mimetype")) for i in msg["attachment_ids"]]
        if len(attachments) != 1 or any(attachments[0][k] != evidence["attachment"][k] for k in ("checksum", "file_size", "mimetype")):
            continue
        notifications = [g.read("mail.notification", i, ("notification_type", "notification_status", "mail_email_address", "res_partner_id")) for i in msg["notification_ids"]]
        if len(notifications) != 1 or notifications[0]["notification_type"] != "email" or str(notifications[0]["mail_email_address"]).casefold() != evidence["email_to"].casefold() or notifications[0]["res_partner_id"]:
            continue
        matches.append({"message_id": msg["id"], "notification": notifications[0], "attachments": attachments})
    if historical:
        matches = [m for m in matches if m["notification"]["notification_status"] == "sent"][-1:]
    sent = len(matches) == 1 and matches[0]["notification"]["notification_status"] == "sent"
    return {"status": "satisfied" if sent else "unconfirmed", "evidence": {
        "delivery": "smtp_accepted" if sent else "unconfirmed", "invoice_id": evidence["invoice"]["id"],
        "recipient_id": evidence["recipient"]["id"], "email_to": evidence["email_to"], "messages": matches,
        "notice": "SMTP acceptance does not prove recipient opening or reading."}}
