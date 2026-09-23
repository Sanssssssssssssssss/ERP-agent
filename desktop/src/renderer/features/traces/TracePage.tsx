import { ArrowDownToLine,ChevronDown,ChevronRight,CircleAlert,CircleCheck,Search } from 'lucide-react'
import { useEffect,useMemo,useRef,useState, type CSSProperties } from 'react'
import { EmptyState,MessageText,StatusBadge } from '../../components/common'
import { roundStatusLabel,runDisplayLabel,toolStatusLabel } from '../../presentation'
import {
BusinessDetailProjection,
Check,
LiveMessage,
Round,
Run,
ToolReceipt,
TraceBundle,
formatCount,
formatDuration,
formatInstant,
jsonText,
labelFor,
runStatusLabel
} from '../../protocol'
import { LazyTraceDetail, type LoadTraceDetail } from './TraceInspectorDetails'
import { instant, timeBounds, traceNodes, visibleTraceNodes, type TraceNode } from './trace-model'

export function RunRow({ run }: { run: Run }) { return <div className="run-row"><div><strong>{run.id}</strong><span>{formatInstant(run.started_at)} · {formatDuration(run.elapsed_seconds)}</span></div><div className="run-row-meta"><StatusBadge status={run.status} label={runDisplayLabel(run.status)} /><span>{formatCount(run.tool_count)} 工具</span></div></div> }

export function VerificationPage({ checks, observedAt, stale }: { checks: Check[]; observedAt?: string; stale: boolean }) { return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">独立回读</span><h3>业务核验</h3></div><span>{stale ? '可能过期' : `读取于 ${formatInstant(observedAt)}`}</span></div>{checks.length === 0 ? <EmptyState title="核验结果未知" detail="主机尚未提供独立业务检查回执。" /> : <div className="check-table">{checks.map((check) => <div className="check-row" key={check.name}><span className={`check-mark check-${check.status}`}>{check.status === 'passed' ? '✓' : check.status === 'failed' ? '!' : '?'}</span><div><strong>{check.label || check.name}</strong><span>{check.detail || '没有详细说明'}</span></div><span className={`state-badge state-${check.status}`}>{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '未知'}</span></div>)}</div>}</div> }

export function TracePage({ trace, runs, readback, liveMessages = [], selectedRunId, loading, target, onRunSelect, onLoadDetail }: { trace: TraceBundle | null; runs: Run[]; readback?: BusinessDetailProjection['business']['readback']; liveMessages?: LiveMessage[]; selectedRunId: string; loading: boolean; target: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null; onRunSelect: (id: string) => void; onLoadDetail?: LoadTraceDetail }) {
  const [selectedNode, setSelectedNode] = useState('run')
  const [query, setQuery] = useState('')
  const [view, setView] = useState<'tree' | 'timeline'>('tree')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const nodes = useMemo(() => trace ? traceNodes(trace) : [], [trace])
  const visibleNodes = useMemo(() => visibleTraceNodes(nodes, query, collapsed), [nodes, query, collapsed])
  const bounds = useMemo(() => timeBounds(nodes), [nodes])
  const selectedRecord = nodes.find(node => node.key === selectedNode)
  const [followingRunId, setFollowingRunId] = useState<string | null>(null)
  const following = Boolean(trace?.run?.id && followingRunId === trace.run.id)
  const setFollowing = (enabled: boolean) => setFollowingRunId(enabled ? trace?.run?.id ?? null : null)
  const treeRef = useRef<HTMLElement>(null)
  const detailRef = useRef<HTMLElement>(null)
  const toolsById = new Map((trace?.tools ?? []).map((tool) => [tool.id, tool]))
  const groupedIds = new Set((trace?.rounds ?? []).flatMap((round) => round.tool_ids))
  // Tool receipts arrive before their completed round. Keep them visible immediately.
  const ungroupedTools = (trace?.tools ?? []).filter((tool) => !groupedIds.has(tool.id))
  const toolEvents = new Map<string, Record<string, unknown>>()
  for (const event of trace?.events ?? []) {
    const id = String(event.tool_call_id || event.tool_id || '')
    if (id && ['tool_start', 'tool_progress', 'tool_end'].includes(String(event.type))) toolEvents.set(id, event)
  }
  const runActive = trace?.run?.status === 'running' || trace?.run?.status === 'cancel_requested'
  const publicMessage = [...liveMessages].reverse().find((message) => message.run_id === trace?.run?.id && (!message.role || message.role === 'assistant') && message.text)
  const streaming = runActive && publicMessage?.status === 'streaming'
  const lastRound = trace?.rounds.at(-1)
  const lastTool = trace?.tools.at(-1)
  const lastEvent = trace?.events?.at(-1)
  const eventToolId = String(lastEvent?.tool_call_id || lastEvent?.tool_id || '')
  const latestNode = streaming ? 'live' : lastEvent?.type === 'round_end' && lastRound ? `round:${lastRound.index}` : eventToolId && (toolsById.has(eventToolId) || toolEvents.has(eventToolId)) ? `tool:${eventToolId}` : ungroupedTools.length ? `tool:${ungroupedTools.at(-1)!.id}` : lastRound ? `round:${lastRound.index}` : publicMessage ? 'live' : lastTool ? `tool:${lastTool.id}` : 'run'
  const updateKey = `${latestNode}:${lastEvent?.at || ''}:${publicMessage?.sequence || 0}:${trace?.run?.status || ''}`

  useEffect(() => {
    setFollowing(false)
    if (target?.toolId) setSelectedNode(`tool:${target.toolId}`)
    else if (target?.actionId) setSelectedNode(`action:${target.actionId}`)
    else if (target?.kind === 'readback') setSelectedNode(`readback:${target.runId || 'unknown'}`)
    else setSelectedNode('run')
  }, [target?.actionId, target?.kind, target?.runId, target?.toolId, trace?.run?.id])
  useEffect(() => { setQuery(''); setCollapsed(new Set()) }, [trace?.run?.id])
  useEffect(() => { if (following) setSelectedNode(latestNode) }, [following, latestNode])
  useEffect(() => {
    const tree = treeRef.current
    const active = tree?.querySelector<HTMLElement>('[aria-current="true"]')
    if (tree && active) {
      const top = active.getBoundingClientRect().top - tree.getBoundingClientRect().top + tree.scrollTop
      if (top < tree.scrollTop || top + active.offsetHeight > tree.scrollTop + tree.clientHeight) tree.scrollTop = top - 8
    }
    if (detailRef.current) detailRef.current.scrollTop = 0
  }, [selectedNode])
  useEffect(() => {
    if (following && selectedNode === 'live' && detailRef.current) detailRef.current.scrollTop = detailRef.current.scrollHeight
  }, [following, selectedNode, publicMessage?.text])

  const selectNode = (node: string) => { setFollowing(false); setSelectedNode(node) }
  const revealNode = (key: string) => { setQuery(''); setCollapsed(new Set()); selectNode(key) }
  const selectedTool = selectedNode.startsWith('tool:') ? toolsById.get(selectedNode.slice(5)) : undefined
  const selectedEvent = selectedNode.startsWith('tool:') ? toolEvents.get(selectedNode.slice(5)) : undefined
  const selectedRound = selectedNode.startsWith('round:') ? trace?.rounds.find((round) => String(round.index) === selectedNode.slice(6)) : undefined
  const selectedAction = selectedNode.startsWith('action:') ? (trace?.tools ?? []).slice().reverse().find((tool) => tool.action_id === selectedNode.slice(7)) : undefined
  const missingTool = selectedNode.startsWith('tool:') && !selectedTool
  const missingAction = selectedNode.startsWith('action:') && !selectedAction && !selectedRecord
  const readbackMatches = target?.kind === 'readback' && Boolean(readback && target.runId && readback.latest_run_id && target.runId === readback.latest_run_id)
  const heading = selectedRecord?.kind === 'action' || selectedRecord?.kind === 'request' || selectedRecord?.kind === 'event' ? selectedRecord.title : selectedTool?.name || selectedAction?.name || (selectedRound ? `第 ${selectedRound.index} 轮` : selectedNode === 'live' ? '公开回复' : selectedNode.startsWith('readback:') ? '独立回读快照' : missingTool ? '工具回执不可用' : missingAction ? '动作回执不可用' : '运行总览')
  const node = (id: string, title: string, status: string, tool = false, active = false) => <button key={id} className={`trace-node ${tool ? 'trace-tool-node' : ''} ${selectedNode === id ? 'active' : ''}`} aria-current={selectedNode === id ? 'true' : undefined} type="button" onClick={() => selectNode(id)}><strong title={title}>{title}</strong><span>{active && <i className="trace-live-dot" aria-hidden="true" />}{status}</span></button>
  const pauseOnKey = (key: string) => { if (['ArrowUp', 'PageUp', 'Home'].includes(key)) setFollowing(false) }
  const issueNodes = nodes.filter(node => node.issue)
  const diagnosticError = trace?.diagnostics?.first_observed_error as { kind?: string; id?: string } | undefined
  const diagnosticEvent = diagnosticError?.kind === 'event' ? trace?.events?.find((event, index) => String(event.id ?? index) === diagnosticError.id) : undefined
  const diagnosticTool = diagnosticEvent?.tool_call_id || diagnosticEvent?.tool_id
  const firstIssue = nodes.find(node => node.key === (diagnosticTool ? `tool:${diagnosticTool}` : `${diagnosticError?.kind}:${diagnosticError?.id}`)) ?? [...issueNodes].sort((a, b) => (instant(a.startedAt) ?? Infinity) - (instant(b.startedAt) ?? Infinity))[0]
  const detailRecord = selectedRecord ?? (selectedAction ? nodes.find(node => node.key === `tool:${selectedAction.id}`) : undefined)
  const runNode: TraceNode = { key: 'run', id: trace?.run?.id || '', kind: 'run', title: '运行', status: trace?.run?.status || 'unknown', search: '', issue: false }
  const queryMatchesRun = Boolean(query.trim() && `${trace?.run?.id || ''} ${trace?.run?.error || ''} ${trace?.run?.error_detail || ''}`.toLocaleLowerCase().includes(query.toLocaleLowerCase().trim()))
  return (
    <div className="trace-page">
      <div className="trace-toolbar">
        <label>运行<select value={selectedRunId} onChange={(event) => onRunSelect(event.target.value)}><option value="">选择运行</option>{runs.map((run) => <option key={run.id} value={run.id} title={run.id}>{formatInstant(run.started_at)} · {labelFor(runStatusLabel, run.status)} · {formatCount(run.model_rounds)} 轮</option>)}</select></label>
        {trace?.run && <div className="trace-metrics"><StatusBadge status={trace.run.status} label={runDisplayLabel(trace.run.status)} /><span>{formatCount(trace.run.model_rounds)} 轮 · {formatCount(trace.run.tool_count)} 工具</span></div>}
        {trace && <div className="trace-navigation"><button type="button" className="secondary-button" aria-pressed={following} onClick={() => setFollowing(!following)}>跟随最新</button><button type="button" className="secondary-button" onClick={() => { selectNode(latestNode); if (treeRef.current) treeRef.current.scrollTop = treeRef.current.scrollHeight }}><ArrowDownToLine size={14} />跳到最新</button><span role="status">{following ? '正在跟随' : '自由浏览'}{runActive && <i className="trace-live-dot trace-update-dot" key={updateKey} aria-hidden="true" />}</span></div>}
      </div>
      {trace && <div className="trace-inspector-controls"><label className="trace-search"><Search size={15} /><input aria-label="搜索调用、订单或错误" placeholder="搜索工具、订单号、错误" value={query} onChange={event => setQuery(event.target.value)} /></label><div className="trace-view-switch" role="group" aria-label="调用显示方式"><button type="button" aria-pressed={view === 'tree'} onClick={() => setView('tree')}>调用树</button><button type="button" aria-pressed={view === 'timeline'} onClick={() => setView('timeline')}>时间轴</button></div><button type="button" onClick={() => setCollapsed(new Set(nodes.map(node => node.key)))}>全部折叠</button><button type="button" onClick={() => setCollapsed(new Set())}>全部展开</button><button type="button" disabled={!firstIssue} onClick={() => firstIssue && revealNode(firstIssue.key)}><CircleAlert size={14} />首个记录异常{issueNodes.length ? ` · ${issueNodes.length}` : ''}</button></div>}
      {loading && <div className="loading-line">正在读取运行详情…</div>}
      {!loading && !trace && <EmptyState title="选择一次运行" detail="这里展示已记录的运行过程。" />}
      {trace && <div className="trace-split">
        <nav ref={treeRef} className="trace-tree" aria-label="运行、轮次与工具" onWheel={() => setFollowing(false)} onPointerDown={() => setFollowing(false)} onKeyDown={(event) => pauseOnKey(event.key)}>
          {node('run', '运行', trace.run?.id || '运行未知')}
          {queryMatchesRun && <p className="trace-search-match">运行错误或标识包含搜索内容，可选择“运行”查看。</p>}
          {target?.kind === 'readback' && node(`readback:${target.runId || 'unknown'}`, '独立回读快照', readbackMatches ? '已返回' : '当前运行无此快照')}
          {view === 'timeline' && <p className="trace-timeline-note">条形仅使用真实起止时间。没有完整时间的记录显示未知。</p>}
          {visibleNodes.map(item => <div key={item.key} className={`trace-tree-row ${item.issue ? 'trace-row-issue' : ''}`} style={{ '--trace-depth': Math.min(item.depth, 4) } as CSSProperties}>
            {item.hasChildren ? <button className="trace-collapse" type="button" aria-label={`${collapsed.has(item.key) ? '展开' : '折叠'} ${item.title}`} aria-expanded={!collapsed.has(item.key)} onClick={() => setCollapsed(old => { const next = new Set(old); if (next.has(item.key)) next.delete(item.key); else next.add(item.key); return next })}>{collapsed.has(item.key) ? <ChevronRight size={14} /> : <ChevronDown size={14} />}</button> : <span className="trace-collapse-spacer" />}
            <div className="trace-node-body">{node(item.key, item.title, item.status === 'receipt_pending' ? '已结束 · 回执未到达' : item.kind === 'round' ? roundStatusLabel(item.status) : traceNodeStatus(item.status) + (item.association === 'unlinked' ? ' · 未关联' : ''), item.kind === 'tool', runActive && item.status === 'running')}{view === 'timeline' && <TimelineBar node={item} bounds={bounds} />}</div>
          </div>)}
          {query && !visibleNodes.length && !queryMatchesRun && <p className="muted">概览中没有匹配记录。尚未加载的正文不在搜索范围内。</p>}
          {publicMessage && node('live', '公开回复', streaming ? '接收中' : '已接收', false, streaming)}
          {target?.toolId && !toolsById.has(target.toolId) && !toolEvents.has(target.toolId) && !groupedIds.has(target.toolId) && node(`tool:${target.toolId}`, target.toolId, '不可用 · 尚未返回回执', true)}
          {missingAction && node(selectedNode, selectedNode.slice(7), '回执不可用 · 尚未返回', true)}
        </nav>
        <section ref={detailRef} className="trace-detail-panel" tabIndex={0} aria-label="所选运行详情" onWheel={() => setFollowing(false)} onPointerDown={() => setFollowing(false)} onKeyDown={(event) => pauseOnKey(event.key)}>
          <div className="section-heading"><h3>{heading}</h3><span>{trace.run?.id || '运行未知'}</span></div>
          {detailRecord && (!missingTool || selectedTool) ? <><NodeFacts node={detailRecord} /><UsageBreakdown usage={selectedRound?.usage ?? trace.requests?.find(request => request.id === detailRecord.id)?.usage} /><LazyTraceDetail runId={trace.run?.id || ''} node={detailRecord} revision={`${detailRecord.status}:${detailRecord.endedAt || ''}:${detailRecord.revision || ''}`} load={onLoadDetail} fallback={selectedTool ? <ToolDetail tool={selectedTool} /> : selectedAction ? <ToolDetail tool={selectedAction} /> : selectedRound ? <RoundDetail round={selectedRound} /> : undefined} /></> : selectedNode === 'live' ? <div className="trace-detail-content trace-public-text"><p className="trace-label" role="status">{streaming ? '正在接收公开回复' : '已接收的公开回复'}</p>{publicMessage ? <MessageText text={publicMessage.text} collapsible={false} /> : <p>本轮暂无公开回复。</p>}{streaming && <span className="trace-stream-caret" key={publicMessage?.sequence} aria-hidden="true" />}</div> : selectedNode.startsWith('readback:') ? <ReadbackDetail readback={readbackMatches ? readback : undefined} /> : missingTool ? <EmptyState title="工具回执不可用" detail={selectedEvent?.type === 'tool_end' ? '工具已结束，完整回执尚未到达。' : runActive && selectedEvent ? '工具正在执行，完整回执尚未到达。' : '当前运行尚未返回该工具的完整回执。'} /> : missingAction ? <EmptyState title="动作回执不可用" detail="当前运行尚未返回该动作的持久化工具回执。" /> : <><RunDetail trace={trace} />{onLoadDetail && <LazyTraceDetail runId={runNode.id} node={runNode} revision={runNode.status} load={onLoadDetail} />}<TraceDiagnostics nodes={nodes} trace={trace} onSelect={revealNode} /></>}
        </section>
      </div>}
    </div>
  )
}

export function RunDetail({ trace }: { trace: TraceBundle }) { if (!trace.run) return <EmptyState title="运行状态未知" detail="主机尚未返回运行摘要。" />; return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{runDisplayLabel(trace.run.status)}</strong></div><div><span>轮次</span><strong>{formatCount(trace.run.model_rounds)}</strong></div><div><span>工具</span><strong>{formatCount(trace.run.tool_count)}</strong></div><div><span>耗时</span><strong>{formatDuration(trace.run.elapsed_seconds)}</strong></div></div><UsageBreakdown usage={trace.run.usage} />{trace.run.error && <div className="notice red"><CircleAlert size={15} /><span>{trace.run.error_detail || trace.run.error}</span></div>}</div> }

function traceNodeStatus(status: string) { return ({ pending: '等待中', recorded: '已留档', linked: '已关联', unlinked: '未关联', success: '完成', sent: '已发送请求', retrying: '重试中', retried: '重试已结束', executed: '已执行', verified: '已核验', rejected: '已拒绝', pending_approval: '待审批', approval_required: '待审批', unknown: '未知', needs_reconciliation: '待核对', error: '错误', failed: '失败', known_failed: '已记录失败' } as Record<string, string>)[status] || toolStatusLabel(status) }

function NodeFacts({ node }: { node: TraceNode }) { return <div className="fact-table trace-node-facts"><div><span>状态</span><strong>{traceNodeStatus(node.status)}</strong></div><div><span>耗时</span><strong>{formatDuration(node.durationMs == null ? undefined : node.durationMs / 1000)}</strong></div><div><span>开始</span><strong>{formatInstant(node.startedAt)}</strong></div><div><span>结束</span><strong>{formatInstant(node.endedAt)}</strong></div></div> }

function TimelineBar({ node, bounds }: { node: TraceNode; bounds: ReturnType<typeof timeBounds> }) {
  const start = instant(node.startedAt)
  const end = instant(node.endedAt)
  if (!bounds || start === undefined || end === undefined || end < start) return <span className="trace-time-unknown">起止时间未知{node.durationMs != null ? ` · 耗时 ${formatDuration(node.durationMs / 1000)}` : ''}</span>
  const range = Math.max(1, bounds.end - bounds.start)
  return <div className="trace-timeline-track" title={`${node.startedAt} — ${node.endedAt}`} aria-label={`真实时间区间 ${formatDuration((end - start) / 1000)}`}><span style={{ left: `${100 * (start - bounds.start) / range}%`, width: `${100 * (end - start) / range}%` }} /><small>{formatDuration((end - start) / 1000)}</small></div>
}

function TraceDiagnostics({ nodes, trace, onSelect }: { nodes: TraceNode[]; trace: TraceBundle; onSelect: (key: string) => void }) {
  const timed = nodes.filter(node => node.kind === 'tool' && node.durationMs != null).sort((a, b) => b.durationMs! - a.durationMs!)
  const requestsWithUsage = nodes.filter(node => node.kind === 'request' && node.tokens != null)
  const costNodes = (requestsWithUsage.length ? requestsWithUsage : nodes.filter(node => node.kind === 'round' && node.tokens != null)).sort((a, b) => b.tokens! - a.tokens!)
  const issues = nodes.filter(node => node.issue)
  const unlinked = trace.requests?.filter(request => request.association === 'unlinked').length ?? 0
  const repeats = (trace.diagnostics?.repeated_tools ?? []) as Array<{ name: string; count: number; tool_ids: string[] }>
  const wait = trace.diagnostics?.approval_wait as { known_seconds?: number | null; execution_seconds?: number | null; complete?: boolean; notice?: string } | undefined
  const compactionRequests = trace.requests?.filter(request => request.kind === 'compaction').length ?? 0
  return <section className="trace-diagnostics" aria-label="运行诊断"><h4>运行诊断</h4><p>{trace.requests?.length ?? 0} 次留档请求 · {trace.actions?.length ?? 0} 个业务动作{unlinked ? ` · ${unlinked} 次请求尚未关联轮次` : ''}</p><p className="muted">金额成本未报告。用量排行仅比较同类已报告记录，不将请求与轮次重复相加。</p>
    {wait && <div className="fact-table"><div><span>已记录审批等待{wait.complete ? '' : '（不完整）'}</span><strong>{formatDuration(wait.known_seconds)}</strong></div><div><span>扣除完整审批等待后的耗时</span><strong>{formatDuration(wait.execution_seconds)}</strong></div></div>}
    {compactionRequests > 0 && <p>已留档的上下文压缩请求：{compactionRequests} 次</p>}
    <div className="trace-rankings"><section><h4>模型用量排行</h4>{costNodes.length ? <ol>{costNodes.slice(0, 5).map(node => <li key={node.key}><button type="button" onClick={() => onSelect(node.key)}><span>{node.title}</span><b>{formatCount(node.tokens)} token</b></button></li>)}</ol> : <p>没有可排序的已报告用量。</p>}</section><section><h4>工具耗时排行</h4>{timed.length ? <ol>{timed.slice(0, 5).map(node => <li key={node.key}><button type="button" onClick={() => onSelect(node.key)}><span>{node.title}</span><b>{formatDuration(node.durationMs! / 1000)}</b></button></li>)}</ol> : <p>没有可排序的已报告耗时。</p>}</section></div>
    {repeats.length > 0 && <section className="trace-repeated"><h4>同参数重复调用</h4><p className="muted">同名、相同 JSON 参数的调用；不据此判定无效。</p>{repeats.map((repeat, index) => <details key={`${repeat.name}:${index}`}><summary>{repeat.name} · {repeat.count} 次</summary><div className="evidence-list">{repeat.tool_ids.map((id, index) => <button key={id} type="button" className="evidence-link" onClick={() => onSelect(`tool:${id}`)}>第 {index + 1} 次 · {id}</button>)}</div></details>)}</section>}
    {issues.length > 0 && <details className="trace-json-fold"><summary>异常记录 · {issues.length}</summary><p className="muted">时间缺失时无法确认先后；首个记录异常不等于业务根因。</p><ul className="trace-issue-list">{issues.map(node => <li key={node.key}><button type="button" onClick={() => onSelect(node.key)}>{node.title} · {traceNodeStatus(node.status)}</button></li>)}</ul></details>}
    {trace.diagnostics && <details className="trace-json-fold"><summary>证据覆盖情况</summary><pre>{jsonText(trace.diagnostics)}</pre></details>}
  </section>
}

export function RoundDetail({ round }: { round: Round }) { return <div className="trace-detail-content"><MessageText text={round.text || '本轮没有公开回复。'} collapsible={false} /><UsageBreakdown usage={round.usage} /><div className="trace-label">状态</div><p>{round.status === 'error' ? '错误' : roundStatusLabel(round.status)} · {formatDuration(round.elapsed_seconds)}</p></div> }

export function ToolDetail({ tool }: { tool: ToolReceipt }) { return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{toolStatusLabel(tool.status)}</strong></div><div><span>轮次</span><strong>{tool.round == null ? '未知' : `第 ${tool.round} 轮`}</strong></div><div><span>耗时</span><strong>{formatDuration(tool.elapsed_seconds)}</strong></div><div><span>审批</span><strong>{tool.action_id || '未知'}</strong></div></div><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div></div> }

export function ReadbackDetail({ readback }: { readback?: BusinessDetailProjection['business']['readback'] }) { return <div className="trace-detail-content"><div className="notice blue"><CircleCheck size={15} /><span>这是独立 Odoo 回读快照，时间与原始工具调用分开记录。</span></div>{readback ? <><div className="fact-table"><div><span>快照时间</span><strong>{formatInstant(readback.observed_at)}</strong></div><div><span>来源</span><strong>独立 Odoo 回读</strong></div><div><span>核验项</span><strong>{readback.checks?.length ?? 0}</strong></div><div><span>状态</span><strong>{readback.stale ? '可能已过期' : '已返回'}</strong></div></div><details className="resource-fields"><summary>查看回读核验</summary><pre>{jsonText(readback.checks ?? [])}</pre></details></> : <div className="empty-state"><strong>快照详情不可用</strong><p>当前运行没有匹配的独立回读快照。</p></div>}</div> }

export function UsageBreakdown({ usage }: { usage?: Run['usage'] }) {
  if (!usage) return <div className="usage-breakdown"><span>用量未知</span></div>
  return <div className="usage-breakdown" aria-label="Token 用量"><span>未缓存输入 {formatCount(usage.input)}</span><span>缓存命中 {formatCount(usage.cache_read)}</span><span>输出（含推理） {formatCount(usage.output)}</span><span>推理 {formatCount(usage.reasoning)}</span><span>总计 {formatCount(usage.total)}</span>{(usage.compaction_calls ?? 0) > 0 && <span>压缩记录 {usage.compaction_calls} 条 · {usage.compaction_total == null ? '未完整报告' : formatCount(usage.compaction_total)} token</span>}{usage.reported_total != null && <span>已报告 {formatCount(usage.reported_total)}{usage.missing_usage_rounds ? `，${usage.missing_usage_rounds} 轮未报告` : ''}</span>}</div>
}

export function ToolReceiptRow({ tool }: { tool: ToolReceipt }) { return <details className="tool-row"><summary><span className={`tool-status tool-${tool.status}`}>{toolStatusLabel(tool.status)}</span><strong>{tool.name}</strong><small>{tool.round ? `第 ${tool.round} 轮 · ` : ''}{formatDuration(tool.elapsed_seconds)}</small></summary><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div>{tool.action_id && <span className="receipt-link">关联审批：{tool.action_id}</span>}</details> }
