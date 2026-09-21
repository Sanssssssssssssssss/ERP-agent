"""Real runtime boundaries with a deterministic, ACL-aware Odoo read double."""

import copy
import json

import pytest

from erp_harness.erp._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from erp_harness.erp.knowledge import NativeKnowledge, tokenize
from erp_harness.erp.reads import NativeReads


class Odoo:
    url, db, username, lang, transport, uid = "http://fixture", "enterprise", "sales", "en_US", "json2", 2

    def __init__(self, count=3):
        self.context = {"allowed_company_ids": [1]}
        self.credential_scope = "credential-a"
        self.rows = {i: {"id": i, "name": f"ORDER-{i:05d}", "note": "ordinary"} for i in range(1, count + 1)}
        self.calls = []
        self.fail_after = None
        self.after_read = None

    def scope_fingerprint(self):
        return self.credential_scope + json.dumps(self.context, sort_keys=True)

    def get_user_context(self):
        return {"uid": self.uid}

    def get_model_fields(self, _model):
        return {"id": {"type": "integer"}, "name": {"type": "char"}, "note": {"type": "text"}}

    def search_read(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if self.fail_after == 0:
            raise PermissionError("fixture read permission revoked")
        rows = sorted(self.rows.values(), key=lambda row: row["id"])
        for leaf in kwargs.get("domain") or []:
            if not isinstance(leaf, list):
                continue
            field, operator, value = leaf
            if operator == ">":
                if self.fail_after is not None and value >= self.fail_after:
                    raise PermissionError("fixture read permission revoked")
                rows = [row for row in rows if row[field] > value]
            elif operator == "in":
                if self.fail_after == 0:
                    raise PermissionError("fixture read permission revoked")
                rows = [row for row in rows if row[field] in value]
            elif operator == "=":
                rows = [row for row in rows if row[field] == value]
        offset, limit = kwargs.get("offset", 0), kwargs.get("limit", 100)
        fields = kwargs.get("fields")
        result = [{key: value for key, value in row.items() if key == "id" or fields is None or key in fields} for row in rows[offset:offset + limit]]
        if self.after_read is not None:
            callback, self.after_read = self.after_read, None
            callback()
        return copy.deepcopy(result)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ERP_KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    monkeypatch.setenv("ODOO_MCP_RATE_LIMIT_MODE", "off")
    monkeypatch.delenv("ERP_KNOWLEDGE_MAX_DOCS", raising=False)
    client = Odoo()
    knowledge = NativeKnowledge(NativeReads(client))
    return client, knowledge


def refresh(knowledge):
    return knowledge.index_knowledge("res.partner", fields=["name", "note"], full_refresh=True)


def test_full_refresh_reaches_10000_and_reopens_without_model_pagination(setup):
    client, knowledge = setup
    client.rows = Odoo(10000).rows
    client.rows[10000]["name"] = "终页海澜阀门有限公司"
    result = refresh(knowledge)
    assert result["success"] and result["complete"] and result["indexed"] == 10000
    assert result["coverage"]["pages"] == 101
    assert all(call["offset"] == 0 and call["order"] == "id asc" for call in client.calls)
    reopened = NativeKnowledge(NativeReads(client))
    found = reopened.search_knowledge("海澜阀门", "res.partner")
    assert found["results"][0]["record_id"] == 10000
    assert found["results"][0]["revalidated_at"]
    assert not found["complete"]  # Ranked candidates do not assert a complete business answer.


def test_refresh_failure_retains_prior_index_and_marks_stale(setup):
    client, knowledge = setup
    assert refresh(knowledge)["success"]
    before = knowledge.knowledge_stats()["indexes"][0]["last_coverage"]["generation"]
    client.rows = Odoo(201).rows
    client.fail_after = 100
    failed = refresh(knowledge)
    assert not failed["success"] and failed["status"] == "source_read_failed"
    reopened = NativeKnowledge(NativeReads(client))
    stats = reopened.knowledge_stats()["indexes"][0]
    assert stats["documents"] == 3 and stats["last_coverage"]["generation"] == before
    assert stats["last_coverage"]["dirty"]
    blocked = reopened.search_knowledge("ORDER", "res.partner")
    assert not blocked["success"] and blocked["results"] == []


def test_capacity_failure_is_explicit_and_does_not_publish_partial_data(setup, monkeypatch):
    client, knowledge = setup
    assert refresh(knowledge)["success"]
    monkeypatch.setenv("ERP_KNOWLEDGE_MAX_DOCS", "4")
    client.rows = Odoo(5).rows
    result = refresh(knowledge)
    assert not result["success"] and result["status"] == "capacity_exceeded"
    assert knowledge.knowledge_stats()["total_documents"] == 3


def test_write_invalidation_refreshes_new_changed_and_deleted_records_on_search(setup):
    client, knowledge = setup
    assert refresh(knowledge)["success"]
    del client.rows[1]
    client.rows[2]["name"] = "ChangedCurrent"
    client.rows[4] = {"id": 4, "name": "NewCurrent", "note": ""}
    knowledge.invalidate(reason="verified_write")
    reopened = NativeKnowledge(NativeReads(client))
    assert reopened.search_knowledge("NewCurrent", "res.partner")["results"][0]["record_id"] == 4
    assert reopened.search_knowledge("ChangedCurrent", "res.partner")["results"][0]["record_id"] == 2
    assert reopened.knowledge_stats()["total_documents"] == 3
    assert not reopened.knowledge_stats()["indexes"][0]["last_coverage"]["dirty"]


def test_write_during_refresh_prevents_publishing_as_current(setup):
    client, knowledge = setup
    assert refresh(knowledge)["success"]
    client.after_read = lambda: knowledge.invalidate(reason="concurrent_write")
    failed = refresh(knowledge)
    assert not failed["success"] and failed["status"] == "refresh_failed"
    assert knowledge.knowledge_stats()["indexes"][0]["last_coverage"]["dirty"]


def test_mixed_appended_definitions_require_explicit_refresh_after_write(setup):
    client, knowledge = setup
    assert knowledge.index_knowledge("res.partner", domain=[["id", "=", 1]], fields=["name"])["success"]
    assert knowledge.index_knowledge("res.partner", domain=[["id", "in", [2, 3]]], fields=["name"])["success"]
    assert len(knowledge.search_knowledge("ORDER", "res.partner")["results"]) == 3
    knowledge.invalidate(reason="write_after_mixed_append")
    reopened = NativeKnowledge(NativeReads(client))
    before = len(client.calls)
    blocked = reopened.search_knowledge("ORDER", "res.partner")
    assert not blocked["success"] and blocked["status"] == "refresh_required"
    assert blocked["results"] == [] and len(client.calls) == before
    assert reopened.knowledge_stats()["total_documents"] == 3
    assert refresh(reopened)["success"]
    client.rows[4] = {"id": 4, "name": "NewCurrent", "note": ""}
    reopened.invalidate(reason="write_after_explicit_definition")
    assert reopened.search_knowledge("NewCurrent", "res.partner")["results"][0]["record_id"] == 4


def test_cached_snippets_do_not_bypass_permission_failures(setup):
    client, knowledge = setup
    assert refresh(knowledge)["success"]
    client.fail_after = 0
    result = knowledge.search_knowledge("ORDER", "res.partner")
    assert not result["success"] and result["status"] == "source_read_failed"
    assert result["results"] == []
    assert result["freshness"]["errors"]
    unmatched = knowledge.search_knowledge("missing_term", "res.partner")
    assert not unmatched["success"] and unmatched["status"] == "source_read_failed"
    assert unmatched["results"] == []


@pytest.mark.parametrize("change", ["company", "credential", "field_policy"])
def test_scope_change_never_reuses_other_identity_material(setup, change):
    client, knowledge = setup
    assert refresh(knowledge)["success"]
    runtime = knowledge.reads
    if change == "company":
        client.context = {"allowed_company_ids": [2]}
    elif change == "credential":
        client.credential_scope = "credential-b"
    else:
        runtime._policy_override = FieldPolicy({"default": {"res.partner": ModelFieldRule("deny", frozenset({"note"}))}})
    result = knowledge.search_knowledge("ORDER", "res.partner")
    assert not result["success"] and result["status"] == "index_missing"
    assert result["results"] == []


def test_chinese_substring_and_existing_english_id_tokens(setup):
    client, knowledge = setup
    client.rows[1]["name"] = "宁波海澜阀门有限公司"
    assert refresh(knowledge)["success"]
    assert knowledge.search_knowledge("海澜阀门", "res.partner")["results"][0]["record_id"] == 1
    assert tokenize("SO/2026-00001 abc_123") == ["so", "2026", "00001", "abc_123"]
    missing = knowledge.search_knowledge("unmatched_unique_term", "res.partner")
    assert missing["success"] and missing["status"] == "no_candidate_match"
    assert missing["coverage"]["complete"] and not missing["complete"]
