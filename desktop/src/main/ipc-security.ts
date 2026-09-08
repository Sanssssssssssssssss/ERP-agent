import type { WorkbenchMethod } from "../shared/protocol";

export const METHODS = new Set<WorkbenchMethod>([
  "list_sessions", "create_session", "rename_session", "archive_session", "get_session",
  "send_message", "confirm_business", "get_business", "start_run", "decide_approval",
  "cancel_run", "get_trace", "refresh_business", "health",
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
       candidate.method !== "get_settings" && candidate.method !== "save_settings")) {
    throw new Error("METHOD_NOT_ALLOWED");
  }
  if (candidate.params !== undefined &&
      (!candidate.params || typeof candidate.params !== "object" || Array.isArray(candidate.params))) {
    throw new Error("INVALID_PARAMS");
  }
}
