import type { Document, WorkbenchMethod } from "../shared/protocol";
import { validateEndpoint } from "./settings";

const LOCAL_METHODS = new Set(["get_settings", "save_settings", "export_business_report", "open_odoo_record"]);

export const METHODS = new Set<WorkbenchMethod>([
  "list_sessions", "create_session", "rename_session", "archive_session", "get_session",
  "send_message", "confirm_business", "get_business", "start_run", "decide_approval",
  "cancel_run", "get_trace", "refresh_business", "reconcile_action", "health", "check_connection",
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

export function observedRecordUrl(endpoint: string, documents: Document[], model: unknown, recordId: unknown): string {
  if (!endpoint) throw new Error("ODOO_NOT_CONFIGURED");
  validateEndpoint("odoo_url", endpoint);
  if (typeof model !== "string" || !/^[a-z][a-z0-9_.]*$/.test(model) ||
      typeof recordId !== "number" || !Number.isSafeInteger(recordId) || recordId <= 0 ||
      !documents.some(document => document.model === model && String(document.id) === String(recordId))) {
    throw new Error("RECORD_NOT_OBSERVED");
  }
  const url = new URL(endpoint);
  url.pathname = `${url.pathname.replace(/\/$/, "")}/web`;
  url.hash = new URLSearchParams({ id: String(recordId), model, view_type: "form" }).toString();
  return url.href;
}
