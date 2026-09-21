import { cp, mkdir, mkdtemp, readFile, readdir, rename, rm, writeFile } from "node:fs/promises";
import { existsSync, lstatSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { basename, dirname, join, resolve, sep } from "node:path";
import process from "node:process";
import { promisify } from "node:util";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(root, "..");
const pythonZip = process.env.WORKBENCH_PYTHON_ZIP ||
  join(repoRoot, ".runtime", "cache", "python-3.13.12-embed-amd64.zip");
const pythonSha256 = (process.env.WORKBENCH_PYTHON_SHA256 ||
  "76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0").toLowerCase();
const backendWheel = process.env.WORKBENCH_BACKEND_WHEEL || join(repoRoot, "dist", "erp_harness-0.5.4-py3-none-any.whl");
const sitePackages = process.env.WORKBENCH_SITE_PACKAGES ||
  join(repoRoot, ".venv", "Lib", "site-packages");
const pythonUrl = process.env.WORKBENCH_PYTHON_URL ||
  "https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip";

const resourcesRoot = resolve(root, "resources");
const finalDestination = resolve(resourcesRoot, "host");
const backupDestination = resolve(resourcesRoot, `.host-backup-${process.pid}`);
const ownedPath = (value) => {
  const resolved = resolve(value);
  if (resolved !== resourcesRoot && !resolved.startsWith(`${resourcesRoot}${sep}`)) {
    throw new Error(`Refusing path outside desktop/resources: ${resolved}`);
  }
  return resolved;
};
ownedPath(finalDestination);
ownedPath(backupDestination);
for (const candidate of [root, resourcesRoot, finalDestination, backupDestination]) {
  if (existsSync(candidate) && lstatSync(candidate).isSymbolicLink()) {
    throw new Error(`Refusing to replace a symlink or junction: ${candidate}`);
  }
}
let stagingDestination;
// A failed process must never leave a partial staging tree for the next run.
process.on("exit", () => {
  try { if (stagingDestination && existsSync(stagingDestination)) rmSync(stagingDestination, { recursive: true, force: true }); } catch { /* best effort during process exit */ }
});

if (!existsSync(backendWheel)) throw new Error("Build the backend wheel first: uv build --wheel");
if (!existsSync(sitePackages)) throw new Error(`Missing site-packages: ${sitePackages}`);
// Refuse an unrelated development environment instead of bundling its packages.
const pins = new Map((await readFile(join(root, "requirements-host.txt"), "utf8")).trim().split(/\r?\n/).map(line => line.split("==")));
const installed = new Map();
for (const entry of await readdir(sitePackages)) {
  if (!entry.endsWith(".dist-info")) continue;
  const metadata = await readFile(join(sitePackages, entry, "METADATA"), "utf8");
  const name = /^Name: (.+)$/m.exec(metadata)?.[1]?.trim();
  const version = /^Version: (.+)$/m.exec(metadata)?.[1]?.trim();
  if (name === "pip" || name === "pi-agent-python" || name === "erp-harness") continue;
  if (!name || pins.get(name) !== version) throw new Error(`Unpinned sidecar dependency: ${name}`);
  installed.set(name, version);
}
if (installed.size !== pins.size) throw new Error("Missing pinned sidecar dependencies");

await mkdir(resourcesRoot, { recursive: true });
stagingDestination = await mkdtemp(join(resourcesRoot, ".host-staging-"));
const destination = stagingDestination;

const execFile = promisify((file, args, options, callback) => {
  const child = spawn(file, args, options);
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  child.once("error", callback);
  child.once("close", (code) => code === 0 ? callback(null) : callback(new Error(stderr || `${file} exited ${code}`)));
});

async function download(url, destination) {
  const response = await fetch(url, { redirect: "follow", signal: AbortSignal.timeout(120_000) });
  if (!response.ok || !response.body) throw new Error(`Python download failed: HTTP ${response.status}`);
  await pipeline(Readable.fromWeb(response.body), createWriteStream(destination));
}

async function sha256(file) {
  const hash = createHash("sha256");
  const data = await readFile(file);
  return hash.update(data).digest("hex");
}

await mkdir(join(destination, "python"), { recursive: true });
await mkdir(join(destination, "app", "site-packages"), { recursive: true });
const archive = resolve(pythonZip);
if (!existsSync(archive)) {
  await mkdir(dirname(archive), { recursive: true });
  const partial = `${archive}.${process.pid}.part`;
  await rm(partial, { force: true });
  try {
    await download(pythonUrl, partial);
    if (await sha256(partial) !== pythonSha256) throw new Error("Downloaded Python archive SHA-256 mismatch");
    await rename(partial, archive);
  } catch (error) {
    await rm(partial, { force: true });
    throw error;
  }
}
const actualSha256 = await sha256(archive);
if (actualSha256 !== pythonSha256) {
  throw new Error(`Python archive SHA-256 mismatch: expected ${pythonSha256}, got ${actualSha256}`);
}
const pythonDir = join(destination, "python");
if (process.platform === "win32") {
  // Windows PowerShell may not have Microsoft.PowerShell.Archive available;
  // the inbox bsdtar handles the official embeddable ZIP without a module.
  await execFile("tar.exe", ["-xf", archive, "-C", pythonDir], { windowsHide: true });
} else {
  await execFile("unzip", ["-q", archive, "-d", pythonDir], {});
}
const banned = /^(mcp|mcp_types|odoo_mcp|erp_harness|pi_ai|pi_agent|pi_coding)(?:-|_|\.|$)/i;
const packaging = /^(pip|setuptools|wheel)(?:-|_|\.|$)/i;
const rejectJunk = (_source, entry) => {
  const name = basename(entry);
  return !name.endsWith(".pyc") && name !== "__pycache__" && name !== ".agents" && !name.endsWith(".pth") &&
    !banned.test(name) && !packaging.test(name);
};
const rejectSourceJunk = (_source, entry) => {
  const name = basename(entry);
  return !name.endsWith(".pyc") && name !== "__pycache__" && name.toLowerCase() !== "mcp.py";
};

await cp(sitePackages, join(destination, "app", "site-packages"), { recursive: true, filter: rejectJunk });
const pth = join(pythonDir, "python313._pth");
await writeFile(pth, "python313.zip\n.\n../app\n../app/site-packages\nimport site\n", "utf8");

// Wheels contain the installed product code and package resources.
if (process.platform === "win32") {
  await execFile("tar.exe", ["-xf", resolve(backendWheel), "-C", join(destination, "app")], { windowsHide: true });
} else {
  await execFile("unzip", ["-q", resolve(backendWheel), "-d", join(destination, "app")], {});
}
await mkdir(join(destination, "licenses"), { recursive: true });
await cp(join(repoRoot, "LICENSE"), join(destination, "licenses", "workbench-MIT.txt"));
await cp(join(repoRoot, "THIRD_PARTY_NOTICES.md"), join(destination, "licenses", "workbench-NOTICES.md"));
await cp(join(repoRoot, "docs", "licenses", "pi-agent-MIT.txt"), join(destination, "licenses", "pi-agent-MIT.txt"));
await cp(join(repoRoot, "docs", "licenses", "pi-agent-NOTICES.md"), join(destination, "licenses", "pi-agent-NOTICES.md"));
await cp(join(repoRoot, "mcp", "LICENSE"), join(destination, "licenses", "odoo-core-MIT.txt"));
const packageEntries = await readdir(join(destination, "app", "site-packages"), { withFileTypes: true });
for (const entry of packageEntries) {
  if (!rejectJunk("", entry.name)) {
    await rm(join(destination, "app", "site-packages", entry.name), { recursive: true, force: true });
  }
}
async function pruneCaches(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name);
    if (entry.isDirectory() && entry.name === "__pycache__") await rm(full, { recursive: true, force: true });
    else if (entry.isDirectory()) await pruneCaches(full);
  }
}
await pruneCaches(destination);
const manifestFiles = [];
async function collect(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const full = join(directory, entry.name);
    if (entry.isDirectory()) await collect(full);
    else if (!entry.name.endsWith(".pyc")) manifestFiles.push(full.slice(destination.length + 1).replaceAll("\\", "/"));
  }
}
await collect(destination);
async function digestDirectory(directory) {
  const digests = {};
  async function visit(current) {
    for (const entry of await readdir(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) await visit(full);
      else if (!entry.name.endsWith(".pyc")) digests[full.slice(destination.length + 1).replaceAll("\\", "/")] = await sha256(full);
    }
  }
  await visit(directory);
  return digests;
}
const sourceDigests = {};
for (const source of ["erp_harness", "site-packages"]) {
  sourceDigests[`app/${source}`] = await digestDirectory(join(destination, "app", source));
}
await writeFile(join(destination, "manifest.json"), JSON.stringify({
  python_version: "3.13.12",
  python_url: pythonUrl,
  python_archive_sha256: actualSha256,
  host_root: "app",
  backend_wheel: basename(backendWheel),
  backend_wheel_sha256: await sha256(backendWheel),
  site_packages: "app/site-packages",
  source_packages: ["app/erp_harness", "app/site-packages"],
  bundle_digests: sourceDigests,
  file_count: manifestFiles.length,
}, null, 2) + "\n", "utf8");
if (process.platform === "win32") {
  await execFile(join(pythonDir, "python.exe"), ["-B", "-c", [
    "import importlib.util, sys",
    "import erp_harness.app.host, erp_harness.app.runner, erp_harness.erp",
    "from erp_harness.tools.router import native_tool_catalog",
    "from erp_harness.erp._odoo_core.agent_tools import load_model_rename_catalog",
    "assert native_tool_catalog()",
    "assert load_model_rename_catalog().get('entries')",
    "assert importlib.util.find_spec('mcp') is None",
    "assert importlib.util.find_spec('odoo_mcp') is None",
    "assert not any(name.startswith(('pi_', 'textual', 'typer')) for name in sys.modules)",
  ].join("; ")], { windowsHide: true });
}
if (existsSync(backupDestination)) {
  if (existsSync(finalDestination)) throw new Error(`Refusing to overwrite an existing sidecar backup: ${backupDestination}`);
  await rename(backupDestination, finalDestination);
}
if (existsSync(finalDestination)) {
  await rename(finalDestination, backupDestination);
}
try {
  await rename(destination, finalDestination);
  if (existsSync(backupDestination)) await rm(backupDestination, { recursive: true, force: true });
} catch (error) {
  if (!existsSync(finalDestination) && existsSync(backupDestination)) {
    await rename(backupDestination, finalDestination);
  }
  throw error;
}
console.log(`Prepared CPython sidecar in ${finalDestination}`);
