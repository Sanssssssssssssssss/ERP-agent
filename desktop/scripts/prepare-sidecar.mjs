import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { basename, dirname, join, resolve } from "node:path";
import process from "node:process";
import { promisify } from "node:util";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const repoRoot = resolve(root, "..");
const pythonZip = process.env.WORKBENCH_PYTHON_ZIP ||
  join(repoRoot, ".runtime", "desktop-acceptance", "downloads", "python-3.13.12-embed-amd64.zip");
const pythonSha256 = (process.env.WORKBENCH_PYTHON_SHA256 ||
  "76f238f606250c87c6beac75dccd35ee99070a13490555936abb6cb64ecce3d0").toLowerCase();
const hostRoot = repoRoot;
const sitePackages = process.env.WORKBENCH_SITE_PACKAGES ||
  join(repoRoot, ".runtime", "stage7-clean-win-final", "Lib", "site-packages");
const pythonUrl = process.env.WORKBENCH_PYTHON_URL ||
  "https://www.python.org/ftp/python/3.13.12/python-3.13.12-embed-amd64.zip";

if (!existsSync(join(hostRoot, "workbench"))) {
  throw new Error("The sidecar inputs must contain workbench and the native site-packages directory.");
}
if (!existsSync(sitePackages)) throw new Error(`Missing site-packages: ${sitePackages}`);
// Refuse an unrelated development environment instead of bundling its packages.
const pins = new Map((await readFile(join(root, "requirements-host.txt"), "utf8")).trim().split(/\r?\n/).map(line => line.split("==")));
const installed = new Map();
for (const entry of await readdir(sitePackages)) {
  if (!entry.endsWith(".dist-info")) continue;
  const metadata = await readFile(join(sitePackages, entry, "METADATA"), "utf8");
  const name = /^Name: (.+)$/m.exec(metadata)?.[1]?.trim();
  const version = /^Version: (.+)$/m.exec(metadata)?.[1]?.trim();
  if (name === "pip" || name === "pi-agent-python") continue;
  if (!name || pins.get(name) !== version) throw new Error(`Unpinned sidecar dependency: ${name}`);
  installed.set(name, version);
}
if (installed.size !== pins.size) throw new Error("Missing pinned sidecar dependencies");

const execFile = promisify((file, args, options, callback) => {
  const child = spawn(file, args, options);
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  child.once("error", callback);
  child.once("close", (code) => code === 0 ? callback(null) : callback(new Error(stderr || `${file} exited ${code}`)));
});

async function download(url, destination) {
  const response = await fetch(url, { redirect: "follow" });
  if (!response.ok || !response.body) throw new Error(`Python download failed: HTTP ${response.status}`);
  const file = createWriteStream(destination);
  const reader = response.body.getReader();
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    file.write(Buffer.from(chunk.value));
  }
  await new Promise((resolve, reject) => { file.end(resolve); file.on("error", reject); });
}

async function sha256(file) {
  const hash = createHash("sha256");
  const data = await readFile(file);
  return hash.update(data).digest("hex");
}

const destination = join(root, "resources", "host");
await rm(destination, { recursive: true, force: true });
await mkdir(join(destination, "python"), { recursive: true });
await mkdir(join(destination, "app", "site-packages"), { recursive: true });
const archive = resolve(pythonZip);
if (!existsSync(archive)) {
  await mkdir(dirname(archive), { recursive: true });
  await download(pythonUrl, archive);
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
const banned = /^(mcp|mcp_types|odoo_mcp|pi_ai|pi_agent|pi_coding)(?:-|_|\.|$)/i;
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
await cp(join(hostRoot, "workbench"), join(destination, "app", "workbench"), { recursive: true, filter: rejectSourceJunk });
await cp(sitePackages, join(destination, "app", "site-packages"), { recursive: true, filter: rejectJunk });
const pth = join(pythonDir, "python313._pth");
await writeFile(pth, "python313.zip\n.\n../app\n../app/site-packages\nimport site\n", "utf8");
for (const packageName of ["pi_ai", "pi_agent", "pi_coding"]) {
  await cp(join(repoRoot, "agent", "src", packageName), join(destination, "app", packageName), { recursive: true, filter: rejectSourceJunk });
}
await cp(join(repoRoot, "odoo_runtime"), join(destination, "app", "odoo_runtime"), { recursive: true, filter: rejectSourceJunk });
const integrationFiles = ["__init__.py", "pi_odoo_runner.py", "stream_events.py", "odoo_tools.py", "world_context.py", "native_tool_catalog.json"];
await mkdir(join(destination, "app", "integration"), { recursive: true });
for (const file of integrationFiles) {
  const source = join(repoRoot, "integration", file);
  if (existsSync(source)) await cp(source, join(destination, "app", "integration", file), { filter: rejectJunk });
}
await mkdir(join(destination, "licenses"), { recursive: true });
await cp(join(repoRoot, "LICENSE"), join(destination, "licenses", "workbench-MIT.txt"));
await cp(join(repoRoot, "THIRD_PARTY_NOTICES.md"), join(destination, "licenses", "workbench-NOTICES.md"));
await cp(join(repoRoot, "agent", "LICENSE"), join(destination, "licenses", "pi-agent-MIT.txt"));
await cp(join(repoRoot, "agent", "THIRD_PARTY_NOTICES.md"), join(destination, "licenses", "pi-agent-NOTICES.md"));
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
for (const source of ["workbench", "pi_ai", "pi_agent", "pi_coding", "integration", "odoo_runtime", "site-packages"]) {
  sourceDigests[`app/${source}`] = await digestDirectory(join(destination, "app", source));
}
await writeFile(join(destination, "manifest.json"), JSON.stringify({
  python_version: "3.13.12",
  python_url: pythonUrl,
  python_archive_sha256: actualSha256,
  host_root: "app/workbench",
  site_packages: "app/site-packages",
  source_packages: ["app/workbench", "app/site-packages", "app/pi_ai", "app/pi_agent", "app/pi_coding", "app/integration", "app/odoo_runtime"],
  bundle_digests: sourceDigests,
  file_count: manifestFiles.length,
}, null, 2) + "\n", "utf8");
if (process.platform === "win32") {
  await execFile(join(pythonDir, "python.exe"), ["-B", "-c", [
    "import importlib.util, sys",
    "import workbench.host, integration.pi_odoo_runner, odoo_runtime",
    "assert importlib.util.find_spec('mcp') is None",
    "assert importlib.util.find_spec('odoo_mcp') is None",
    "assert 'pi_agent.mcp' not in sys.modules",
  ].join("; ")], { windowsHide: true });
}
console.log(`Prepared CPython sidecar in ${destination}`);
