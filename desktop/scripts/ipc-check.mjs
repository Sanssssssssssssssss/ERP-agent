// Local scripted endpoints, real packaged Electron/preload/Python/worker chain.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { cp, mkdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { _electron as electron } from "playwright";

const desktop = resolve(import.meta.dirname, "..");
const output = resolve(desktop, "../.runtime/ipc-check", `run-${process.pid}`);
const profile = join(output, "profile");
await mkdir(output, { recursive: true });
const log = [];
let writes = 0;
let comment = "Original fixture comment";
let calls = 0;
const server = createServer(async (request, response) => {
  try {
    const buffers = [];
    for await (const chunk of request) buffers.push(chunk);
    const body = JSON.parse(Buffer.concat(buffers).toString() || "{}");
    log.push({ path: request.url, body });
    if (request.url === "/v1/chat/completions") {
      calls++;
      const catalog = body.tools.map(t => t.function.name);
      const used = body.messages.flatMap(m => m.tool_calls ?? []).map(t => t.function.name);
      const userText = body.messages.filter(m => m.role === "user").map(m => JSON.stringify(m.content)).join(" ");
      let tool, args;
      if (catalog.includes("propose_business")) {
        if (!used.includes("propose_business")) {
          tool = "propose_business";
          args = { type: "purchase", title: "IPC fixture", goal: "Update the observed vendor comment after approval.", completion_target: "draft" };
        }
      } else if (!used.includes("configure_odoo_tools")) {
        tool = "configure_odoo_tools";
        args = { capabilities: ["actions"] };
      } else if (!used.includes("mcp_odoo_validate_write")) {
        tool = "mcp_odoo_validate_write";
        args = { model: "res.partner", operation: "write", record_ids: [10], values: { comment: "Approved fixture comment" } };
      } else if (!used.includes("mcp_odoo_execute_approved_write")) {
        tool = "mcp_odoo_execute_approved_write";
        args = JSON.parse(body.messages.findLast(m => m.role === "tool" && m.name === "mcp_odoo_validate_write").content).execution_request;
      }
      if (tool) assert.ok(catalog.includes(tool), `${tool} must be advertised`);
      const delta = tool ? { role: "assistant", tool_calls: [{ index: 0, id: `fixture-${calls}`, type: "function", function: { name: tool, arguments: JSON.stringify(args) } }] }
        : { role: "assistant", content: "Local fixture finished." };
      response.writeHead(200, { "Content-Type": "text/event-stream" });
      if (userText.includes("FIXTURE_CANCEL")) await new Promise(r => setTimeout(r, 2000));
      response.end(`data: ${JSON.stringify({ id: `fixture-${calls}`, model: "fixture", choices: [{ index: 0, delta, finish_reason: tool ? "tool_calls" : "stop" }], usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120 } })}\n\ndata: [DONE]\n\n`);
      return;
    }
    const [model, method] = request.url.split("/").slice(-2);
    let result;
    if (method === "context_get") result = { lang: "en_US", tz: "UTC", allowed_company_ids: [1] };
    else if (["check_access_rights", "check_access_rule", "check_access"].includes(method)) result = true;
    else if (method === "fields_get") result = {
      id: { type: "integer", string: "ID", readonly: true },
      name: { type: "char", string: "Name" },
      ref: { type: "char", string: "Reference" },
      comment: { type: "html", string: "Comment" },
      write_date: { type: "datetime", string: "Updated" },
    };
    else if (method === "write") {
      assert.equal(model, "res.partner");
      assert.deepEqual(body.ids, [10]);
      assert.equal(body.vals.comment, "Approved fixture comment");
      writes++;
      comment = body.vals.comment;
      result = true;
    } else if (method === "search_count") result = model === "res.partner" ? 1 : 0;
    else if (method === "search") result = model === "res.partner" ? [10] : [];
    else if (["read", "search_read"].includes(method)) result = model === "res.partner" ? [{ id: 10, name: "Fixture vendor", ref: "FIXTURE", comment, write_date: "2026-09-21 00:00:00" }] : [];
    else throw new Error(`Unexpected fixture operation ${model}.${method}`);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(result));
  } catch (error) {
    log.push({ fixture_error: String(error) });
    response.writeHead(500);
    response.end(JSON.stringify({ error: String(error) }));
  }
});
await new Promise(r => server.listen(0, "127.0.0.1", r));
const endpoint = `http://127.0.0.1:${server.address().port}`;
const environment = { ...process.env };
for (const name of ["ELECTRON_RUN_AS_NODE", "WORKBENCH_HOST_ROOT", "WORKBENCH_PYTHON", "WORKBENCH_SITE_PACKAGES", "PYTHONPATH"]) delete environment[name];
const initialExe = process.env.WORKBENCH_PACKAGED_EXE || join(desktop, "dist/win-unpacked/Odoo Workbench.exe");
const upgradeExe = process.env.WORKBENCH_UPGRADE_EXE || initialExe;
let app, page;
async function launch(executablePath, userData) {
  app = await electron.launch({ executablePath, args: [`--user-data-dir=${userData}`], env: environment, timeout: 30_000 });
  page = await app.firstWindow();
  await page.getByRole("button", { name: /连接设置/ }).waitFor();
}
const call = (method, params = {}) => page.evaluate(({ method, params }) => window.workbench.call(method, params), { method, params });
async function until(read, accepts) {
  const deadline = Date.now() + 30_000;
  let last;
  while (Date.now() < deadline) {
    last = await read();
    if (accepts(last)) return last;
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error(`Fixture wait failed: ${JSON.stringify(last)}`);
}
async function prepareApproval() {
  const initialCalls = calls;
  const session = await call("create_session", { title: "Migration fixture" });
  const scope = { session_id: session.id };
  const material = await call("import_material", { ...scope, name: "fixture.csv", content_base64: Buffer.from("vendor,note\nFixture vendor,review\n").toString("base64") });
  await call("send_message", { ...scope, text: "Prepare the vendor comment change.", material_ids: [material.id] });
  const detail = await until(() => call("get_session", scope), d => d.conversation_runs?.some(r => r.status === "completed") && d.messages.some(m => m.proposal));
  const proposal = detail.messages.find(m => m.proposal).proposal;
  const business = await call("confirm_business", { ...scope, proposal_id: proposal.id, confirmed: true });
  scope.business_id = business.id;
  assert.equal(calls - initialCalls, 2, "Confirming a proposal must not start execution");
  const run = await call("start_run", scope);
  scope.run_id = run.id;
  const pending = await until(() => call("get_business", scope), d => d.runs?.some(r => r.id === run.id && r.status === "awaiting_approval"));
  assert.equal(writes, 0, "No writes before human approval");
  assert.equal(calls - initialCalls, 4, "Pause must not spend another model call");
  return { session, scope, material, run, pending };
}
try {
  await launch(initialExe, profile);
  assert.equal((await call("health")).host_ready, true);
  await until(() => call("save_settings", { model: "fixture", base_url: `${endpoint}/v1`, model_key: "local-fixture-key", odoo_url: endpoint, odoo_db: "fixture", odoo_username: "admin", odoo_key: "local-fixture-odoo" }).catch(error => {
    if (String(error).includes("CONFIG_BUSY")) return null;
    throw error;
  }), Boolean);
  assert.equal((await call("check_connection")).odoo.status, "connected");
  const old = await prepareApproval();
  await page.reload();
  await page.getByRole("button", { name: /连接设置/ }).waitFor();
  await page.screenshot({ path: join(output, "before-upgrade.png") });
  await app.close();
  app = undefined;
  const copy = join(output, "upgraded-profile");
  await cp(profile, copy, { recursive: true });
  await launch(upgradeExe, copy);
  const settings = await call("get_settings");
  assert.equal(settings.has_model_key, true);
  assert.equal(settings.has_odoo_key, true);
  assert.equal((await call("get_session", { session_id: old.session.id })).materials[0].sha256, old.material.sha256);
  const restored = await call("get_business", old.scope);
  assert.equal(restored.approvals[0].action_id, old.pending.approvals[0].action_id);
  assert.equal(restored.approvals[0].status, "interrupted", "Restart must invalidate old approval authority");
  await assert.rejects(call("decide_approval", { ...old.scope, action_id: restored.approvals[0].action_id, decision: "approve" }), /VALUEERROR/);
  assert.equal(writes, 0);
  await page.screenshot({ path: join(output, "after-upgrade.png") });
  const { session, scope, run, pending } = await prepareApproval();
  await call("decide_approval", { ...scope, action_id: pending.approvals[0].action_id, decision: "approve" });
  const completed = await until(() => call("get_business", scope), d => d.runs.some(r => r.id === run.id && r.status === "completed"));
  assert.equal(writes, 1, "Approval must execute exactly once");
  assert.equal(completed.approvals[0].status, "verified");
  const trace = await call("get_trace", scope);
  assert.ok(trace.tools.length > 0);
  const receipt = join(output, "business-receipt.json");
  await app.evaluate(({ dialog }, filePath) => { dialog.showSaveDialog = async () => ({ canceled: false, filePath }); }, receipt);
  const exported = await call("export_business_report", scope);
  assert.equal(exported.path, receipt);
  assert.ok(JSON.parse(await readFile(receipt, "utf8")));
  assert.ok((await call("get_business", scope)).artifacts.length > 0);
  await assert.rejects(call("_export_document", scope), /METHOD_NOT_ALLOWED/);
  await assert.rejects(call("open_odoo_record", { ...scope, model: "res.partner", record_id: 999999 }), /RECORD_NOT_OBSERVED/);
  await call("send_message", { session_id: session.id, text: "FIXTURE_CANCEL" });
  const active = await call("get_session", { session_id: session.id });
  const conversation = active.conversation_runs.find(r => r.status === "running");
  await call("cancel_conversation", { session_id: session.id, run_id: conversation.id });
  await until(() => call("get_session", { session_id: session.id }), d => !d.conversation_runs.some(r => r.status === "running"));
  await writeFile(join(output, "result.json"), JSON.stringify({ passed: true, real_ipc: true, paid_api_calls: 0, writes, model_fixture_calls: calls, upgraded: initialExe !== upgradeExe, scope }, null, 2));
  console.log(JSON.stringify({ passed: true, real_ipc: true, paid_api_calls: 0, writes, output }));
} finally {
  if (app) await app.close();
  server.closeAllConnections();
  await new Promise(r => server.close(r));
  await writeFile(join(output, "fixture-log.json"), JSON.stringify(log, null, 2));
}
