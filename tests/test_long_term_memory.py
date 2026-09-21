"""Scope, provenance, uncertain-write and resume boundaries for optional memory."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from erp_harness.memory.learning import admit, collect_evidence
from erp_harness.memory.recall import SparseRecall
from erp_harness.memory.store import scope_for
from erp_harness.runtime.messages import UserMessage


@pytest.mark.parametrize("mode", [None, "on", "off"])
def test_host_memory_mode_is_opt_in(monkeypatch, mode):
    from erp_harness.app.worker import child_environment

    monkeypatch.delenv("ERP_MEMORY_MODE", raising=False)
    if mode is not None:
        monkeypatch.setenv("ERP_MEMORY_MODE", mode)
    assert child_environment("session", "run")["ERP_MEMORY_MODE"] == (mode or "off")


def test_off_reaches_worker_and_skips_memory_initialization(tmp_path, monkeypatch):
    from erp_harness.app.worker import child_environment
    from erp_harness import memory

    def unexpected(*args, **kwargs):
        pytest.fail("disabled memory must not access identity, storage or learning")

    monkeypatch.setenv("ERP_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setenv("ERP_MEMORY_MODE", "off")
    monkeypatch.setattr(memory, "scope_for", unexpected)
    monkeypatch.setattr(memory, "LongTermMemory", unexpected)
    assert child_environment("session", "run")["ERP_MEMORY_MODE"] == "off"
    assert memory.attach(object(), [], tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_authenticated_company_scope():
    client = SimpleNamespace(
        context={},
        get_user_context=lambda: {"uid": 2},
        read_records=lambda *a, **k: [{"company_id": [1, "one"], "company_ids": [1, 2]}],
    )
    native = SimpleNamespace(
        instance="default",
        instances={"default": SimpleNamespace(client=client)},
        identity_context=lambda: {"database": "a", "credential_hash": "key"},
    )
    first = scope_for(native)
    client.context = {"allowed_company_ids": [2]}
    assert scope_for(native) != first
    client.context = {"allowed_company_ids": [3]}
    with pytest.raises(ValueError):
        scope_for(native)


def test_recall_is_once_per_task_and_survives_resume(tmp_path):
    calls = []

    def search(event):
        calls.append(event)
        return [{"source": "x", "text": "先读库存，再申请写入审批。"}]

    path = tmp_path / "state.json"
    messages = [UserMessage(content="采购业务", timestamp=1)]
    first = SparseRecall("scope", search, path)
    projected = asyncio.run(first.context_transform()(messages, None))
    resumed = SparseRecall("scope", search, path)
    assert asyncio.run(resumed.context_transform()(messages, None)) == projected
    assert len(calls) == 1 and len(projected) == 2
    assert "permission to write" in projected[0].text
    wrong_scope = SparseRecall("another", search, path)
    assert asyncio.run(wrong_scope.context_transform()(messages, None)) == messages


def test_candidates_require_sources_and_observed_recovery():
    sources = [
        {"source": "bad", "success": False, "arguments": {"model": "stock.location"}},
        {"source": "good", "success": True, "arguments": {"model": "stock.location"}},
    ]
    assert not admit([{"memory": "[source=invented] use a field"}], sources)
    assert not admit([{"memory": "[source=bad] fixed"}], sources)
    assert not admit([{"memory": "[source=bad,good] fixed"}], list(reversed(sources)))
    wrong_model = [sources[0], {**sources[1], "arguments": {"model": "res.partner"}}]
    assert not admit([{"memory": "[source=bad,good] fixed"}], wrong_model)
    assert (
        admit([{"memory": "[source=bad,good] Read schema before using fields."}], sources)[0][
            "group"
        ]
        == "error:stock.location"
    )


def test_uncertain_write_blocks_learning_and_reasoning_is_excluded(tmp_path):
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "PRIVATE"},
                {
                    "type": "toolCall",
                    "id": "t1",
                    "name": "write",
                    "arguments": {
                        "model": "res.partner",
                        "values": {"name": "PRIVATE CUSTOMER"},
                        "approval_token": "SECRET",
                    },
                },
            ],
        },
        {
            "role": "toolResult",
            "toolCallId": "t1",
            "content": [
                {"type": "text", "text": json.dumps({"success": True, "action_status": "verified"})}
            ],
        },
        {
            "role": "assistant",
            "stopReason": "stop",
            "content": [{"type": "text", "text": "Invented business outcome"}],
        },
    ]
    session = tmp_path / "session.jsonl"
    session.write_text("\n".join(json.dumps({"message": m}) for m in messages), encoding="utf-8")
    ledger = tmp_path / "action-ledger-summary.json"
    ledger.write_text(json.dumps({"status_counts": {"needs_reconciliation": 1}}), encoding="utf-8")
    assert not collect_evidence(session, tmp_path)["sources"]
    ledger.write_text(json.dumps({"status_counts": {"verified": 1}}), encoding="utf-8")
    evidence = collect_evidence(session, tmp_path)
    assert evidence["sources"][0]["written_fields"] == ["name"]
    assert all(secret not in json.dumps(evidence) for secret in ["PRIVATE", "SECRET", "Invented"])
