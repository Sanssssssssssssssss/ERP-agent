"""No-LLM differential gate on an isolated Odoo database, never a production DB.

Provision by feeding this file to `odoo shell -d bench_read_gate --no-http`.
Then run it with the same lab runtime as pi_odoo_runner.py. Only provisioning
writes data, and it is kept in a separate database from the A/B seed snapshot.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def provision_security(env):
    if env.cr.dbname != "bench_read_gate":
        raise RuntimeError("Security fixtures may only be created in bench_read_gate")
    users = env["res.users"].with_context(no_reset_password=True)
    group_field = "group_ids" if "group_ids" in users._fields else "groups_id"
    user = users.create({
        "name": "Pi Read Gate", "login": "pi_read_gate",
        group_field: [(6, 0, [env.ref("base.group_user").id])],
        "company_id": env.company.id, "company_ids": [(6, 0, [env.company.id])],
    })
    hidden = env["res.partner"].create({"name": "PI_READ_DENIED", "comment": "FIELD_POLICY_CANARY"})
    visible = env["res.partner"].create({"name": "PI_READ_VISIBLE", "email": False, "comment": "FIELD_POLICY_CANARY"})
    env["ir.rule"].create({
        "name": "Pi read gate: one restricted record",
        "model_id": env.ref("base.model_res_partner").id,
        "domain_force": f"[('id', '!=', {hidden.id})] if user.id == {user.id} else []",
        "perm_read": True, "perm_write": False, "perm_create": False, "perm_unlink": False,
    })
    key = env["res.users.apikeys"].sudo().with_user(user.id)._generate(
        "rpc", "pi-read-gate", datetime.now() + timedelta(days=1)
    )
    env.cr.commit()
    Path("/tmp/pi-read-gate-key").write_text(key)
    Path("/tmp/pi-read-gate-fixture.json").write_text(json.dumps({"hidden": hidden.id, "visible": visible.id, "uid": user.id}))
    print("READ_GATE_SECURITY_FIXTURE_READY")


async def run_gate():
    from pi_agent.mcp import McpToolSet
    from integration.native_reads import NativeReads
    from integration.odoo_tools import route_tools

    root = Path("/logs/agent")
    fixture = json.loads(Path("/tmp/pi-read-gate-fixture.json").read_text())
    policy = root / "read-gate-field-policy.json"
    policy.write_text(json.dumps({"field_acl": {"default": {"res.partner": {"deny": ["comment"]}}}}))
    os.environ["ODOO_MCP_FIELD_POLICY_FILE"] = str(policy)
    cases = [
        ("get_odoo_profile", {"include_modules": False}),
        ("get_odoo_profile", {}),
        ("get_model_fields", {"model": "res.company", "max_fields": 10}),
        ("get_model_fields", {"model": "res.partner", "field_names": ["name", "comment", "email"]}),
        ("search_records", {"model": "res.partner", "fields": ["name", "email", "comment"], "limit": 5, "order": "id"}),
        ("search_records", {"model": "res.partner", "domain": [["id", "=", fixture["hidden"]]], "fields": ["name", "comment"]}),
        ("search_records", {"model": "res.partner", "query": "PI_READ_VISIBLE", "limit": 2, "order": "id"}),
        ("search_records", {"model": "res.partner", "query": "PI_READ_VISIBLE", "limit": 2, "order": "id"}),
        ("search_records", {"model": "res.partner", "domain": "bad domain"}),
        ("search_records", {"model": "res.partner", "query": "null", "fields": ["name"], "limit": 2}),
        ("read_record", {"model": "res.partner", "record_id": fixture["visible"], "fields": ["name", "email", "comment"]}),
        ("read_record", {"model": "res.partner", "record_id": fixture["hidden"], "fields": ["name"]}),
        ("read_record", {"model": "res.partner", "record_id": 2147483647, "fields": ["name"]}),
        ("read_record", {"model": "res.partner", "record_id": fixture["visible"], "fields": ["name", "email"], "extra": "ignored"}),
    ]
    results = []
    processes = []
    try:
        for username, port, key_path in (
            ("admin", 8011, "/etc/odoo/api_key"),
            ("pi_read_gate", 8012, "/tmp/pi-read-gate-key"),
        ):
            key = Path(key_path).read_text().strip()
            environment = {
                **os.environ, "ODOO_URL": "http://127.0.0.1:8069", "ODOO_DB": "bench_read_gate",
                "ODOO_USERNAME": username, "ODOO_API_KEY": key, "ODOO_PASSWORD": key,
                "ODOO_TRANSPORT": "json2", "ODOO_LOCALE": "en_US", "ODOO_MCP_ENABLE_WRITES": "0",
                "ODOO_REQUEST_LOG": str(root / f"gate-{username}-mcp-rpc.jsonl"), "ODOO_REQUEST_BACKEND": "mcp",
            }
            with (root / f"gate-{username}-server.log").open("w") as log:
                process = subprocess.Popen([
                    str(Path(sys.executable).with_name("odoo-mcp")), "--transport", "streamable-http", "--host", "127.0.0.1",
                    "--port", str(port), "--path", "/mcp",
                ], env=environment, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"MCP gate server exited for {username}; inspect its local log")
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                raise TimeoutError("MCP gate server did not start")
            os.environ.update(environment)
            os.environ["ODOO_REQUEST_LOG"] = str(root / f"gate-{username}-native-rpc.jsonl")
            os.environ["ODOO_REQUEST_BACKEND"] = "native"
            native = NativeReads.from_environment()
            async with McpToolSet(f"http://127.0.0.1:{port}/mcp") as toolset:
                a = {tool.name: tool for tool in route_tools(toolset.tools, root / f"gate-{username}-a.jsonl")}
                b = {tool.name: tool for tool in route_tools(toolset.tools, root / f"gate-{username}-b.jsonl", native)}
                for number, (name, arguments) in enumerate(cases):
                    full_name = f"mcp_odoo_{name}"
                    expected = await a[full_name].execute(str(number), arguments)
                    actual = await b[full_name].execute(str(number), arguments)
                    equal = expected.model_dump() == actual.model_dump()
                    row = {"principal": username, "tool": name, "arguments": arguments, "equal": equal,
                           "a": expected.model_dump(), "b": actual.model_dump()}
                    results.append(row)
                    if not equal:
                        raise AssertionError(f"Read differential mismatch: {username}/{name}/case {number}")
                    payload = json.loads(actual.text)
                    assert "FIELD_POLICY_CANARY" not in actual.text, "field policy leaked a value"
                    if name == "get_model_fields" and arguments["model"] == "res.company":
                        assert payload["count"] == 10
                        assert "chart_template" not in payload["result"]
                    if name == "read_record" and arguments["record_id"] == fixture["visible"]:
                        assert payload["success"] and payload["result"]["email"] is False
                        assert "comment" not in payload["result"]
                    if username == "pi_read_gate" and name == "read_record" and arguments["record_id"] == fixture["hidden"]:
                        assert payload["success"] is False, "record rule bypassed"
                    if username == "pi_read_gate" and name == "search_records" and arguments.get("domain") == [["id", "=", fixture["hidden"]]]:
                        assert payload["result"] == [], "restricted record visible in search"
            results.append({"principal": username, "cache_hits": native.cache_hits, "cache_misses": native.cache_misses})
        summary = {"status": "passed", "comparisons": sum("equal" in row for row in results), "llm_calls": 0}
        (root / "read-gate-summary.json").write_text(json.dumps(summary, indent=2))
        print(json.dumps(summary), flush=True)
    except BaseException as exc:
        (root / "read-gate-summary.json").write_text(json.dumps({"status": "failed", "error": str(exc), "comparisons": sum("equal" in row for row in results), "llm_calls": 0}, indent=2))
        raise
    finally:
        (root / "read-gate-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if "env" in globals():
    provision_security(env)
elif __name__ == "__main__":
    asyncio.run(run_gate())
