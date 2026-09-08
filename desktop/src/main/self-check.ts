import assert from "node:assert/strict";
import { app } from "electron";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { publicSettings, saveSettings } from "./settings";
import { assertRequest, canChangeSettings } from "./ipc-security";

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
  console.log("desktop self-check: PASS (settings, secrets, busy guard, IPC allowlist)");
  } finally {
    app.setPath("userData", previous);
    await rm(isolated, { recursive: true, force: true });
  }
}
