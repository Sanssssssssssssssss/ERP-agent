"""Small, local-only material parser for the desktop erp_harness.app."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any


MAX_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_SESSION = 10
MAX_ROWS = 200
MAX_CHARS = 20_000
MAX_PREVIEW_CHARS = 4_000


def _media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".txt":
        return "text/plain"
    types = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
    if suffix in types:
        return types[suffix]
    raise ValueError("支持 UTF-8 CSV/TXT、XLSX、PDF、PNG、JPG")


def validate_name(name: Any) -> tuple[str, str]:
    if not isinstance(name, str):
        raise ValueError("material name is required")
    clean = name.strip()
    if (not clean or len(clean) > 200 or Path(clean).name != clean or clean in {".", ".."} or
            any(char in clean for char in ("/", "\\", ":", "\x00"))):
        raise ValueError("material name must be a simple file name")
    return clean, _media_type(clean)


def parse_material(name: Any, raw: bytes) -> dict[str, Any]:
    clean, media_type = validate_name(name)
    if not isinstance(raw, bytes):
        raise ValueError("material content must be bytes")
    if len(raw) > MAX_BYTES:
        raise ValueError("material exceeds the 10 MiB limit")
    if not raw:
        raise ValueError("material is empty")
    extracted = {}
    if media_type in {"text/csv", "text/plain"}:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("material must be valid UTF-8") from exc
    else:
        from .material_extract import extract
        text, extracted = extract(Path(clean).suffix.lower(), raw)
    if len(text) > MAX_CHARS:
        raise ValueError("material exceeds the 20000 character limit")
    if media_type == "text/csv":
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error as exc:
            raise ValueError("material is not valid CSV") from exc
    else:
        rows = text.splitlines()
    if not rows:
        raise ValueError("material is empty")
    if media_type in {"text/csv", "text/plain"} and len(rows) > MAX_ROWS:
        raise ValueError("material exceeds the 200 row limit")
    return {
        "name": clean,
        "media_type": media_type,
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "row_count": len(rows),
        "preview": text[:MAX_PREVIEW_CHARS],
        "status": "ready", "warnings": extracted.get("warnings", []),
        **({"extraction": {**extracted, "text": text}} if extracted else {}),
    }


def read_material_text(path: str | Path, metadata: dict[str, Any]) -> str:
    raw = Path(path).read_bytes()
    if metadata.get("status") in {"parsing", "failed"}:
        raise ValueError("材料尚未解析成功")
    if metadata.get("extracted_path"):
        if hashlib.sha256(raw).hexdigest() != metadata.get("sha256"):
            raise ValueError("material hash does not match imported content")
        extracted = Path(metadata["extracted_path"]).read_bytes()
        if hashlib.sha256(extracted).hexdigest() != metadata.get("extracted_sha256"):
            raise ValueError("extracted material hash mismatch")
        text = json.loads(extracted)["text"]
        if not isinstance(text, str) or len(text) > MAX_CHARS:
            raise ValueError("invalid extracted text")
        return text
    parsed = parse_material(metadata.get("name"), raw)
    if parsed["sha256"] != metadata.get("sha256"):
        raise ValueError("material hash does not match imported content")
    return raw.decode("utf-8-sig")
