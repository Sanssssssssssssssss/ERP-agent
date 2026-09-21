import { CircleAlert,CircleCheck } from 'lucide-react'
import { useEffect,useState } from 'react'
import { EmptyState,MessageText,StatusBadge } from '../../components/common'
import { roundStatusLabel,runDisplayLabel,toolStatusLabel } from '../../presentation'
import {
BusinessDetailProjection,
Check,
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

export function TracePage({ trace, runs, readback, selectedRunId, loading, target, onRunSelect }: { trace: TraceBundle | null; runs: Run[]; readback?: BusinessDetailProjection['business']['readback']; selectedRunId: string; loading: boolean; target: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null; onRunSelect: (id: string) => void }) {
  const toolsById = new Map((trace?.tools ?? []).map((tool) => [tool.id, tool]))
  const [selectedNode, setSelectedNode] = useState('run')
  const targetTool = target?.toolId ? toolsById.get(target.toolId) : undefined
  const missingTargetTool = target?.toolId && !targetTool
  useEffect(() => {
    if (target?.toolId) setSelectedNode(`tool:${target.toolId}`)
    else if (target?.actionId) setSelectedNode(`action:${target.actionId}`)
    else if (target?.kind === 'readback') setSelectedNode(`readback:${target.runId || 'unknown'}`)
    else setSelectedNode('run')
  }, [target?.actionId, target?.kind, target?.runId, target?.toolId, trace?.run?.id])
  const selectedTool = selectedNode.startsWith('tool:') ? toolsById.get(selectedNode.slice(5)) : undefined
  const selectedRound = selectedNode.startsWith('round:') ? trace?.rounds.find((round) => String(round.index) === selectedNode.slice(6)) : undefined
  const selectedAction = selectedNode.startsWith('action:') ? (trace?.tools ?? []).slice().reverse().find((tool) => tool.action_id === selectedNode.slice(7)) : undefined
  const missingTargetAction = Boolean(target?.actionId && selectedNode === `action:${target.actionId}` && !selectedAction)
  const readbackMatches = target?.kind === 'readback' && Boolean(readback && target.runId && readback.latest_run_id && target.runId === readback.latest_run_id)
  const activeTools = (trace?.events ?? []).map((event) => ({
    id: String(event.tool_id || event.id || ''),
    name: String(event.tool_name || event.name || event.tool_id || '活动工具'),
    status: String(event.status || event.event || '执行中')
  })).filter((tool) => tool.id && !toolsById.has(tool.id))
  return (
    <div className="trace-page">
      <div className="trace-toolbar">
        <label>运行<select value={selectedRunId} onChange={(event) => onRunSelect(event.target.value)}><option value="">选择运行</option>{runs.map((run) => <option key={run.id} value={run.id}>{run.id} · {labelFor(runStatusLabel, run.status)}</option>)}</select></label>
        {trace?.run && <div className="trace-metrics"><span>{formatCount(trace.run.model_rounds)} 轮</span><span>{formatCount(trace.run.tool_count)} 工具</span><UsageBreakdown usage={trace.run.usage} /></div>}
      </div>
      {loading && <div className="loading-line">正在读取运行详情…</div>}
      {!loading && !trace && <EmptyState title="选择一次运行" detail="运行详情只读取已持久化的回执，不会重新执行模型。" />}
      {trace && <div className="trace-split">
        <nav className="trace-tree" aria-label="运行、轮次与工具"><button className={`trace-node ${selectedNode === 'run' ? 'active' : ''}`} type="button" onClick={() => setSelectedNode('run')}><strong>运行</strong><span>{trace.run?.id || '运行未知'}</span></button>{target?.kind === 'readback' && <button className={`trace-node ${selectedNode.startsWith('readback:') ? 'active' : ''}`} type="button" onClick={() => setSelectedNode(`readback:${target.runId || 'unknown'}`)}><strong>独立回读快照</strong><span>{readbackMatches ? '已返回' : '当前运行无此快照'}</span></button>}{trace.rounds.map((round) => <div key={round.index} className="trace-tree-round"><button className={`trace-node ${selectedNode === `round:${round.index}` ? 'active' : ''}`} type="button" onClick={() => setSelectedNode(`round:${round.index}`)}><strong>第 {round.index} 轮</strong><span>{roundStatusLabel(round.status)}</span></button>{round.tool_ids.map((id) => { const tool = toolsById.get(id); return <button className={`trace-node trace-tool-node ${selectedNode === `tool:${id}` ? 'active' : ''}`} type="button" key={id} onClick={() => setSelectedNode(`tool:${id}`)}><strong>{tool?.name || id}</strong><span>{tool ? toolStatusLabel(tool.status) : '回执未知'}</span></button> })}</div>)}{activeTools.map((tool) => <button className={`trace-node trace-tool-node ${selectedNode === `tool:${tool.id}` ? 'active' : ''}`} type="button" key={`active:${tool.id}`} onClick={() => setSelectedNode(`tool:${tool.id}`)}><strong>{tool.name}</strong><span>活动中 · 回执尚未到达</span></button>)}{missingTargetTool && <button className={`trace-node trace-tool-node unavailable ${selectedNode === `tool:${target?.toolId}` ? 'active' : ''}`} type="button" onClick={() => setSelectedNode(`tool:${target?.toolId}`)}><strong>{target?.toolId}</strong><span>不可用 · 尚未返回回执</span></button>}{missingTargetAction && <button className={`trace-node trace-tool-node unavailable ${selectedNode === `action:${target?.actionId}` ? 'active' : ''}`} type="button" onClick={() => setSelectedNode(`action:${target?.actionId}`)}><strong>{target?.actionId}</strong><span>回执不可用 · 尚未返回</span></button>}</nav>
       <section className="trace-detail-panel" aria-live="polite"><div className="section-heading"><div><span className="eyebrow">运行详情</span><h3>{selectedTool ? selectedTool.name : selectedAction ? selectedAction.name : selectedRound ? `第 ${selectedRound.index} 轮` : selectedNode.startsWith('readback:') ? '独立回读快照' : missingTargetTool ? '工具回执不可用' : missingTargetAction ? '动作回执不可用' : '运行总览'}</h3></div><span>{trace.run?.id || '运行未知'}</span></div>{selectedTool ? <ToolDetail tool={selectedTool} /> : selectedAction ? <ToolDetail tool={selectedAction} /> : selectedRound ? <RoundDetail round={selectedRound} /> : selectedNode.startsWith('readback:') ? <ReadbackDetail readback={readbackMatches ? readback : undefined} /> : missingTargetTool ? <div className="empty-state"><strong>工具回执不可用</strong><p>工具 {target?.toolId} 尚未出现在当前运行的持久化回执中。</p></div> : missingTargetAction ? <div className="empty-state"><strong>动作回执不可用</strong><p>动作 {target?.actionId} 尚未出现在当前运行的持久化工具回执中。</p></div> : <RunDetail trace={trace} />}</section>
      </div>}
    </div>
  )
}

export function RunDetail({ trace }: { trace: TraceBundle }) { if (!trace.run) return <EmptyState title="运行状态未知" detail="主机尚未返回运行摘要。" />; return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{runDisplayLabel(trace.run.status)}</strong></div><div><span>轮次</span><strong>{formatCount(trace.run.model_rounds)}</strong></div><div><span>工具</span><strong>{formatCount(trace.run.tool_count)}</strong></div><div><span>耗时</span><strong>{formatDuration(trace.run.elapsed_seconds)}</strong></div></div><UsageBreakdown usage={trace.run.usage} />{trace.run.error && <div className="notice red"><CircleAlert size={15} /><span>{trace.run.error_detail || trace.run.error}</span></div>}</div> }

export function RoundDetail({ round }: { round: Round }) { return <div className="trace-detail-content"><MessageText text={round.text || '没有公开摘要；隐藏思维不会在工作台展示。'} /><UsageBreakdown usage={round.usage} /><div className="trace-label">状态</div><p>{roundStatusLabel(round.status)} · {formatDuration(round.elapsed_seconds)}</p></div> }

export function ToolDetail({ tool }: { tool: ToolReceipt }) { return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{toolStatusLabel(tool.status)}</strong></div><div><span>轮次</span><strong>{tool.round == null ? '未知' : `第 ${tool.round} 轮`}</strong></div><div><span>耗时</span><strong>{formatDuration(tool.elapsed_seconds)}</strong></div><div><span>审批</span><strong>{tool.action_id || '未知'}</strong></div></div><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div></div> }

export function ReadbackDetail({ readback }: { readback?: BusinessDetailProjection['business']['readback'] }) { return <div className="trace-detail-content"><div className="notice blue"><CircleCheck size={15} /><span>这是独立 Odoo 回读快照，时间与原始工具调用分开记录。</span></div>{readback ? <><div className="fact-table"><div><span>快照时间</span><strong>{formatInstant(readback.observed_at)}</strong></div><div><span>来源</span><strong>独立 Odoo 回读</strong></div><div><span>核验项</span><strong>{readback.checks?.length ?? 0}</strong></div><div><span>状态</span><strong>{readback.stale ? '可能已过期' : '已返回'}</strong></div></div><details className="resource-fields"><summary>查看回读核验</summary><pre>{jsonText(readback.checks ?? [])}</pre></details></> : <div className="empty-state"><strong>快照详情不可用</strong><p>当前运行没有匹配的独立回读快照。</p></div>}</div> }

export function UsageBreakdown({ usage }: { usage?: Run['usage'] }) {
  if (!usage) return <div className="usage-breakdown"><span>用量未知</span></div>
  return <div className="usage-breakdown" aria-label="Token 用量"><span>未缓存输入 {formatCount(usage.input)}</span><span>缓存命中 {formatCount(usage.cache_read)}</span><span>输出（含推理） {formatCount(usage.output)}</span><span>推理 {formatCount(usage.reasoning)}</span><span>总计 {formatCount(usage.total)}</span>{(usage.compaction_calls ?? 0) > 0 && <span>含上下文压缩 {usage.compaction_calls} 次 · {usage.compaction_total == null ? '未完整报告' : formatCount(usage.compaction_total)} token</span>}{usage.reported_total != null && <span>已报告 {formatCount(usage.reported_total)}{usage.missing_usage_rounds ? `，${usage.missing_usage_rounds} 轮未报告` : ''}</span>}</div>
}

export function ToolReceiptRow({ tool }: { tool: ToolReceipt }) { return <details className="tool-row"><summary><span className={`tool-status tool-${tool.status}`}>{toolStatusLabel(tool.status)}</span><strong>{tool.name}</strong><small>{tool.round ? `第 ${tool.round} 轮 · ` : ''}{formatDuration(tool.elapsed_seconds)}</small></summary><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div>{tool.action_id && <span className="receipt-link">关联审批：{tool.action_id}</span>}</details> }
