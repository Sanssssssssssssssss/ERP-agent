import { app, safeStorage } from "electron";
import { randomUUID } from "node:crypto";
import { promises as fs } from "node:fs";
import { join } from "node:path";
import type { Settings, SettingsInput } from "../shared/protocol";

interface StoredSettings {
  model?: string;
  base_url?: string;
  odoo_url?: string;
  odoo_db?: string;
  odoo_username?: string;
  model_key?: string;
  odoo_key?: string;
}

const SETTINGS_FILE = "settings.json";
const DEFAULTS: StoredSettings = {
  model: "",
  base_url: "",
  odoo_url: "",
  odoo_db: "bench",
  odoo_username: "admin",
};

function validateEndpoint(name: string, value: string): void {
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
    base_url: stored.base_url ?? "",
    odoo_url: stored.odoo_url ?? "",
    odoo_db: stored.odoo_db ?? "",
    odoo_username: stored.odoo_username ?? "",
    has_model_key: Boolean(unprotect(stored.model_key)),
    has_odoo_key: Boolean(unprotect(stored.odoo_key)),
    environment: unprotect(stored.model_key) && unprotect(stored.odoo_key) ? "configured" : "demo",
  };
}

export async function saveSettings(input: SettingsInput): Promise<Settings> {
  const allowed = new Set(["model", "base_url", "odoo_url", "odoo_db", "odoo_username", "model_key", "odoo_key"]);
  if (Object.entries(input).some(([key, value]) => !allowed.has(key) || typeof value !== "string" || value.length > 8192)) {
    throw new Error("CONFIG_INPUT_INVALID");
  }
  const current = await read();
  const next: StoredSettings = { ...current };
  for (const field of ["model", "base_url", "odoo_url", "odoo_db", "odoo_username"] as const) {
    const value = input[field];
    if (typeof value === "string") next[field] = value.trim();
  }
  validateEndpoint("base_url", next.base_url ?? "");
  validateEndpoint("odoo_url", next.odoo_url ?? "");
  if (typeof input.model_key === "string" && input.model_key.length > 0) next.model_key = protect(input.model_key);
  if (typeof input.odoo_key === "string" && input.odoo_key.length > 0) next.odoo_key = protect(input.odoo_key);
  await write(next);
  return publicSettings();
}

export async function secretEnvironment(): Promise<Record<string, string>> {
  const stored = await read();
  const env: Record<string, string> = {};
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
