"""Exactly one provider POST per frozen case/arm. Returned tools are never run."""
import argparse
import asyncio
import copy
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

import httpx

from erp_harness.app.request_receipts import RequestReceipts
from erp_harness.providers.env import OpenAICompatibleConfig
from erp_harness.providers.openai_compatible import OpenAICompatibleProvider
from erp_harness.runtime.provider import provider_request_kind

from .cases import DEFAULT
from .freeze import canonical, digest, read, verify, write_once
from .oracle import evaluate

ENDPOINT = "https://api.commandcode.ai/provider/v1"


class RecordedStream(httpx.AsyncByteStream):
    def __init__(self, source, path):
        self.source, self.path = source, path

    async def __aiter__(self):
        with self.path.open("xb") as target:
            async for chunk in self.source:
                target.write(chunk)
                target.flush()
                yield chunk

    async def aclose(self):
        await self.source.aclose()


class OnePost(httpx.AsyncBaseTransport):
    """Count at the transport boundary; observer errors cannot bypass this gate."""
    def __init__(self, inner, payload, directory):
        self.inner, self.payload, self.directory = inner, payload, Path(directory)
        self.posts = 0

    async def handle_async_request(self, request):
        if self.posts or request.method != "POST" or str(request.url) != ENDPOINT + "/chat/completions":
            raise RuntimeError("Only one POST to the frozen provider endpoint is permitted")
        if json.loads(request.content) != self.payload:
            raise RuntimeError("Actual provider payload differs from prepared payload")
        self.posts += 1
        # Written before transport: a crash makes this attempt unknown, never replayable.
        write_once(self.directory / "transport.json", {"posts": self.posts,
                   "attempted_at": datetime.now(UTC).isoformat(), "payload_sha256": digest(canonical(self.payload))})
        (self.directory / "wire.request.json").write_bytes(request.content)
        response = await self.inner.handle_async_request(request)
        if response.is_stream_consumed:
            (self.directory / "response.sse").write_bytes(response.content)
        else:
            response.stream = RecordedStream(response.stream, self.directory / "response.sse")
        return response

    async def aclose(self):
        await self.inner.aclose()


class FrozenReceipts(RequestReceipts):
    def __init__(self, directory, payload):
        super().__init__(directory, max_model_requests=1)
        self.payload = payload

    async def before_provider_request(self, _payload):
        return await super().before_provider_request(copy.deepcopy(self.payload))


async def complete(payload, directory, *, api_key, transport=None):
    """Uses the production stream parser; no agent loop, tools, or Odoo client."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "started.json").open("x", encoding="utf8") as stream:
        json.dump({"at": datetime.now(UTC).isoformat(), "payload_sha256": digest(canonical(payload))}, stream)
    gate = OnePost(transport or httpx.AsyncHTTPTransport(retries=0), payload, directory)
    hooks = FrozenReceipts(directory / "requests", payload)
    config = OpenAICompatibleConfig(api_key=api_key, base_url=ENDPOINT, max_retries=0,
                                   timeout_seconds=None, provider_hooks=hooks, infer_api_from_model=False)
    output = {"text": "", "reasoning": "", "tool_calls": [], "usage": None,
              "stop_reason": "unknown", "error": None}
    started = monotonic()
    request_kind = provider_request_kind.set("normal")
    try:
        async with httpx.AsyncClient(transport=gate, timeout=None, follow_redirects=False, trust_env=False) as client:
            provider = OpenAICompatibleProvider(config, client=client)
            async for event in provider.stream_response(model=payload["model"], system="", messages=[], tools=[]):
                if event.type in {"done", "error"}:
                    message = getattr(event, "message", None) or getattr(event, "error", None)
                    output.update(text=message.text, reasoning=message.thinking_text, stop_reason=message.stop_reason,
                                  error=message.error_message, usage=RequestReceipts._usage(message),
                                  tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls])
    except BaseException as exc:
        # Keep the uncertain attempt marker even on Ctrl+C/cancellation.
        output.update(error=type(exc).__name__, stop_reason="aborted")
        raise
    finally:
        provider_request_kind.reset(request_kind)
        # The parser normalizes absent usage to zeros. Preserve missing billing evidence.
        usages = []
        response_path = directory / "response.sse"
        if response_path.exists():
            for line in response_path.read_text(encoding="utf8", errors="replace").splitlines():
                if line.startswith("data:"):
                    try:
                        row = json.loads(line[5:].strip())
                    except (ValueError, TypeError):
                        continue
                    if isinstance(row, dict) and isinstance(row.get("usage"), dict) and row["usage"]:
                        usages.append(row["usage"])
        output["provider_usage"] = usages
        if not usages:
            output["usage"] = None
        output.update(posts=gate.posts, duration_ms=(monotonic() - started) * 1000, executed_tools=0, compaction_requests=0)
        write_once(directory / "output.json", output)
    return output


def summary(directory=DEFAULT):
    directory = Path(directory)
    frozen = verify(directory)
    rows = []
    for case in frozen["cases"]:
        for arm in frozen["arms"]:
            path = directory / "results" / case["id"] / arm
            if not (path / "started.json").exists():
                continue
            output = read(path / "output.json") if (path / "output.json").exists() else {}
            verdict = read(path / "verdict.json") if (path / "verdict.json").exists() else {}
            usage = output.get("usage") or {}
            rows.append({"case": case["id"], "arm": arm, "status": verdict.get("status", "inconclusive"),
                         "requests": output.get("posts"), "tool_intents": len(output.get("tool_calls", [])),
                         "executed_tools": 0, "duration_ms": output.get("duration_ms"),
                         **{key: usage.get(field) for key, field in {"fresh": "input", "cache": "cache_read",
                            "output_including_reasoning": "output", "reasoning_subset": "reasoning"}.items()}})
    result = {"rows": rows, "compaction_requests": 0, "tool_execution": False,
              "meaning": "One next decision, not final ERP state. Text/semantic review is separate; reasoning is included in output.",
              "totals": {}}
    for arm in frozen["arms"]:
        selected = [r for r in rows if r["arm"] == arm]
        result["totals"][arm] = {"cases_started": len(selected), "metrics": {
            key: {"known_sum": sum(r[key] for r in selected if r[key] is not None),
                  "unknown_cases": sum(r[key] is None for r in selected)}
            for key in ("requests", "tool_intents", "fresh", "cache", "output_including_reasoning", "reasoning_subset", "duration_ms")}}
    # This is a derived view; immutable raw requests/results remain authoritative.
    (directory / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    return result


async def run(directory=DEFAULT, *, cases=None, groups=None, arms=("baseline", "candidate"), api_key=None):
    directory = Path(directory)
    frozen = verify(directory)
    selected = cases or [x["id"] for x in frozen["cases"]]
    allowed = {x["id"] for x in frozen["cases"]}
    if not set(selected) <= allowed:
        raise ValueError("Unknown case")
    if groups:
        selected = [case for case in selected if read(directory / "cases" / case / "manifest.json")["group"] in groups]
    if not selected or len(selected) != len(set(selected)) or len(arms) != len(set(arms)) or not set(selected) <= allowed or not set(arms) <= set(frozen["arms"]):
        raise ValueError("Unknown or duplicate case/arm")
    api_key = api_key or os.environ.get("COMMAND_CODE_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Set COMMAND_CODE_API_KEY (never pass credentials on the command line)")
    from .prepare import verify_prepared
    prepared = verify_prepared(directory)
    for case in selected:
        folder = directory / "cases" / case
        manifest = read(folder / "manifest.json")
        reference = read(folder / "source.output.json") if (folder / "source.output.json").exists() else None
        for arm in arms:
            target = directory / "results" / case / arm
            if (target / "started.json").exists():
                raise RuntimeError(f"Refuse replay of started {case}/{arm}; usage may be unknown")
            payload = read(directory / "prepared" / case / f"{arm}.json")
            if digest(canonical(payload)) != prepared[case][arm]:
                raise ValueError("Prepared payload changed")
            output = await complete(payload, target, api_key=api_key)
            verdict = evaluate(manifest, payload, output, reference)
            write_once(target / "verdict.json", verdict)
            print(json.dumps({"case": case, "arm": arm, "status": verdict["status"],
                              "posts": output["posts"], "usage": output["usage"]}, ensure_ascii=False), flush=True)
            summary(directory)
            if verdict["status"] == "inconclusive":
                raise RuntimeError("Provider attempt incomplete; pool stopped without retry or continuation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    parser.add_argument("--case", action="append")
    parser.add_argument("--group", choices=("tool_contract", "phase_boundary", "failure_recovery", "evidence_semantics"), action="append")
    parser.add_argument("--arm", choices=("baseline", "candidate"), action="append")
    parser.add_argument("--paid", action="store_true", help="Explicitly execute the already-authorized paid pool")
    parser.add_argument("--summary", action="store_true", help="Rebuild usage view locally; no provider calls")
    args = parser.parse_args()
    if args.summary:
        print(json.dumps(summary(args.directory)["totals"], ensure_ascii=False))
    elif not args.paid:
        parser.error("This command sends paid requests; use --paid only for the authorized frozen pool")
    else:
        asyncio.run(run(args.directory, cases=args.case, groups=args.group, arms=args.arm or ("baseline", "candidate")))
