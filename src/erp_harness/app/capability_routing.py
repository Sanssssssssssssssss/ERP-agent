"""Opt-in capability publication with runtime-owned dependencies and recovery."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from erp_harness.app.model_config import capability_router_config
from erp_harness.erp.store import ActionStore
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider, _build_chat_payload
from erp_harness.runtime.provider import provider_request_kind
from erp_harness.app.request_receipts import routing_decision_id
from erp_harness.runtime.messages import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from erp_harness.tools.dynamic_tools import CAPABILITY_GROUPS, tool_contract_sha256
from erp_harness.tools.sops import SOPS
from erp_harness.app.routing_state import sop_requirements
from erp_harness.app.routing_context import VERSION, assemble_context, host_ledger, selection_batch

ROUTING_CONTROLS = frozenset({"configure_odoo_tools", "list_odoo_capabilities"})


def project_routing_result(name, content):
    """Remove historical publication state, retaining availability and failure evidence."""
    if name not in {"configure_odoo_tools", "list_odoo_capabilities", "recover_capabilities", "diagnose_current_run"}:
        return content
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(value, dict) or value.get("success") is not True:
        return content
    if name == "list_odoo_capabilities" and (not isinstance(value.get("capabilities"), list)
                                             or any(not isinstance(g, dict) for g in value["capabilities"])):
        return content
    if name == "diagnose_current_run":
        if "routing" not in value:
            return content
        value.pop("routing")  # Live state belongs to the current call, never an old diagnosis.
    else:
        for key in ("active", "added", "removed", "published_tools", "tool_contract_sha256",
                    "available_next_turn", "notice"):
            value.pop(key, None)
        for group in value.get("capabilities", []):
            if isinstance(group, dict):
                group.pop("active", None)
        value["publication"] = "Historical receipt; use current request tools for availability."
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class CapabilityRoutingProvider(OpenAICompatibleProvider):
    """Update the actual turn tool list before building or sending its request."""

    def bind_router(self, controller, store, directory: Path, *, goal=None, stage=None, identity=None, world=None) -> None:
        self.selector_config = capability_router_config()
        self.selector = json.loads(Path(self.selector_config).read_text(encoding='utf8')) if self.selector_config else {}
        self.backend = self.selector.get('backend', 'openjev')
        if self.backend not in {'openjev', 'laya'}:
            raise ValueError('Unknown capability selector')
        if self.selector and self.selector.get('contract') != ('host_facts_v1' if self.backend == 'laya' else VERSION):
            raise ValueError('Selector context contract mismatch')
        self.candidate_groups = self.selector.get('candidate_groups', list(CAPABILITY_GROUPS))
        if (not isinstance(self.candidate_groups, list) or not self.candidate_groups
                or any(not isinstance(g, str) or g not in CAPABILITY_GROUPS for g in self.candidate_groups)
                or len(set(self.candidate_groups)) != len(self.candidate_groups)):
            raise ValueError('Invalid selector candidate groups')
        self.controller, self.store = controller, store
        controller.bind_probe_scope(self.candidate_groups, identity)
        self.directory = directory / "routing"
        self.process = None
        self.stderr = None
        self.failed = False
        self.task_goal, self.task_stage, self.identity, self.world = goal, stage, identity, world
        self.routing_state = {"decision_id": None, "status": "not_decided", "reason": None,
                              "proposed": None, "dependencies": [], "recovery": []}
        try:
            self.directory.mkdir(exist_ok=True)
        except OSError:
            self._takeover("routing_directory_unavailable")

    def _record(self, row):
        with (self.directory / "decisions.jsonl").open("a", encoding="utf8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def routing_diagnostic(self):
        """Live publication only; no trace content, business truth or replay authority."""
        return {**self.routing_state, "active": list(self.controller._active),
                "owner": "runtime", "fallback": "registered_catalog" if self.failed else "available",
                "business_truth": False, "automatic_business_retry": False}

    def publication_notice(self):
        return ("\nOnly the tools in this request establish current availability. "
                "Publication grants no execution approval.")

    def project_model_context(self, messages):
        # Keep stored history and the trained selector input intact. Clean only the main-model view.
        projected, count = [], 0
        last_assistant = max((i for i, message in enumerate(messages) if message.role == "assistant"), default=-1)
        for index, message in enumerate(messages):
            if isinstance(message, ToolResultMessage) and message.tool_name in ROUTING_CONTROLS:
                count += 1
                continue
            if isinstance(message, AssistantMessage) and any(c.name in ROUTING_CONTROLS for c in message.tool_calls):
                # Remove obsolete routing calls and their planning. Preserve mixed business calls and visible facts.
                business = [c for c in message.tool_calls if c.name not in ROUTING_CONTROLS]
                content = [c for c in message.content if isinstance(c, TextContent) or
                           isinstance(c, ToolCall) and c.name not in ROUTING_CONTROLS] if business else []
                count += 1
                if not content:
                    continue
                message = message.model_copy(update={"content": content})
            if isinstance(message, ToolResultMessage) and not message.is_error:
                # A just-requested diagnosis must reach the model once before it becomes history.
                content = (message.text if message.tool_name == "diagnose_current_run" and index > last_assistant
                           else project_routing_result(message.tool_name, message.text))
                if content != message.text:
                    message = message.model_copy(update={"content": [TextContent(text=content)],
                                                         "details": json.loads(content)})
                    count += 1
            projected.append(message)
        return projected, count

    def _takeover(self, reason):
        self.failed = True
        try:
            path = self.directory / "host-owner.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps({"reason": reason}), encoding="utf8")
            temporary.replace(path)
        except OSError:
            print("Selector state unavailable; runtime owns fallback publication.", file=sys.stderr)

    def _hold_reason(self, rows=None):
        if self.failed or (self.directory / "host-owner.json").exists():
            return "host_takeover"
        # Read the live ledger, not historical approval text in model context.
        if any(row["status"] not in {"verified", "known_failed", "rejected", "expired"}
               for row in (ActionStore.read_receipts(self.store.path) if rows is None else rows)):
            return "unresolved_write"
        return None

    def _dependencies(self, messages):
        """Trusted SOP receipts and dispatcher rejections; neither authorizes a write."""
        owners = {name: group for group, spec in CAPABILITY_GROUPS.items() for name in spec["tools"]}
        path = self.directory.parent / "sop-events.jsonl"
        # ponytail: Dependencies live for this host-confirmed run; expiry needs verified phase boundaries.
        events = [json.loads(line) for line in path.read_text(encoding='utf8').splitlines()] if path.exists() else []
        groups, evidence = sop_requirements(events, CAPABILITY_GROUPS, SOPS)
        recovery_path = self.directory.parent / 'dynamic-tools.jsonl'
        for line in recovery_path.read_text(encoding='utf8').splitlines() if recovery_path.exists() else []:
            row = json.loads(line)
            if row.get('event') == 'end' and row.get('tool') == 'recover_capabilities' and row.get('success') is True:
                recovered = row.get('recovery_groups')
                if not isinstance(recovered, list) or any(g not in CAPABILITY_GROUPS for g in recovered):
                    raise ValueError('Invalid recovery receipt')
                groups.update(recovered)
                evidence.append({'source': 'executor_recovery', 'tool_call_id': row['tool_call_id'], 'groups': recovered})
        # Only the latest assistant/result block can recover a missing dispatch. No old-run pinning.
        latest = next((m for m in reversed(messages) if isinstance(m, AssistantMessage)), None)
        calls = {c.id: c.name for c in latest.tool_calls} if latest else {}
        recovery = []
        for message in reversed(messages):
            if isinstance(message, AssistantMessage):
                break
            if (isinstance(message, ToolResultMessage) and message.is_error
                    and calls.get(message.tool_call_id) == message.tool_name
                    and message.text == f"Tool {message.tool_name} not found"):
                group = owners.get(message.tool_name.removeprefix("mcp_odoo_"))
                if group and any(t.name == message.tool_name for t in self.controller._all):
                    recovery.append({"source": "dispatch_rejection", "tool_call_id": message.tool_call_id, "groups": [group]})
        return groups, evidence, recovery

    async def _decide(self, payload):
        endpoint = os.environ.get('ERP_SELECTOR_ENDPOINT') if self.backend == 'laya' else None
        if endpoint:
            from erp_harness.providers.selector_service import decide
            endpoint = json.loads(endpoint)
            if endpoint['run_id'] != os.environ.get('HARBOR_TRIAL_ID'):
                raise ValueError('Selector endpoint scope mismatch')
            if endpoint['config_sha256'] != hashlib.sha256(Path(self.selector_config).read_bytes()).hexdigest():
                raise ValueError('Selector configuration changed during the run')
            result = await asyncio.to_thread(decide, endpoint, {**payload, 'run_id': endpoint['run_id']})
        elif self.process is None:
            self.stderr = (self.directory / "worker.stderr.log").open("ab")
            # This worker only needs local model files; do not inherit business credentials.
            env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "ODOO_", "COMMAND_CODE_"))}
            self.process = await asyncio.create_subprocess_exec(
                self.selector['python'], '-P', '-u', '-X', 'utf8',
                str(Path(__file__).parents[1] / ('providers/laya_worker.py' if self.backend == 'laya' else 'providers/openjev_worker.py')),
                self.selector_config, str(self.directory),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.stderr, env=env,
            )
        if not endpoint:
            self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf8"))
            await self.process.stdin.drain()
            result = json.loads(await self.process.stdout.readline())
        if (not isinstance(result, dict) or result.get('id') != payload['id']
                or result.get('scope') != 'capability_publication_only'):
            raise ValueError("Invalid router response")
        if result.get('status') == 'ok':
            detail = result.get('result')
            decisions = detail.get('decisions') if isinstance(detail, dict) else None
            if (not isinstance(decisions, dict) or set(decisions) != set(payload['groups'])
                    or any(not isinstance(d, dict) or d.get('answer') not in ('A', 'B') for d in decisions.values())
                    or result.get('capabilities') != [g for g in payload['groups'] if decisions[g]['answer'] == 'A']):
                raise ValueError('Incomplete or inconsistent selector decisions')
        return result

    async def _publish(self, kwargs, call_id=None):
        started = time.perf_counter()
        call_id = call_id or "selector:" + uuid.uuid4().hex
        row = {"call_id": call_id, "request_number": getattr(self._config.provider_hooks, "number", 0) + 1}
        before = self.controller._active
        additive = self.backend == 'laya'
        row.update(publication_policy='add_until_run_end' if additive else 'replace', retained_before=list(before))
        published = False
        status, reason, proposed = "fallback", None, None
        required, evidence, recovery = set(), [], []
        try:
            ledger_rows = ActionStore.read_receipts(self.store.path)
            reason = self._hold_reason(ledger_rows)
            unresolved = reason == "unresolved_write"
            required, evidence, recovery = self._dependencies(kwargs["messages"])
            required.update(g for item in recovery for g in item["groups"])
            if unresolved:
                required.add("actions")
            row['dependency_ms'] = round((time.perf_counter() - started) * 1000, 2)
            context_started = time.perf_counter()
            payload = _build_chat_payload(
                model=kwargs["model"], system=kwargs["system"], messages=kwargs["messages"], tools=kwargs["tools"],
                compat=self._config.compat, reasoning_effort=self._config.reasoning_effort,
                supports_images=self._config.supports_images, provider=self._config.provider_name, api=self._config.api,
            )
            # A live ledger and World state replace the selector's former transcript truncation.
            if self.backend == 'laya':
                from erp_harness.app.laya_state import build_routing_state, ledger_state
                context = build_routing_state(payload, goal=self.task_goal, stage=self.task_stage,
                    ledger=ledger_state(ledger_rows, identity=self.identity),
                    identity=self.identity, world=self.world, required=required | set(before))
            else:
                context = assemble_context(payload, goal=self.task_goal, stage=self.task_stage,
                    ledger=host_ledger(ledger_rows, self.identity),
                    identity=self.identity, world=self.world, required=required)
            row['context_build_ms'] = round((time.perf_counter() - context_started) * 1000, 2)
            decision = {"status": "fallback", "reason": "host_takeover"}
            if reason != "host_takeover":
                if self.controller._availability is None:
                    await self.controller._list(call_id)
                groups = [g for g in self.candidate_groups
                          if self.controller._availability[g]['status'] != 'module_missing'
                          and (not additive or g not in required | set(before))]
                payload = {'id': call_id.replace(':', '-'), 'groups': groups,
                           'context_version': context['version']}
                payload.update({'state': context} if self.backend == 'laya' else
                               {'messages': selection_batch(context, groups)})
                (self.directory / (call_id.replace(":", "-") + ".request.json")).write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf8")
                try:
                    decision = (await self._decide(payload) if groups else
                                {'status': 'ok', 'capabilities': [], 'inference_skipped': True})
                except (OSError, ValueError, TypeError, KeyError, RuntimeError) as error:
                    decision = {"status": "fallback", "reason": "router_error", "error_type": type(error).__name__}
                self._record({**row, "event": "decision", **decision})
            if decision.get("status") == "ok":
                proposed = decision.get("capabilities")
                if (not isinstance(proposed, list) or any(not isinstance(g, str) or g not in CAPABILITY_GROUPS for g in proposed)
                        or len(proposed) != len(set(proposed))):
                    proposed = None
                    raise ValueError("Invalid capability selection")
                selected = set(proposed)
            else:
                reason = decision.get("reason", "router_fallback")
                selected = set(before)
                if reason != "unstable_capability_selection":
                    self._takeover(reason)
                    if self.controller._availability is None:
                        await self.controller._list(call_id)
                    selected = {g for g in CAPABILITY_GROUPS
                                if self.controller._availability[g]["status"] != "module_missing"}
            selected = sorted(selected | required)
            if additive:
                # A negative decision means no addition, never revoke tools mid-action.
                # Durable run receipts preserve this order across approval resumes.
                selected = [*before, *(g for g in selected if g not in before)]
            if set(selected) == set(self.controller._active):
                status = "unchanged" if decision.get("status") == "ok" else "fallback"
                self._record({**row, "status": status, "dependencies": evidence, "recovery": recovery})
                return
            result = await self.controller.publish(call_id, selected)
            if result.details.get("success") is not True:
                reason = "publication_rejected"
                self._takeover("publication_rejected")
                self._record({**row, "status": "fallback", "reason": "publication_rejected"})
                return
            # The loop dispatches from this same list. Never replace only the HTTP schema.
            kwargs["tools"][:] = self.controller.tools
            published = True
            status = "applied"
            self._record({**row, "status": "applied", "active": selected,
                          "dependencies": evidence, "recovery": recovery,
                          "published_tools": [t.name for t in kwargs["tools"]]})
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, StopIteration, sqlite3.Error) as error:
            status, reason = "fallback", "router_error"
            # Roll back only an uncommitted publication. A diagnostic-log failure must
            # retain the successful dynamic receipt's selection, including on restart.
            if not published and self.controller._active != before:
                self.controller._active = before
                self.controller._publisher(self.controller.tools)
                kwargs["tools"][:] = self.controller.tools
            self._takeover("router_error")
            try:
                self._record({**row, "status": "fallback", "error_type": type(error).__name__})
            except OSError:
                print("Selector receipt unavailable; current tools retained.", file=sys.stderr)
            if self.process is not None and self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        finally:
            self.routing_state = {"decision_id": call_id, "status": status, "reason": reason,
                                  "proposed": proposed, "required_by_runtime": sorted(required),
                                  "publication_policy": row['publication_policy'], "retained_before": list(before),
                                  "dependencies": evidence, "recovery": recovery}
            try:
                self._record({**row, 'event': 'timing', 'elapsed_ms': round((time.perf_counter() - started) * 1000, 2)})
            except OSError:
                self._takeover('routing_timing_unavailable')

    async def stream_response(self, **kwargs):
        call_id = None
        if provider_request_kind.get() == "normal" and kwargs["tools"] and hasattr(self, "controller"):
            call_id = "selector:" + uuid.uuid4().hex
            await self._publish(kwargs, call_id)
            kwargs["messages"], projected = self.project_model_context(kwargs["messages"])
            kwargs["system"] += self.publication_notice()
            try:
                self._record({"event": "publication", "call_id": call_id, **self.routing_diagnostic(),
                              "history_results_projected": projected,
                              "tool_contract_sha256": tool_contract_sha256([
                                  {"name": t.name, "description": t.description, "parameters": dict(t.parameters)}
                                  for t in kwargs["tools"]])})
            except OSError:
                self._takeover("publication_receipt_unavailable")
                self.routing_state.update(status="fallback", reason="publication_receipt_unavailable")
        source = super().stream_response(**kwargs)
        try:
            while True:
                # The runtime can drive each anext in a different cancellation task.
                token = routing_decision_id.set(call_id)
                try:
                    event = await anext(source)
                except StopAsyncIteration:
                    return
                finally:
                    routing_decision_id.reset(token)
                yield event
        finally:
            token = routing_decision_id.set(call_id)
            try:
                await source.aclose()
            finally:
                routing_decision_id.reset(token)

    async def aclose(self):
        process = getattr(self, "process", None)
        try:
            if process is not None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        finally:
            if getattr(self, "stderr", None) is not None:
                self.stderr.close()
            await super().aclose()


# Compatibility for existing experiment imports.
OpenJevProvider = CapabilityRoutingProvider
