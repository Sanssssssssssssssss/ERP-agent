import { app, safeStorage } from "electron";
import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import { basename, isAbsolute, join, relative, resolve, sep } from "node:path";
import type { Settings, SettingsInput } from "../shared/protocol";

interface StoredSettings {
  model?: string;
  base_url?: string;
  odoo_url?: string;
  odoo_db?: string;
  odoo_username?: string;
  model_key?: string;
  odoo_key?: string;
  long_term_memory?: boolean;
  data_dir?: string;
}

const SETTINGS_FILE = "settings.json";
const DEFAULTS: StoredSettings = {
  model: "",
  base_url: "",
  odoo_url: "",
  odoo_db: "bench",
  odoo_username: "admin",
  long_term_memory: false,
};

export function validateEndpoint(name: string, value: string): void {
  if (!value) return;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error(`${name.toUpperCase()}_INVALID`);
  }
  const loopback = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback.has(parsed.hostname))) {
    throw new Error(`${name.toUpperCase()}_PROTOCOL_INVALID`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${name.toUpperCase()}_CREDENTIALS_INVALID`);
  }
}

function path(): string {
  return join(app.getPath("userData"), SETTINGS_FILE);
}

function protect(value: string): string {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("SECURE_STORAGE_UNAVAILABLE");
  }
  return `safe:${safeStorage.encryptString(value).toString("base64")}`;
}

function unprotect(value: string | undefined): string | undefined {
  if (!value) return undefined;
  if (!value.startsWith("safe:")) return undefined;
  if (!safeStorage.isEncryptionAvailable()) return undefined;
  try {
    return safeStorage.decryptString(Buffer.from(value.slice(5), "base64"));
  } catch {
    return undefined;
  }
}

async function read(): Promise<StoredSettings> {
  try {
    const raw = await fs.readFile(path(), "utf8");
    return { ...DEFAULTS, ...(JSON.parse(raw) as StoredSettings) };
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === "ENOENT") return { ...DEFAULTS };
    throw new Error("CONFIG_READ_FAILED");
  }
}

async function write(settings: StoredSettings): Promise<void> {
  const file = path();
  await fs.mkdir(join(file, ".."), { recursive: true });
  const temporary = `${file}.${randomUUID()}.tmp`;
  await fs.writeFile(temporary, JSON.stringify(settings, null, 2), { encoding: "utf8", mode: 0o600 });
  await fs.rename(temporary, file);
}

export async function publicSettings(): Promise<Settings> {
  const stored = await read();
  return {
    model: stored.model ?? "",
    data_dir: stored.data_dir || join(app.getPath("userData"), "data"),
    base_url: stored.base_url ?? "",
    odoo_url: stored.odoo_url ?? "",
    odoo_db: stored.odoo_db ?? "",
    odoo_username: stored.odoo_username ?? "",
    long_term_memory: stored.long_term_memory === true && process.env.ERP_MEMORY_MODE !== "off",
    has_model_key: Boolean(unprotect(stored.model_key)),
    has_odoo_key: Boolean(unprotect(stored.odoo_key)),
    environment: unprotect(stored.model_key) && unprotect(stored.odoo_key) ? "configured" : "demo",
  };
}

export async function dataDirectory(): Promise<string> {
  return (await publicSettings()).data_dir!;
}

// Called only after the host has stopped. Publish a complete copy; never merge
// another store or alter the source, including unresolved action ledgers.
export async function copyDataDirectory(source: string, destination: string): Promise<void> {
  const sourceRoot = await fs.realpath(source).catch((cause: NodeJS.ErrnoException) => {
    if (cause.code === "ENOENT") return resolve(source);
    throw cause;
  });
  const parent = await fs.realpath(join(destination, ".."));
  const target = join(parent, basename(destination));
  const contains = (root: string, child: string) => {
    const part = relative(root, child);
    return part === "" || (!part.startsWith(`..${sep}`) && part !== ".." && !isAbsolute(part));
  };
  if (contains(sourceRoot, target) || contains(target, sourceRoot)) throw new Error("DATA_DIRECTORY_OVERLAP");
  try { await fs.lstat(target); throw new Error("DATA_DIRECTORY_EXISTS"); }
  catch (cause) { if ((cause as NodeJS.ErrnoException).code !== "ENOENT") throw cause; }
  const staging = await fs.mkdtemp(join(parent, ".erp-agent-copy-"));
  try {
    const exists = await fs.stat(sourceRoot).catch((cause: NodeJS.ErrnoException) => {
      if (cause.code === "ENOENT") return undefined;
      throw cause;
    });
    if (exists) for (const entry of await fs.readdir(sourceRoot)) await fs.cp(join(sourceRoot, entry), join(staging, entry), { recursive: true, force: false, errorOnExist: true,
      filter: async (file) => {
        if ((await fs.lstat(file)).isSymbolicLink()) throw new Error("DATA_DIRECTORY_SYMLINK");
        return relative(sourceRoot, file) !== "workbench-state.lock";
      },
    });
    const stateFile = join(staging, "workbench-state.json");
    const raw = await fs.readFile(stateFile, "utf8").catch((cause: NodeJS.ErrnoException) => {
      if (cause.code === "ENOENT") return undefined;
      throw cause;
    });
    if (raw) {
      const state = JSON.parse(raw);
      const rebase = (row: Record<string, unknown>, key: string) => {
        const value = row[key];
        if (typeof value === "string" && isAbsolute(value) && contains(sourceRoot, value)) row[key] = join(target, relative(sourceRoot, value));
      };
      for (const row of Object.values(state.materials || {}) as Record<string, unknown>[]) {
        rebase(row, "path"); rebase(row, "extracted_path");
      }
      for (const business of Object.values(state.businesses || {}) as { artifacts?: Record<string, unknown>[] }[]) {
        for (const artifact of business.artifacts || []) rebase(artifact, "path");
      }
      await fs.writeFile(stateFile, JSON.stringify(state), "utf8");
    }
    await fs.rename(staging, target);
  } finally {
    // Only this mkdtemp-created staging directory is removed, never source/target.
    await fs.rm(staging, { recursive: true, force: true });
  }
}

export async function saveSettings(input: SettingsInput, activate?: () => Promise<void>): Promise<Settings> {
  const allowed = new Set(["model", "base_url", "odoo_url", "odoo_db", "odoo_username", "model_key", "odoo_key", "long_term_memory", "data_dir"]);
  if (Object.entries(input).some(([key, value]) => !allowed.has(key) || (key === "long_term_memory"
    ? typeof value !== "boolean"
    : typeof value !== "string" || value.length > 8192))) {
    throw new Error("CONFIG_INPUT_INVALID");
  }
  const current = await read();
  const next: StoredSettings = { ...current };
  if (typeof input.long_term_memory === "boolean") next.long_term_memory = input.long_term_memory;
  for (const field of ["model", "base_url", "odoo_url", "odoo_db", "odoo_username"] as const) {
    const value = input[field];
    if (typeof value === "string") next[field] = value.trim();
  }
  validateEndpoint("base_url", next.base_url ?? "");
  validateEndpoint("odoo_url", next.odoo_url ?? "");
  if (typeof input.model_key === "string" && input.model_key.length > 0) next.model_key = protect(input.model_key);
  if (typeof input.odoo_key === "string" && input.odoo_key.length > 0) next.odoo_key = protect(input.odoo_key);
  const previousDirectory = current.data_dir || join(app.getPath("userData"), "data");
  if (input.data_dir !== undefined && input.data_dir !== previousDirectory) {
    if (!input.data_dir || !isAbsolute(input.data_dir) || input.data_dir.includes("\0")) throw new Error("DATA_DIRECTORY_INVALID");
    next.data_dir = resolve(input.data_dir);
    if (relative(previousDirectory, next.data_dir) !== "") await copyDataDirectory(previousDirectory, next.data_dir);
  }
  await write(next);
  if (activate) {
    try { await activate(); }
    catch (cause) { await write(current); throw cause; }
  }
  return publicSettings();
}

export async function secretEnvironment(): Promise<Record<string, string>> {
  const stored = await read();
  const env: Record<string, string> = {
    ERP_MEMORY_MODE: stored.long_term_memory === true && process.env.ERP_MEMORY_MODE !== "off" ? "on" : "off",
  };
  const modelKey = unprotect(stored.model_key);
  const odooKey = unprotect(stored.odoo_key);
  if (modelKey) env.LLM_API_KEY = modelKey;
  if (odooKey) env.ODOO_API_KEY = odooKey;
  for (const [input, output] of Object.entries({
    model: "LLM_MODEL",
    base_url: "LLM_BASE_URL",
    odoo_url: "ODOO_URL",
    odoo_db: "ODOO_DB",
    odoo_username: "ODOO_USERNAME",
  })) {
    const value = stored[input as keyof StoredSettings];
    if (typeof value === "string" && value) env[output] = value;
  }
  return env;
}
