"""Freeze independent source facts, then replay 120 read-only tool cases. No model API."""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import threading
import time
import tracemalloc
import types
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from erp_harness.erp._odoo_core.field_policy import FieldPolicy, ModelFieldRule
from erp_harness.erp.gateway import Json2ReadClient
from erp_harness.erp.knowledge import NativeKnowledge
from erp_harness.erp.reads import NativeReads

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / ".runtime/enterprise-validation-20260922"
FIELDS = {
    "product.product": ["name", "default_code"],
    "sale.order": ["name", "client_order_ref", "partner_id", "state"],
    "purchase.order": ["name", "partner_ref", "partner_id", "state"],
    "mrp.production": ["name", "origin", "product_id", "state"],
}
LOG_LOCK = threading.Lock()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class Client(Json2ReadClient):
    def __init__(self, role):
        accounts, connection = load(DATA / "accounts.json"), load(DATA / "connection.json")
        account = accounts[role]
        self.rpc_count = 0
        super().__init__(url=connection["ODOO_URL"], db=connection["ODOO_DB"], username=account["login"],
                         api_key=account["api_key"], context={"allowed_company_ids": account["company_ids"], "lang": "zh_CN"})

    def _json2_call(self, *args, **kwargs):
        self.rpc_count += 1
        return super()._json2_call(*args, **kwargs)


def source_rows(client, model, fields, domain=None):
    """Oracle uses raw Odoo reads, never search ranking or the tool being scored."""
    rows, last = [], 0
    while True:
        page = client.search_read(model_name=model, fields=fields, domain=(domain or []) + [["id", ">", last]], limit=1000, order="id")
        rows.extend(page)
        if len(page) < 1000:
            return rows
        assert page[-1]["id"] > last
        last = page[-1]["id"]


def prepare(folder):
    if folder.exists():
        raise ValueError("Use a new output directory; frozen questions are never overwritten")
    report = load(DATA / "seed-report.json")
    if report["source_target"] != 10000:
        raise ValueError("Freeze only after all 10,000 source documents are present")
    snapshot = DATA / "snapshots/baseline-10000/database.dump"
    if not snapshot.is_file():
        raise ValueError("Create the baseline-10000 snapshot before freezing questions")
    folder.mkdir(parents=True)
    client = Client("manager")
    products = source_rows(client, "product.product", ["id", "name", "default_code"], [["default_code", "=like", "CC-%"]])
    sales = source_rows(client, "sale.order", ["id", "name", "client_order_ref", "partner_id", "company_id", "state", "amount_total", "user_id"], [["client_order_ref", "=like", "CC-SO-%"]])
    purchases = source_rows(client, "purchase.order", ["id", "name", "partner_ref"], [["partner_ref", "=like", "CC-PO-%"]])
    productions = source_rows(client, "mrp.production", ["id", "name", "origin"], [["origin", "=like", "CC-MO-%"]])
    assert (len(products), len(sales), len(purchases), len(productions)) == (1000, 5000, 3000, 2000)
    by_sku = {row["default_code"]: row for row in products}
    by_ref = {row["client_order_ref"]: row for row in sales}
    tail_sources = [("sale.order", "client_order_ref", "CC-SO-", 5000, by_ref),
                    ("purchase.order", "partner_ref", "CC-PO-", 3000, {r["partner_ref"]: r for r in purchases}),
                    ("mrp.production", "origin", "CC-MO-", 2000, {r["origin"]: r for r in productions})]
    bases = [100 + 131 * i for i in range(12)] + [4500, 4700, 4900]
    selected = [by_ref[f"CC-SO-{i+j:05d}"]["id"] for i in bases for j in range(3)]
    lines = source_rows(client, "sale.order.line", ["id", "order_id", "product_id", "product_uom_qty"], [["order_id", "in", selected]])
    by_order = defaultdict(list)
    for line in lines:
        by_order[line["order_id"][0]].append(line)
    cases = []
    chinese_queries = ["控制阀", "温度", "传感器", "温度传感", "安装", "支架", "安装支架", "密封", "密封圈", "连接", "接头", "连接接头", "成品阀组", "半成品阀芯", "澄川温度"]

    def add(family, i, tool, args, expected, role="manager", **extra):
        cases.append({"id": f"{family}-{i:02d}", "family": family, "holdout": i >= 12, "role": role,
                      "tool": tool, "arguments": args, "expected": expected, **extra})

    for i in range(15):
        model, ref_field, prefix, count, source = tail_sources[i % 3]
        tail = count - 100 - i * 13 if i < 12 else count - 1 - (i - 12)
        record = source[f"{prefix}{tail:05d}"]
        add("order_tail", i, "search_knowledge", {"model": model, "query": record[ref_field], "limit": 20}, [record["id"]])
        query = chinese_queries[i]
        add("chinese", i, "search_knowledge", {"model": "product.product", "query": query, "limit": 20}, sorted(r["id"] for r in products if query in r["name"]), candidate_subset=True)
        p = by_sku[f"CC-{i * 73 if i < 12 else [970, 998, 999][i-12]:04d}"]
        add("exact_sku", i, "find_records", {"model": "product.product", "domain": [["default_code", "=", p["default_code"]]]}, [p["id"]])
        prefix = f"CC-SO-{i if i < 12 else i + 35:03d}"
        subset = [r for r in sales if r["client_order_ref"].startswith(prefix)]
        domain = [["client_order_ref", "=like", prefix + "%"]]
        add("complete_pages", i, "find_records", {"model": "sale.order", "domain": domain, "limit": 20}, sorted(r["id"] for r in subset), all_pages=True)
        selected_ids = [by_ref[f"CC-SO-{bases[i]+j:05d}"]["id"] for j in range(3)]
        first = by_order[selected_ids[0]][0]
        product = first["product_id"][0]
        minimum = first["product_uom_qty"] + int(i % 3 == 0)
        excluded = by_order[selected_ids[0]][1]["product_id"][0] if i % 3 == 1 else by_order[selected_ids[-1]][-1]["product_id"][0]
        expected = sorted(rid for rid in selected_ids if any(l["product_id"][0] == product and l["product_uom_qty"] >= minimum for l in by_order[rid]) and not any(l["product_id"][0] == excluded for l in by_order[rid]))
        add("same_line", i, "find_records", {"model": "sale.order", "domain": [["id", "in", selected_ids], ["order_line", "any", [["product_id", "=", product], ["product_uom_qty", ">=", minimum]]], ["order_line", "not any", [["product_id", "=", excluded]]]]}, expected)
        if i < 9:
            add("no_answer", i, "find_records", {"model": "product.product", "domain": [["default_code", "=", f"NO-SKU-{i:08d}"]]}, [])
        else:
            add("no_answer", i, "search_knowledge", {"model": "sale.order", "query": f"NORECORD{i:08d}", "limit": 20}, [])
        if i < 10:
            role = "sales" if i % 2 == 0 else "trade_sales"
            targets = [by_ref[f"CC-SO-{100+i*10:05d}"], by_ref[f"CC-SO-{109+i*10:05d}"]]
            account = load(DATA / "accounts.json")[role]
            expected = sorted(r["id"] for r in targets if r["company_id"][0] in account["company_ids"] and r["user_id"][0] == account["uid"])
            add("permissions", i, "find_records", {"model": "sale.order", "domain": [["id", "in", [r["id"] for r in targets]]]}, expected, role=role)
        else:
            add("permissions", i, "find_records", {"model": "product.product", "domain": [["standard_price", ">", i]]}, [], denied_field="standard_price", expected_denied=True)
        groups = defaultdict(lambda: {"count": 0, "amount": Decimal(0)})
        for row in subset:
            groups[row["state"]]["count"] += 1
            groups[row["state"]]["amount"] += Decimal(str(row["amount_total"]))
        gold = {state: {"count": totals["count"], "amount": str(totals["amount"])} for state, totals in groups.items()}
        add("aggregates", i, "aggregate_records", {"model": "sale.order", "group_by": ["state"], "measures": ["amount_total:sum", "__count"], "domain": domain}, gold)
    assert len(cases) == 120 and sum(case["holdout"] for case in cases) == 24
    oracle = {"products": products, "sales": sales, "purchases": purchases, "productions": productions, "selected_lines": lines}
    write(folder / "oracle.json", oracle)
    write(folder / "cases.json", cases)
    revision = subprocess.check_output(["git", "rev-parse", "backend-baseline-20260922-memory-off"], cwd=ROOT, text=True).strip()
    write(folder / "lifecycle-cases.json", {"cases": ["create_known_write", "rename_external_full_refresh", "archive", "delete", "same_identity_role_revocation"],
          "canary_ref": "EV-LIFECYCLE-RETRIEVAL", "company_id": load(DATA / "accounts.json")["manager"]["company_ids"][0],
          "sales_record_id": by_ref["CC-SO-00100"]["id"], "sales_query": "CC-SO-00100"})
    write(folder / "commitment.json", {"cases_sha256": digest(folder / "cases.json"), "oracle_sha256": digest(folder / "oracle.json"),
          "fixture_sha256": digest(DATA / "fixtures.json"), "baseline_commit": revision, "cases": 120, "holdout": 24,
          "snapshot_sha256": digest(snapshot),
          "lifecycle_sha256": digest(folder / "lifecycle-cases.json"),
          "oracle_rpc_count": client.rpc_count, "scope": "synthetic_source_facts_and_explicit_tool_arguments_not_natural_language_planning"})
    print("Frozen 120 tool cases, including 24 holdout cases")


def baseline_class(revision):
    source = subprocess.check_output(["git", "show", f"{revision}:src/erp_harness/erp/knowledge.py"], cwd=ROOT, text=True, encoding="utf-8")
    module = types.ModuleType("erp_harness.erp._retrieval_baseline")
    module.__package__ = "erp_harness.erp"
    sys.modules[module.__name__] = module
    exec(compile(source, "frozen-knowledge.py", "exec"), module.__dict__)  # noqa: S102 - trusted frozen repository code is the control.
    return module.NativeKnowledge


def perform(case, reads, knowledge):
    args = dict(case["arguments"])
    if case["tool"] == "search_knowledge":
        return knowledge.search_knowledge(**args)
    if case.get("all_pages"):
        pages, rows, offset, seen = [], [], 0, set()
        while True:
            result = reads.call("find_records", {**args, "offset": offset})
            pages.append(result)
            if not result.get("success"):
                return {**result, "pages": pages}
            page = result["result"]
            if any(r["id"] in seen for r in page):
                raise ValueError("Pagination repeated IDs")
            seen.update(r["id"] for r in page)
            rows.extend(page)
            if not result["has_more"]:
                return {"success": True, "result": rows, "pages": pages, "complete": True}
            offset = result["next_offset"]
    return reads.call(case["tool"], args)


def score(case, result):
    if case.get("expected_denied"):
        return {"passed": result.get("success") is False and "Field policy" in result.get("error", "")}
    if not result.get("success"):
        return {"passed": False}
    expected = case["expected"]
    if case["tool"] == "aggregate_records":
        rows = {row["state"]: row for row in result["rows"]}
        passed = set(rows) == set(expected) and all(rows[state].get("__count") == gold["count"] and abs(Decimal(str(rows[state].get("amount_total:sum", rows[state].get("amount_total", "NaN")))) - Decimal(gold["amount"])) <= Decimal("0.01") for state, gold in expected.items())
        return {"passed": passed}
    actual = [r["record_id"] if "record_id" in r else r["id"] for r in result.get("results", result.get("result", []))]
    if case["tool"] == "search_knowledge":
        passed = bool(actual) and set(actual) <= set(expected) if case.get("candidate_subset") else set(expected) <= set(actual) if expected else not actual
        passed = passed and len(actual) <= case["arguments"].get("limit", 5) and len(actual) == len(set(actual))
        return {"passed": passed, "hit_at_20": bool(set(actual) & set(expected)) if expected else None,
                "record_recall_at_20": len(set(expected) & set(actual)) / len(expected) if expected else None}
    return {"passed": sorted(actual) == expected}


def log(folder, value):
    with LOG_LOCK, (folder / "tool-trace.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def runtime(role="manager", denied_field=None, knowledge_type=NativeKnowledge):
    client = Client(role)
    policy = FieldPolicy({"default": {"product.product": ModelFieldRule("deny", frozenset({denied_field}))}}) if denied_field else None
    reads = NativeReads(client, policy=policy)
    return client, reads, knowledge_type(reads)


def run(folder):
    commitment = load(folder / "commitment.json")
    assert digest(folder / "cases.json") == commitment["cases_sha256"]
    assert digest(folder / "oracle.json") == commitment["oracle_sha256"]
    assert digest(DATA / "fixtures.json") == commitment["fixture_sha256"], "Fixture changed after freeze"
    if (folder / "results.json").exists() or (folder / "tool-trace.jsonl").exists():
        raise ValueError("Refusing to overwrite a measured run")
    cases = load(folder / "cases.json")
    os.environ["ERP_KNOWLEDGE_DIR"] = str(folder / "knowledge")
    os.environ["ODOO_REQUEST_LOG"] = str(folder / "rpc.jsonl")
    os.environ["ODOO_REQUEST_BACKEND"] = "native-enterprise-validation"
    tracemalloc.start()
    output = []
    for name, kind in [("baseline", baseline_class(commitment["baseline_commit"])), ("candidate", NativeKnowledge)]:
        runtimes = {}
        manager = runtime(knowledge_type=kind)
        runtimes[("manager", None)] = manager
        for model, fields in FIELDS.items():
            start, before = time.perf_counter(), manager[0].rpc_count
            args = {"model": model, "fields": fields, "replace": True}
            args.update({"full_refresh": True} if name == "candidate" else {"limit": 2000})
            result = manager[2].index_knowledge(**args)
            log(folder, {"phase": "index", "variant": name, "arguments": args, "result": result, "elapsed_ms": (time.perf_counter()-start)*1000, "rpc_count": manager[0].rpc_count-before})
            assert result["success"], result
        for number, case in enumerate(cases):
            key = (case["role"], case.get("denied_field"))
            if key not in runtimes:
                runtimes[key] = runtime(*key, knowledge_type=kind)
            client, reads, knowledge = runtimes[key]
            start, before = time.perf_counter(), client.rpc_count
            try:
                result = perform(case, reads, knowledge)
            except Exception as error:  # noqa: BLE001 - retain failed experiment receipts.
                result = {"success": False, "error": str(error), "error_type": type(error).__name__}
            try:
                grade = score(case, result)
            except (KeyError, TypeError, ValueError, ArithmeticError) as error:
                grade = {"passed": False, "scoring_error": str(error)}
            elapsed = (time.perf_counter()-start)*1000
            row = {"case": case["id"], "family": case["family"], "holdout": case["holdout"], "variant": name, "elapsed_ms": elapsed,
                   "rpc_count": client.rpc_count-before, "tool_success": result.get("success") is True,
                   "result_utf8_bytes": len(json.dumps(result, ensure_ascii=False).encode()), **grade}
            output.append(row)
            log(folder, {**row, "arguments": case["arguments"], "result": result})
            if (number + 1) % 20 == 0:
                print(f"{name}: {number+1}/120", flush=True)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    summaries = {name: {"passed": sum(r["passed"] for r in output if r["variant"] == name), "total": 120,
                       "holdout_passed": sum(r["passed"] for r in output if r["variant"] == name and r["holdout"]), "holdout_total": 24,
                       "by_family": {family: sum(r["passed"] for r in output if r["variant"] == name and r["family"] == family) for family in sorted({c["family"] for c in cases})}} for name in ("baseline", "candidate")}
    write(folder / "results.json", {"summary": summaries, "cases": output, "memory": {"measurement": "python_tracemalloc_not_total_RSS", "current_bytes": current, "peak_bytes": peak},
          "paid_model_calls": 0, "model_tokens": 0, "limitation": "Tool correctness is not ERP business success or measured agent token savings",
          "source_sha256": {p: digest(ROOT / p) for p in ["src/erp_harness/erp/knowledge.py", "src/erp_harness/erp/reads.py", "experiments/enterprise_validation/retrieval_check.py"]}})
    print(json.dumps(summaries, ensure_ascii=False))


def concurrency(folder, *, fully_warm=False):
    assert (folder / "results.json").exists(), "Run the frozen functional cases first"
    stem = "concurrency-fully-warm" if fully_warm else "concurrency"
    if (folder / f"{stem}.json").exists():
        raise ValueError("Use a fresh run instead of overwriting concurrency results")
    os.environ["ERP_KNOWLEDGE_DIR"] = str(folder / "knowledge")
    os.environ["ODOO_REQUEST_LOG"] = str(folder / f"{stem}-rpc.jsonl")
    cases = [case for case in load(folder / "cases.json") if case["family"] == "order_tail"]
    reports = []
    for workers in (1, 5, 20):
        local = threading.local()
        if not fully_warm:
            tracemalloc.start()

        def execute(number, local=local, workers=workers):
            cold = not hasattr(local, "runtime")
            start = time.perf_counter()
            if cold:
                local.runtime = runtime()
            client, reads, knowledge = local.runtime
            before = 0 if cold else client.rpc_count
            case = cases[number % len(cases)]
            try:
                result = perform(case, reads, knowledge)
                passed = score(case, result)["passed"]
            except Exception as error:  # noqa: BLE001 - retain failed experiment receipts.
                result, passed = {"success": False, "error_type": type(error).__name__, "error": str(error)}, False
            row = {"phase": stem, "workers": workers, "cold": cold, "case": case["id"], "passed": passed,
                   "tool_success": result.get("success") is True, "elapsed_ms": (time.perf_counter()-start)*1000,
                   "rpc_count": client.rpc_count-before, "result_utf8_bytes": len(json.dumps(result, ensure_ascii=False).encode())}
            log(folder, {**row, "result": result})
            return row

        with ThreadPoolExecutor(max_workers=workers) as executor:
            if fully_warm:
                barrier = threading.Barrier(workers)

                def prime(_number, barrier=barrier, local=local, workers=workers):
                    try:
                        local.runtime = runtime()
                        _, reads, knowledge = local.runtime
                        for case in cases[:3]:  # Each worker loads SO, PO, and MO before timing begins.
                            result = perform(case, reads, knowledge)
                            log(folder, {"phase": "concurrency_warmup", "workers": workers, "case": case["id"], "result": result})
                            assert score(case, result)["passed"]
                        barrier.wait()
                    except Exception:  # Release other workers when preparation fails.
                        barrier.abort()
                        raise

                list(executor.map(prime, range(workers)))
            rows = list(executor.map(execute, range(max(60, workers * 4))))
        peak = None
        if not fully_warm:
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        phases = {}
        for cold in (True, False):
            selected = [row for row in rows if row["cold"] == cold]
            times = sorted(row["elapsed_ms"] for row in selected)
            phases["cold" if cold else "warm"] = {"requests": len(times), "p50_ms": times[math.ceil(len(times)*0.5)-1] if times else None,
                "p95_ms": times[math.ceil(len(times)*0.95)-1] if times else None, "errors": sum(not row["passed"] for row in selected),
                "tool_errors": sum(not row["tool_success"] for row in selected),
                "incorrect_results": sum(row["tool_success"] and not row["passed"] for row in selected),
                "rpc_count": sum(row["rpc_count"] for row in selected)}
        reports.append({"workers": workers, "phases": phases, "python_peak_bytes": peak, "rows": rows})
        print(f"Concurrency {workers}: {sum(r['passed'] for r in rows)}/{len(rows)}", flush=True)
    notes = (["All three model indexes are preloaded in every worker before timing", "No tracemalloc instrumentation; heap measured separately in the original concurrency run"] if fully_warm else
             ["cold = new client and index load; warm = same thread runtime and can include first load of another model", "tracemalloc instrumentation affects latency; heap excludes SQLite and native allocations", "P95 cold n=1/5/20 is descriptive only"])
    write(folder / f"{stem}.json", {"levels": reports, "paid_model_calls": 0, "notes": notes})


def lifecycle(folder):
    """Fixture mutations run in the isolated Odoo shell; reads use ordinary role credentials."""
    from manage import shell

    commitment = load(folder / "commitment.json")
    assert digest(folder / "lifecycle-cases.json") == commitment["lifecycle_sha256"]
    cases = load(folder / "lifecycle-cases.json")
    receipt = folder / "lifecycle"
    receipt.mkdir(exist_ok=False)
    os.environ["ERP_KNOWLEDGE_DIR"] = str(receipt / "knowledge")
    os.environ["ODOO_REQUEST_LOG"] = str(receipt / "rpc.jsonl")
    _, _, knowledge = runtime()
    ref, company_id = cases["canary_ref"], cases["company_id"]
    canary_id, role_before, results = None, None, []

    def mutate(source):
        # Match Odoo HTTP transaction completion; commit alone leaves other workers' ACL caches stale.
        source = "assert env.cr.dbname == 'erp_harness_enterprise_v1'\n" + source + "\nenv.cr.commit()\nresult['cache_invalidation_published']=sorted(env.registry.cache_invalidated)\nenv.registry.signal_changes()\nprint('ENTERPRISE_RESULT=' + json.dumps(result))"
        log(receipt, {"phase": "fixture_mutation", "orm_script": source})
        result = shell("import json\n" + source)
        log(receipt, {"phase": "fixture_mutation_result", "result": result})
        return result

    def find(query):
        result = knowledge.search_knowledge(query, "res.partner", limit=20)
        log(receipt, {"phase": "search", "query": query, "result": result})
        return result

    def refresh_canary():
        result = knowledge.index_knowledge("res.partner", domain=[["ref", "=", ref]], fields=["name", "ref", "active"], full_refresh=True)
        log(receipt, {"phase": "refresh", "result": result})
        assert result["success"], result

    try:
        refresh_canary()
        assert not find("EVLifeCreatedAlpha")["results"]
        result = mutate(f"assert not env['res.partner'].with_context(active_test=False).search([('ref','=',{ref!r})])\nr = env['res.partner'].with_company({company_id}).create({{'name':'EVLifeCreatedAlpha','ref':{ref!r},'company_id':{company_id}}})\nresult = {{'id':r.id,'name':r.name}}")
        canary_id = result["id"]
        knowledge.invalidate(reason="fixture_known_write")
        assert find("EVLifeCreatedAlpha")["results"][0]["record_id"] == canary_id
        results.append({"case": "create_known_write", "passed": True})
        mutate(f"r=env['res.partner'].browse({canary_id}); r.name='EVLifeRenamedBeta'\nresult={{'id':r.id,'name':r.name}}")
        before_refresh = find("EVLifeRenamedBeta")
        assert before_refresh["status"] == "no_candidate_match" and before_refresh["freshness"]["external_changes"] == "explicit_full_refresh_required"
        refresh_canary()
        assert find("EVLifeRenamedBeta")["results"][0]["record_id"] == canary_id
        results.append({"case": "rename_external_full_refresh", "passed": True})
        mutate(f"r=env['res.partner'].browse({canary_id}); r.active=False\nresult={{'id':r.id,'active':r.active}}")
        knowledge.invalidate(reason="fixture_archive")
        assert find("EVLifeRenamedBeta")["results"] == []
        results.append({"case": "archive", "passed": True})
        mutate(f"r=env['res.partner'].browse({canary_id}); r.active=True\nresult={{'id':r.id,'active':r.active}}")
        knowledge.invalidate(reason="fixture_unarchive")
        assert find("EVLifeRenamedBeta")["results"][0]["record_id"] == canary_id
        removed = mutate(f"r=env['res.partner'].browse({canary_id}); r.unlink()\nresult={{'remaining':env['res.partner'].with_context(active_test=False).search_count([('id','=',{canary_id})])}}")
        assert removed["remaining"] == 0
        knowledge.invalidate(reason="fixture_delete")
        assert find("EVLifeRenamedBeta")["results"] == []
        results.append({"case": "delete", "passed": True})
        canary_id = None
        sales_client, _, sales_knowledge = runtime("sales")
        source_id, query = cases["sales_record_id"], cases["sales_query"]
        indexed = sales_knowledge.index_knowledge("sale.order", domain=[["id", "=", source_id]], fields=["name", "client_order_ref"], full_refresh=True)
        assert indexed["success"] and indexed["fetched"] == 1
        found = sales_knowledge.search_knowledge(query, "sale.order")
        assert found["results"][0]["record_id"] == source_id
        sales_uid = load(DATA / "accounts.json")["sales"]["uid"]
        role_before = mutate(f"u=env['res.users'].browse({sales_uid})\nresult={{'uid':u.id,'groups':u.group_ids.ids}}")
        mutate(f"u=env['res.users'].browse({sales_uid}); g=env.ref('sales_team.group_sale_salesman'); u.write({{'group_ids':[(3,g.id)]}})\nresult={{'uid':u.id,'has_sales':u.has_group('sales_team.group_sale_salesman')}}")
        denied = sales_knowledge.search_knowledge(query, "sale.order")
        log(receipt, {"phase": "same_identity_revoked", "before": found, "after": denied, "uid": sales_uid, "rpc_count": sales_client.rpc_count})
        assert not denied["success"] and denied["results"] == []
        assert sales_knowledge._scope(None)[1] == found["scope"]["scope_sha256"]
        results.append({"case": "same_identity_role_revocation", "passed": True})
    except Exception as error:  # noqa: BLE001 - persist failed fixture experiments, then restore access.
        results.append({"case": cases["cases"][len(results)], "passed": False, "error_type": type(error).__name__, "error": str(error)})
    finally:
        cleanup = []
        scripts = []
        if role_before is not None:
            scripts.append(("restore_groups", f"u=env['res.users'].browse({role_before['uid']}); u.write({{'group_ids':[(6,0,{role_before['groups']!r})]}})\nresult={{'uid':u.id,'groups_restored':sorted(u.group_ids.ids)=={sorted(role_before['groups'])!r}}}\nassert result['groups_restored']"))
        if canary_id is not None:
            scripts.append(("remove_canary", f"r=env['res.partner'].with_context(active_test=False).browse({canary_id}).exists(); assert not r or r.ref=={ref!r}; r.unlink()\nresult={{'canary_removed':True}}"))
        for operation, source in scripts:
            try:
                cleanup.append({"operation": operation, "success": True, "result": mutate(source)})
            except Exception as error:  # noqa: BLE001 - never lose test receipts when cleanup fails.
                cleanup.append({"operation": operation, "success": False, "error_type": type(error).__name__, "error": str(error)})
        completed = {row["case"] for row in results}
        results.extend({"case": case, "passed": False, "status": "not_run_after_failure"} for case in cases["cases"] if case not in completed)
        write(receipt / "results.json", {"cases": results, "passed": sum(row["passed"] for row in results), "total": 5, "cleanup": cleanup,
              "paid_model_calls": 0, "boundary": "Fixture ORM maintenance, current role readback, no claim of agent business execution"})
    print(json.dumps({"lifecycle_passed": sum(row["passed"] for row in results), "total": 5}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "run", "concurrency", "warm_concurrency", "lifecycle"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to((ROOT / ".runtime").resolve()):
        raise ValueError("Experiment outputs must remain under .runtime")
    {"prepare": prepare, "run": run, "concurrency": concurrency, "warm_concurrency": lambda folder: concurrency(folder, fully_warm=True), "lifecycle": lifecycle}[args.operation](output)
