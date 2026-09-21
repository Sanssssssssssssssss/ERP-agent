"""Identity-scoped BM25 knowledge over current, policy-filtered Odoo records.

Ranking is adapted from the bundled MIT-licensed Odoo MCP implementation;
see ``mcp/LICENSE``. Cached snippets are never returned without a current Odoo
read under the same credential, company context, and field policy.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import unicodedata
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from erp_harness.erp._odoo_core.tool_helpers import (
    MAX_SEARCH_LIMIT,
    clamp_limit,
    normalize_domain_input,
    validate_model_name,
)

from .reads import NativeReads

DEFAULT_KNOWLEDGE_MAX_DOCS = 5000
MAX_INDEX_FETCH = 2000
BM25_K1 = 1.5
BM25_B = 0.75
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]+")
_TAG_RE = re.compile(r"<[^>]+>")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def knowledge_max_docs() -> int:
    raw = os.environ.get("ODOO_MCP_KNOWLEDGE_MAX_DOCS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_KNOWLEDGE_MAX_DOCS
    except ValueError:
        value = DEFAULT_KNOWLEDGE_MAX_DOCS
    return max(1, value)


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    tokens = _TOKEN_RE.findall(stripped)
    # Keep the original token for exact names/IDs; bigrams admit Chinese substrings.
    for run in _CJK_RE.findall(stripped):
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def flatten_record_text(record: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in record.items():
        if key == "id":
            continue
        if isinstance(value, str):
            parts.append(_TAG_RE.sub(" ", value) if "<" in value and ">" in value else value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(str(value))
        elif isinstance(value, (list, tuple)):
            parts.extend(str(item) for item in value if isinstance(item, str))
    return " ".join(parts)


def _record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, default=str).encode()
    ).hexdigest()


@dataclass
class IndexedDocument:
    record_id: int
    text: str
    fields: tuple[str, ...] | None
    source_sha256: str
    indexed_at: str
    revalidated_at: str | None = None
    tokens: Counter[str] = field(default_factory=Counter)
    length: int = 0


class BM25Index:
    def __init__(self) -> None:
        self.documents: dict[int, IndexedDocument] = {}
        self.document_frequency: Counter[str] = Counter()
        self.total_length = 0
        self.batches = 0
        self.last_coverage: dict[str, Any] = {}

    def add(
        self,
        record: dict[str, Any],
        fields: list[str] | None,
        *,
        revalidated: bool = False,
    ) -> bool:
        record_id = record.get("id")
        if type(record_id) is not int:
            return False
        text = flatten_record_text(record)
        if not text.strip():
            self.remove(record_id)
            return False
        previous = self.documents.get(record_id)
        indexed_at = previous.indexed_at if previous else _now()
        self.remove(record_id)
        tokens = Counter(tokenize(text))
        document = IndexedDocument(
            record_id=record_id,
            text=text,
            fields=tuple(fields) if fields is not None else None,
            source_sha256=_record_digest(record),
            indexed_at=indexed_at,
            revalidated_at=_now() if revalidated else None,
            tokens=tokens,
            length=sum(tokens.values()),
        )
        self.documents[record_id] = document
        self.total_length += document.length
        for term in tokens:
            self.document_frequency[term] += 1
        return True

    def remove(self, record_id: int) -> bool:
        document = self.documents.pop(record_id, None)
        if document is None:
            return False
        self.total_length -= document.length
        for term in document.tokens:
            self.document_frequency[term] -= 1
            if self.document_frequency[term] <= 0:
                del self.document_frequency[term]
        return True

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        terms = tokenize(query)
        if not terms or not self.documents:
            return []
        document_count = len(self.documents)
        average_length = self.total_length / document_count
        scores: dict[int, float] = {}
        for term in terms:
            term_frequency = self.document_frequency.get(term, 0)
            if not term_frequency:
                continue
            inverse_frequency = math.log(
                1
                + (document_count - term_frequency + 0.5)
                / (term_frequency + 0.5)
            )
            for record_id, document in self.documents.items():
                frequency = document.tokens.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + BM25_K1 * (
                    1 - BM25_B + BM25_B * document.length / average_length
                )
                scores[record_id] = scores.get(record_id, 0.0) + inverse_frequency * (
                    frequency * (BM25_K1 + 1) / denominator
                )
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [
            {
                "record_id": record_id,
                "score": round(score, 4),
                "snippet": self.documents[record_id].text[:300],
                "source_sha256": self.documents[record_id].source_sha256,
                "indexed_at": self.documents[record_id].indexed_at,
                "revalidated_at": self.documents[record_id].revalidated_at,
            }
            for record_id, score in ranked[: max(1, limit)]
        ]


def bm25_rank_texts(query: str, texts: list[str], limit: int) -> list[dict[str, Any]]:
    """Rank transient text documents with the same BM25 implementation as knowledge search.

    Schema exploration has no durable knowledge index, so these documents are
    deliberately ephemeral and contain field names plus safe metadata labels.
    """
    index = BM25Index()
    for record_id, text in enumerate(texts):
        index.add({"id": record_id, "text": text}, None)
    return index.search(query, limit)


class KnowledgeStore:
    def __init__(self, max_docs: int | None = None) -> None:
        self.max_docs = max_docs or knowledge_max_docs()
        self.explicit_max_docs = max_docs
        self._indexes: dict[tuple[str, str], BM25Index] = {}
        self._lock = threading.RLock()
        directory = os.environ.get("ERP_KNOWLEDGE_DIR")
        self.root = Path(directory).resolve() if directory else None
        self._failed_epoch: str | None = None
        self._memory_epoch = ""

    def epoch(self) -> str:
        if self._failed_epoch is not None:
            return self._failed_epoch
        if self.root is None:
            return self._memory_epoch
        try:
            return (self.root / "invalidation").read_text(encoding="ascii")
        except FileNotFoundError:
            return ""

    @contextmanager
    def _database(self, scope: str):
        if not re.fullmatch(r"[a-f0-9]{64}", scope):
            raise ValueError("Invalid knowledge scope")
        folder = self.root / scope
        folder.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(folder / "knowledge.sqlite3", timeout=30)
        try:
            connection.execute("CREATE TABLE IF NOT EXISTS indexes (model TEXT PRIMARY KEY, coverage TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS documents (model TEXT, id INTEGER, payload TEXT NOT NULL, PRIMARY KEY(model,id))")
            yield connection
        finally:
            connection.close()

    def _load(self, scope: str, model: str) -> BM25Index | None:
        key = (scope, model)
        if self.root is None:
            return self._indexes.get(key)
        with self._database(scope) as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT coverage FROM indexes WHERE model=?", (model,)).fetchone()
            if row is None:
                return None
            coverage = json.loads(row[0])
            if coverage.get("invalidation_epoch", "") != self.epoch():
                coverage.update(dirty=True, dirty_reason="write", complete=False)
            current = self._indexes.get(key)
            if current is not None and current.last_coverage.get("generation") == coverage.get("generation"):
                current.last_coverage = coverage
                return current
            index = BM25Index()
            for (payload,) in connection.execute("SELECT payload FROM documents WHERE model=? ORDER BY id", (model,)):
                values = json.loads(payload)
                values["tokens"] = Counter(values["tokens"])
                document = IndexedDocument(**values)
                index.documents[document.record_id] = document
                index.total_length += document.length
                index.document_frequency.update(document.tokens.keys())
            index.last_coverage = coverage
            index.batches = coverage.get("batches", 1)
            self._indexes[key] = index
            return index

    def _publish(self, scope: str, model: str, index: BM25Index, expected_invalidation: str | None) -> None:
        if self.root is not None:
            with self._database(scope) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                if index.last_coverage.get("invalidation_epoch", "") != self.epoch():
                    raise ValueError("Knowledge changed during refresh; retry from current data")
                previous = connection.execute("SELECT coverage FROM indexes WHERE model=?", (model,)).fetchone()
                if previous and json.loads(previous[0]).get("invalidated_at") != expected_invalidation:
                    raise ValueError("Knowledge changed during refresh; retry from current data")
                connection.execute("DELETE FROM documents WHERE model=?", (model,))
                connection.executemany("INSERT INTO documents VALUES (?,?,?)", (
                    (model, document.record_id, json.dumps(vars(document), ensure_ascii=False))
                    for document in index.documents.values()
                ))
                connection.execute("INSERT OR REPLACE INTO indexes VALUES (?,?)", (model, json.dumps(index.last_coverage)))
        self._indexes[(scope, model)] = index

    def coverage(self, scope: str, model: str) -> dict[str, Any] | None:
        with self._lock:
            index = self._load(scope, model)
            return dict(index.last_coverage) if index is not None else None

    def mark_dirty(self, scope: str, model: str, reason: str) -> None:
        with self._lock:
            coverage = self.coverage(scope, model)
            if coverage is None:
                return
            if self.root is not None:
                with self._database(scope) as connection, connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute("SELECT coverage FROM indexes WHERE model=?", (model,)).fetchone()
                    if row is None:
                        return
                    coverage = json.loads(row[0])
                    coverage.update(dirty=True, dirty_reason=reason, complete=False, invalidated_at=_now())
                    connection.execute("UPDATE indexes SET coverage=? WHERE model=?", (json.dumps(coverage), model))
            else:
                coverage.update(dirty=True, dirty_reason=reason, complete=False, invalidated_at=_now())
            self._indexes[(scope, model)].last_coverage = coverage

    def invalidate(self, reason: str) -> None:
        # One marker invalidates every scope, including indexes held by other workers.
        with self._lock:
            epoch = uuid4().hex
            self._memory_epoch = epoch
            for index in self._indexes.values():
                index.last_coverage.update(dirty=True, dirty_reason=reason, complete=False)
            if self.root is not None:
                self._failed_epoch = epoch
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = self.root / (epoch + ".tmp")
                temporary.write_text(epoch, encoding="ascii")
                temporary.replace(self.root / "invalidation")
                self._failed_epoch = None

    def index_records(
        self,
        scope: str,
        model: str,
        records: list[dict[str, Any]],
        fields: list[str] | None,
        coverage: dict[str, Any],
        *,
        replace: bool,
        capacity: int | None = None,
        expected_invalidation: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            previous = self._load(scope, model)
            if coverage.get("invalidation_epoch", "") != self.epoch():
                raise ValueError("Knowledge changed during refresh; retry from current data")
            if previous is not None and previous.last_coverage.get("invalidated_at") != expected_invalidation:
                raise ValueError("Knowledge changed during refresh; retry from current data")
            index = BM25Index() if replace or previous is None else copy.deepcopy(previous)
            indexed = skipped = removed_empty = 0
            for record in records:
                record_id = record.get("id")
                if type(record_id) is not int:
                    continue
                if record_id not in index.documents and len(index.documents) >= (capacity or self.max_docs):
                    raise ValueError(f"Knowledge capacity exceeded: {capacity or self.max_docs} documents; previous index retained")
                existed = record_id in index.documents
                if index.add(record, fields):
                    indexed += 1
                elif existed:
                    removed_empty += 1
            index.batches += 1
            index.last_coverage = dict(coverage)
            index.last_coverage.update(generation=uuid4().hex, batches=index.batches)
            self._publish(scope, model, index, expected_invalidation)
            return {
                "indexed": indexed,
                "removed_empty": removed_empty,
                "skipped_over_budget": skipped,
                "documents_in_index": len(index.documents),
                "max_documents": capacity or self.max_docs,
            }

    def candidates(self, scope: str, model: str, query: str, limit: int) -> list[dict[str, Any]] | None:
        with self._lock:
            index = self._load(scope, model)
            return None if index is None else index.search(query, limit)

    def fields(self, scope: str, model: str, record_id: int) -> list[str] | None:
        with self._lock:
            document = self._indexes[(scope, model)].documents[record_id]
            return list(document.fields) if document.fields is not None else None

    def revalidate(self, scope: str, model: str, record: dict[str, Any], fields: list[str] | None) -> None:
        with self._lock:
            self._indexes[(scope, model)].add(record, fields, revalidated=True)

    def remove(self, scope: str, model: str, record_id: int) -> None:
        with self._lock:
            index = self._indexes.get((scope, model))
            if index is not None:
                index.remove(record_id)

    def drop_scope(self, scope: str) -> None:
        with self._lock:
            for key in [key for key in self._indexes if key[0] == scope]:
                del self._indexes[key]

    def stats(self, scope: str) -> dict[str, Any]:
        with self._lock:
            if self.root is not None:
                with self._database(scope) as connection:
                    models = [row[0] for row in connection.execute("SELECT model FROM indexes")]
                for model in models:
                    self._load(scope, model)
            rows = [
                {
                    "model": model,
                    "documents": len(index.documents),
                    "batches": index.batches,
                    "last_coverage": index.last_coverage,
                    "max_documents": index.last_coverage.get("capacity_documents", self.max_docs),
                }
                for (candidate_scope, model), index in sorted(self._indexes.items())
                if candidate_scope == scope
            ]
            return {
                "indexes": rows,
                "total_documents": sum(row["documents"] for row in rows),
                "max_documents": max((row["max_documents"] for row in rows), default=self.max_docs),
                "capacity_scope": "per_model_index",
            }


class NativeKnowledge:
    def __init__(self, reads: NativeReads, *, max_docs: int | None = None) -> None:
        self.reads = reads
        self.store = KnowledgeStore(max_docs=max_docs)
        self._active_scopes: dict[str, str] = {}
        self._scope_lock = threading.Lock()

    def _scope(self, instance: str | None) -> tuple[NativeReads, str, dict[str, Any]]:
        name = instance or self.reads.instance
        runtime = self.reads.instances.get(name)
        if runtime is None:
            raise ValueError(f"Unknown Odoo instance {name!r}")
        with runtime._lock:
            runtime._refresh_scope()
            identity = runtime.identity_context()
            client = runtime.client
            authenticated = getattr(client, "get_user_context", None)
            if callable(authenticated):
                context = authenticated()
                if not isinstance(context, dict) or type(context.get("uid")) is not int:
                    raise ValueError("Knowledge requires an authenticated Odoo user")
                identity["uid"] = context["uid"]
                if not identity["context"].get("allowed_company_ids"):
                    user = client.read_records("res.users", [context["uid"]], fields=["company_id"])
                    if not user or not user[0].get("company_id"):
                        raise ValueError("Knowledge requires an authenticated company scope")
                    identity["context"]["allowed_company_ids"] = [user[0]["company_id"][0]]
            scope = hashlib.sha256(
                json.dumps(
                    [identity, runtime._policy_version], sort_keys=True, default=str
                ).encode()
            ).hexdigest()
        with self._scope_lock:
            previous = self._active_scopes.get(name)
            if previous is not None and previous != scope:
                self.store.drop_scope(previous)
            self._active_scopes[name] = scope
        public_scope = {
            "instance": name,
            "database": identity.get("database"),
            "identity_id": identity.get("identity_id"),
            "allowed_company_ids": identity.get("context", {}).get("allowed_company_ids"),
            "scope_sha256": scope,
        }
        return runtime, scope, public_scope

    @staticmethod
    def _paged_records(
        runtime: NativeReads,
        *,
        model: str,
        domain: list[Any],
        fields: list[str] | None,
        limit: int,
        keyset: bool = False,
    ) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        resolved_fields = fields
        redacted_fields: set[str] = set()
        response: dict[str, Any] = {"success": True}
        last_id = 0
        pages = 0
        while len(records) < limit:
            page_limit = min(MAX_SEARCH_LIMIT, limit - len(records))
            response = runtime.call(
                "search_records",
                {
                    "model": model,
                    "domain": domain + ([["id", ">", last_id]] if keyset else []),
                    "fields": fields,
                    "limit": page_limit,
                    "offset": 0 if keyset else len(records),
                    "order": "id asc",
                },
            )
            if not response.get("success"):
                return response
            page = response.get("result")
            if not isinstance(page, list):
                return {
                    "success": False,
                    "error": "Knowledge source returned a non-list result",
                }
            pages += 1
            if keyset and page:
                ids = [row.get("id") for row in page if isinstance(row, dict)]
                if len(ids) != len(page) or any(type(record_id) is not int for record_id in ids) or ids != sorted(set(ids)) or ids[0] <= last_id:
                    return {"success": False, "error": "Knowledge pagination failed to advance unique ordered IDs"}
                last_id = ids[-1]
            records.extend(page)
            page_fields = response.get("fields_used")
            if isinstance(page_fields, list):
                resolved_fields = page_fields
            redacted_fields.update(response.get("redacted_fields") or [])
            if len(page) < page_limit:
                break
        combined = dict(response)
        combined.update(
            {
                "success": True,
                "count": len(records),
                "result": records,
                "fields_used": resolved_fields,
                "pages": pages,
            }
        )
        if redacted_fields:
            combined["redacted_fields"] = sorted(redacted_fields)
        else:
            combined.pop("redacted_fields", None)
        return combined

    def index_knowledge(
        self,
        model: str,
        domain: Any = None,
        fields: list[str] | None = None,
        limit: int = 500,
        replace: bool = False,
        instance: str | None = None,
        full_refresh: bool = False,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if type(full_refresh) is not bool:
            raise ValueError("full_refresh must be boolean")
        capacity = (self.store.explicit_max_docs or max(1, int(os.environ.get("ERP_KNOWLEDGE_MAX_DOCS", "100000")))) if full_refresh else self.store.max_docs
        limit = capacity + 1 if full_refresh else clamp_limit(limit, maximum=MAX_INDEX_FETCH)
        domain = normalize_domain_input(domain)
        runtime, scope, public_scope = self._scope(instance)
        previous_coverage = self.store.coverage(scope, model) or {}
        invalidation_epoch = self.store.epoch()
        response = self._paged_records(
            runtime,
            model=model,
            domain=domain,
            fields=fields,
            limit=limit,
            keyset=full_refresh,
        )
        if not response.get("success"):
            self.store.mark_dirty(scope, model, "refresh_failed")
            return {"success": False, "tool": "index_knowledge", "model": model, "status": "source_read_failed", "complete": False, "error": response.get("error", "Knowledge source read failed"), "coverage": self.store.coverage(scope, model)}
        records = response["result"]
        if len(records) > capacity:
            self.store.mark_dirty(scope, model, "capacity_exceeded")
            return {"success": False, "tool": "index_knowledge", "model": model, "status": "capacity_exceeded", "complete": False, "error": f"Knowledge capacity exceeded: {capacity} documents; previous index retained", "coverage": self.store.coverage(scope, model)}
        if self._scope(instance)[1] != scope:
            self.store.mark_dirty(scope, model, "identity_changed")
            return {"success": False, "status": "identity_changed", "complete": False, "error": "Identity changed during knowledge refresh"}
        resolved_fields = response.get("fields_used")
        coverage = {
            "domain": domain,
            "fields": resolved_fields,
            "fetch_limit": limit,
            "fetched": len(records),
            "possibly_truncated": len(records) >= limit,
            "refreshed_at": _now(),
            "full_refresh": full_refresh,
            "complete": len(records) < limit and not response.get("redacted_fields"),
            "dirty": False,
            "pages": response["pages"],
            "consistency": "visible_scope_paged_reads_not_database_snapshot",
            "invalidation_epoch": invalidation_epoch,
            "capacity_documents": capacity,
        }
        if previous_coverage and not replace and not full_refresh:
            coverage["mixed_append_definitions"] = bool(
                previous_coverage.get("mixed_append_definitions")
                or previous_coverage.get("domain") != domain
                or previous_coverage.get("fields") != resolved_fields
            )
            if coverage["mixed_append_definitions"]:
                coverage["complete"] = False
        try:
            outcome = self.store.index_records(scope, model, records, resolved_fields, coverage,
                                               replace=replace or full_refresh, capacity=capacity,
                                               expected_invalidation=previous_coverage.get("invalidated_at"))
        except (ValueError, sqlite3.Error, OSError) as exc:
            self.store.mark_dirty(scope, model, "refresh_failed")
            return {"success": False, "tool": "index_knowledge", "model": model, "status": "refresh_failed", "complete": False, "error": str(exc), "coverage": self.store.coverage(scope, model)}
        result = {
            "success": True,
            "tool": "index_knowledge",
            "instance": public_scope["instance"],
            "model": model,
            "scope": public_scope,
            "fetched": len(records),
            "indexed_fields": resolved_fields,
            "coverage": coverage,
            "complete": coverage["complete"],
            **outcome,
        }
        if response.get("redacted_fields"):
            result["redacted_fields"] = response["redacted_fields"]
        return result

    def invalidate(self, instance: str | None = None, reason: str = "write") -> None:
        if instance is not None and instance not in self.reads.instances:
            raise ValueError(f"Unknown Odoo instance {instance!r}")
        self.store.invalidate(reason)

    def search_knowledge(
        self,
        query: str,
        model: str,
        limit: int = 5,
        instance: str | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        limit = clamp_limit(limit, maximum=50)
        runtime, scope, public_scope = self._scope(instance)
        coverage = self.store.coverage(scope, model)
        if coverage and coverage.get("dirty"):
            if coverage.get("mixed_append_definitions"):
                return {"success": False, "tool": "search_knowledge", "model": model,
                        "status": "refresh_required", "complete": False, "results": [],
                        "error": "Mixed appended index definitions require an explicit full_refresh with the complete domain and fields",
                        "coverage": coverage, "freshness": {"dirty": True, "complete_current": False}}
            refreshed = self.index_knowledge(model, domain=coverage["domain"], fields=coverage["fields"], instance=instance, full_refresh=True)
            if not refreshed.get("success"):
                return {**refreshed, "tool": "search_knowledge", "results": [], "status": "refresh_required", "freshness": {"dirty": True, "errors": [refreshed.get("error")]}}
            coverage = self.store.coverage(scope, model)
        candidate_limit = max(50, limit * 10)
        candidates = self.store.candidates(scope, model, query, candidate_limit)
        if candidates is None:
            return {
                "success": False,
                "tool": "search_knowledge",
                "model": model,
                "scope": public_scope,
                "error": f"No current knowledge index for {model}; run index_knowledge first.",
                "status": "index_missing", "complete": False, "results": [],
            }
        if not candidates:
            probe = self._paged_records(runtime, model=model, domain=coverage.get("domain", []), fields=["id"], limit=1)
            if not probe.get("success"):
                return {"success": False, "tool": "search_knowledge", "status": "source_read_failed", "complete": False, "results": [], "error": probe.get("error"), "coverage": coverage}
        groups: dict[tuple[str, ...] | None, list[int]] = {}
        for candidate in candidates:
            record_id = candidate["record_id"]
            fields = self.store.fields(scope, model, record_id)
            groups.setdefault(tuple(fields) if fields is not None else None, []).append(
                record_id
            )

        revalidated: set[int] = set()
        stale_removed = 0
        errors: list[dict[str, Any]] = []
        failed_read = False
        for field_key, record_ids in groups.items():
            fields = list(field_key) if field_key is not None else None
            current = self._paged_records(
                runtime,
                model=model,
                domain=(coverage.get("domain", []) if coverage and coverage.get("full_refresh") else []) + [["id", "in", record_ids]],
                fields=fields,
                limit=len(record_ids),
            )
            rows = current.get("result") if current.get("success") else []
            failed_read = failed_read or not current.get("success")
            by_id = {
                row["id"]: row
                for row in rows
                if isinstance(row, dict) and type(row.get("id")) is int
            }
            for record_id in record_ids:
                record = by_id.get(record_id)
                if record is None:
                    self.store.remove(scope, model, record_id)
                    stale_removed += 1
                    errors.append(
                        {
                            "record_id": record_id,
                            "error": current.get(
                                "error", "record missing or no longer readable"
                            ),
                        }
                    )
                    continue
                self.store.revalidate(scope, model, record, fields)
                if flatten_record_text(record).strip():
                    revalidated.add(record_id)
                else:
                    stale_removed += 1
        reranked = self.store.candidates(scope, model, query, candidate_limit) or []
        results = [row for row in reranked if row["record_id"] in revalidated][:limit]
        if self._scope(instance)[1] != scope:
            return {"success": False, "tool": "search_knowledge", "status": "identity_changed", "complete": False, "results": [], "error": "Identity changed during candidate revalidation"}
        if failed_read:
            results = []
        return {
            "success": not failed_read,
            "tool": "search_knowledge",
            "instance": public_scope["instance"],
            "model": model,
            "query": query,
            "results": results,
            "scope": public_scope,
            "status": "source_read_failed" if failed_read else "candidates" if results else "no_candidate_match",
            "complete": False,
            "coverage": coverage,
            "freshness": {
                "complete_current": False,
                "external_changes": "explicit_full_refresh_required",
                "candidate_records_revalidated": len(revalidated),
                "stale_records_removed": stale_removed,
                "errors": errors,
                "new_or_newly_matching_records_require_reindex": True,
            },
        }

    def knowledge_stats(self) -> dict[str, Any]:
        _, scope, public_scope = self._scope(None)
        stats = self.store.stats(scope)
        for row in stats["indexes"]:
            row["key"] = f"{public_scope['instance']}:{row['model']}"
        return {
            "success": True,
            "tool": "knowledge_stats",
            "scope": public_scope,
            **stats,
        }
