"""Native Odoo diagnostic, business, fan-out, and background capabilities."""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import cache, partial
from pathlib import Path
from typing import Any, get_type_hints

from odoo_mcp.access_helpers import (
    _access_diagnosis_codes,
    _acl_row_applies,
    _available_user_read_fields,
    _field_names,
    _group_field_names,
    _m2m_ids,
    _record_id_domain,
    _rule_applies,
    _safe_odoo_read,
    access_permission_field,
)
from odoo_mcp.accounting_tools import (
    MAX_AGING_LINES,
    build_aging_report,
    build_unreconciled_summary,
    fetch_aging_lines,
    parse_as_of,
)
from odoo_mcp.agent_tools import (
    build_domain_report,
    lookup_model_history_report,
    scan_addons_source_report,
)
from odoo_mcp.agent_tools import (
    business_pack_report as build_business_pack_report,
)
from odoo_mcp.cross_instance import (
    DEFAULT_LIMIT_PER_INSTANCE,
    MAX_LIMIT_PER_INSTANCE,
    combine_aggregate_rows,
    combine_bucket_reports,
    envelope,
    parse_instances_meta,
    select_instances,
    tag_and_merge,
)
from odoo_mcp.data_quality import build_data_quality_report
from odoo_mcp.diagnostics import (
    analyze_upgrade_log_report,
    classify_access_error,
    diagnose_odoo_call_report,
    generate_json2_payload_report,
    inspect_model_relationships_report,
)
from odoo_mcp.diagnostics import (
    fit_gap_report as build_fit_gap_report,
)
from odoo_mcp.diagnostics import (
    upgrade_risk_report as build_upgrade_risk_report,
)
from odoo_mcp.odoo_client import list_configured_instances
from odoo_mcp.tool_helpers import (
    clamp_limit,
    normalize_domain_input,
    parse_measure_spec,
    validate_model_name,
)
from pydantic import ConfigDict, create_model

from .knowledge import NativeKnowledge
from .reads import NativeReads

CAPABILITY_TOOLS = frozenset(
    {
        "receivable_payable_aging",
        "accounting_health_summary",
        "submit_async_task",
        "get_async_task",
        "cancel_async_task",
        "list_async_tasks",
        "search_across_instances",
        "aggregate_across_instances",
        "accounting_health_across_instances",
        "data_quality_report",
        "diagnose_odoo_call",
        "generate_json2_payload",
        "inspect_model_relationships",
        "diagnose_access",
        "upgrade_risk_report",
        "analyze_upgrade_log",
        "lookup_model_history",
        "fit_gap_report",
        "scan_addons_source",
        "build_domain",
        "business_pack_report",
        "index_knowledge",
        "search_knowledge",
        "knowledge_stats",
    }
)

ASYNC_OPERATIONS = frozenset(
    {
        "data_quality_report",
        "scan_addons_source",
        "receivable_payable_aging",
        "search_across_instances",
        "aggregate_across_instances",
        "accounting_health_across_instances",
        "index_knowledge",
    }
)


class _PolicyReadClient:
    """Client-shaped adapter that keeps pure helpers behind native field policy."""

    def __init__(self, runtime: NativeReads):
        self.runtime = runtime
        self.uid = getattr(runtime.client, "uid", None)

    def _locked(self, fn: Callable[[], Any]) -> Any:
        with self.runtime._lock:
            self.runtime._refresh_scope()
            return fn()

    def get_model_fields(self, model: str) -> dict[str, Any]:
        validate_model_name(model)

        def read() -> dict[str, Any]:
            fields = self.runtime._metadata(model)
            allowed, _ = self.runtime.policy.filter_fields(
                self.runtime.instance, model, fields
            )
            return {name: fields[name] for name in allowed}

        return self._locked(read)

    def get_models(self) -> dict[str, Any]:
        def read() -> dict[str, Any]:
            self.runtime._require_fields("ir.model", ["model", "name"])
            return self.runtime.client.get_models()

        return self._locked(read)

    def get_installed_modules(self, limit: int = 200) -> list[dict[str, Any]]:
        result = self._locked(
            lambda: self.runtime.get_odoo_profile(
                include_modules=True, module_limit=min(limit, 500)
            )
        )
        return list(result.get("profile", {}).get("installed_modules") or [])

    def get_user_context(self) -> dict[str, Any]:
        return self._locked(lambda: self.runtime.client.get_user_context())

    def search_read(
        self,
        model_name: str | None = None,
        domain: Any = None,
        fields: list[str] | None = None,
        offset: int = 0,
        limit: int = 100,
        order: str | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        model = model_name or kwargs.pop("model", None)
        if kwargs or not isinstance(model, str):
            raise ValueError("Unsupported native search_read arguments")
        if limit < 1 or offset < 0:
            raise ValueError(
                "search_read limit must be positive and offset non-negative"
            )
        if fields:
            self._locked(lambda: self.runtime._require_fields(model, fields))
        rows: list[dict[str, Any]] = []
        while len(rows) < limit:
            page_limit = min(100, limit - len(rows))
            result = self._locked(
                partial(
                    self.runtime.search_records,
                    model=model,
                    domain=domain,
                    fields=fields,
                    limit=page_limit,
                    offset=offset + len(rows),
                    order=order,
                )
            )
            page = result.get("result", [])
            if not isinstance(page, list):
                raise TypeError("Invalid native search_read result")
            rows.extend(page)
            if len(page) < page_limit:
                break
        return rows

    def execute_method(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        validate_model_name(model)
        if method == "search_read":
            domain = args[0] if args else kwargs.pop("domain", None)
            return self.search_read(model, domain, **kwargs)
        if method == "fields_get":
            return self.get_model_fields(model)
        if method == "context_get" and model == "res.users":
            return self.get_user_context()

        def read() -> Any:
            if method in {"search", "search_count"}:
                domain = normalize_domain_input(
                    args[0] if args else kwargs.get("domain")
                )
                self.runtime._query_policy(model, domain, kwargs.get("order"))
                return self.runtime.client.execute_method(
                    model,
                    method,
                    domain,
                    **{key: value for key, value in kwargs.items() if key != "domain"},
                )
            if method == "read":
                ids = args[0] if args else kwargs.pop("ids", None)
                fields = kwargs.get("fields")
                resolved = self.runtime._fields(model, fields)
                if resolved:
                    self.runtime._require_fields(model, resolved)
                rows = self.runtime.client.execute_method(
                    model, method, ids, **{**kwargs, "fields": resolved}
                )
                redacted, _ = self.runtime.policy.redact_records(
                    self.runtime.instance, model, rows or []
                )
                return redacted
            if method in {"read_group", "formatted_read_group"}:
                domain = normalize_domain_input(
                    args[0] if args else kwargs.get("domain")
                )
                measures = (
                    args[1]
                    if len(args) > 1
                    else kwargs.get(
                        "fields" if method == "read_group" else "aggregates", []
                    )
                )
                groups = args[2] if len(args) > 2 else kwargs.get("groupby", [])
                referenced = [
                    str(value).split(":", 1)[0]
                    for value in [*(groups or []), *(measures or [])]
                ]
                blocked = self.runtime.policy.check_aggregate(
                    self.runtime.instance, model, referenced
                )
                if blocked:
                    raise ValueError(blocked)
                self.runtime._query_policy(
                    model, domain, kwargs.get("order") or kwargs.get("orderby")
                )
                return self.runtime.client.execute_method(
                    model, method, *args, **kwargs
                )
            raise ValueError(f"Native capability gateway refuses {model}.{method}")

        return self._locked(read)


class _TaskStore:
    """Small persistent queue: interrupted work is observable, never resumed blindly."""

    def __init__(self, path: Path, max_workers: int = 2, max_tasks: int = 50):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="odoo-native-task"
        )
        self.max_tasks = max_tasks
        with self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS capability_tasks (
                    task_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, started_at REAL, finished_at REAL,
                    result_json TEXT, error TEXT
                )
            """)
            self._db.execute(
                "UPDATE capability_tasks SET status='interrupted', finished_at=?, "
                "error='Process ended before task completion' WHERE status IN ('pending','running')",
                (time.time(),),
            )

    def _purge_locked(self) -> None:
        self._db.execute(
            "DELETE FROM capability_tasks WHERE finished_at IS NOT NULL AND finished_at < ?",
            (time.time() - 3600,),
        )
        stale = self._db.execute(
            "SELECT task_id FROM capability_tasks WHERE status NOT IN ('pending','running') "
            "ORDER BY created_at DESC, task_id DESC LIMIT -1 OFFSET ?",
            (self.max_tasks,),
        ).fetchall()
        if stale:
            self._db.executemany(
                "DELETE FROM capability_tasks WHERE task_id=?",
                [(row["task_id"],) for row in stale],
            )

    @staticmethod
    def _snapshot(row: sqlite3.Row, include_result: bool = True) -> dict[str, Any]:
        result = {
            key: row[key]
            for key in (
                "task_id",
                "name",
                "status",
                "created_at",
                "started_at",
                "finished_at",
                "error",
            )
        }
        if include_result and row["status"] == "succeeded":
            result["result"] = json.loads(row["result_json"])
        return result

    def _row(self, task_id: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM capability_tasks WHERE task_id=?", (task_id,)
        ).fetchone()

    def submit(self, name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock, self._db:
            self._purge_locked()
            live = self._db.execute(
                "SELECT count(*) FROM capability_tasks WHERE status IN ('pending','running')"
            ).fetchone()[0]
            if live >= self.max_tasks:
                return {"success": False, "error": f"Too many live tasks ({live})"}
            task_id = uuid.uuid4().hex[:12]
            created = time.time()
            self._db.execute(
                "INSERT INTO capability_tasks(task_id,name,status,created_at) VALUES(?,?,?,?)",
                (task_id, name, "pending", created),
            )

        def run() -> None:
            with self._lock, self._db:
                row = self._row(task_id)
                if row is None or row["status"] == "cancelled":
                    return
                self._db.execute(
                    "UPDATE capability_tasks SET status='running', started_at=? WHERE task_id=?",
                    (time.time(), task_id),
                )
            try:
                result, error = fn(), None
            except Exception as exc:  # noqa: BLE001 - returned to task poller
                result, error = None, f"{type(exc).__name__}: {exc}"
            with self._lock, self._db:
                row = self._row(task_id)
                if row is not None and row["status"] == "running":
                    self._db.execute(
                        "UPDATE capability_tasks SET status=?, finished_at=?, result_json=?, error=? WHERE task_id=?",
                        (
                            "failed" if error else "succeeded",
                            time.time(),
                            None if result is None else json.dumps(result, default=str),
                            error,
                            task_id,
                        ),
                    )
                    self._purge_locked()

        self._executor.submit(run)
        return {"success": True, **self.status(task_id, include_result=False)}

    def status(self, task_id: str, include_result: bool = True) -> dict[str, Any]:
        with self._lock:
            row = self._row(task_id)
            if row is None:
                return {"success": False, "error": f"Unknown task_id: {task_id}"}
            return {"success": True, **self._snapshot(row, include_result)}

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock, self._db:
            row = self._row(task_id)
            if row is None:
                return {"success": False, "error": f"Unknown task_id: {task_id}"}
            if row["status"] not in {"pending", "running"}:
                return {"success": False, "error": f"Task already {row['status']}"}
            note = (
                "Cancelled before start."
                if row["status"] == "pending"
                else "Marked cancelled; the running worker may finish but its result is discarded."
            )
            self._db.execute(
                "UPDATE capability_tasks SET status='cancelled', finished_at=? WHERE task_id=?",
                (time.time(), task_id),
            )
            return {
                "success": True,
                "task_id": task_id,
                "status": "cancelled",
                "note": note,
            }

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._db:
            self._purge_locked()
            rows = self._db.execute(
                "SELECT * FROM capability_tasks ORDER BY created_at DESC, task_id DESC LIMIT ?",
                (self.max_tasks,),
            ).fetchall()
            return [self._snapshot(row, include_result=False) for row in rows]

    def close(self) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE capability_tasks SET status='interrupted', finished_at=?, "
                "error='Session ended before task completion' WHERE status IN ('pending','running')",
                (time.time(),),
            )
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._db.close()


@cache
def _arguments_model(name: str):
    function = getattr(NativeCapabilities, name)
    hints = get_type_hints(function)
    fields = {
        key: (
            hints[key],
            ... if param.default is inspect.Parameter.empty else param.default,
        )
        for key, param in inspect.signature(function).parameters.items()
        if key != "self"
    }
    return create_model(
        f"{name}CapabilityArguments", __config__=ConfigDict(extra="forbid"), **fields
    )


def normalize_capability_arguments(
    name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    parsed = dict(arguments)
    model = _arguments_model(name)
    for key, field in model.model_fields.items():
        value = parsed.get(key)
        if field.annotation is not str and isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if decoded is None or isinstance(decoded, (list, dict)):
                parsed[key] = decoded
    try:
        return model.model_validate(parsed).model_dump()
    except ValueError as exc:
        raise RuntimeError(f"Error executing tool {name}: {exc}") from exc


class NativeCapabilities:
    def __init__(self, reads: NativeReads, *, task_path: Path):
        self.reads = reads
        self.tasks = _TaskStore(task_path)
        self.knowledge = NativeKnowledge(reads)

    def close(self) -> None:
        self.tasks.close()

    def _runtime(self, instance: str | None = None) -> NativeReads:
        name = instance or self.reads.instance
        if name not in self.reads.instances:
            raise ValueError(
                f"Unknown Odoo instance {name!r}; available: {sorted(self.reads.instances)}"
            )
        return self.reads.instances[name]

    def _client(self, instance: str | None = None) -> _PolicyReadClient:
        return _PolicyReadClient(self._runtime(instance))

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in CAPABILITY_TOOLS:
            raise ValueError(f"Not a native capability tool: {name}")
        try:
            result = getattr(self, name)(
                **normalize_capability_arguments(name, arguments)
            )
            if not isinstance(result, dict):
                raise TypeError("Native capability returned a non-object")
            return result
        except Exception as exc:  # noqa: BLE001 - preserve MCP's error envelope
            return {"success": False, "tool": name, "error": str(exc)}

    def receivable_payable_aging(
        self,
        direction: str = "receivable",
        as_of: str | None = None,
        top_partners: int = 15,
        limit: int = MAX_AGING_LINES,
        instance: str | None = None,
    ) -> dict[str, Any]:
        if direction not in {"receivable", "payable"}:
            raise ValueError("direction must be 'receivable' or 'payable'")
        top_partners = clamp_limit(top_partners, maximum=100)
        limit = clamp_limit(limit, maximum=MAX_AGING_LINES)
        lines = fetch_aging_lines(self._client(instance), direction, limit)
        report = build_aging_report(lines, direction, parse_as_of(as_of), top_partners)
        if len(lines) >= limit:
            report["truncated"] = (
                f"Line fetch hit the {limit} cap; totals may be partial. Narrow the scope or raise limit."
            )
        return {"success": True, "tool": "receivable_payable_aging", **report}

    def accounting_health_summary(self, instance: str | None = None) -> dict[str, Any]:
        return {
            "success": True,
            "tool": "accounting_health_summary",
            **build_unreconciled_summary(self._client(instance)),
        }

    def index_knowledge(
        self,
        model: str,
        domain: Any = None,
        fields: list[str] | None = None,
        limit: int = 500,
        replace: bool = False,
        instance: str | None = None,
    ) -> dict[str, Any]:
        return self.knowledge.index_knowledge(
            model, domain, fields, limit, replace, instance
        )

    def search_knowledge(
        self,
        query: str,
        model: str,
        limit: int = 5,
        instance: str | None = None,
    ) -> dict[str, Any]:
        return self.knowledge.search_knowledge(query, model, limit, instance)

    def knowledge_stats(self) -> dict[str, Any]:
        return self.knowledge.knowledge_stats()

    def submit_async_task(
        self,
        operation: str,
        params: dict[str, Any] | None = None,
        instance: str | None = None,
    ) -> dict[str, Any]:
        if operation not in ASYNC_OPERATIONS:
            raise ValueError(
                f"Unknown native operation {operation!r}; allowed: {sorted(ASYNC_OPERATIONS)}"
            )
        arguments = dict(params or {})
        if instance is not None and operation not in {
            "search_across_instances",
            "aggregate_across_instances",
            "accounting_health_across_instances",
            "scan_addons_source",
        }:
            arguments["instance"] = instance
        submitted = self.tasks.submit(
            operation, lambda: self.call(operation, arguments)
        )
        return {"tool": "submit_async_task", **submitted}

    def get_async_task(self, task_id: str) -> dict[str, Any]:
        return {"tool": "get_async_task", **self.tasks.status(task_id)}

    def cancel_async_task(self, task_id: str) -> dict[str, Any]:
        return {"tool": "cancel_async_task", **self.tasks.cancel(task_id)}

    def list_async_tasks(self) -> dict[str, Any]:
        return {"success": True, "tool": "list_async_tasks", "tasks": self.tasks.list()}

    def _selection(self, requested: Any):
        configured = list_configured_instances()
        # Tests and explicit runtimes need not duplicate environment configuration.
        for name in self.reads.instances:
            configured.setdefault(name, {"tags": [], "cross_instance": True})
        return select_instances(requested, parse_instances_meta(configured))

    @staticmethod
    def _fan_out(selected: list[str], worker: Callable[[str], Any]):
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(selected)))) as pool:
            futures = {pool.submit(worker, name): name for name in selected}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as exc:  # noqa: BLE001 - partial failure is the contract
                    errors[name] = f"{type(exc).__name__}: {exc}"
        return results, errors

    def search_across_instances(
        self,
        model: str,
        domain: Any = None,
        fields: list[str] | None = None,
        limit_per_instance: int = DEFAULT_LIMIT_PER_INSTANCE,
        instances: Any = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        domain = normalize_domain_input(domain)
        limit = clamp_limit(limit_per_instance, maximum=MAX_LIMIT_PER_INSTANCE)
        selection = self._selection(instances)
        results, errors = self._fan_out(
            selection.selected,
            lambda name: self._client(name).search_read(
                model, domain, fields=fields, limit=limit
            ),
        )
        payload = envelope(
            {name: {"count": len(rows)} for name, rows in results.items()},
            errors,
            selection,
        )
        payload.update(
            model=model,
            merged=tag_and_merge(results),
            merged_count=sum(map(len, results.values())),
        )
        return {"tool": "search_across_instances", **payload}

    def aggregate_across_instances(
        self,
        model: str,
        group_by: list[str],
        measures: list[str] | None = None,
        domain: Any = None,
        instances: Any = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if not group_by:
            raise ValueError("group_by must include at least one field")
        domain = normalize_domain_input(domain)
        parsed = [parse_measure_spec(spec) for spec in measures or []]
        normalized = [f"{field}:{agg}" for field, agg in parsed]
        selection = self._selection(instances)
        results, errors = self._fan_out(
            selection.selected,
            lambda name: self._client(name).execute_method(
                model, "read_group", domain, normalized, group_by
            ),
        )
        payload = envelope(results, errors, selection)
        payload.update(
            model=model,
            **combine_aggregate_rows(results, [field for field, _ in parsed]),
        )
        return {"tool": "aggregate_across_instances", **payload}

    def accounting_health_across_instances(
        self,
        direction: str = "receivable",
        as_of: str | None = None,
        top_partners: int = 10,
        instances: Any = None,
    ) -> dict[str, Any]:
        if direction not in {"receivable", "payable"}:
            raise ValueError("direction must be 'receivable' or 'payable'")
        day = parse_as_of(as_of)
        top_partners = clamp_limit(top_partners, maximum=100)
        selection = self._selection(instances)
        results, errors = self._fan_out(
            selection.selected,
            lambda name: build_aging_report(
                fetch_aging_lines(self._client(name), direction),
                direction,
                day,
                top_partners,
            ),
        )
        payload = envelope(results, errors, selection)
        payload.update(
            direction=direction,
            as_of=day.isoformat(),
            **combine_bucket_reports(results),
        )
        return {"tool": "accounting_health_across_instances", **payload}

    def data_quality_report(
        self,
        model: str,
        checks: list[str] | None = None,
        key_fields: list[str] | None = None,
        sample_limit: int = 500,
        instance: str | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        runtime = self._runtime(instance)
        return build_data_quality_report(
            self._client(runtime.instance),
            runtime.instance,
            model,
            checks,
            key_fields,
            clamp_limit(sample_limit, maximum=2000),
        )

    def diagnose_odoo_call(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        transport: str = "auto",
        target_version: str | None = None,
        observed_error: Any = None,
        include_debug: bool = False,
        metadata: dict[str, Any] | None = None,
        use_live_metadata: bool = False,
    ) -> dict[str, Any]:
        report = diagnose_odoo_call_report(
            model=model,
            method=method,
            args=args,
            kwargs=kwargs,
            transport=transport,
            target_version=target_version,
            observed_error=observed_error,
            include_debug=include_debug,
            metadata=metadata,
        )
        if use_live_metadata:
            report["issues"].append(
                {
                    "code": "live_metadata_not_used",
                    "severity": "info",
                    "message": "diagnose_odoo_call is preview-only; use inspect_model_relationships for live metadata.",
                }
            )
        return report

    def generate_json2_payload(
        self,
        model: str,
        method: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        base_url: str | None = None,
        database: str | None = None,
        include_database_header: bool = True,
    ) -> dict[str, Any]:
        return generate_json2_payload_report(
            model=model,
            method=method,
            args=args,
            kwargs=kwargs,
            base_url=base_url,
            database=database,
            include_database_header=include_database_header,
        )

    def inspect_model_relationships(
        self,
        model: str,
        fields_metadata: dict[str, Any] | None = None,
        include_readonly: bool = True,
        include_computed: bool = True,
        use_live_metadata: bool = True,
        instance: str | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        source, error = (
            ("input", None) if fields_metadata is not None else ("none", None)
        )
        if fields_metadata is None and use_live_metadata:
            source = "server"
            try:
                fields_metadata = self._client(instance).get_model_fields(model)
            except Exception as exc:  # noqa: BLE001 - report preserves uncertainty
                fields_metadata, error = None, str(exc)
        return inspect_model_relationships_report(
            model=model,
            fields_metadata=fields_metadata,
            metadata_source=source,
            metadata_error=error,
            include_readonly=include_readonly,
            include_computed=include_computed,
        )

    def diagnose_access(
        self,
        model: str,
        operation: str = "read",
        domain: Any = None,
        record_ids: list[int] | None = None,
        expected_count: int | None = None,
        include_rules: bool = True,
        observed_error: Any = None,
        limit: int = 50,
        instance: str | None = None,
    ) -> dict[str, Any]:
        validate_model_name(model)
        if expected_count is not None and expected_count < 0:
            raise ValueError("expected_count must be greater than or equal to 0")
        client = self._client(instance)
        limit = clamp_limit(limit, maximum=500)
        ids = [int(value) for value in record_ids or [] if int(value) > 0]
        normalized_domain = normalize_domain_input(domain)
        permission = access_permission_field(operation)
        errors: list[dict[str, Any]] = []

        def safe(label: str, fn: Callable[[], Any], default: Any):
            value, error = _safe_odoo_read(label, fn)
            if error:
                errors.append(error)
                return default
            return value

        model_rows = safe(
            "ir.model",
            lambda: client.execute_method(
                "ir.model",
                "search_read",
                [["model", "=", model]],
                fields=["id", "name", "model"],
                limit=1,
            ),
            [],
        )
        model_record = (
            model_rows[0] if isinstance(model_rows, list) and model_rows else None
        )
        model_id = (
            int(model_record["id"])
            if isinstance(model_record, dict) and model_record.get("id")
            else None
        )
        context = safe("res.users.context_get", client.get_user_context, {})
        uid = client.uid or (context.get("uid") if isinstance(context, dict) else None)
        current_user: dict[str, Any] = {
            "uid": uid,
            "context": context if isinstance(context, dict) else {},
            "record": None,
            "group_ids": None,
            "direct_group_ids": None,
            "group_field": None,
            "all_group_field": None,
        }
        user_group_ids: set[int] | None = None
        if isinstance(uid, int) and uid > 0:
            metadata = safe(
                "res.users.fields_get", lambda: client.get_model_fields("res.users"), {}
            )
            rows = safe(
                "res.users.read",
                lambda: client.execute_method(
                    "res.users",
                    "read",
                    [uid],
                    fields=_available_user_read_fields(_field_names(metadata)),
                ),
                [],
            )
            if rows:
                current_user["record"] = rows[0]
                direct, all_groups = _group_field_names(rows[0])
                current_user["group_field"], current_user["all_group_field"] = (
                    direct,
                    all_groups,
                )
                direct_ids = _m2m_ids(rows[0].get(direct)) if direct else set()
                all_ids = _m2m_ids(rows[0].get(all_groups)) if all_groups else set()
                user_group_ids = all_ids or direct_ids
                current_user["group_ids"] = sorted(user_group_ids)
                current_user["direct_group_ids"] = sorted(direct_ids)

        acl_rows: list[dict[str, Any]] = []
        if model_id is not None:
            acl_rows = safe(
                "ir.model.access",
                lambda: client.execute_method(
                    "ir.model.access",
                    "search_read",
                    [["model_id", "=", model_id]],
                    fields=[
                        "id",
                        "name",
                        "model_id",
                        "group_id",
                        "perm_read",
                        "perm_write",
                        "perm_create",
                        "perm_unlink",
                    ],
                    limit=limit,
                ),
                [],
            )
        active: list[dict[str, Any]] = []
        if include_rules and model_id is not None:
            rules = safe(
                "ir.rule",
                lambda: client.execute_method(
                    "ir.rule",
                    "search_read",
                    [["model_id", "=", model_id]],
                    fields=[
                        "id",
                        "name",
                        "model_id",
                        "domain_force",
                        "groups",
                        "active",
                        "perm_read",
                        "perm_write",
                        "perm_create",
                        "perm_unlink",
                    ],
                    limit=limit,
                ),
                [],
            )
            active = [
                row
                for row in rules
                if isinstance(row, dict)
                and row.get("active", True)
                and row.get(permission, True)
            ]
        global_rules = [row for row in active if not _m2m_ids(row.get("groups"))]
        group_rules = [row for row in active if _m2m_ids(row.get("groups"))]
        applicable = [row for row in active if _rule_applies(row, user_group_ids)]
        actual_count = None
        if expected_count is not None or ids:
            value = safe(
                f"{model}.search_count",
                lambda: client.execute_method(
                    model,
                    "search_count",
                    _record_id_domain(ids) if ids else normalized_domain,
                ),
                None,
            )
            actual_count = value if isinstance(value, int) else None
        granting = [
            row
            for row in acl_rows
            if row.get(permission) and _acl_row_applies(row, user_group_ids)
        ]
        codes = _access_diagnosis_codes(
            metadata_errors=errors,
            acl_rows=acl_rows,
            granting_acl_rows=granting,
            active_rules=active,
            applicable_rules=applicable,
            actual_count=actual_count,
            expected_count=expected_count,
            record_ids=ids,
        )
        return {
            "success": True,
            "tool": "diagnose_access",
            "model": model,
            "operation": operation,
            "permission_field": permission,
            "domain": normalized_domain,
            "record_ids": ids,
            "expected_count": expected_count,
            "actual_count": actual_count,
            "model_metadata": {"record": model_record},
            "current_user": current_user,
            "access": {
                "rows": acl_rows,
                "granting_rows": granting,
                "granting_count": len(granting),
            },
            "rules": {
                "included": include_rules,
                "active": active,
                "global": global_rules,
                "group_bound": group_rules,
                "applicable": applicable,
            },
            "diagnosis": {"codes": codes},
            "error_classification": classify_access_error(observed_error),
            "metadata_errors": errors,
            "metadata_used": {
                "live_odoo": True,
                "acl": bool(acl_rows),
                "rules": include_rules,
                "current_user": current_user["record"] is not None,
                "sudo": False,
                "impersonation": False,
            },
        }

    def upgrade_risk_report(
        self,
        source_version: str | None = None,
        target_version: str | None = None,
        modules: list[dict[str, Any]] | None = None,
        methods: list[dict[str, Any]] | None = None,
        source_findings: list[dict[str, Any]] | None = None,
        observed_errors: list[Any] | None = None,
        use_live_metadata: bool = False,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        report = build_upgrade_risk_report(
            source_version=source_version,
            target_version=target_version,
            modules=modules,
            methods=methods,
            source_findings=source_findings,
            observed_errors=observed_errors,
            include_debug=include_debug,
        )
        if use_live_metadata:
            report["risks"].append(
                {
                    "code": "live_metadata_not_used",
                    "severity": "info",
                    "evidence": "upgrade_risk_report is input-driven in this release.",
                    "recommendation": "Pass module/method/source findings explicitly.",
                }
            )
        return report

    def analyze_upgrade_log(
        self,
        log_text: str,
        source_version: str | None = None,
        target_version: str | None = None,
    ) -> dict[str, Any]:
        return analyze_upgrade_log_report(
            log_text, source_version=source_version, target_version=target_version
        )

    def lookup_model_history(self, name: str) -> dict[str, Any]:
        return lookup_model_history_report(name)

    def fit_gap_report(
        self,
        requirements: list[Any],
        available_models: list[str] | None = None,
        available_fields: dict[str, Any] | None = None,
        installed_modules: list[Any] | None = None,
        business_context: dict[str, Any] | None = None,
        use_live_metadata: bool = False,
    ) -> dict[str, Any]:
        report = build_fit_gap_report(
            requirements=requirements,
            available_models=available_models,
            available_fields=available_fields,
            installed_modules=installed_modules,
            business_context=business_context,
        )
        if use_live_metadata:
            report["assumptions"].append(
                "fit_gap_report is input-driven; use native schema tools first."
            )
        return report

    @staticmethod
    def _restrict_addons_paths(addons_paths: list[str] | None) -> list[str] | None:
        if addons_paths is None:
            return None
        roots = [
            Path(value).expanduser().resolve(strict=False)
            for value in os.environ.get("ODOO_ADDONS_PATHS", "").split(os.pathsep)
            if value
        ]
        if not roots:
            raise ValueError(
                "scan_addons_source requires ODOO_ADDONS_PATHS when addons_paths are provided"
            )
        result = []
        for value in addons_paths:
            candidate = Path(value).expanduser().resolve(strict=False)
            if not any(
                candidate == root or candidate.is_relative_to(root) for root in roots
            ):
                raise ValueError(
                    f"{candidate} is outside configured ODOO_ADDONS_PATHS roots"
                )
            result.append(str(candidate))
        return result

    def scan_addons_source(
        self,
        addons_paths: list[str] | None = None,
        max_files: int = 200,
        max_file_bytes: int = 300_000,
    ) -> dict[str, Any]:
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be greater than 0")
        return scan_addons_source_report(
            addons_paths=self._restrict_addons_paths(addons_paths),
            max_files=clamp_limit(max_files, maximum=1000),
            max_file_bytes=max_file_bytes,
        )

    def build_domain(
        self,
        conditions: list[dict[str, Any]],
        logical_operator: str = "and",
        fields_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return build_domain_report(
            conditions=conditions,
            logical_operator=logical_operator,
            fields_metadata=fields_metadata,
        )

    def business_pack_report(
        self,
        pack: str,
        use_live_metadata: bool = True,
        instance: str | None = None,
    ) -> dict[str, Any]:
        models = modules = None
        if use_live_metadata:
            client = self._client(instance)
            models = client.get_models().get("model_names", [])
            modules = [
                str(row.get("name"))
                for row in client.get_installed_modules(200)
                if row.get("name")
            ]
        return build_business_pack_report(
            pack=pack, available_models=models, installed_modules=modules
        )
