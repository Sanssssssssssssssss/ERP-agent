import { rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import process from "node:process";

const desktop = resolve(fileURLToPath(new URL("..", import.meta.url)));
const electron = process.env.ELECTRON_BIN || join(desktop, "node_modules", "electron", "dist", "electron.exe");
const profile = join(desktop, "..", ".runtime", "desktop-acceptance", "profiles", `self-check-${process.pid}`);
const main = join(desktop, "out", "main", "index.js");
const child = spawn(electron, [`--user-data-dir=${profile}`, "--self-check", main], {
  cwd: desktop,
  windowsHide: true,
  stdio: ["ignore", "pipe", "pipe"],
});
let output = "";
child.stdout.on("data", (chunk) => { output += chunk; });
child.stderr.on("data", (chunk) => { output += chunk; });
const timer = setTimeout(() => child.kill(), 30_000);
const code = await new Promise((resolveExit, reject) => {
  child.once("error", reject);
  child.once("close", (exitCode, signal) => resolveExit(exitCode ?? (signal ? 1 : 0)));
});
clearTimeout(timer);
await rm(profile, { recursive: true, force: true });
if (code !== 0) {
  console.error(output.trim() || `Electron self-check exited with ${code}`);
  process.exit(code || 1);
}
console.log(output.trim());
