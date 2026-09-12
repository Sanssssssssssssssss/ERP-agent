import assert from "node:assert/strict";
import { mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { _electron as electron } from "playwright";

const desktop = resolve(import.meta.dirname, "..");
const executable = process.env.WORKBENCH_PACKAGED_EXE ||
  join(desktop, "dist", "win-unpacked", "Odoo Workbench.exe");
const profile = join(desktop, "..", ".runtime", "package-check", `profile-${process.pid}`);
await mkdir(resolve(profile, ".."), { recursive: true });
const environment = { ...process.env };
for (const name of ["ELECTRON_RUN_AS_NODE", "WORKBENCH_HOST_ROOT", "WORKBENCH_PYTHON", "WORKBENCH_SITE_PACKAGES", "WORKBENCH_PYTHON_ZIP"]) {
  delete environment[name];
}
const application = await electron.launch({
  executablePath: executable,
  args: [`--user-data-dir=${profile}`],
  env: environment,
  timeout: 30_000,
});
try {
  const page = await application.firstWindow();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.getByRole("button", { name: /连接设置/ }).waitFor();
  const health = await page.evaluate(() => window.workbench.call("health", {}));
  assert.equal(health.host_ready, true);
  assert.equal(health.odoo.status, "unconfigured");
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ packaged: true, host_ready: true, odoo: "unconfigured", page_errors: 0 }));
} finally {
  await application.close();
  await rm(profile, { recursive: true, force: true });
}
