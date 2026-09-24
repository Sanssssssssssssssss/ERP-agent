"""Local Mem0 storage. Each operation releases the Qdrant process lock."""

import hashlib
import json
import math
import os
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONS = 384


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def scope_for(native):
    """Bind recall to the actual credential, database and current Odoo company context."""
    if len(native.instances) != 1:
        raise ValueError("Memory requires one explicit Odoo instance per task")
    identity = native.identity_context()
    client = native.instances[native.instance].client
    context = client.get_user_context()
    if not isinstance(context, dict) or context.get("error") or not context.get("uid"):
        raise ValueError("Memory requires an authenticated Odoo user context")
    user = client.read_records("res.users", [context["uid"]], fields=["company_id", "company_ids"])[
        0
    ]
    companies = (
        client.context.get("allowed_company_ids")
        or context.get("allowed_company_ids")
        or [user["company_id"][0]]
    )
    if not isinstance(companies, list) or not companies:
        raise ValueError("Memory requires an explicit authenticated company scope")
    if not set(companies) <= set(user["company_ids"]):
        raise ValueError("Memory company scope exceeds the authenticated user's companies")
    return digest({"identity": identity, "uid": context["uid"], "companies": companies})


def _full_text_embedding(memory, cache):
    """Pool complete token windows; never silently drop the end of a long task."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(next(cache.rglob("tokenizer.json"))))
    window = min((tokenizer.truncation or {}).get("max_length", 128), 128) - 2
    tokenizer.no_truncation()
    original = memory.embedding_model.embed

    def embed(text, memory_action=None):
        encoded = tokenizer.encode(text, add_special_tokens=False)
        if len(encoded.ids) <= window:
            return original(text, memory_action)
        bounds = (
            [0]
            + [encoded.offsets[i][0] for i in range(window, len(encoded.ids), window)]
            + [len(text)]
        )
        vectors = [original(text[a:b], memory_action) for a, b in pairwise(bounds)]
        pooled = [sum(v[i] for v in vectors) / len(vectors) for i in range(len(vectors[0]))]
        norm = math.sqrt(sum(x * x for x in pooled)) or 1
        return [x / norm for x in pooled]

    memory.embedding_model.embed = embed
    memory.embedding_model.embed_batch = lambda texts, memory_action=None: [
        embed(t, memory_action) for t in texts
    ]


@contextmanager
def open_memory(root, scope, *, staging=False, download=False, write=False):
    """Staging extraction uses its own store; network waits never lock the live index."""
    from filelock import FileLock

    root = Path(root)
    if not re.fullmatch(r"[a-f0-9]{64}", scope):
        raise ValueError("Invalid memory scope")
    cache = root / "models"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["MEM0_TELEMETRY"] = "false"
    os.environ["MEM0_DIR"] = str(root / "sdk")
    os.environ["FASTEMBED_CACHE_PATH"] = str(cache)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if not download and not (cache / "ready.json").exists():
        raise RuntimeError("memory_model_not_ready")
    folder = root / ("staging" if staging else "scopes") / scope
    folder.mkdir(parents=True, exist_ok=True)
    # ponytail: one local writer per scope; a server is only needed for concurrent workers.
    with FileLock(str(folder / "operation.lock"), timeout=-1 if write else 0):
        from mem0 import Memory

        offline_before = os.environ.get("HF_HUB_OFFLINE")
        memory = None
        if not download:
            os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            memory = Memory.from_config(
                {
                    "vector_store": {
                        "provider": "qdrant",
                        "config": {
                            "path": str(folder / "qdrant"),
                            "collection_name": "experience",
                            "embedding_model_dims": DIMENSIONS,
                        },
                    },
                    "embedder": {
                        "provider": "fastembed",
                        "config": {"model": MODEL, "embedding_dims": DIMENSIONS},
                    },
                    "llm": {
                        "provider": "openai",
                        "config": {
                            "api_key": "local-retrieval-only",
                            "openai_base_url": "http://127.0.0.1:9/v1",
                        },
                    },
                    "history_db_path": str(folder / "history.sqlite3"),
                }
            )
            _full_text_embedding(memory, cache)
            if download:
                memory.embedding_model.embed("ERP business experience")
                # Mem0 2.1 loads BM25 lazily; prepare it here so recall stays offline.
                if memory.vector_store._get_bm25_encoder() is None:
                    raise RuntimeError("memory_bm25_model_not_ready")
                save(cache / "ready.json", {"model": MODEL, "dimensions": DIMENSIONS})
            yield memory
        finally:
            if offline_before is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = offline_before
            if memory is not None:
                memory.db.close()
                memory.vector_store.client.close()
                memory.llm.client.close()


class ExperienceStore:
    def __init__(self, root, scope, contract):
        if not re.fullmatch(r"[a-f0-9]{64}", scope):
            raise ValueError("Invalid memory scope")
        self.root, self.scope, self.contract = Path(root), scope, contract

    def search(self, event):
        group = "workflow" if event["kind"] == "task_start" else "error:" + event["model"]
        with open_memory(self.root, self.scope) as memory:
            hits = memory.search(
                event["query"],
                filters={
                    "user_id": self.scope,
                    "agent_id": group,
                    "contract": self.contract,
                    "admitted": True,
                },
                top_k=3,
            )["results"]
        return [{"source": hit["id"], "text": hit["memory"]} for hit in hits]

    def add(self, cards, job_id, evidence_hash):
        receipts = []
        with open_memory(self.root, self.scope, write=True) as memory:
            for card in cards:
                key = digest(
                    {"text": card["text"], "group": card["group"], "contract": self.contract}
                )
                if memory.get_all(filters={"user_id": self.scope, "card_hash": key})["results"]:
                    continue
                result = memory.add(
                    card["text"],
                    user_id=self.scope,
                    agent_id=card["group"],
                    run_id=job_id,
                    metadata={
                        "contract": self.contract,
                        "admitted": True,
                        "card_hash": key,
                        "sources": card["sources"],
                        "evidence_hash": evidence_hash,
                        "quality": "automatically_extracted_historical_reference",
                    },
                    expiration_date=(datetime.now(UTC).date() + timedelta(days=30)).isoformat(),
                    infer=False,
                )
                receipts.append(result)
        return receipts
