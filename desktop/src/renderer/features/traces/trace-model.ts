import type { TraceBundle, TraceDetailKind } from '../../protocol'

export interface TraceNode {
  key: string
  id: string
  kind: TraceDetailKind
  title: string
  status: string
  parent?: string
  startedAt?: string
  endedAt?: string
  durationMs?: number
  tokens?: number | null
  search: string
  issue: boolean
  revision?: string
  association?: 'linked' | 'unlinked'
}

export function instant(value: unknown): number | undefined {
  if (typeof value !== 'string' || !value) return undefined
  const time = Date.parse(value)
  return Number.isFinite(time) ? time : undefined
}

const isIssue = (status: string) => ['error', 'failed', 'known_failed', 'needs_reconciliation', 'retrying', 'interrupted'].includes(status)
const timestamp = (value: number | string | undefined): string | undefined => typeof value === 'number' && Number.isFinite(value) ? new Date(value * 1000).toISOString() : typeof value === 'string' ? value : undefined
function eventStatus(event: Record<string, unknown>, type: string): string {
  if (typeof event.status === 'string' && event.status.trim()) return event.status
  if (/retry|compact/i.test(type)) {
    if (/_start$/i.test(type)) return /retry/i.test(type) ? 'retrying' : 'running'
    if (event.aborted === true) return 'interrupted'
    if (event.error || event.is_error || event.success === false) return 'failed'
    if (/compact.*_end$/i.test(type) && event.success === true) return 'completed'
    return 'unknown'
  }
  return /error|fail/i.test(type) || event.is_error ? 'error' : type
}
export function traceNodes(trace: TraceBundle): TraceNode[] {
  const nodes: TraceNode[] = []
  const rounds = new Set(trace.rounds.map(round => round.index))
  const parentRound = (round?: number) => round != null && rounds.has(round) ? `round:${round}` : undefined
  for (const round of trace.rounds) nodes.push({ key: `round:${round.index}`, id: String(round.index), kind: 'round', title: `第 ${round.index} 轮`, status: round.status, startedAt: round.started_at, endedAt: round.ended_at, durationMs: round.elapsed_seconds == null ? undefined : round.elapsed_seconds * 1000, tokens: round.usage?.total, search: `${round.index} ${round.status} ${round.error || ''}`, issue: isIssue(round.status) || Boolean(round.error) })
  for (const request of trace.requests ?? []) nodes.push({ key: `request:${request.id}`, id: request.id, kind: 'request', title: `${request.kind === 'compaction' ? '上下文压缩' : '模型请求'} · ${request.id}`, status: request.status, parent: parentRound(request.round), startedAt: request.started_at, endedAt: request.ended_at, durationMs: request.duration_ms, tokens: request.usage?.total, association: request.association, revision: `${request.association}:${request.round ?? ""}`, search: `${request.id} ${request.kind} ${request.status} ${request.error || ''} ${request.http_status ?? ''} ${request.association === 'unlinked' ? '未关联' : ''}`, issue: isIssue(request.status) || Boolean(request.error) || (request.http_status ?? 0) >= 400 })
  for (const tool of trace.tools) {
    const round = tool.round ?? trace.rounds.find(round => round.tool_ids.includes(tool.id))?.index
    nodes.push({ key: `tool:${tool.id}`, id: tool.id, kind: 'tool', title: tool.name, status: tool.status, parent: parentRound(round), startedAt: tool.started_at, endedAt: tool.ended_at, durationMs: tool.elapsed_seconds == null ? undefined : tool.elapsed_seconds * 1000, search: `${tool.id} ${tool.name} ${tool.status} ${tool.action_id || ''} ${tool.search_text || ''} ${JSON.stringify(tool.arguments ?? {})}`, issue: isIssue(tool.status) })
  }
  for (const action of trace.actions ?? []) {
    const toolId = action.tool_ids.find(id => trace.tools.some(tool => tool.id === id))
    nodes.push({ key: `action:${action.id}`, id: action.id, kind: 'action', title: `${action.model || '业务动作'} · ${action.operation || action.kind || action.id}`, status: action.status, parent: toolId ? `tool:${toolId}` : undefined, startedAt: action.started_at ?? timestamp(action.sent_at), endedAt: action.ended_at ?? timestamp(action.finished_at), search: `${action.id} ${action.kind} ${action.model} ${action.operation} ${action.status} ${(action.record_ids ?? []).join(' ')}`, issue: isIssue(action.status) })
  }
  const latestToolEvents = new Map<string, Record<string, unknown>>()
  for (const [index, event] of (trace.events ?? []).entries()) {
    const type = String(event.type ?? '')
    const toolId = String(event.tool_call_id || event.tool_id || '')
    if (toolId && ['tool_start', 'tool_progress', 'tool_end'].includes(type)) latestToolEvents.set(toolId, event)
    if (event.is_error && toolId) {
      const tool = nodes.find(node => node.key === `tool:${toolId}`)
      if (tool) { tool.issue = true; if (type === 'tool_end') continue }
    }
    if (!/retry|compact|error|fail|cancel|interrupt/i.test(type) && !event.is_error) continue
    const id = String(event.id ?? index)
    const status = eventStatus(event, type)
    nodes.push({ key: `event:${id}`, id, kind: 'event', title: /compact/i.test(type) ? '上下文压缩事件' : /retry/i.test(type) ? '请求重试' : '运行异常事件', status, parent: parentRound(typeof event.round === 'number' ? event.round : undefined), startedAt: typeof event.at === 'string' ? event.at : undefined, search: JSON.stringify(event), issue: Boolean(event.is_error) || isIssue(status) || /error|fail|interrupt/i.test(type) })
  }
  for (const [id, event] of latestToolEvents) if (!nodes.some(node => node.key === `tool:${id}`)) nodes.push({ key: `tool:${id}`, id, kind: 'tool', title: String(event.tool_name || id), status: event.type === 'tool_end' ? 'receipt_pending' : trace.run?.status === 'running' ? 'running' : 'unknown', parent: parentRound(typeof event.round === 'number' ? event.round : undefined), search: `${id} ${event.tool_name || ''}`, issue: false })
  return nodes
}

export function visibleTraceNodes(nodes: TraceNode[], query: string, collapsed: Set<string>): Array<TraceNode & { depth: number; hasChildren: boolean }> {
  const byKey = new Map(nodes.map(node => [node.key, node]))
  const children = new Map<string | undefined, TraceNode[]>()
  for (const node of nodes) children.set(node.parent, [...(children.get(node.parent) ?? []), node])
  const included = new Set<string>()
  const terms = query.toLocaleLowerCase().trim().split(/\s+/).filter(Boolean)
  if (terms.length) for (const node of nodes) if (terms.every(term => `${node.title} ${node.search}`.toLocaleLowerCase().includes(term))) {
    let current: TraceNode | undefined = node
    while (current && !included.has(current.key)) { included.add(current.key); current = current.parent ? byKey.get(current.parent) : undefined }
  }
  const result: Array<TraceNode & { depth: number; hasChildren: boolean }> = []
  const visit = (parent: string | undefined, depth: number) => {
    for (const node of children.get(parent) ?? []) {
      if (terms.length && !included.has(node.key)) continue
      const hasChildren = Boolean(children.get(node.key)?.length)
      result.push({ ...node, depth, hasChildren })
      if (!collapsed.has(node.key) || terms.length) visit(node.key, depth + 1)
    }
  }
  visit(undefined, 0)
  return result
}

export function timeBounds(nodes: TraceNode[]) {
  const timed = nodes.filter(node => instant(node.startedAt) !== undefined && instant(node.endedAt) !== undefined && instant(node.endedAt)! >= instant(node.startedAt)!)
  if (!timed.length) return null
  return { start: Math.min(...timed.map(node => instant(node.startedAt)!)), end: Math.max(...timed.map(node => instant(node.endedAt)!)) }
}
