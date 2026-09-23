import type {
  Approval,
  BusinessArtifact,
  Business,
  BusinessDetail,
  BusinessEvidence,
  BusinessExecution,
  BusinessOutcome,
  BusinessProposal,
  Check,
  Document,
  Health,
  ConversationRun,
  LiveMessage,
  Message,
  Round,
  Run,
  SessionSummary,
  Settings,
  Tool,
  Usage,
  WorkbenchEvent
} from '../shared/protocol'

export type {
  Approval,
  TraceRequest,
  TraceAction,
  TraceDetail,
  TraceDetailKind,
  BusinessReceipt,
  BusinessArtifact,
  Business,
  BusinessDetail,
  BusinessEvidence,
  BusinessExecution,
  BusinessOutcome,
  BusinessProposal,
  Check,
  Document,
  Health,
  ConversationRun,
  LiveMessage,
  Message,
  Round,
  Run,
  SessionSummary,
  Settings,
  Tool,
  Usage,
  WorkbenchEvent
} from '../shared/protocol'

export type BusinessTab = 'execution' | 'documents' | 'approvals' | 'trace'

export interface BusinessReadback {
  latest_run_id?: string
  observed_at?: string
  stale?: boolean
  checks?: Check[]
  documents?: Document[]
}

export type BusinessDetailProjection = BusinessDetail & {
  business: Business & { readback?: BusinessReadback }
}

export interface SessionDetail {
  session: SessionSummary
  messages: Message[]
  businesses: Business[]
  conversation_runs?: ConversationRun[]
  live_messages?: LiveMessage[]
  materials?: Array<{ id: string; session_id: string; name: string; size: number; sha256: string; created_at: string; row_count?: number; preview?: string; media_type?: string }>
}

export type ToolReceipt = Tool

export interface TraceBundle {
  run: Run | null
  rounds: Round[]
  tools: ToolReceipt[]
  events?: Array<Record<string, unknown>>
  requests?: import('../shared/protocol').TraceRequest[]
  actions?: import('../shared/protocol').TraceAction[]
  diagnostics?: Record<string, unknown>
  summary_only?: boolean
}

export type HostEvent = WorkbenchEvent

export const runStatusLabel: Record<string, string> = {
  idle: '空闲',
  ready: '待执行',
  blocked: '需核对',
  running: '执行中',
  awaiting_approval: '等待审批',
  cancel_requested: '正在取消',
  cancelled: '已取消',
  interrupted: '已中断',
  completed: '本轮结束',
  failed: '失败',
  needs_reconciliation: '待对账'
}

export const businessStatusLabel: Record<string, string> = {
  idle: '待执行',
  ready: '待执行',
  blocked: '需核对',
  running: '执行中',
  awaiting_approval: '等待审批',
  completed: '本轮结束',
  failed: '执行失败',
  needs_reconciliation: '需要复核',
  cancelled: '已取消',
  interrupted: '已中断'
}

export function labelFor(map: Record<string, string>, value: string | undefined, fallback = '未知') {
  return value ? map[value] ?? fallback : fallback
}

export function formatInstant(value?: string) {
  if (!value) return '未知时间'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date)
}

export function formatCount(value: number | null | undefined) {
  return value == null ? '未知' : value.toLocaleString('zh-CN')
}

export function formatDuration(seconds: number | null | undefined) {
  if (seconds == null) return '未知'
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
}

export function formatExpiry(value: number | string) {
  const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value)
  if (!Number.isFinite(timestamp)) return '有效期未知'
  return timestamp <= Date.now() ? '已过期' : `截至 ${formatInstant(new Date(timestamp).toISOString())}`
}

export function isExpired(value: number | string) {
  const timestamp = typeof value === 'number' ? value * 1000 : Date.parse(value)
  return Number.isFinite(timestamp) && timestamp <= Date.now()
}

export function jsonText(value: unknown) {
  if (value == null) return '未知'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
