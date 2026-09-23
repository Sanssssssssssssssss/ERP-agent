"""On-demand public transcript snapshots; the original JSONL stays untouched."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from erp_harness.runtime.exporting.transcript import export_session_html
from erp_harness.runtime.storage.jsonl import entry_from_json_line

from .trace_inspector import _sanitize

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CREDENTIAL = re.compile(r"(?i)(\b(?:[\w-]*(?:token|secret|password|api[_-]?key|authorization|cookie)[\w-]*)\b[\"']?\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;<>}\]]+)")
_AUTH = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_AUTH_HEADER = re.compile(r"(?im)(\b(?:cookie|set-cookie|authorization)\s*[:=]\s*)[^\r\n]+")
_URL_AUTH = re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@")
_SIGNATURES = {"thoughtsignature", "thinkingsignature", "textsignature"}
_NOTICE = "这是业务会话快照，可能包含多次运行和审批续接。用量与真实请求次数请查看工作台的「运行详情」；此快照不作为业务完成或邮件送达证明。"


def _public_entries(entries: list[Any]) -> list[Any]:
    # Use the existing structured privacy policy; snake_case preserves numeric usage fields.
    cleaned, _ = _sanitize([entry.model_dump(mode="json", by_alias=False) for entry in entries])
    secrets = sorted({value for key, value in os.environ.items()
                      if re.search(r"(?i)(token|secret|password|api[_-]?key)", key) and len(value) >= 8},
                     key=len, reverse=True)

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: public(item) for key, item in value.items()
                    if key.replace("_", "").lower() not in _SIGNATURES}
        if isinstance(value, list):
            return [public(item) for item in value
                    if not (isinstance(item, dict) and item.get("type") == "hidden")]
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "<redacted>")
            value = _AUTH_HEADER.sub(r"\1<redacted>", value)
            value = _URL_AUTH.sub(r"\1<redacted>@", value)
            value = _AUTH.sub(r"\1 <redacted>", value)
            return _CREDENTIAL.sub(lambda match: match[1] + '"<redacted>"', value)
        return value

    return [entry_from_json_line(json.dumps(public(entry), ensure_ascii=False)) for entry in cleaned]


def export_session_snapshot(root: Path, business: dict[str, Any]) -> dict[str, Any]:
    """The host checks business ownership; no caller-supplied file path is accepted."""
    identifier = business.get("id")
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise ValueError("业务会话标识无效。")
    root = root.resolve()
    source = root / "sessions" / identifier / "pi-agent-session.jsonl"
    destination = root / "exports" / "session-snapshots" / f"{identifier}.html"
    if source.resolve() != source or destination.resolve() != destination:
        raise ValueError("业务会话快照路径不在当前业务目录。")
    if not source.is_file():
        raise ValueError("当前业务尚无已保存的会话记录。")
    try:
        entries = [entry_from_json_line(line, line_number=index)
                   for index, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
                   if line.strip()]
    except (ValueError, UnicodeError):
        # Parser errors may contain private input. Never return their raw text to the desktop.
        raise ValueError("会话记录不完整或格式无效，未生成快照；请保留原始日志。") from None
    if not entries:
        raise ValueError("当前业务尚无已保存的会话记录。")
    entries = _public_entries(entries)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".html", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        export_session_html(entries, temporary,
                            title=f"业务会话快照（可能包含多次运行） · {identifier}",
                            source=f"{identifier}.jsonl", usage_notice=_NOTICE)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(destination), "name": destination.name,
            "entry_count": len(entries), "scope": "business_session"}
