export type BusinessType = "sale_invoice";
export type Role = "user" | "assistant" | "system";
export type ApprovalDecision = "approve" | "reject";

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  status: string;
}

export interface Message {
  id: string;
  role: Role;
  text: string;
  created_at: string;
  business_id?: string;
  proposal?: BusinessProposal;
}

export interface BusinessProposal {
  id: string;
  type: BusinessType;
  title: string;
  goal: string;
  status: "pending" | "confirmed" | "rejected";
}

export interface Business {
  id: string;
  session_id: string;
  type: BusinessType;
  title: string;
  goal: string;
  status: string;
  created_at: string;
  updated_at: string;
  active_run_id?: string;
}

export interface Run {
  id: string;
  business_id: string;
  session_id: string;
  status: string;
  started_at: string;
  ended_at?: string;
  error?: string;
  error_detail?: string;
  usage?: Usage;
  tool_count?: number;
  model_rounds?: number;
  elapsed_seconds?: number;
  verification_status?: string;
}

export interface Approval {
  action_id: string;
  run_id: string;
  business_id: string;
  status: string;
  title: string;
  model: string;
  operation: string;
  record_ids: number[];
  values: Record<string, unknown>;
  prestate: unknown;
  expires_at: number | string;
  source?: string;
  result?: unknown;
  verification?: unknown;
}

export interface Document {
  id: string;
  model: string;
  name: string;
  state: string;
  fields: Record<string, unknown>;
  observed_at?: string;
  source: string;
}

export interface Check {
  name: string;
  label: string;
  status: "passed" | "failed" | "unknown";
  detail?: string;
  source?: string;
}

export interface Usage {
  input: number | null;
  cache_read: number | null;
  output: number | null;
  reasoning: number | null;
  total: number | null;
}

export interface Round {
  index: number;
  status: string;
  text?: string;
  usage?: Usage;
  elapsed_seconds?: number;
  tool_ids: string[];
}

export interface Tool {
  id: string;
  name: string;
  round?: number;
  status: string;
  arguments: Record<string, unknown>;
  result?: unknown;
  elapsed_seconds?: number;
  action_id?: string;
}

export interface BusinessDetail {
  business: Business;
  runs: Run[];
  approvals: Approval[];
  documents: Document[];
  checks: Check[];
  observed_at?: string;
  stale: boolean;
  summary?: string;
  activity?: {
    phase: string;
    label: string;
    detail: string;
    at?: string;
    tool_name?: string;
    round?: number;
    tool_count?: number;
    model_rounds?: number;
    last_event?: string;
  };
}

export interface Health {
  host_ready: boolean;
  odoo_status?: string;
  model_configured?: boolean;
  environment?: string;
  active_run_id?: string | null;
  odoo?: {
    status: "unconfigured" | "unchecked" | "connected" | "unavailable" | "permission_denied" | "error";
    checked_at?: string;
    latency_ms?: number;
    endpoint?: string;
    database?: string;
    account?: string;
    detail?: string;
  };
}

export interface Settings {
  model: string;
  base_url: string;
  odoo_url: string;
  odoo_db: string;
  odoo_username: string;
  has_model_key: boolean;
  has_odoo_key: boolean;
  environment: "demo" | "configured";
}

export interface SettingsInput extends Partial<Omit<Settings, "has_model_key" | "has_odoo_key">> {
  model_key?: string;
  odoo_key?: string;
}

export type WorkbenchMethod =
  | "list_sessions"
  | "create_session"
  | "rename_session"
  | "archive_session"
  | "get_session"
  | "send_message"
  | "confirm_business"
  | "get_business"
  | "start_run"
  | "decide_approval"
  | "cancel_run"
  | "get_trace"
  | "refresh_business"
  | "get_settings"
  | "save_settings"
  | "check_connection"
  | "health";

export interface WorkbenchRequest {
  id: string;
  method: WorkbenchMethod;
  params: Record<string, unknown>;
}

export interface WorkbenchResponse {
  id: string;
  result?: unknown;
  error?: { code: string; message: string };
}

export interface WorkbenchEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface WorkbenchBridge {
  call(method: WorkbenchMethod, params?: Record<string, unknown>): Promise<unknown>;
  subscribe(listener: (event: WorkbenchEvent) => void): () => void;
  windowControl(action: "minimize" | "maximize" | "close"): Promise<void>;
}

declare global {
  interface Window {
    workbench: WorkbenchBridge;
  }
}
