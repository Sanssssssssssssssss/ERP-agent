import { ArrowDownToLine,CircleAlert,CircleCheck } from 'lucide-react'
import { useEffect,useRef,useState } from 'react'
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

export function RunRow({ run }: { run: Run }) { return <div className="run-row"><div><strong>{run.id}</strong><span>{formatInstant(run.started_at)} · {formatDuration(run.elapsed_seconds)}</span></div><div className="run-row-meta"><StatusBadge status={run.status} label={runDisplayLabel(run.status)} /><span>{formatCount(run.tool_count)} 工具</span></div></div> }

export function VerificationPage({ checks, observedAt, stale }: { checks: Check[]; observedAt?: string; stale: boolean }) { return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">独立回读</span><h3>业务核验</h3></div><span>{stale ? '可能过期' : `读取于 ${formatInstant(observedAt)}`}</span></div>{checks.length === 0 ? <EmptyState title="核验结果未知" detail="主机尚未提供独立业务检查回执。" /> : <div className="check-table">{checks.map((check) => <div className="check-row" key={check.name}><span className={`check-mark check-${check.status}`}>{check.status === 'passed' ? '✓' : check.status === 'failed' ? '!' : '?'}</span><div><strong>{check.label || check.name}</strong><span>{check.detail || '没有详细说明'}</span></div><span className={`state-badge state-${check.status}`}>{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '未知'}</span></div>)}</div>}</div> }

export function TracePage({ trace, runs, readback, liveMessages = [], selectedRunId, loading, target, onRunSelect }: { trace: TraceBundle | null; runs: Run[]; readback?: BusinessDetailProjection['business']['readback']; liveMessages?: LiveMessage[]; selectedRunId: string; loading: boolean; target: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null; onRunSelect: (id: string) => void }) {
  const [selectedNode, setSelectedNode] = useState('run')
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
  const missingReceipts = [...toolEvents].filter(([id]) => !toolsById.has(id) && !groupedIds.has(id))
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
  const selectedTool = selectedNode.startsWith('tool:') ? toolsById.get(selectedNode.slice(5)) : undefined
  const selectedEvent = selectedNode.startsWith('tool:') ? toolEvents.get(selectedNode.slice(5)) : undefined
  const selectedRound = selectedNode.startsWith('round:') ? trace?.rounds.find((round) => String(round.index) === selectedNode.slice(6)) : undefined
  const selectedAction = selectedNode.startsWith('action:') ? (trace?.tools ?? []).slice().reverse().find((tool) => tool.action_id === selectedNode.slice(7)) : undefined
  const missingTool = selectedNode.startsWith('tool:') && !selectedTool
  const missingAction = selectedNode.startsWith('action:') && !selectedAction
  const readbackMatches = target?.kind === 'readback' && Boolean(readback && target.runId && readback.latest_run_id && target.runId === readback.latest_run_id)
  const heading = selectedTool?.name || selectedAction?.name || (selectedRound ? `第 ${selectedRound.index} 轮` : selectedNode === 'live' ? '公开回复' : selectedNode.startsWith('readback:') ? '独立回读快照' : missingTool ? '工具回执不可用' : missingAction ? '动作回执不可用' : '运行总览')
  const node = (id: string, title: string, status: string, tool = false, active = false) => <button key={id} className={`trace-node ${tool ? 'trace-tool-node' : ''} ${selectedNode === id ? 'active' : ''}`} aria-current={selectedNode === id ? 'true' : undefined} type="button" onClick={() => selectNode(id)}><strong title={title}>{title}</strong><span>{active && <i className="trace-live-dot" aria-hidden="true" />}{status}</span></button>
  const toolNode = (id: string) => { const tool = toolsById.get(id); return node(`tool:${id}`, tool?.name || id, tool ? toolStatusLabel(tool.status) : '回执未知', true, runActive && tool?.status === 'running') }
  const pauseOnKey = (key: string) => { if (['ArrowUp', 'PageUp', 'Home'].includes(key)) setFollowing(false) }
  return (
    <div className="trace-page">
      <div className="trace-toolbar">
        <label>运行<select value={selectedRunId} onChange={(event) => onRunSelect(event.target.value)}><option value="">选择运行</option>{runs.map((run) => <option key={run.id} value={run.id}>{run.id} · {labelFor(runStatusLabel, run.status)}</option>)}</select></label>
        {trace?.run && <div className="trace-metrics"><StatusBadge status={trace.run.status} label={runDisplayLabel(trace.run.status)} /><span>{formatCount(trace.run.model_rounds)} 轮 · {formatCount(trace.run.tool_count)} 工具</span></div>}
        {trace && <div className="trace-navigation"><button type="button" className="secondary-button" aria-pressed={following} onClick={() => setFollowing(!following)}>跟随最新</button><button type="button" className="secondary-button" onClick={() => { selectNode(latestNode); if (treeRef.current) treeRef.current.scrollTop = treeRef.current.scrollHeight }}><ArrowDownToLine size={14} />跳到最新</button><span role="status">{following ? '正在跟随' : '自由浏览'}{runActive && <i className="trace-live-dot trace-update-dot" key={updateKey} aria-hidden="true" />}</span></div>}
      </div>
      {loading && <div className="loading-line">正在读取运行详情…</div>}
      {!loading && !trace && <EmptyState title="选择一次运行" detail="这里展示已记录的运行过程。" />}
      {trace && <div className="trace-split">
        <nav ref={treeRef} className="trace-tree" aria-label="运行、轮次与工具" onWheel={() => setFollowing(false)} onPointerDown={() => setFollowing(false)} onKeyDown={(event) => pauseOnKey(event.key)}>
          {node('run', '运行', trace.run?.id || '运行未知')}
          {target?.kind === 'readback' && node(`readback:${target.runId || 'unknown'}`, '独立回读快照', readbackMatches ? '已返回' : '当前运行无此快照')}
          {trace.rounds.map((round) => <div key={round.index} className="trace-tree-round">{node(`round:${round.index}`, `第 ${round.index} 轮`, round.status === 'error' ? '错误' : roundStatusLabel(round.status), false, runActive && round.status === 'running')}{round.tool_ids.map(toolNode)}</div>)}
          {ungroupedTools.map((tool) => toolNode(tool.id))}
          {missingReceipts.map(([id, event]) => node(`tool:${id}`, String(event.tool_name || id), event.type === 'tool_end' ? '已结束 · 回执未到达' : runActive ? '活动中 · 回执未到达' : '回执未知', true, runActive && event.type !== 'tool_end'))}
          {publicMessage && node('live', '公开回复', streaming ? '接收中' : '已接收', false, streaming)}
          {target?.toolId && !toolsById.has(target.toolId) && !toolEvents.has(target.toolId) && !groupedIds.has(target.toolId) && node(`tool:${target.toolId}`, target.toolId, '不可用 · 尚未返回回执', true)}
          {missingAction && node(selectedNode, selectedNode.slice(7), '回执不可用 · 尚未返回', true)}
        </nav>
        <section ref={detailRef} className="trace-detail-panel" tabIndex={0} aria-label="所选运行详情" onWheel={() => setFollowing(false)} onPointerDown={() => setFollowing(false)} onKeyDown={(event) => pauseOnKey(event.key)}>
          <div className="section-heading"><h3>{heading}</h3><span>{trace.run?.id || '运行未知'}</span></div>
          {selectedTool ? <ToolDetail tool={selectedTool} /> : selectedAction ? <ToolDetail tool={selectedAction} /> : selectedRound ? <RoundDetail round={selectedRound} /> : selectedNode === 'live' ? <div className="trace-detail-content trace-public-text"><p className="trace-label" role="status">{streaming ? '正在接收公开回复' : '已接收的公开回复'}</p>{publicMessage ? <MessageText text={publicMessage.text} collapsible={false} /> : <p>本轮暂无公开回复。</p>}{streaming && <span className="trace-stream-caret" key={publicMessage?.sequence} aria-hidden="true" />}</div> : selectedNode.startsWith('readback:') ? <ReadbackDetail readback={readbackMatches ? readback : undefined} /> : missingTool ? <EmptyState title="工具回执不可用" detail={selectedEvent?.type === 'tool_end' ? '工具已结束，完整回执尚未到达。' : runActive && selectedEvent ? '工具正在执行，完整回执尚未到达。' : '当前运行尚未返回该工具的完整回执。'} /> : missingAction ? <EmptyState title="动作回执不可用" detail="当前运行尚未返回该动作的持久化工具回执。" /> : <RunDetail trace={trace} />}
        </section>
      </div>}
    </div>
  )
}

export function RunDetail({ trace }: { trace: TraceBundle }) { if (!trace.run) return <EmptyState title="运行状态未知" detail="主机尚未返回运行摘要。" />; return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{runDisplayLabel(trace.run.status)}</strong></div><div><span>轮次</span><strong>{formatCount(trace.run.model_rounds)}</strong></div><div><span>工具</span><strong>{formatCount(trace.run.tool_count)}</strong></div><div><span>耗时</span><strong>{formatDuration(trace.run.elapsed_seconds)}</strong></div></div><UsageBreakdown usage={trace.run.usage} />{trace.run.error && <div className="notice red"><CircleAlert size={15} /><span>{trace.run.error_detail || trace.run.error}</span></div>}</div> }

export function RoundDetail({ round }: { round: Round }) { return <div className="trace-detail-content"><MessageText text={round.text || '本轮没有公开回复。'} collapsible={false} /><UsageBreakdown usage={round.usage} /><div className="trace-label">状态</div><p>{round.status === 'error' ? '错误' : roundStatusLabel(round.status)} · {formatDuration(round.elapsed_seconds)}</p></div> }

export function ToolDetail({ tool }: { tool: ToolReceipt }) { return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{toolStatusLabel(tool.status)}</strong></div><div><span>轮次</span><strong>{tool.round == null ? '未知' : `第 ${tool.round} 轮`}</strong></div><div><span>耗时</span><strong>{formatDuration(tool.elapsed_seconds)}</strong></div><div><span>审批</span><strong>{tool.action_id || '未知'}</strong></div></div><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div></div> }

export function ReadbackDetail({ readback }: { readback?: BusinessDetailProjection['business']['readback'] }) { return <div className="trace-detail-content"><div className="notice blue"><CircleCheck size={15} /><span>这是独立 Odoo 回读快照，时间与原始工具调用分开记录。</span></div>{readback ? <><div className="fact-table"><div><span>快照时间</span><strong>{formatInstant(readback.observed_at)}</strong></div><div><span>来源</span><strong>独立 Odoo 回读</strong></div><div><span>核验项</span><strong>{readback.checks?.length ?? 0}</strong></div><div><span>状态</span><strong>{readback.stale ? '可能已过期' : '已返回'}</strong></div></div><details className="resource-fields"><summary>查看回读核验</summary><pre>{jsonText(readback.checks ?? [])}</pre></details></> : <div className="empty-state"><strong>快照详情不可用</strong><p>当前运行没有匹配的独立回读快照。</p></div>}</div> }

export function UsageBreakdown({ usage }: { usage?: Run['usage'] }) {
  if (!usage) return <div className="usage-breakdown"><span>用量未知</span></div>
  return <div className="usage-breakdown" aria-label="Token 用量"><span>未缓存输入 {formatCount(usage.input)}</span><span>缓存命中 {formatCount(usage.cache_read)}</span><span>输出（含推理） {formatCount(usage.output)}</span><span>推理 {formatCount(usage.reasoning)}</span><span>总计 {formatCount(usage.total)}</span>{(usage.compaction_calls ?? 0) > 0 && <span>含上下文压缩 {usage.compaction_calls} 次 · {usage.compaction_total == null ? '未完整报告' : formatCount(usage.compaction_total)} token</span>}{usage.reported_total != null && <span>已报告 {formatCount(usage.reported_total)}{usage.missing_usage_rounds ? `，${usage.missing_usage_rounds} 轮未报告` : ''}</span>}</div>
}

export function ToolReceiptRow({ tool }: { tool: ToolReceipt }) { return <details className="tool-row"><summary><span className={`tool-status tool-${tool.status}`}>{toolStatusLabel(tool.status)}</span><strong>{tool.name}</strong><small>{tool.round ? `第 ${tool.round} 轮 · ` : ''}{formatDuration(tool.elapsed_seconds)}</small></summary><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div>{tool.action_id && <span className="receipt-link">关联审批：{tool.action_id}</span>}</details> }
