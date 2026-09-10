export type BusinessType = "sale_invoice" | "sale_purchase_invoice" | "purchase";
export type CompletionTarget = "read_only" | "draft" | "confirmed" | "posted";
export type Role = "user" | "assistant" | "system";
export type ApprovalDecision = "approve" | "reject";

export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  archived: boolean;
  status: string;
  pending_material_ids?: string[];
}

export interface Message {
  id: string;
  role: Role;
  text: string;
  created_at: string;
  business_id?: string;
  context_business_id?: string | null;
  run_id?: string;
  sequence?: number;
  proposal?: BusinessProposal;
  material_ids?: string[];
}

export interface Material {
  id: string;
  session_id: string;
  name: string;
  size: number;
  sha256: string;
  created_at: string;
  row_count?: number;
  preview: string;
  media_type: string;
}

export interface ConversationRun {
  id: string;
  session_id: string;
  business_id?: string | null;
  context_business_id?: string | null;
  kind: "conversation";
  status: string;
  started_at?: string;
  ended_at?: string;
  error?: string;
  error_detail?: string;
}

export interface LiveMessage {
  id: string;
  session_id: string;
  business_id?: string | null;
  run_id: string;
  sequence: number;
  text: string;
  role?: Role;
  status?: "streaming" | "ended" | "interrupted" | "failed";
  created_at?: string;
}

export interface BusinessProposal {
  id: string;
  type: BusinessType;
  title: string;
  goal: string;
  status: "pending" | "confirmed" | "rejected";
  completion_target?: CompletionTarget;
  material_ids?: string[];
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
  material_ids?: string[];
  completion_target?: CompletionTarget;
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
  id: string | number;
  model: string;
  name: string;
  state?: string;
  fields: Record<string, unknown>;
  observed_at?: string;
  source_observed_at?: string;
  source: string;
  source_run_id?: string;
  source_tool_id?: string;
  document_scope?: string;
  is_reference?: boolean;
}

export interface BusinessArtifact {
  id: string;
  name: string;
  path: string;
  created_at?: string;
  kind: "business_receipt" | "odoo_pdf" | "odoo_csv" | string;
  model?: string;
  record_id?: number;
  source?: string;
  run_id?: string;
  available?: boolean;
  error?: string;
}

export interface Check {
  name: string;
  label: string;
  status: "passed" | "failed" | "unknown";
  detail?: string;
  source?: string;
}

export type BusinessStageId = "read" | "quote" | "confirm" | "invoice" | "verify";
export type BusinessStageStatus = "pending" | "active" | "awaiting_approval" | "observed" | "verified" | "failed" | "unknown";

export interface BusinessEvidence {
  run_id: string;
  kind?: "tool" | "action" | "readback";
  tool_id?: string;
  action_id?: string;
  observed_at?: string;
  label: string;
}

export interface BusinessStage {
  id: BusinessStageId;
  label: string;
  status: BusinessStageStatus;
  detail: string;
  evidence: BusinessEvidence[];
}

export interface BusinessExecution {
  run_id?: string;
  current_stage_id?: BusinessStageId;
  stages: BusinessStage[];
}

export interface BusinessOutcome {
  status: "unknown" | "passed" | "failed";
  label: string;
  detail: string;
  scope: string;
}

export interface Usage {
  input: number | null;
  cache_read: number | null;
  output: number | null;
  reasoning: number | null;
  total: number | null;
  compaction_total?: number | null;
  compaction_calls?: number;
  reported_total?: number | null;
  missing_usage_rounds?: number;
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
  live_messages?: LiveMessage[];
  approvals: Approval[];
  documents: Document[];
  artifacts?: BusinessArtifact[];
  checks: Check[];
  observed_at?: string;
  stale: boolean;
  summary?: string;
  execution?: BusinessExecution;
  outcome?: BusinessOutcome;
  activity?: {
    phase: string;
    label: string;
    detail: string;
    intent?: string;
    at?: string;
    tool_name?: string;
    round?: number;
    tool_count?: number;
    model_rounds?: number;
    last_event?: string;
  };
  materials?: Material[];
}

export interface Health {
  host_ready: boolean;
  odoo_status?: string;
  model_configured?: boolean;
  environment?: string;
  data_dir?: string;
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
  | "cancel_conversation"
  | "confirm_business"
  | "get_business"
  | "start_run"
  | "decide_approval"
  | "cancel_run"
  | "reconcile_action"
  | "get_trace"
  | "refresh_business"
  | "export_business_report"
  | "open_odoo_record"
  | "open_business_artifact"
  | "reveal_business_artifact"
  | "get_settings"
  | "save_settings"
  | "import_material"
  | "download_document"
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
