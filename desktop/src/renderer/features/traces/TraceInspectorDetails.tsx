import { useEffect, useRef, useState, type ReactNode } from 'react'
import { MessageText } from '../../components/common'
import { formatCount, formatInstant, jsonText, type TraceDetail, type TraceDetailKind } from '../../protocol'
import type { TraceNode } from './trace-model'

export type LoadTraceDetail = (runId: string, kind: TraceDetailKind, id: string) => Promise<TraceDetail | null>
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
export function JsonFold({ label, value, open = false }: { label: string; value: unknown; open?: boolean }) { return <details className="trace-json-fold" open={open}><summary>{label}</summary><pre>{value == null ? '未记录' : jsonText(value)}</pre></details> }

function RequestDetail({ data, redaction }: { data: Record<string, unknown>; redaction?: TraceDetail['redaction'] }) {
  const input = object(data.input)
  const response = object(data.response)
  const comparison = object(data.comparison)
  const metadata = object(data.metadata)
  const contentStats = object(data.content_stats)
  const roleChars = object(contentStats.messages_by_role)
  const headers = object(response.headers)
  const output = object(response.output)
  const messages = Array.isArray(input.messages) ? input.messages : []
  const tools = Array.isArray(input.tools) ? input.tools : []
  const publicOutput = response.text ?? output.text
  const error = output.error ?? metadata.error
  const extraInput = Object.fromEntries(Object.entries(input).filter(([key]) => !['messages', 'tools'].includes(key)))
  return <div className="trace-detail-content trace-request-detail">
    <div className="fact-table"><div><span>轮次关联</span><strong>{data.association === 'linked' ? '明确关联' : '未关联，不推断轮次'}</strong></div><div><span>HTTP 响应</span><strong>{String(response.status ?? headers.status ?? metadata.http_status ?? '未知')}</strong></div><div><span>请求状态</span><strong>{String(metadata.status ?? '未知')}</strong></div><div><span>模型</span><strong>{String(input.model ?? '未记录')}</strong></div><div><span>停止原因</span><strong>{String(output.stop_reason ?? metadata.stop_reason ?? '未记录')}</strong></div></div>
    {error != null && <div className="notice red">{jsonText(error)}</div>}
    <section className="trace-context-diff" aria-label="相邻请求上下文变化"><h4>相邻请求上下文变化</h4>{comparison.previous_request_id ? <><p>相对 {String(comparison.previous_request_id)} · 字符数，不是 token</p><dl><div><dt>相同前缀消息</dt><dd>{formatCount(typeof comparison.unchanged_messages === 'number' ? comparison.unchanged_messages : null)}</dd></div><div><dt>本次后缀消息</dt><dd>{formatCount(typeof comparison.added_messages === 'number' ? comparison.added_messages : null)}</dd></div><div><dt>上次后缀消息</dt><dd>{formatCount(typeof comparison.removed_messages === 'number' ? comparison.removed_messages : null)}</dd></div><div><dt>本次后缀字符</dt><dd>{formatCount(typeof comparison.added_chars === 'number' ? comparison.added_chars : null)}</dd></div><div><dt>上次后缀字符</dt><dd>{formatCount(typeof comparison.removed_chars === 'number' ? comparison.removed_chars : null)}</dd></div></dl><small>按完整相同前缀比较，JSON 序列化长度计数。</small></> : <p>没有可比较的上一请求。</p>}</section>
    {Object.keys(contentStats).length > 0 && <section className="trace-context-diff"><h4>本次发送内容分布</h4><dl>{Object.entries(roleChars).map(([role, count]) => <div key={role}><dt>{role}</dt><dd>{typeof count === 'number' ? formatCount(count) : '未知'} 字符</dd></div>)}<div><dt>工具定义</dt><dd>{typeof contentStats.tools_schema_chars === 'number' ? formatCount(contentStats.tools_schema_chars) : '未知'} 字符</dd></div><div><dt>已隐藏内容</dt><dd>{typeof contentStats.hidden_chars === 'number' ? formatCount(contentStats.hidden_chars) : '未知'} 字符</dd></div></dl><small>{String(contentStats.method ?? '按脱敏后的 JSON Unicode 字符计数，不是 token。')}</small></section>}
    {comparison.method != null && <p className="muted">比较口径：{String(comparison.method)}</p>}
    <h4>实际发送的消息 <span>{messages.length}</span></h4>
    {messages.map((message, index) => { const row = object(message); return <details className="trace-message" key={index}><summary><b>{String(row.role ?? '未知角色')}</b><span>消息 {index + 1}</span>{row.name != null && <span>{String(row.name)}</span>}{row.tool_call_id != null && <span>{String(row.tool_call_id)}</span>}</summary><JsonFold label="完整消息结构" value={row} />{typeof row.content === 'string' ? <pre>{row.content}</pre> : <pre>{jsonText(row.content ?? row.tool_calls ?? '未记录内容')}</pre>}</details> })}
    {!messages.length && <p className="muted">请求未记录消息内容。</p>}
    <details className="trace-json-fold"><summary>发送给模型的工具定义 · {tools.length}</summary>{tools.map((tool, index) => { const row = object(tool); const fn = object(row.function); return <JsonFold key={index} label={String(fn.name ?? row.name ?? `工具 ${index + 1}`)} value={row} /> })}</details>
    <JsonFold label="模型与请求配置" value={extraInput} />
    <section><h4>公开输出</h4>{typeof publicOutput === 'string' && publicOutput ? <MessageText text={publicOutput} collapsible={false} /> : <p className="muted">{String(response.output_unavailable ?? '未记录可明确关联的公开输出。')}</p>}</section>
    <JsonFold label="响应与错误元数据" value={response} />
    <JsonFold label="内容隐藏记录" value={redaction} />
  </div>
}

function ActionDetail({ data }: { data: Record<string, unknown> }) {
  const ledger = object(data.action ?? data.ledger ?? data)
  const payload = object(ledger.payload)
  const approval = object(ledger.approval)
  return <div className="trace-action-detail"><div className="fact-table"><div><span>动作状态</span><strong>{String(ledger.status ?? '未知')}</strong></div><div><span>业务动作</span><strong>{String(payload.model ?? ledger.model ?? '')} · {String(payload.operation ?? payload.method ?? ledger.operation ?? '未记录')}</strong></div></div>
    <ol className="trace-action-flow"><li><h4>审批依据</h4><p>记录时间：{formatInstant(typeof ledger.approval_requested_at === 'string' ? ledger.approval_requested_at : undefined)}</p><JsonFold label="拟提交字段" value={approval.values ?? payload.values} /><JsonFold label="执行前读取与依据" value={approval.prestate ?? ledger.prestate} /><JsonFold label="授权与审批记录" value={ledger.approval ?? { status: ledger.status, expires_at: ledger.expires_at, approved_at: ledger.approved_at }} /></li><li><h4>执行记录</h4><p>{ledger.status === 'verified' ? '回读已核验' : ['sending', 'executing', 'needs_reconciliation'].includes(String(ledger.status)) ? '执行结果待核对' : ledger.sent_at ? '已有发送记录，结果以回执为准' : '尚无发送记录'}</p><JsonFold label="规范化载荷" value={ledger.payload} /><JsonFold label="执行结果" value={ledger.result} open /></li><li><h4>回读核验</h4><JsonFold label="核验回执" value={ledger.verification ?? ledger.reconciliation} open /></li></ol><JsonFold label="完整动作账本" value={ledger} /></div>
}

function DetailBody({ detail }: { detail: TraceDetail }) {
  const data = detail.data
  if (detail.kind === 'request') return <RequestDetail data={data} redaction={detail.redaction} />
  if (detail.kind === 'run') return <section className="trace-run-instruction"><h4>本次运行的实际指令</h4>{typeof data.instruction === 'string' ? <MessageText text={data.instruction} /> : <p>未保存运行指令。</p>}<JsonFold label="当前业务目标（可能在运行后更新）" value={data.current_business ?? data.business} />{typeof data.summary === 'string' && <details className="trace-json-fold"><summary>Agent 公开总结</summary><MessageText text={data.summary} collapsible={false} /></details>}</section>
  if (detail.kind === 'action') return <ActionDetail data={data} />
  if (detail.kind === 'tool') return <><ToolResultFacts data={data} /><JsonFold label="模型原始请求参数" value={data.arguments ?? data.original_arguments} open /><JsonFold label="运行时规范化载荷" value={data.normalized_arguments ?? data.executed_arguments} open /><JsonFold label="工具结果回执" value={data.result} open />{(data.action ?? data.ledger) != null && <details className="trace-json-fold"><summary>关联动作账本</summary><ActionDetail data={object(data.action ?? data.ledger)} /></details>}<JsonFold label="完整工具记录" value={data} /></>
  if (detail.kind === 'round') return <><MessageText text={typeof data.text === 'string' && data.text ? data.text : '本轮没有公开回复。'} collapsible={false} /><div className="fact-table"><div><span>停止原因</span><strong>{String(data.stop_reason ?? '未记录')}</strong></div></div>{data.error != null && <div className="notice red trace-round-error" role="note"><strong>本轮错误</strong><p>{typeof data.error === 'string' ? data.error : jsonText(data.error)}</p></div>}<JsonFold label="轮次记录" value={data} /></>
  return <JsonFold label="事件记录" value={data} open />
}

function ToolResultFacts({ data }: { data: Record<string, unknown> }) {
  const result = object(data.result)
  const payload = object(result.result ?? result.data ?? result)
  const args = object(data.normalized_arguments ?? data.arguments)
  const rows = payload.records ?? payload.rows
  const count = payload.total_count ?? payload.total ?? payload.count
  const complete = payload.complete ?? payload.is_complete
  if (!Object.keys(payload).length) return null
  return <section className="trace-query-facts"><h4>结果范围</h4><div className="fact-table"><div><span>已返回行数</span><strong>{Array.isArray(rows) ? rows.length : '未记录'}</strong></div><div><span>声明数量</span><strong>{typeof count === 'number' ? formatCount(count) : '未记录'}</strong></div><div><span>读取完整性</span><strong>{complete === true ? '工具声明完整' : complete === false ? '读取不完整' : '未知'}</strong></div></div>{args.domain != null && <JsonFold label="请求查询条件" value={args.domain} />}</section>
}

export function LazyTraceDetail({ runId, node, revision, load, fallback }: { runId: string; node: TraceNode; revision: string; load?: LoadTraceDetail; fallback?: ReactNode }) {
  const [result, setResult] = useState<{ key: string; detail?: TraceDetail; error?: string } | null>(null)
  const [retry, setRetry] = useState(0)
  const generation = useRef(0)
  const key = `${runId}:${node.key}:${revision}`
  useEffect(() => {
    const current = ++generation.current
    if (!load) return
    setResult(null)
    void load(runId, node.kind, node.id).then(detail => {
      if (current !== generation.current) return
      if (!detail || detail.id !== node.id || detail.kind !== node.kind) { setResult({ key, error: '详情未返回或记录标识不一致。' }); return }
      setResult({ key, detail })
    }).catch(error => { if (current === generation.current) setResult({ key, error: String(error instanceof Error ? error.message : error) }) })
    return () => { generation.current += 1 }
  }, [runId, node.id, node.kind, key, retry, load])
  if (!load) return <>{fallback ?? <p>当前接口未提供按需详情。</p>}</>
  if (result?.key !== key) return <p className="loading-line" role="status">正在读取所选记录…</p>
  if (result.error) return <div className="trace-detail-error" role="alert"><p>{result.error}</p><button type="button" onClick={() => setRetry(value => value + 1)}>重新读取详情</button></div>
  return result.detail ? <DetailBody detail={result.detail} /> : null
}
