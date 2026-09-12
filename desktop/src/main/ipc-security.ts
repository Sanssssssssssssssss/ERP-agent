import { basename, extname, isAbsolute } from "node:path";
import type { Document, WorkbenchMethod } from "../shared/protocol";
import { validateEndpoint } from "./settings";

const LOCAL_METHODS = new Set(["get_settings", "save_settings", "import_material", "download_document", "export_business_report", "open_odoo_record", "open_business_artifact", "reveal_business_artifact"]);

export const METHODS = new Set<WorkbenchMethod>([
  "list_sessions", "create_session", "rename_session", "archive_session", "get_session",
  "send_message", "confirm_business", "get_business", "check_business_connection", "start_run", "decide_approval",
  "cancel_run", "cancel_conversation", "get_trace", "refresh_business", "reconcile_action", "health", "check_connection",
]);

export interface ValidIpcRequest {
  method: WorkbenchMethod | "get_settings" | "save_settings";
  params?: Record<string, unknown>;
}

export function canChangeSettings(settingsChanging: boolean, hostBusy: boolean): boolean {
  return !settingsChanging && !hostBusy;
}

export function assertRequest(request: unknown): asserts request is ValidIpcRequest {
  if (!request || typeof request !== "object" || Array.isArray(request)) throw new Error("INVALID_REQUEST");
  const candidate = request as { method?: unknown; params?: unknown };
  if (typeof candidate.method !== "string" ||
      (!METHODS.has(candidate.method as WorkbenchMethod) &&
       !LOCAL_METHODS.has(candidate.method))) {
    throw new Error("METHOD_NOT_ALLOWED");
  }
  if (candidate.params !== undefined &&
      (!candidate.params || typeof candidate.params !== "object" || Array.isArray(candidate.params))) {
    throw new Error("INVALID_PARAMS");
  }
}

export function businessScope(params: Record<string, unknown>): { session_id: string; business_id: string; run_id?: string } {
  for (const key of ["session_id", "business_id", ...(params.run_id === undefined ? [] : ["run_id"])]) {
    if (typeof params[key] !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(params[key])) throw new Error("INVALID_BUSINESS_SCOPE");
  }
  return { session_id: params.session_id as string, business_id: params.business_id as string,
    ...(params.run_id === undefined ? {} : { run_id: params.run_id as string }) };
}

export function observedRecordUrl(endpoint: string, database: string, documents: Document[], model: unknown, recordId: unknown): string {
  if (!endpoint) throw new Error("ODOO_NOT_CONFIGURED");
  if (!database || !/^[\w.-]{1,128}$/.test(database)) throw new Error("ODOO_NOT_CONFIGURED");
  validateEndpoint("odoo_url", endpoint);
  if (typeof model !== "string" || !/^[a-z][a-z0-9_.]*$/.test(model) ||
      typeof recordId !== "number" || !Number.isSafeInteger(recordId) || recordId <= 0 ||
      !documents.some(document => document.model === model && String(document.id) === String(recordId))) {
    throw new Error("RECORD_NOT_OBSERVED");
  }
  const url = new URL(endpoint);
  url.pathname = `${url.pathname.replace(/\/$/, "")}/web`;
  url.search = new URLSearchParams({ db: database }).toString();
  url.hash = new URLSearchParams({ id: String(recordId), model, view_type: "form" }).toString();
  return url.href;
}

export function recordedArtifactPath(artifacts: ReadonlyArray<{ id: string; path: string; kind?: string }>, artifactId: unknown): string {
  if (typeof artifactId !== "string") throw new Error("ARTIFACT_NOT_FOUND");
  const artifact = artifacts.find(item => item.id === artifactId);
  if (!artifact) throw new Error("ARTIFACT_NOT_FOUND");
  // Only files registered after the native receipt save dialog can be opened.
  const extension = extname(artifact.path).toLowerCase();
  const kindExtension: Record<string, string> = { business_receipt: ".json", odoo_pdf: ".pdf", odoo_csv: ".csv" };
  if (!isAbsolute(artifact.path) || !kindExtension[artifact.kind ?? "business_receipt"] || extension !== kindExtension[artifact.kind ?? "business_receipt"]) throw new Error("ARTIFACT_FORMAT_INVALID");
  return artifact.path;
}

export function materialSessionId(value: unknown): string {
  if (typeof value !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(value)) throw new Error("INVALID_SESSION_SCOPE");
  return value;
}

export function safeMaterialName(value: unknown): string {
  if (typeof value !== "string" || value.length < 1 || value.length > 255 || basename(value) !== value || value === "." || value === ".." || value.includes("\0")) throw new Error("MATERIAL_NAME_INVALID");
  const extension = extname(value).toLowerCase();
  if (!new Set([".csv", ".txt"]).has(extension)) throw new Error("MATERIAL_FORMAT_INVALID");
  return value;
}

export function strictBase64(value: unknown, maxDecodedBytes = 2 * 1024 * 1024): Buffer {
  const maxEncodedLength = 4 * Math.ceil(maxDecodedBytes / 3) + 4;
  if (typeof value !== "string" || value.length < 4 || value.length > maxEncodedLength || value.length % 4 !== 0 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) throw new Error("MATERIAL_BASE64_INVALID");
  const decoded = Buffer.from(value, "base64");
  if (decoded.length > maxDecodedBytes) throw new Error("MATERIAL_TOO_LARGE");
  return decoded;
}
