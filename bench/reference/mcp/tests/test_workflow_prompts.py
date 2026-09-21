"""Surface and safety tests for the operational workflow prompts."""

import asyncio
import importlib
import inspect

server = importlib.import_module("odoo_mcp.server")

WORKFLOW_PROMPTS = {
    "invoice_approval_chain",
    "po_to_receipt",
    "customer_onboarding",
    "expense_claim_review",
    "accounting_close_checklist",
    "pre_migration_data_quality",
}

# Prompts that actually perform writes must route through the gate.
CRUD_WRITE_BEARING = {"customer_onboarding", "pre_migration_data_quality"}

GATE_TOOLS = ("preview_write", "validate_write", "execute_approved_write")


def _prompt_texts():
    """Render every registered prompt to its text, keyed by name."""
    texts = {}
    for prompt in asyncio.run(server.mcp.list_prompts()):
        fn = getattr(server, f"prompt_{prompt.name}", None)
        if fn is None:
            continue
        # Fill required params with placeholders; optional ones default.
        kwargs = {}
        for pname, param in inspect.signature(fn).parameters.items():
            if param.default is inspect.Parameter.empty:
                kwargs[pname] = "X"
        texts[prompt.name] = fn(**kwargs)
    return texts


def test_prompt_registry_has_eleven():
    names = {p.name for p in asyncio.run(server.mcp.list_prompts())}
    assert WORKFLOW_PROMPTS <= names
    assert len(names) == 11


def test_workflow_prompts_name_their_tools():
    texts = _prompt_texts()
    # CRUD write workflows reference the durable gate.
    for name in CRUD_WRITE_BEARING:
        text = texts[name]
        for tool in GATE_TOOLS:
            assert tool in text, f"{name} missing gate tool {tool}"


def test_workflow_prompts_have_module_guards():
    texts = _prompt_texts()
    # Every workflow prompt must instruct a module/availability check.
    for name in WORKFLOW_PROMPTS:
        text = texts[name].lower()
        assert any(
            marker in text
            for marker in ("business_pack_report", "list_models", "requires")
        ), f"{name} has no module guard"


def test_business_transitions_use_methods_not_state_writes():
    texts = _prompt_texts()
    invoice = texts["invoice_approval_chain"]
    expense = texts["expense_claim_review"]
    assert "account.move.action_post" in invoice
    assert "execute_method" in invoice
    assert "writing state" in invoice
    assert "execute_method" in expense
    assert "Never change expense state with write" in expense


def test_read_only_prompts_do_not_promise_writes():
    texts = _prompt_texts()
    # accounting_close_checklist is strictly read-only.
    close = texts["accounting_close_checklist"].lower()
    assert "read-only" in close
    assert "receivable_payable_aging" in texts["accounting_close_checklist"]
    assert "accounting_health_summary" in texts["accounting_close_checklist"]


def test_customer_onboarding_dedups_first():
    text = server.prompt_customer_onboarding(company_name="Acme")
    assert "search_records" in text
    assert "dedup" in text.lower()
