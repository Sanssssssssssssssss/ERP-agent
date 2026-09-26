"""Opt-in local Laya publication; the existing model router remains the fallback."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import uuid

from erp_harness.erp.store import ActionStore
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider, _build_chat_payload
from erp_harness.runtime.provider import provider_request_kind


class LayaProvider(OpenAICompatibleProvider):
    """Update the actual turn tool list before building or sending its request."""

    def bind_router(self, controller, store, directory: Path) -> None:
        self.controller, self.store = controller, store
        self.directory = directory / "laya"
        self.directory.mkdir(exist_ok=True)
        self.process = None
        self.stderr = None

    def _record(self, row):
        with (self.directory / "decisions.jsonl").open("a", encoding="utf8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _takeover(self, reason):
        (self.directory / "host-owner.json").write_text(json.dumps({"reason": reason}), encoding="utf8")

    def _hold_reason(self):
        if (self.directory / "host-owner.json").exists():
            return "host_takeover"
        own_log = self.directory / "decisions.jsonl"
        own_ids = {json.loads(line).get("call_id") for line in own_log.read_text(encoding="utf8").splitlines()} if own_log.exists() else set()
        dynamic_log = self.directory.parent / "dynamic-tools.jsonl"
        if dynamic_log.exists():
            for line in dynamic_log.read_text(encoding="utf8").splitlines():
                event = json.loads(line)
                if (event.get("tool") == "configure_odoo_tools" and event.get("event") == "end"
                        and event.get("success") is True and event.get("tool_call_id") not in own_ids):
                    self._takeover("model_configured_capabilities")
                    return "host_takeover"
        # Read the live ledger, not historical approval text in model context.
        if any(row["status"] not in {"verified", "known_failed", "rejected", "expired"}
               for row in ActionStore.read_receipts(self.store.path)):
            return "unresolved_write"
        return None

    async def _decide(self, payload):
        if self.process is None:
            self.stderr = (self.directory / "worker.stderr.log").open("ab")
            # This worker only needs local model files; do not inherit business credentials.
            env = {k: v for k, v in os.environ.items() if not k.startswith(("LLM_", "ODOO_", "COMMAND_CODE_"))}
            self.process = await asyncio.create_subprocess_exec(
                os.environ["ERP_LAYA_PYTHON"], "-u", "-X", "utf8", "-m", "experiments.tool_routing.router",
                "--model", os.environ["ERP_LAYA_MODEL"], "--verify-labels", "--jsonl",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self.stderr, env=env,
            )
        self.process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf8"))
        await self.process.stdin.drain()
        result = json.loads(await self.process.stdout.readline())
        if not isinstance(result, dict):
            raise ValueError("Invalid router response")
        return result

    async def _publish(self, kwargs):
        call_id = "laya:" + uuid.uuid4().hex
        row = {"call_id": call_id, "request_number": getattr(self._config.provider_hooks, "number", 0) + 1}
        try:
            reason = self._hold_reason()
            if reason:
                self._record({**row, "status": "held", "reason": reason})
                return
            payload = _build_chat_payload(
                model=kwargs["model"], system=kwargs["system"], messages=kwargs["messages"], tools=kwargs["tools"],
                compat=self._config.compat, reasoning_effort=self._config.reasoning_effort,
                supports_images=self._config.supports_images, provider=self._config.provider_name, api=self._config.api,
            )
            (self.directory / (call_id.replace(":", "-") + ".request.json")).write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf8")
            decision = await self._decide(payload)
            # Persist provenance before configure so a restart can identify host-origin calls.
            self._record({**row, "event": "decision", **decision})
            if decision.get("status") != "ok":
                self._takeover("router_fallback")
                return
            configure = next(t for t in self.controller.tools if t.name == "configure_odoo_tools")
            result = await configure.execute(call_id, {"capabilities": decision.get("capabilities")})
            if result.details.get("success") is not True:
                self._takeover("publication_rejected")
                self._record({**row, "status": "fallback", "reason": "publication_rejected"})
                return
            # The loop dispatches from this same list. Never replace only the HTTP schema.
            kwargs["tools"][:] = self.controller.tools
            self._record({**row, "status": "applied", "published_tools": [t.name for t in kwargs["tools"]]})
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, StopIteration, sqlite3.Error) as error:
            self._takeover("router_error")
            self._record({**row, "status": "fallback", "error_type": type(error).__name__})

    async def stream_response(self, **kwargs):
        if provider_request_kind.get() == "normal" and kwargs["tools"] and hasattr(self, "controller"):
            await self._publish(kwargs)
        async for event in super().stream_response(**kwargs):
            yield event

    async def aclose(self):
        process = getattr(self, "process", None)
        try:
            if process is not None:
                process.stdin.close()
                await process.wait()
        finally:
            if getattr(self, "stderr", None) is not None:
                self.stderr.close()
            await super().aclose()
