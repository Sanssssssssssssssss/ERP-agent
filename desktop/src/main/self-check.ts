import assert from "node:assert/strict";
import { app } from "electron";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { publicSettings, saveSettings } from "./settings";
import { assertRequest, businessScope, canChangeSettings, observedRecordUrl, recordedArtifactPath, safeMaterialName, strictBase64 } from "./ipc-security";
import { safeErrorMessage } from "./host";

export async function runSelfCheck(): Promise<void> {
  // Even a manually invoked packaged --self-check must not overwrite settings.
  const previous = app.getPath("userData");
  const isolated = await mkdtemp(join(tmpdir(), "odoo-workbench-self-check-"));
  app.setPath("userData", isolated);
  try {
  const initial = await publicSettings();
  assert.equal("model_key" in initial, false);
  assert.equal("odoo_key" in initial, false);

  await assert.rejects(
    () => saveSettings({ base_url: "http://example.com" }),
    /BASE_URL_PROTOCOL_INVALID/,
  );
  await assert.rejects(
    () => saveSettings({ model_key: 123 } as never),
    /CONFIG_INPUT_INVALID/,
  );
  await assert.rejects(
    () => saveSettings({ odoo_url: "https://user:password@example.com" }),
    /ODOO_URL_CREDENTIALS_INVALID/,
  );
  await assert.rejects(
    () => saveSettings({ odoo_url: "http://127.0.0.1:8069/?api_key=secret" }),
    /ODOO_URL_CREDENTIALS_INVALID/,
  );

  const saved = await saveSettings({
    model: "self-check-model",
    base_url: "https://provider.invalid/v1",
    odoo_url: "http://127.0.0.1:8069",
    model_key: "SELF_CHECK_SENTINEL",
  });
  assert.equal(saved.has_model_key, true);
  assert.equal(JSON.stringify(saved).includes("SELF_CHECK_SENTINEL"), false);
  const reread = await publicSettings();
  assert.equal(JSON.stringify(reread).includes("SELF_CHECK_SENTINEL"), false);

  assert.equal(canChangeSettings(false, false), true);
  assert.equal(canChangeSettings(true, false), false);
  assert.equal(canChangeSettings(false, true), false);
  assert.doesNotThrow(() => assertRequest({ method: "health", params: {} }));
  assert.doesNotThrow(() => assertRequest({ method: "check_connection", params: {} }));
  assert.doesNotThrow(() => assertRequest({ method: "get_settings" }));
  assert.throws(() => assertRequest({ method: "shell_exec" }), /METHOD_NOT_ALLOWED/);
  assert.throws(() => assertRequest({ method: "health", params: [] }), /INVALID_PARAMS/);
  assert.doesNotThrow(() => assertRequest({ method: "export_business_report", params: { session_id: "s_a", business_id: "b_a" } }));
  assert.deepEqual(businessScope({ session_id: "s_a", business_id: "b_a", path: "ignored" }), { session_id: "s_a", business_id: "b_a" });
  assert.throws(() => businessScope({ session_id: "s_a", business_id: "../b" }), /INVALID_BUSINESS_SCOPE/);
  assert.throws(() => businessScope({ session_id: "s_a", business_id: "b_a", run_id: null }), /INVALID_BUSINESS_SCOPE/);
  const documents = [{ id: "42", model: "sale.order", name: "SO42", state: "sale", fields: {}, source: "native_read_receipt" }];
  assert.equal(observedRecordUrl("http://127.0.0.1:18069", "demo", documents, "sale.order", 42), "http://127.0.0.1:18069/web?db=demo#id=42&model=sale.order&view_type=form");
  assert.throws(() => observedRecordUrl("https://odoo.example", "demo", documents, "account.move", 42), /RECORD_NOT_OBSERVED/);
  assert.throws(() => observedRecordUrl("https://odoo.example", "demo", documents, "sale.order", 43), /RECORD_NOT_OBSERVED/);
  assert.throws(() => observedRecordUrl("file:///C:/Windows", "demo", documents, "sale.order", 42), /PROTOCOL_INVALID/);
  assert.throws(() => observedRecordUrl("https://odoo.example/?secret=x", "demo", documents, "sale.order", 42), /CREDENTIALS_INVALID/);
  const artifactPath = join(isolated, "receipt.json");
  assert.equal(recordedArtifactPath([{ id: "a_receipt", path: artifactPath }], "a_receipt"), artifactPath);
  assert.throws(() => recordedArtifactPath([{ id: "a_receipt", path: artifactPath }], "a_other_business"), /NOT_FOUND/);
  assert.throws(() => recordedArtifactPath([{ id: "a_receipt", path: join(isolated, "program.exe") }], "a_receipt"), /FORMAT_INVALID/);
  assert.throws(() => recordedArtifactPath([{ id: "a_receipt", path: "https://example.com/receipt.json" }], "a_receipt"), /FORMAT_INVALID/);
  assert.throws(() => assertRequest({ method: "_record_artifact", params: { path: artifactPath } }), /METHOD_NOT_ALLOWED/);
  assert.doesNotThrow(() => assertRequest({ method: "import_material", params: { session_id: "s_a", name: "items.csv", content_base64: "YQ==" } }));
  assert.doesNotThrow(() => assertRequest({ method: "download_document", params: { session_id: "s_a", business_id: "b_a", model: "sale.order", record_id: 1, format: "pdf" } }));
  assert.equal(safeMaterialName("items.csv"), "items.csv");
  assert.throws(() => safeMaterialName("..\\items.csv"), /MATERIAL_NAME_INVALID/);
  assert.throws(() => safeMaterialName("items.pdf"), /MATERIAL_FORMAT_INVALID/);
  assert.equal(strictBase64("YQ==").toString("utf8"), "a");
  assert.throws(() => strictBase64("not base64"), /MATERIAL_BASE64_INVALID/);
  assert.equal(recordedArtifactPath([{ id: "a_pdf", path: join(isolated, "doc.pdf"), kind: "odoo_pdf" }], "a_pdf"), join(isolated, "doc.pdf"));
  assert.throws(() => recordedArtifactPath([{ id: "a_pdf", path: join(isolated, "doc.json"), kind: "odoo_pdf" }], "a_pdf"), /FORMAT_INVALID/);
  assert.equal(safeErrorMessage("ODOO_NOT_CONFIGURED", "internal detail"), "[ODOO_NOT_CONFIGURED] Odoo 尚未配置，请先填写连接设置。");
  assert.equal(safeErrorMessage("ODOO_BUSINESS_CONNECTION_MISMATCH", "internal detail"), "[ODOO_BUSINESS_CONNECTION_MISMATCH] 当前业务属于其他 Odoo 连接，请切回原连接或新建业务。");
  assert.equal(safeErrorMessage("KeyError", "'unknown proposal'"), "[KEYERROR] 未找到可处理的业务提案，可能已处理或已过期。");
  assert.equal(safeErrorMessage("ValueError", "material exceeds the 2 MiB limit"), "[VALUEERROR] 材料超过 2 MiB 大小限制。");
  assert.equal(safeErrorMessage("ValueError", "DOCUMENT_PDF_UNAVAILABLE"), "[VALUEERROR] 当前单据没有可下载的正式 PDF，请改用 CSV 或先在 Odoo 生成正式报表。");
  assert.equal(safeErrorMessage("ValueError", "DOCUMENT_INVOICE_PDF_NOT_GENERATED"), "[VALUEERROR] 发票已过账，但尚未生成正式 PDF，请先生成发票文件后再下载。");
  assert.equal(safeErrorMessage("ValueError", "document is not observed in this business"), "[VALUEERROR] 该单据未被当前业务观测，无法下载。");
  const generic = safeErrorMessage("business_validation_failed", "Authorization: Bearer sk_actual_123 Cookie: a=abc; b=xyz");
  assert.equal(generic, "[BUSINESS_VALIDATION_FAILED] 请求失败，请检查当前操作状态后重试。");
  assert.doesNotMatch(generic, /sk_actual_123|a=abc|b=xyz/);
  console.log("desktop self-check: PASS (settings, secrets, busy guard, IPC allowlist)");
  } finally {
    app.setPath("userData", previous);
    await rm(isolated, { recursive: true, force: true });
  }
}
