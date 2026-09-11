"""Identity-scoped BM25 knowledge over current, policy-filtered Odoo records.

Ranking is adapted from the bundled MIT-licensed Odoo MCP implementation;
see ``mcp/LICENSE``. Cached snippets are never returned without a current Odoo
read under the same credential, company context, and field policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from odoo_runtime._odoo_core.tool_helpers import (
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
    return _TOKEN_RE.findall(stripped)


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


class KnowledgeStore:
    def __init__(self, max_docs: int | None = None) -> None:
        self.max_docs = max_docs or knowledge_max_docs()
        self._indexes: dict[tuple[str, str], BM25Index] = {}
        self._lock = threading.RLock()

    def _total_docs(self) -> int:
        return sum(len(index.documents) for index in self._indexes.values())

    def index_records(
        self,
        scope: str,
        model: str,
        records: list[dict[str, Any]],
        fields: list[str] | None,
        coverage: dict[str, Any],
        *,
        replace: bool,
    ) -> dict[str, Any]:
        key = (scope, model)
        with self._lock:
            if replace:
                self._indexes.pop(key, None)
            index = self._indexes.setdefault(key, BM25Index())
            indexed = skipped = removed_empty = 0
            for record in records:
                record_id = record.get("id")
                if type(record_id) is not int:
                    continue
                if record_id not in index.documents and self._total_docs() >= self.max_docs:
                    skipped += 1
                    continue
                existed = record_id in index.documents
                if index.add(record, fields):
                    indexed += 1
                elif existed:
                    removed_empty += 1
            index.batches += 1
            index.last_coverage = dict(coverage)
            return {
                "indexed": indexed,
                "removed_empty": removed_empty,
                "skipped_over_budget": skipped,
                "documents_in_index": len(index.documents),
                "max_documents": self.max_docs,
            }

    def candidates(self, scope: str, model: str, query: str, limit: int) -> list[dict[str, Any]] | None:
        with self._lock:
            index = self._indexes.get((scope, model))
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
            rows = [
                {
                    "model": model,
                    "documents": len(index.documents),
                    "batches": index.batches,
                    "last_coverage": index.last_coverage,
                }
                for (candidate_scope, model), index in sorted(self._indexes.items())
                if candidate_scope == scope
            ]
            return {
                "indexes": rows,
                "total_documents": sum(row["documents"] for row in rows),
                "max_documents": self.max_docs,
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
    ) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        resolved_fields = fields
        redacted_fields: set[str] = set()
        response: dict[str, Any] = {"success": True}
        while len(records) < limit:
            page_limit = min(MAX_SEARCH_LIMIT, limit - len(records))
            response = runtime.call(
                "search_records",
                {
                    "model": model,
                    "domain": domain,
                    "fields": fields,
                    "limit": page_limit,
                    "offset": len(records),
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
    ) -> dict[str, Any]:
        validate_model_name(model)
        limit = clamp_limit(limit, maximum=MAX_INDEX_FETCH)
        domain = normalize_domain_input(domain)
        runtime, scope, public_scope = self._scope(instance)
        response = self._paged_records(
            runtime,
            model=model,
            domain=domain,
            fields=fields,
            limit=limit,
        )
        if not response.get("success"):
            raise ValueError(response.get("error", "Knowledge source read failed"))
        records = response["result"]
        resolved_fields = response.get("fields_used")
        coverage = {
            "domain": domain,
            "fields": resolved_fields,
            "fetch_limit": limit,
            "fetched": len(records),
            "possibly_truncated": len(records) >= limit,
            "refreshed_at": _now(),
        }
        outcome = self.store.index_records(
            scope,
            model,
            records,
            resolved_fields,
            coverage,
            replace=replace,
        )
        result = {
            "success": True,
            "tool": "index_knowledge",
            "instance": public_scope["instance"],
            "model": model,
            "scope": public_scope,
            "fetched": len(records),
            "indexed_fields": resolved_fields,
            "coverage": coverage,
            **outcome,
        }
        if response.get("redacted_fields"):
            result["redacted_fields"] = response["redacted_fields"]
        return result

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
        candidate_limit = max(50, limit * 10)
        candidates = self.store.candidates(scope, model, query, candidate_limit)
        if candidates is None:
            return {
                "success": False,
                "tool": "search_knowledge",
                "model": model,
                "scope": public_scope,
                "error": f"No current knowledge index for {model}; run index_knowledge first.",
            }
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
        for field_key, record_ids in groups.items():
            fields = list(field_key) if field_key is not None else None
            current = self._paged_records(
                runtime,
                model=model,
                domain=[["id", "in", record_ids]],
                fields=fields,
                limit=len(record_ids),
            )
            rows = current.get("result") if current.get("success") else []
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
        return {
            "success": True,
            "tool": "search_knowledge",
            "instance": public_scope["instance"],
            "model": model,
            "query": query,
            "results": results,
            "scope": public_scope,
            "freshness": {
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
