"""Capture immutable inputs before candidate implementation; no provider import."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from .cases import CASES, DEFAULT, ROOT


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def write_once(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical(value)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Refuse to replace frozen file: {path.name}")
        return
    with path.open("xb") as stream:
        stream.write(data)


def freeze(destination=DEFAULT):
    destination = Path(destination)
    if (destination / "freeze.json").exists():
        verify(destination)
        return read(destination / "freeze.json")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    items = []
    for spec in CASES:
        source = ROOT / spec["source"]
        raw = source.read_bytes()
        payload = json.loads(raw)
        folder = destination / "cases" / spec["id"]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "request.json"
        if target.exists() and target.read_bytes() != raw:
            raise ValueError("Previously captured request differs")
        if not target.exists():
            with target.open("xb") as stream:
                stream.write(raw)
        stem = source.name.removesuffix(".request.json")
        meta_path = source.with_name(stem + ".meta.json")
        meta = read(meta_path) if meta_path.exists() else {}
        evidence = {}
        for suffix in ("meta", "output", "response"):
            sibling = source.with_name(stem + f".{suffix}.json")
            if sibling.exists():
                data = sibling.read_bytes()
                saved = folder / f"source.{suffix}.json"
                if saved.exists() and saved.read_bytes() != data:
                    raise ValueError("Previously captured evidence differs")
                if not saved.exists():
                    saved.write_bytes(data)
                evidence[saved.name] = digest(data)
        ancestor = None
        if source.name == "request.json" and source.with_name("manifest.json").exists():
            ancestor = read(source.with_name("manifest.json"))
            write_once(folder / "source.manifest.json", ancestor)
            evidence["source.manifest.json"] = digest((folder / "source.manifest.json").read_bytes())
        if "/runs/" in spec["source"]:
            state_path = ROOT / ".runtime/full-flow-readback-fix-20260923/profile/data/workbench-state.json"
            state = read(state_path)
            run_id = source.parent.parent.name
            business = state["businesses"][state["runs"][run_id]["business_id"]]
            proposals = [m["proposal"] for messages in state["messages"].values() for m in messages
                         if m.get("proposal", {}).get("status") == "confirmed"
                         and m["proposal"].get("type") == business["type"]
                         and m["proposal"].get("completion_target") == business["completion_target"]
                         and m["proposal"].get("title") == business["title"]]
            if len(proposals) != 1:
                raise ValueError("Confirmed proposal provenance is ambiguous")
            keys = ("id", "session_id", "type", "title", "goal", "completion_target", "goal_submitted", "references", "source_messages")
            fixture = {"business": {k: business.get(k) for k in keys}, "confirmed_proposal": proposals[0],
                       "state_sha256": digest(state_path.read_bytes()), "source": str(state_path.relative_to(ROOT)),
                       "initial_messages": [], "initial_goal_submitted": False,
                       "reconstruction": "Initial instruction used the not-yet-submitted business goal; later tool history is untouched.",
                       "original_instruction": source.parent.parent.joinpath("instruction.txt").read_text(encoding="utf8")}
            write_once(folder / "business.json", fixture)
            evidence["business.json"] = digest((folder / "business.json").read_bytes())
        tool_messages = [{"index": i, "name": m.get("name"), "tool_call_id": m.get("tool_call_id"),
                          "content_sha256": digest(canonical(m.get("content")))}
                         for i, m in enumerate(payload["messages"]) if m.get("role") == "tool"]
        sop = [f"/messages/{t['index']}/content" for t in tool_messages if t["name"] == "get_odoo_sop"]
        paths = sop + (["/messages/1/content"] if "/runs/" in spec["source"] else [])
        if spec["id"] in {"A03", "C01", "C02", "C03"} and tool_messages:
            paths.append(f"/messages/{tool_messages[-1]['index']}/content")
        # Only descriptions/schema for this existing production diagnostic tool may change.
        paths += [f"/tools/{i}" for i, tool in enumerate(payload.get("tools", []))
                  if tool.get("function", {}).get("name") == "mcp_odoo_diagnose_odoo_call"]
        manifest = {**spec, "capture_commit": commit,
                    "source_commit": ancestor.get("commit") if ancestor else None,
                    "source_kind": "prepared_trace_branch" if ancestor else "recorded_request",
                    "request_id": meta.get("request_id"), "call_id": meta.get("call_id"),
                    "request_sha256": digest(raw), "payload_sha256": digest(canonical(payload)),
                    "oracle_sha256": digest(canonical({k: spec[k] for k in ("oracle", "policy", "required_intent")})),
                    "tool_observations": tool_messages, "allowed_patch_paths": sorted(set(paths)),
                    "source_evidence_hashes": evidence, "max_posts_per_arm": 1,
                    "actual_tool_execution": False, "semantic_verdict_requires_review": True,
                    "upstream_root": "See boundary; this request is a decision cut, not proof the root originated here."}
        write_once(folder / "manifest.json", manifest)
        items.append({"id": spec["id"], "manifest_sha256": digest((folder / "manifest.json").read_bytes())})
    frozen = {"version": 1, "capture_commit": commit, "cases": items, "arms": ["baseline", "candidate"],
              "maximum_posts": 24, "retries": 0, "timeout": None, "paid_calls_at_freeze": 0}
    write_once(destination / "freeze.json", frozen)
    return frozen


def verify(destination=DEFAULT):
    destination = Path(destination)
    frozen = read(destination / "freeze.json")
    for item in frozen["cases"]:
        folder = destination / "cases" / item["id"]
        if digest((folder / "manifest.json").read_bytes()) != item["manifest_sha256"]:
            raise ValueError("Frozen oracle/manifest changed")
        manifest = read(folder / "manifest.json")
        if digest((folder / "request.json").read_bytes()) != manifest["request_sha256"]:
            raise ValueError("Frozen context changed")
        for name, expected in manifest["source_evidence_hashes"].items():
            if digest((folder / name).read_bytes()) != expected:
                raise ValueError("Frozen evidence changed")
    return frozen


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = freeze(args.directory)
    print(json.dumps({"cases": len(result["cases"]), "capture_commit": result["capture_commit"], "paid_calls": 0}))
