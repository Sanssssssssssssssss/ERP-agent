import { Copy } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { MessageText } from '../../components/common'
import { formatCount, formatInstant, jsonText, type TraceDetail, type TraceDetailKind } from '../../protocol'
import type { TraceNode } from './trace-model'
import { approvalStatusLabel, documentModelLabel, documentStateLabel, invoiceStatusLabel, operationLabel, readableValue } from '../../presentation'
import { approvalFieldLabel } from '../approvals/ApprovalsPage'

export type LoadTraceDetail = (runId: string, kind: TraceDetailKind, id: string) => Promise<TraceDetail | null>
const object = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
export function JsonFold({ label, value, open = false }: { label: string; value: unknown; open?: boolean }) {
  const [copyStatus, setCopyStatus] = useState('')
  const text = value == null ? '未记录' : jsonText(value)
  useEffect(() => { setCopyStatus('') }, [text])
  return <details className="trace-json-fold" open={open}><summary>{label}</summary><div className="trace-raw-toolbar"><button type="button" aria-label={`复制 ${label}`} onClick={() => { void Promise.resolve().then(() => navigator.clipboard.writeText(text)).then(() => setCopyStatus('已复制'), () => setCopyStatus('复制失败')) }}><Copy size={13} />复制</button><span role="status">{copyStatus}</span></div><pre>{text}</pre></details>
}

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
    <section className="trace-response-preview"><h4>公开输出</h4>{typeof publicOutput === 'string' && publicOutput ? <MessageText text={publicOutput} collapsible={false} /> : <p className="muted">{String(response.output_unavailable ?? '未记录可明确关联的公开输出。')}</p>}</section>
    <details className="trace-json-fold"><summary>请求 · {messages.length} 条消息 / {tools.length} 个工具定义</summary>
    <details className="trace-context-diff trace-json-fold" aria-label="相邻请求上下文变化"><summary>相邻请求上下文变化</summary>{comparison.previous_request_id ? <><p>相对 {String(comparison.previous_request_id)} · 字符数，不是 token</p><dl><div><dt>相同前缀消息</dt><dd>{formatCount(typeof comparison.unchanged_messages === 'number' ? comparison.unchanged_messages : null)}</dd></div><div><dt>本次后缀消息</dt><dd>{formatCount(typeof comparison.added_messages === 'number' ? comparison.added_messages : null)}</dd></div><div><dt>上次后缀消息</dt><dd>{formatCount(typeof comparison.removed_messages === 'number' ? comparison.removed_messages : null)}</dd></div><div><dt>本次后缀字符</dt><dd>{formatCount(typeof comparison.added_chars === 'number' ? comparison.added_chars : null)}</dd></div><div><dt>上次后缀字符</dt><dd>{formatCount(typeof comparison.removed_chars === 'number' ? comparison.removed_chars : null)}</dd></div></dl><small>按完整相同前缀比较，JSON 序列化长度计数。</small></> : <p>没有可比较的上一请求。</p>}</details>
    {Object.keys(contentStats).length > 0 && <section className="trace-context-diff"><h4>本次发送内容分布</h4><dl>{Object.entries(roleChars).map(([role, count]) => <div key={role}><dt>{role}</dt><dd>{typeof count === 'number' ? formatCount(count) : '未知'} 字符</dd></div>)}<div><dt>工具定义</dt><dd>{typeof contentStats.tools_schema_chars === 'number' ? formatCount(contentStats.tools_schema_chars) : '未知'} 字符</dd></div><div><dt>已隐藏内容</dt><dd>{typeof contentStats.hidden_chars === 'number' ? formatCount(contentStats.hidden_chars) : '未知'} 字符</dd></div></dl><small>{String(contentStats.method ?? '按脱敏后的 JSON Unicode 字符计数，不是 token。')}</small></section>}
    {comparison.method != null && <JsonFold label="比较口径" value={comparison.method} />}
    <h4>实际发送的消息 <span>{messages.length}</span></h4>
    {messages.map((message, index) => { const row = object(message); return <details className="trace-message" key={index}><summary><b>{String(row.role ?? '未知角色')}</b><span>消息 {index + 1}</span>{row.name != null && <span>{String(row.name)}</span>}{row.tool_call_id != null && <span>{String(row.tool_call_id)}</span>}</summary><JsonFold label="完整消息结构" value={row} />{typeof row.content === 'string' ? <pre>{row.content}</pre> : <pre>{jsonText(row.content ?? row.tool_calls ?? '未记录内容')}</pre>}</details> })}
    {!messages.length && <p className="muted">请求未记录消息内容。</p>}
    <details className="trace-json-fold"><summary>发送给模型的工具定义 · {tools.length}</summary>{tools.map((tool, index) => { const row = object(tool); const fn = object(row.function); return <JsonFold key={index} label={String(fn.name ?? row.name ?? `工具 ${index + 1}`)} value={row} /> })}</details>
    <JsonFold label="模型与请求配置" value={extraInput} />
    </details>
    <JsonFold label="回执 · 响应与错误元数据" value={response} />
    <details className="trace-json-fold"><summary>原始数据</summary><JsonFold label="完整请求记录" value={data} /><JsonFold label="内容隐藏记录" value={redaction} /></details>
  </div>
}

function ActionDetail({ data }: { data: Record<string, unknown> }) {
  const ledger = object(data.action ?? data.ledger ?? data)
  const payload = object(ledger.payload)
  const approval = object(ledger.approval)
  const records = payload.record_ids ?? payload.ids ?? payload.record_id ?? object(payload.kwargs).ids
  const riskData = { name: 'execute_approved_write', arguments: payload, classification: ledger.classification }
  return <div className="trace-action-detail"><div className="fact-table"><div><span>动作状态</span><strong>{approvalStatusLabel(String(ledger.status ?? 'unknown'))}</strong></div><div><span>业务动作</span><strong>{documentModelLabel(String(payload.model ?? ledger.model ?? ''))} · {operationLabel(String(payload.operation ?? payload.method ?? ledger.operation ?? '未记录'))}</strong></div>{records != null && <div><span>目标记录</span><strong>{readableValue(records)}</strong></div>}<div><span>风险等级</span><strong data-trace-risk={toolRiskTone(riskData)}>{toolRisk(riskData)}</strong></div></div>
    <ol className="trace-action-flow"><li><h4>审批依据</h4><p>记录时间：{formatInstant(typeof ledger.approval_requested_at === 'string' ? ledger.approval_requested_at : undefined)}</p><JsonFold label="拟提交字段" value={approval.values ?? payload.values} /><JsonFold label="执行前读取与依据" value={approval.prestate ?? ledger.prestate} /><JsonFold label="授权与审批记录" value={ledger.approval ?? { status: ledger.status, expires_at: ledger.expires_at, approved_at: ledger.approved_at }} /></li><li><h4>执行记录</h4><p>{ledger.status === 'verified' ? '回读已核验' : ['sending', 'executing', 'needs_reconciliation'].includes(String(ledger.status)) ? '执行结果待核对' : ledger.sent_at ? '已有发送记录，结果以回执为准' : '尚无发送记录'}</p><JsonFold label="规范化载荷" value={ledger.payload} /><JsonFold label="执行结果" value={ledger.result} /></li><li><h4>回读核验</h4><JsonFold label="核验回执" value={ledger.verification ?? ledger.reconciliation} /></li></ol><JsonFold label="完整动作账本" value={ledger} /></div>
}

function DetailBody({ detail }: { detail: TraceDetail }) {
  const data = detail.data
  if (detail.kind === 'request') return <RequestDetail data={data} redaction={detail.redaction} />
  if (detail.kind === 'run') return <section className="trace-run-instruction"><details className="trace-json-fold"><summary>本次运行的实际指令</summary>{typeof data.instruction === 'string' ? <MessageText text={data.instruction} /> : <p>未保存运行指令。</p>}</details><JsonFold label="当前业务目标（可能在运行后更新）" value={data.current_business ?? data.business} />{typeof data.summary === 'string' && <details className="trace-json-fold"><summary>Agent 公开总结</summary><MessageText text={data.summary} collapsible={false} /></details>}</section>
  if (detail.kind === 'action') return <ActionDetail data={data} />
  if (detail.kind === 'tool') return <ToolDetailBody data={data} />
  if (detail.kind === 'round') return <><MessageText text={typeof data.text === 'string' && data.text ? data.text : '本轮没有公开回复。'} collapsible={false} /><div className="fact-table"><div><span>停止原因</span><strong>{stopReasonLabel(data.stop_reason)}</strong></div></div>{data.error != null && <div className="notice red trace-round-error" role="note"><strong>本轮错误</strong><p>{typeof data.error === 'string' ? data.error : jsonText(data.error)}</p></div>}<JsonFold label="轮次记录" value={data} /></>
  return <JsonFold label="事件记录" value={data} />
}

export function stopReasonLabel(value: unknown) {
  return ({ toolUse: '转入工具调用', tool_calls: '转入工具调用', end_turn: '回复结束', stop: '回复结束', error: '发生错误', max_tokens: '达到输出上限' } as Record<string, string>)[String(value)] ?? String(value ?? '未记录')
}

const toolCategories: Record<string, string[]> = {
  '业务读取': ['find_records', 'read_record', 'read_attachment', 'read', 'search_employee', 'search_holidays', 'search_across_instances', 'get_async_task', 'list_async_tasks'],
  '字段与结构': ['get_model_fields', 'schema_catalog', 'list_models', 'inspect_model_relationships'],
  '业务规则': ['list_odoo_sops', 'get_odoo_sop', 'build_domain', 'index_knowledge', 'search_knowledge', 'knowledge_stats', 'business_pack_report'],
  '变更与审批': ['preview_write', 'validate_write', 'execute_approved_write', 'execute_method', 'chatter_post'],
  '汇总与核验': ['aggregate_records', 'aggregate_across_instances', 'read_supply_context', 'read_invoice_eligibility', 'read_purchase_allocation', 'data_quality_report', 'receivable_payable_aging', 'accounting_health_summary', 'accounting_health_across_instances', 'refresh_business'],
  '连接与诊断': ['health_check', 'check_odoo_connection', 'diagnose_current_run', 'read_run_diagnostics', 'diagnose_access', 'diagnose_odoo_call', 'get_odoo_profile', 'list_instances', 'get_current_time', 'generate_json2_payload', 'upgrade_risk_report', 'analyze_upgrade_log', 'lookup_model_history', 'fit_gap_report', 'scan_addons_source', 'submit_async_task', 'cancel_async_task'],
}
export function toolCategory(name: string) {
  const nativeName = name.startsWith('mcp_odoo_') ? name.slice(9) : name
  return Object.entries(toolCategories).find(([, names]) => names.includes(nativeName))?.[0] ?? '其他工具'
}
export function toolRisk(data: Record<string, unknown>) {
  const result = object(data.result)
  const classification = object(result.classification ?? data.classification ?? object(data.display).classification)
  const args = object(data.normalized_arguments ?? data.arguments)
  const name = String(data.name ?? result.tool ?? '')
  const operation = args.operation ?? args.method ?? object(data.display).operation
  if (classification.destructive_method === true || operation === 'unlink') return '高 · 删除或破坏动作'
  if (['preview_write', 'validate_write'].includes(name.replace(/^mcp_odoo_/, ''))) return '低 · 变更预检'
  if (classification.safety === 'read_only') return '低 · 无业务写入'
  if (classification.safety === 'side_effect' || toolCategory(name) === '变更与审批') return '需审批 · 业务写入'
  if (classification.safety && classification.safety !== 'read_only') return '未明确分类'
  if (classification.safety === 'read_only' || toolCategory(name) !== '其他工具') return '低 · 无业务写入'
  return '未明确分类'
}

export function toolRiskTone(data: Record<string, unknown>) {
  const risk = toolRisk(data)
  return risk.startsWith('高') ? 'high' : risk.startsWith('需审批') ? 'write' : risk.startsWith('低') ? 'low' : 'unknown'
}

export const toolParameterLabels: Record<string, string> = {
  addons_paths: '扩展模块目录', approval: '审批信息', args: '位置参数', as_of: '统计截止日期', attachment_id: '附件编号', attachment_ids: '附件编号', available_fields: '可用字段', available_models: '可用模型', base_url: '服务地址', body: '消息正文', business_context: '业务上下文', checks: '检查项目', conditions: '查询条件', confirm: '确认操作', context: '调用上下文', database: '数据库', direction: '排序方向', domain: '筛选条件', employee_id: '员工编号', end_date: '结束日期', expected_count: '预期记录数', field_names: '字段名称', fields: '读取字段', fields_metadata: '字段定义', final: '最终提交', full_refresh: '完整刷新', group_by: '分组字段', include_computed: '包含计算字段', include_data: '包含数据', include_database_header: '包含数据库请求头', include_debug: '包含诊断信息', include_fields: '包含字段', include_manufacturing: '包含生产信息', include_modules: '包含模块', include_readonly: '包含只读字段', include_rules: '包含访问规则', installed_modules: '已安装模块', instance: '目标实例', instances: '目标实例', key_fields: '关键字段', kwargs: '命名参数', lazy: '延迟分组', limit: '最多返回条数', limit_per_instance: '每个实例最多返回条数', log_text: '日志内容', logical_operator: '条件组合方式', max_fields: '字段数上限', max_file_bytes: '文件大小上限', max_files: '文件数上限', measures: '汇总指标', message_type: '消息类型', metadata: '附加信息', method: '业务动作', methods: '业务动作', model: '业务对象', models: '业务对象', module_limit: '模块数上限', modules: '业务模块', name: '名称', observed_error: '已观测错误', observed_errors: '已观测错误', offset: '跳过记录数', operation: '业务动作', order: '排序方式', order_ids: '订单编号', pack: '业务能力包', params: '请求参数', partner_ids: '往来单位编号', product_ids: '产品编号', purchase_ids: '采购订单编号', query: '查询内容', record_id: '记录编号', record_ids: '记录编号', refresh: '刷新数据', relevance: '相关性条件', replace: '替换已有内容', requirements: '业务要求', sample_limit: '样本数上限', source_findings: '来源检查结果', source_version: '当前版本', start_date: '开始日期', subtype_xmlid: '消息子类型', target_version: '目标版本', task_id: '任务编号', top_partners: '主要往来单位数量', transport: '通信方式', use_live_metadata: '使用当前字段定义', values: '拟提交字段', values_list: '批量拟提交字段',
}
export const toolFailureLabels: Record<string, string> = {
  invoice_eligibility_blocked: '当前开票条件不满足，请查看可开票数量及已有发票。',
  model_unavailable: '当前 Odoo 实例没有该模型，请查看可用模型及已安装模块。', purchase_allocation_unverified: '采购来源数量分配未通过核验，请核对采购数量、销售来源及共同需求容量。',
  instance_unknown: '指定实例未配置，请选择已配置的实例。', tool_failed_unknown: '本次工具调用失败，原因尚未确定，请查看运行诊断。', tool_arguments_invalid: '调用参数不符合当前工具契约。', local_resource_missing: '工具需要的本地文件或目录不存在。',
  connection_unconfigured: 'Odoo 连接未配置完整。', tls_error: 'Odoo 安全连接或证书验证失败。', dns_failed: '无法解析 Odoo 服务地址。', connection_timeout: 'Odoo 连接或读取超时。', connection_refused: 'Odoo 服务拒绝连接，请检查服务和端口。', connection_unavailable: 'Odoo 网络连接不可用。', database_unavailable: 'Odoo 数据库不存在或不可用。', authentication_failed: 'Odoo 认证失败，请检查账号和密钥。', field_policy_denied: '字段访问策略拒绝此操作。', permission_denied: '当前账号没有所需权限。', rate_limited: '请求受到限流。', endpoint_not_found: 'Odoo 接口地址不存在。', record_unavailable: '记录不存在或当前账号不可见。', query_invalid: '查询字段或条件不被当前接口接受。', invalid_response: 'Odoo 返回的数据格式无效。', response_too_large: '读取结果超过响应大小上限。', server_error: 'Odoo 服务端处理失败。', read_failed_unknown: '本次读取失败，原因尚未确定。',
}
export function toolErrorText(error: unknown, model: string, reasonCode?: unknown) {
  const text = typeof error === 'string' ? error : readableValue(error)
  const missing = text.match(/Unknown field\(s\)\s*\[([^\]]*)\]\s*on\s*([\w.]+);\s*use live get_model_fields\.(?:\s*Valid candidates:\s*(.*))?/i)
  if (missing) {
    const fields = missing[1].replace(/['"]/g, '').split(',').map(field => field.trim()).filter(Boolean)
    const candidates = (missing[3] ?? '').replace(/\.$/, '').split(',').map(field => field.trim()).filter(Boolean)
    return `${documentModelLabel(missing[2] || model)}不支持读取字段「${fields.join('、')}」。${candidates.length ? `回执提供的候选字段：${candidates.map(field => approvalFieldLabel(field, missing[2] || model)).join('、')}。` : ''}请先查询当前字段定义，再调整读取字段。`
  }
  if (/^trusted host approval is required(?:; repeating the call does not authorize it)?\.?$/i.test(text)) return '此动作需要人工批准，尚未执行；重复调用不会获得授权。'
  if (/[\u3400-\u9fff]/.test(text)) return text
  return toolFailureLabels[String(reasonCode ?? object(error).reason_code)] ?? '本次调用失败，详细原因见原始记录。'
}

function fieldValue(key: string, value: unknown, model: string) {
  return key === 'state' && typeof value === 'string' ? documentStateLabel(model, value) : key === 'invoice_status' && typeof value === 'string' ? invoiceStatusLabel(value) : readableValue(value)
}
const fieldOrder = ['id', 'name', 'display_name', 'state', 'partner_id', 'company_id', 'amount_untaxed', 'amount_total', 'currency_id', 'invoice_status', 'picking_ids', 'invoice_ids']
export function ResultRecords({ rows, model, title = '返回记录', labels }: { rows: unknown[]; model: string; title?: string; labels?: Record<string, string> }) {
  const records = rows.map(object)
  const fields = [...new Set(records.flatMap(row => Object.keys(row)))].sort((a, b) => {
    const rank = (key: string) => fieldOrder.includes(key) ? fieldOrder.indexOf(key) : fieldOrder.length
    return rank(a) - rank(b)
  })
  const label = (key: string) => labels?.[key] ?? approvalFieldLabel(key, model)
  if (!fields.length && rows.length) return <section className="trace-result-preview"><h4>{title} · {rows.length} 项</h4><pre>{jsonText(rows.slice(0, 8))}</pre>{rows.length > 8 && <small>显示前 8 项，完整内容见回执。</small>}</section>
  return <section className="trace-result-preview"><h4>{title} <span>{rows.length}</span></h4>{!rows.length ? <p className="muted">本次返回 0 条记录。</p> : rows.length === 1 ? <dl className="trace-record-fields">{fields.slice(0, 12).map(key => <div key={key}><dt title={key}>{label(key)}</dt><dd>{fieldValue(key, records[0][key], model)}</dd></div>)}</dl> : <div className="trace-result-table"><table><thead><tr>{fields.slice(0, 6).map(key => <th key={key} title={key}>{label(key)}</th>)}</tr></thead><tbody>{records.slice(0, 8).map((row, index) => <tr key={index}>{fields.slice(0, 6).map(key => <td key={key}>{Object.hasOwn(row, key) ? fieldValue(key, row[key], model) : '未返回'}</td>)}</tr>)}</tbody></table></div>}{(rows.length > 8 || fields.length > (rows.length === 1 ? 12 : 6)) && <small>显示{rows.length > 8 ? '前 8 条记录的' : ''}主要字段，完整内容见回执。</small>}</section>
}

export function ToolDetailBody({ data, receiptLabel = '回执 · 工具结果回执', rawLabel = '原始数据 · 完整工具记录' }: { data: Record<string, unknown>; receiptLabel?: string; rawLabel?: string }) {
  const result = object(data.result)
  const raw = Object.hasOwn(result, 'result') ? result.result : Object.hasOwn(result, 'data') ? result.data : data.result
  const payload = object(raw)
  const args = object(data.normalized_arguments ?? data.arguments)
  const rows = Array.isArray(raw) ? raw : payload.records ?? payload.rows
  const count = result.total_count ?? result.count ?? payload.total_count ?? payload.total ?? payload.count
  const complete = result.complete ?? result.is_complete ?? payload.complete ?? payload.is_complete
  const success = typeof result.success === 'boolean' ? result.success : typeof result.ok === 'boolean' ? result.ok : typeof payload.ok === 'boolean' ? payload.ok : undefined
  const error = result.error ?? payload.error ?? data.error
  const model = typeof args.model === 'string' ? args.model : ''
  const identity = payload.name ?? args.name ?? payload.id ?? args.record_id ?? args.record_ids ?? args.ids ?? object(args.kwargs).ids
  const action = typeof args.method === 'string' ? args.method : typeof args.operation === 'string' ? args.operation : ''
  const category = toolCategory(String(data.name ?? result.tool ?? ''))
  const actionStatus = String(result.action_status ?? object(data.action).status ?? data.status ?? '')
  const unknownWrite = category === '变更与审批' && ['needs_reconciliation', 'unknown', 'sending', 'executing'].includes(actionStatus)
  const awaiting = result.approval_required === true || ['pending_approval', 'awaiting_approval'].includes(actionStatus)
  const outcome = unknownWrite ? '写入结果待核对' : awaiting ? '等待人工批准' : actionStatus === 'verified' ? '动作已执行 · 已核验' : actionStatus === 'executed' ? '动作已执行 · 待核验' : success === false ? '本次调用失败' : success === true && Array.isArray(rows) ? `已返回 ${rows.length} 条记录` : success === true ? '已返回结果' : data.result == null ? '回执未记录' : '回执已记录'
  const schema = category === '字段与结构' && String(data.name ?? result.tool).replace(/^mcp_odoo_/, '') === 'get_model_fields'
  const sop = object(result.sop)
  const failure = object(result.diagnostic ?? result.failure ?? (result.reason_code ? result : error))
  const publicFields = Object.fromEntries(Object.entries(payload).filter(([key, value]) => !['success', 'ok', 'tool', 'error', 'text', 'rows', 'records', 'sops', 'sop', 'verification', 'classification'].includes(key) && (typeof value !== 'object' || Array.isArray(value))))
  const values = object(args.values ?? object(args.kwargs).values)
  const otherArgs = Object.fromEntries(Object.entries(args).filter(([key]) => !['model', 'fields', 'limit', 'domain', 'values'].includes(key)))
  return <><section className="trace-query-facts"><div className="trace-result-heading"><span className="trace-category">{category}</span><strong className={unknownWrite || awaiting ? 'trace-result-waiting' : success === false || error != null ? 'trace-tool-error' : ''}>{outcome}</strong></div><div className="fact-table">
    {(model || identity != null) && <div><span>对象</span><strong>{model && documentModelLabel(model)}{identity != null ? ` · ${readableValue(identity)}` : ''}</strong></div>}
    {action && <div><span>动作</span><strong>{operationLabel(action)}</strong></div>}
    <div><span>风险等级</span><strong data-trace-risk={toolRiskTone(data)} title="根据工具用途及已记录的安全分类展示；审批决定仍由原执行链路处理。">{toolRisk(data)}</strong></div>
    {typeof payload.state === 'string' && <div><span>单据状态</span><strong>{documentStateLabel(model, String(payload.state))}</strong></div>}
    {Array.isArray(rows) && <div><span>已返回行数</span><strong>{rows.length}</strong></div>}
    {typeof count === 'number' && <div><span>声明数量</span><strong>{formatCount(count)}</strong></div>}
    {!schema && (Array.isArray(rows) || typeof count === 'number') && <div><span>读取完整性</span><strong>{complete === true ? '工具声明完整' : complete === false ? '读取不完整' : '未知'}</strong></div>}
  </div>{unknownWrite && <p className="trace-result-waiting">需要核对当前单据状态；未知写入不会自动重放。</p>}{error != null && <div className={awaiting ? 'trace-result-waiting' : 'trace-tool-error'} role="note">{toolErrorText(failure.error ?? failure.message ?? error, model, failure.reason_code ?? result.reason_code)}</div>}</section>

  {schema ? <section className="trace-result-preview"><h4>字段定义 <span>{Object.keys(payload).length}</span></h4><div className="trace-result-table"><table><thead><tr><th>字段</th><th>类型</th><th>约束</th></tr></thead><tbody>{Object.entries(payload).slice(0, 8).map(([key, value]) => { const field = object(value); return <tr key={key}><td>{approvalFieldLabel(key, model)}<small>{key}</small></td><td>{({ char: '短文本', text: '长文本', html: '富文本', integer: '整数', float: '小数', monetary: '金额', boolean: '是 / 否', selection: '枚举选项', many2one: '关联单条记录', one2many: '关联记录列表', many2many: '多条关联记录', date: '日期', datetime: '日期时间', binary: '文件数据', json: '结构化数据' } as Record<string, string>)[String(field.type)] ?? '未映射类型'}</td><td>{[field.required === true ? '必填' : '', field.readonly === true ? '只读' : '', field.relation ? documentModelLabel(String(field.relation)) : ''].filter(Boolean).join(' · ') || '未声明限制'}</td></tr> })}</tbody></table></div>{Object.keys(payload).length > 8 && <small>显示前 8 个字段，完整定义见回执。</small>}</section> : Array.isArray(rows) ? <ResultRecords rows={rows} model={model} /> : Object.hasOwn(payload, 'id') || Object.hasOwn(payload, 'name') ? <ResultRecords rows={[payload]} model={model} /> : Object.keys(publicFields).length > 0 ? <ResultRecords rows={[publicFields]} model={model} title="返回摘要" /> : null}
  {Array.isArray(result.sops) && <ResultRecords rows={result.sops} model="" title="可用业务规则" />}
  {Object.keys(sop).length > 0 && <section className="trace-result-preview"><h4>{({ safe_write_review: '单项变更审查', stock_delivery_and_return: '库存收发与退货' } as Record<string, string>)[String(sop.id)] ?? '业务操作规程'}</h4>{typeof sop.description === 'string' && <p>{sop.description}</p>}{Array.isArray(sop.steps) && <details className="trace-json-fold"><summary>原始规程步骤 · {sop.steps.length}</summary><ol>{sop.steps.map((step, index) => <li key={index}>{readableValue(step)}</li>)}</ol></details>}{Array.isArray(sop.checkpoints) && <><h4>检查点</h4><ul>{sop.checkpoints.map((point, index) => <li key={index}>{readableValue(point)}</li>)}</ul></>}</section>}
  {result.verification != null && <section className="trace-result-preview"><h4>回读核验</h4><p>{({ satisfied: '已满足核验条件', unconfirmed: '核验尚未确认', no_match: '未找到符合条件的记录', unknown: '核验结果未知' } as Record<string, string>)[String(object(result.verification).status)] ?? '核验结果未知'}</p>{Array.isArray(object(object(result.verification).evidence).records) && <ResultRecords rows={object(object(result.verification).evidence).records as unknown[]} model={model} />}</section>}
  {typeof (payload.text ?? raw) === 'string' && <section className="trace-result-preview"><h4>返回内容</h4><div className="trace-result-text"><MessageText text={String(payload.text ?? raw).slice(0, 2000)} collapsible={false} /></div>{String(payload.text ?? raw).length > 2000 && <small>显示前 2,000 个字符，完整内容见回执。</small>}</section>}
  {(args.fields != null || args.domain != null || args.limit != null || Object.keys(values).length > 0 || Object.keys(otherArgs).length > 0) && <section className="trace-result-preview"><h4>调用参数</h4><dl className="trace-record-fields">{Array.isArray(args.fields) && <div><dt>读取字段</dt><dd>{args.fields.map(field => approvalFieldLabel(String(field), model)).join('、')}</dd></div>}{args.limit != null && <div><dt>最多返回</dt><dd>{readableValue(args.limit)} 条</dd></div>}{Array.isArray(args.domain) && <div><dt>筛选条件</dt><dd>{args.domain.map(term => Array.isArray(term) && term.length === 3 ? `${approvalFieldLabel(String(term[0]), model)} ${({ '=': '等于', '!=': '不等于', '>': '大于', '<': '小于', '>=': '不少于', '<=': '不超过', in: '属于', 'not in': '不属于', ilike: '包含' } as Record<string, string>)[String(term[1])] ?? String(term[1])} ${fieldValue(String(term[0]), term[2], model)}` : ({ '&': '并且', '|': '或者', '!': '非' } as Record<string, string>)[String(term)] ?? readableValue(term)).join('；') || '无筛选条件'}</dd></div>}</dl>{Object.keys(values).length > 0 && <ResultRecords rows={[values]} model={model} title="拟提交字段" />}{Object.keys(otherArgs).length > 0 && <ResultRecords rows={[otherArgs]} model="" title="其他参数" labels={toolParameterLabels} />}</section>}
  <details className="trace-json-fold"><summary>请求</summary><div className="fact-table">{args.fields != null && <div><span>读取字段</span><strong>{readableValue(args.fields)}</strong></div>}{args.limit != null && <div><span>数量上限</span><strong>{readableValue(args.limit)}</strong></div>}</div>{args.domain != null && <JsonFold label="请求查询条件" value={args.domain} />}<JsonFold label="模型原始请求参数" value={data.arguments ?? data.original_arguments} />{(data.normalized_arguments ?? data.executed_arguments) != null && <JsonFold label="运行时规范化载荷" value={data.normalized_arguments ?? data.executed_arguments} />}</details>
  <JsonFold label={receiptLabel} value={data.result} />{(data.action ?? data.ledger) != null && <details className="trace-json-fold"><summary>关联动作账本</summary><ActionDetail data={object(data.action ?? data.ledger)} /></details>}<JsonFold label={rawLabel} value={data} /></>
}

export function LazyTraceDetail({ runId, node, revision, load, fallback }: { runId: string; node: TraceNode; revision: string; load?: LoadTraceDetail; fallback?: ReactNode }) {
  const [result, setResult] = useState<{ scope: string; key: string; detail?: TraceDetail; error?: string } | null>(null)
  const [retry, setRetry] = useState(0)
  const generation = useRef(0)
  const scope = `${runId}:${node.key}`
  const key = `${scope}:${revision}:${retry}`
  useEffect(() => {
    const current = ++generation.current
    if (!load) return
    void load(runId, node.kind, node.id).then(detail => {
      if (current !== generation.current) return
      if (!detail || detail.id !== node.id || detail.kind !== node.kind) { setResult(previous => ({ scope, key, detail: previous?.scope === scope ? previous.detail : undefined, error: '详情未返回或记录标识不一致。' })); return }
      setResult({ scope, key, detail })
    }).catch(error => { if (current === generation.current) setResult(previous => ({ scope, key, detail: previous?.scope === scope ? previous.detail : undefined, error: String(error instanceof Error ? error.message : error) })) })
    return () => { generation.current += 1 }
  }, [runId, node.id, node.kind, key, retry, load])
  if (!load) return <>{fallback ?? <p>当前接口未提供按需详情。</p>}</>
  if (result?.scope !== scope) return <p className="loading-line" role="status">正在读取所选记录…</p>
  return <>{result.key !== key ? <p className="loading-line" role="status">正在更新所选记录…</p> : result.error ? <div className="trace-detail-error" role="alert"><p>{result.error}</p><button type="button" onClick={() => setRetry(value => value + 1)}>重新读取详情</button></div> : null}{result.detail && <DetailBody key={scope} detail={result.detail} />}</>
}
