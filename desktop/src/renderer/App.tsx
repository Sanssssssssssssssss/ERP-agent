import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react'
import type { WorkbenchMethod } from '../shared/protocol'
import {
  Approval,
  Business,
  BusinessDetail,
  BusinessTab,
  Check,
  Document,
  HostEvent,
  Health,
  Message,
  Run,
  SessionDetail,
  SessionSummary,
  Settings,
  ToolReceipt,
  TraceBundle,
  businessStatusLabel,
  formatCount,
  formatDuration,
  formatExpiry,
  formatInstant,
  isExpired,
  jsonText,
  labelFor,
  runStatusLabel
} from './protocol'

type ConnectionState = 'checking' | 'connected' | 'disconnected' | 'crashed' | 'protocol_error'

const tabs: Array<{ id: BusinessTab; label: string }> = [
  { id: 'overview', label: '概览' },
  { id: 'documents', label: '单据' },
  { id: 'approvals', label: '审批' },
  { id: 'verification', label: '核验' },
  { id: 'trace', label: 'Trace' }
]

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [selectedSessionId, setSelectedSessionId] = useState('')
  const [selectedBusinessId, setSelectedBusinessId] = useState('')
  const [businessDetail, setBusinessDetail] = useState<BusinessDetail | null>(null)
  const [trace, setTrace] = useState<TraceBundle | null>(null)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [tab, setTab] = useState<BusinessTab>('overview')
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [businessLoading, setBusinessLoading] = useState(false)
  const [traceLoading, setTraceLoading] = useState(false)
  const [connection, setConnection] = useState<ConnectionState>('checking')
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState('')
  const [liveText, setLiveText] = useState('')
  const [settings, setSettings] = useState<Settings | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({})
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [renamingId, setRenamingId] = useState('')
  const [messageBusinessId, setMessageBusinessId] = useState('')
  const [sessionQuery, setSessionQuery] = useState('')
  const [businessWidth, setBusinessWidth] = useState(500)
  const [traceRefreshToken, setTraceRefreshToken] = useState(0)
  const settingsButtonRef = useRef<HTMLButtonElement>(null)
  const sessionIdRef = useRef('')
  const businessIdRef = useRef('')
  const sessionRequestRef = useRef(0)
  const businessRequestRef = useRef(0)
  const traceRequestRef = useRef(0)
  const traceRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const call = useCallback(async <T,>(method: string, params?: Record<string, unknown>) => {
    if (!window.workbench) throw new Error('桌面主机桥未连接')
    return window.workbench.call(method as WorkbenchMethod, params) as Promise<T>
  }, [])

  const loadSessions = useCallback(async (selectFirst = true) => {
    const result = await call<SessionSummary[]>('list_sessions')
    const active = result.filter((item) => !item.archived)
    setSessions(active)
    if (selectFirst && !selectedSessionId && active[0]) setSelectedSessionId(active[0].id)
    return active
  }, [call, selectedSessionId])

  const loadSession = useCallback(async (sessionId: string) => {
    if (!sessionId) return
    const requestId = ++sessionRequestRef.current
    const result = await call<SessionDetail>('get_session', { session_id: sessionId })
    if (requestId !== sessionRequestRef.current || sessionIdRef.current !== sessionId) return
    setSession(result)
    sessionIdRef.current = sessionId
    setSelectedBusinessId((current) => {
      if (current && result.businesses.some((item) => item.id === current)) return current
      return result.businesses[0]?.id ?? ''
    })
    setMessageBusinessId((current) => {
      if (current === '__new__' || (current && result.businesses.some((item) => item.id === current))) return current
      return result.businesses[0]?.id ?? '__new__'
    })
    setError('')
  }, [call])

  const loadBusiness = useCallback(async (sessionId: string, businessId: string) => {
    const requestId = ++businessRequestRef.current
    if (!sessionId || !businessId) {
      setBusinessDetail(null)
      return
    }
    setBusinessLoading(true)
    try {
      const result = await call<BusinessDetail>('get_business', { session_id: sessionId, business_id: businessId })
      if (requestId !== businessRequestRef.current || sessionIdRef.current !== sessionId || businessIdRef.current !== businessId) return
      setBusinessDetail(result)
      businessIdRef.current = businessId
      const currentRun = result.runs.find((run) => run.id === result.business.active_run_id) ?? result.runs[0]
      setSelectedRunId((current) => result.runs.some((run) => run.id === current) ? current : currentRun?.id || '')
      setError('')
    } catch (reason) {
      if (requestId === businessRequestRef.current && sessionIdRef.current === sessionId && businessIdRef.current === businessId) setError(messageForError(reason))
    } finally {
      if (requestId === businessRequestRef.current && sessionIdRef.current === sessionId && businessIdRef.current === businessId) setBusinessLoading(false)
    }
  }, [call])

  useEffect(() => {
    let mounted = true
    void (async () => {
      try {
        const result = await call<Health>('health')
        if (mounted) {
          setHealth(result)
          setConnection(result.host_ready ? 'connected' : 'disconnected')
          if (!result.host_ready) setError('本地 host 尚未就绪')
        }
        await loadSessions()
      } catch (reason) {
        if (mounted) {
          setConnection('disconnected')
          setError(messageForError(reason))
        }
      }
    })()
    return () => { mounted = false }
  }, [call, loadSessions])

  useEffect(() => {
    if (!selectedSessionId) return
    void loadSession(selectedSessionId).catch((reason) => setError(messageForError(reason)))
  }, [loadSession, selectedSessionId])

  useEffect(() => {
    sessionIdRef.current = selectedSessionId
    businessIdRef.current = selectedBusinessId
    if (selectedSessionId && selectedBusinessId) void loadBusiness(selectedSessionId, selectedBusinessId)
    else {
      setBusinessDetail(null)
      setBusinessLoading(false)
    }
  }, [loadBusiness, selectedBusinessId, selectedSessionId])

  useEffect(() => {
    if (!selectedSessionId || tab !== 'trace' || !selectedBusinessId || !selectedRunId) {
      traceRequestRef.current += 1
      setTrace(null)
      setTraceLoading(false)
      return
    }
    const requestId = ++traceRequestRef.current
    setTraceLoading(true)
    void call<TraceBundle>('get_trace', {
      session_id: selectedSessionId,
      business_id: selectedBusinessId,
      run_id: selectedRunId
    }).then((result) => {
      if (requestId === traceRequestRef.current) setTrace(result)
    }).catch((reason) => {
      if (requestId === traceRequestRef.current) setError(messageForError(reason))
    }).finally(() => {
      if (requestId === traceRequestRef.current) setTraceLoading(false)
    })
  }, [call, selectedBusinessId, selectedRunId, selectedSessionId, tab, traceRefreshToken])

  const reloadCurrent = useCallback(async () => {
    const sessionId = selectedSessionId
    const businessId = selectedBusinessId
    if (!sessionId || sessionIdRef.current !== sessionId) return
    await loadSession(sessionId)
    if (sessionIdRef.current !== sessionId || businessIdRef.current !== businessId) return
    if (businessId) await loadBusiness(sessionId, businessId)
  }, [loadBusiness, loadSession, selectedBusinessId, selectedSessionId])

  useEffect(() => {
    if (!window.workbench) return
    const handleEvent = (event: HostEvent) => {
      const data = event.data || {}
      const eventSession = String(data.session_id || '')
      const eventBusiness = String(data.business_id || '')
      if (eventSession && eventSession !== sessionIdRef.current) {
        void loadSessions(false).catch(() => undefined)
        return
      }
      if (event.event === 'host_status') {
        const status = String(data.status || '')
        if (status === 'ready') {
          setConnection('connected')
          setError('')
        } else if (status === 'crashed') {
          setConnection('crashed')
          setError(`本地 host 已崩溃${data.code ? `（${String(data.code)}）` : ''}`)
        }
        return
      }
      if (event.event === 'host_protocol_error') {
        setConnection('protocol_error')
        setError(String(data.message || '本地 host 协议错误'))
        return
      }
      if (event.event === 'message_delta') {
        setLiveText((current) => `${current}${String(data.text || '')}`)
        return
      }
      if (event.event === 'run_trace' || event.event === 'trace') {
        const eventRun = String(data.run_id || '')
        if (tab === 'trace' && eventBusiness === selectedBusinessId && (!eventRun || eventRun === selectedRunId)) {
          if (traceRefreshTimerRef.current) clearTimeout(traceRefreshTimerRef.current)
          traceRefreshTimerRef.current = setTimeout(() => setTraceRefreshToken((value) => value + 1), 40)
        }
        return
      }
      if (event.event === 'changed') {
        const changeStatus = String(data.run_status || data.status || '')
        const focusHint = String(data.focus || data.kind || data.change || data.reason || '')
        if (eventBusiness && ['business_created', 'created', 'run_started', 'started'].includes(focusHint)) {
          businessIdRef.current = eventBusiness
          businessRequestRef.current += 1
          setSelectedBusinessId(eventBusiness)
          setMessageBusinessId(eventBusiness)
        }
        const eventRun = String(data.run_id || '')
        if (tab === 'trace' && eventBusiness === selectedBusinessId && (!eventRun || eventRun === selectedRunId)) {
          if (traceRefreshTimerRef.current) clearTimeout(traceRefreshTimerRef.current)
          traceRefreshTimerRef.current = setTimeout(() => setTraceRefreshToken((value) => value + 1), 40)
        }
        void reloadCurrent().then(() => {
          if (['completed', 'failed', 'cancelled', 'interrupted'].includes(changeStatus)) setLiveText('')
        }).catch((reason) => setError(messageForError(reason)))
      }
    }
    return window.workbench.subscribe(handleEvent)
  }, [loadSessions, reloadCurrent, selectedBusinessId, selectedRunId, selectedSessionId, tab])

  const activeBusiness = useMemo(
    () => session?.businesses.find((item) => item.id === selectedBusinessId) ?? businessDetail?.business ?? null,
    [businessDetail?.business, selectedBusinessId, session?.businesses]
  )

  const chooseSession = (id: string) => {
    if (sessionIdRef.current === id && selectedSessionId === id) return
    sessionIdRef.current = id
    businessIdRef.current = ''
    sessionRequestRef.current += 1
    businessRequestRef.current += 1
    traceRequestRef.current += 1
    setSelectedSessionId(id)
    setSelectedBusinessId('')
    setBusinessDetail(null)
    setBusinessLoading(false)
    setLoading(false)
    setTrace(null)
    setSelectedRunId('')
    setMessageBusinessId('')
    setTab('overview')
    setLiveText('')
  }

  const chooseBusiness = (id: string) => {
    if (businessIdRef.current === id && selectedBusinessId === id) return
    businessIdRef.current = id
    businessRequestRef.current += 1
    traceRequestRef.current += 1
    setSelectedBusinessId(id)
    setMessageBusinessId(id)
    setBusinessDetail(null)
    setTrace(null)
    setBusinessLoading(true)
    setLoading(false)
    setTab('overview')
    setSelectedRunId('')
  }

  const createSession = async () => {
    setLoading(true)
    try {
      const created = await call<SessionSummary>('create_session')
      await loadSessions(false)
      chooseSession(created.id)
    } catch (reason) {
      setError(messageForError(reason))
    } finally {
      setLoading(false)
    }
  }

  const archiveSession = async (id: string) => {
    if (!window.confirm('归档这个会话？归档不会删除业务记录。')) return
    const requestSessionId = selectedSessionId
    try {
      await call('archive_session', { session_id: id })
      const remaining = await loadSessions(false)
      if (sessionIdRef.current === requestSessionId && requestSessionId === id) chooseSession(remaining[0]?.id ?? '')
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId) setError(messageForError(reason))
    }
  }

  const renameSession = async (id: string, title: string) => {
    if (!title.trim()) return
    try {
      await call('rename_session', { session_id: id, title: title.trim() })
      await loadSessions(false)
      if (id === selectedSessionId) await loadSession(id)
      setRenamingId('')
    } catch (reason) {
      setError(messageForError(reason))
    }
  }

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || !selectedSessionId || loading) return
    setLoading(true)
    setDraft('')
    setLiveText('')
    const requestSessionId = selectedSessionId
    const targetBusinessId = messageBusinessId === '__new__' ? '' : messageBusinessId || selectedBusinessId
    try {
      await call('send_message', {
        session_id: requestSessionId,
        text,
        ...(targetBusinessId ? { business_id: targetBusinessId } : {})
      })
      if (sessionIdRef.current === requestSessionId) await loadSession(requestSessionId)
    } catch (reason) {
      setDraft(text)
      if (sessionIdRef.current === requestSessionId) setError(messageForError(reason))
    } finally {
      if (sessionIdRef.current === requestSessionId) setLoading(false)
    }
  }

  const confirmProposal = async (proposal: ProposalLike, confirmed: boolean) => {
    if (!selectedSessionId) return
    const requestSessionId = selectedSessionId
    setLoading(true)
    try {
      const business = await call<Business | null>('confirm_business', {
        session_id: requestSessionId,
        proposal_id: proposal.id,
        confirmed
      })
      if (sessionIdRef.current !== requestSessionId) return
      await loadSession(requestSessionId)
      if (business && sessionIdRef.current === requestSessionId) {
        chooseBusiness(business.id)
      }
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId) setError(messageForError(reason))
    } finally {
      if (sessionIdRef.current === requestSessionId) setLoading(false)
    }
  }

  const startRun = async () => {
    if (!selectedSessionId || !selectedBusinessId) return
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    setLoading(true)
    try {
      const run = await call<Run>('start_run', { session_id: requestSessionId, business_id: requestBusinessId })
      if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      setSelectedRunId(run.id)
      await reloadCurrent()
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    } finally {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setLoading(false)
    }
  }

  const cancelRun = async (run: Run) => {
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    try {
      await call('cancel_run', { session_id: requestSessionId, business_id: requestBusinessId, run_id: run.id })
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) await reloadCurrent()
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    }
  }

  const decideApproval = async (approval: Approval, decision: 'approve' | 'reject') => {
    const requestSessionId = selectedSessionId
    const requestBusinessId = approval.business_id
    setLoading(true)
    try {
      await call('decide_approval', {
        session_id: requestSessionId,
        business_id: requestBusinessId,
        run_id: approval.run_id,
        action_id: approval.action_id,
        decision
      })
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) await reloadCurrent()
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    } finally {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setLoading(false)
    }
  }

  const refreshBusiness = async () => {
    if (!selectedSessionId || !selectedBusinessId) return
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    const requestId = ++businessRequestRef.current
    setBusinessLoading(true)
    try {
      const result = await call<BusinessDetail>('refresh_business', { session_id: requestSessionId, business_id: requestBusinessId })
      if (requestId !== businessRequestRef.current || sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      setBusinessDetail(result)
      if (sessionIdRef.current === requestSessionId) await loadSession(requestSessionId)
    } catch (reason) {
      if (requestId === businessRequestRef.current && sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    } finally {
      if (requestId === businessRequestRef.current && sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setBusinessLoading(false)
    }
  }

  const openSettings = async () => {
    setSettingsOpen(true)
    try {
      const current = await call<Settings>('get_settings')
      setSettings(current)
      setSettingsDraft({
        model: current.model,
        base_url: current.base_url,
        odoo_url: current.odoo_url,
        odoo_db: current.odoo_db,
        odoo_username: current.odoo_username,
        model_key: '',
        odoo_key: ''
      })
    } catch (reason) {
      setError(messageForError(reason))
    }
  }

  const closeSettings = useCallback(() => {
    setSettingsOpen(false)
    requestAnimationFrame(() => settingsButtonRef.current?.focus())
  }, [])

  const saveSettings = async () => {
    setSettingsSaving(true)
    try {
      const updated = await call<Settings>('save_settings', settingsDraft)
      setSettings(updated)
      setSettingsDraft((current) => ({ ...current, model_key: '', odoo_key: '' }))
      const currentHealth = await call<Health>('health')
      setHealth(currentHealth)
      setConnection(currentHealth.host_ready ? 'connected' : 'disconnected')
      setNotice('设置已保存，连接状态已刷新。')
      closeSettings()
    } catch (reason) {
      setError(messageForError(reason))
    } finally {
      setSettingsSaving(false)
    }
  }

  const retryHealth = async () => {
    setConnection('checking')
    try {
      const currentHealth = await call<Health>('health')
      setHealth(currentHealth)
      setConnection(currentHealth.host_ready ? 'connected' : 'disconnected')
      if (currentHealth.host_ready) setError('')
      else setError('本地 host 尚未就绪')
    } catch (reason) {
      setConnection('disconnected')
      setError(messageForError(reason))
    }
  }

  const resizeBusiness = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const move = (moveEvent: PointerEvent) => {
      const width = Math.max(360, Math.min(700, window.innerWidth - moveEvent.clientX))
      setBusinessWidth(width)
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop, { once: true })
  }

  const visibleMessages = session?.messages ?? []
  const pendingProposal = visibleMessages.find((message) => message.proposal?.status === 'pending')?.proposal

  return (
    <div className="app-shell">
      <header className="window-bar">
        <div className="brand-lockup"><span className="brand-mark">O</span><span>业务工作台</span><small>Odoo native harness</small></div>
        <div className="window-bar-state"><span className={`connection-dot ${connection}`} />{connectionLabel(connection)}<span className="health-separator">·</span><span className={`odoo-health odoo-${health?.odoo_status || 'unknown'}`}>Odoo {healthLabel(health?.odoo_status)}</span></div>
        <button ref={settingsButtonRef} className="settings-button" onClick={() => void openSettings()}>连接设置</button>
        <div className="window-actions">
          <button aria-label="最小化" onClick={() => void window.workbench?.windowControl('minimize')}>−</button>
          <button aria-label="最大化" onClick={() => void window.workbench?.windowControl('maximize')}>□</button>
          <button aria-label="关闭" className="close" onClick={() => void window.workbench?.windowControl('close')}>×</button>
        </div>
      </header>

      {settingsOpen && <SettingsDialog settings={settings} draft={settingsDraft} saving={settingsSaving} onChange={(key, value) => setSettingsDraft((current) => ({ ...current, [key]: value }))} onClose={closeSettings} onSave={() => void saveSettings()} />}

      {error && <div className="global-alert" role="alert"><span>{error}</span>{connection !== 'connected' && <button onClick={() => void retryHealth()}>重试连接</button>}<button onClick={() => setError('')}>关闭</button></div>}
      {notice && <div className="global-notice" role="status"><span>{notice}</span><button onClick={() => setNotice('')}>关闭</button></div>}

      <main className="workspace-grid" style={{ '--business-width': `${businessWidth}px` } as CSSProperties}>
        <SessionRail
          sessions={sessions}
          selectedId={selectedSessionId}
          loading={loading}
          renamingId={renamingId}
          onSelect={chooseSession}
          onCreate={() => void createSession()}
          onArchive={(id) => void archiveSession(id)}
          onRenameStart={setRenamingId}
          onRename={renameSession}
          query={sessionQuery}
          onQueryChange={setSessionQuery}
        />
        <ConversationPane
          session={session}
          draft={draft}
          liveText={liveText}
          loading={loading}
          pendingProposal={pendingProposal}
          businesses={session?.businesses ?? []}
          messageBusinessId={messageBusinessId || selectedBusinessId || '__new__'}
          onMessageBusinessChange={setMessageBusinessId}
          onDraftChange={setDraft}
          onSubmit={sendMessage}
          onProposal={(proposal, confirmed) => void confirmProposal(proposal, confirmed)}
        />
        <div className="workspace-divider" role="separator" tabIndex={0} aria-label="调整业务工作区宽度" onPointerDown={resizeBusiness} onKeyDown={(event) => { if (event.key === 'ArrowLeft') setBusinessWidth((width) => Math.min(700, width + 24)); if (event.key === 'ArrowRight') setBusinessWidth((width) => Math.max(360, width - 24)) }} />
        <BusinessWorkspace
          session={session}
          activeBusiness={activeBusiness}
          detail={businessDetail}
          tab={tab}
          trace={trace}
          traceLoading={traceLoading}
          businessLoading={businessLoading}
          loading={loading}
          selectedRunId={selectedRunId}
          onBusinessSelect={chooseBusiness}
          onTabChange={setTab}
          onRunSelect={setSelectedRunId}
          onRefresh={() => void refreshBusiness()}
          onStart={() => void startRun()}
          onCancel={(run) => void cancelRun(run)}
          onApproval={(approval, decision) => void decideApproval(approval, decision)}
        />
      </main>
    </div>
  )
}

type ProposalLike = { id: string; title: string; goal: string; type: string }

function SessionRail({
  sessions,
  selectedId,
  loading,
  renamingId,
  onSelect,
  onCreate,
  onArchive,
  onRenameStart,
  onRename,
  query,
  onQueryChange
}: {
  sessions: SessionSummary[]
  selectedId: string
  loading: boolean
  renamingId: string
  onSelect: (id: string) => void
  onCreate: () => void
  onArchive: (id: string) => void
  onRenameStart: (id: string) => void
  onRename: (id: string, title: string) => void
  query: string
  onQueryChange: (value: string) => void
}) {
  const visibleSessions = sessions.filter((item) => `${item.title} ${item.id}`.toLowerCase().includes(query.trim().toLowerCase()))
  return (
    <aside className="session-rail">
      <div className="rail-heading"><div><span className="eyebrow">Workspace</span><h1>会话</h1></div><button className="new-button" onClick={onCreate} disabled={loading}>新建</button></div>
      <label className="session-search"><span className="sr-only">搜索会话</span><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>
      <div className="rail-summary"><span>{query ? `${visibleSessions.length} / ${sessions.length} 个会话` : `${sessions.length} 个活跃会话`}</span><span className="quiet-rule" /></div>
      <div className="session-list">
        {sessions.length === 0 && <EmptyState title="还没有会话" detail="创建会话后，从一句业务意图开始。" />}
        {sessions.length > 0 && visibleSessions.length === 0 && <EmptyState title="没有匹配会话" detail="换一个名称或会话 ID 试试。" />}
        {visibleSessions.map((item) => (
          <div key={item.id} className={`session-row ${item.id === selectedId ? 'active' : ''}`}>
            <button className="session-select" onClick={() => onSelect(item.id)}>
              <span className={`session-status-dot status-${item.status}`} />
              <span className="session-copy"><strong>{item.title || '未命名会话'}</strong><small>{formatInstant(item.updated_at)}</small></span>
            </button>
            {item.id === selectedId && (
              <div className="session-row-actions">
                {renamingId === item.id ? (
                  <input autoFocus defaultValue={item.title} aria-label="会话名称" onKeyDown={(event) => { if (event.key === 'Enter') onRename(item.id, event.currentTarget.value); if (event.key === 'Escape') onRenameStart('') }} />
                ) : <button title="重命名" onClick={() => onRenameStart(item.id)}>改名</button>}
                <button title="归档" onClick={() => onArchive(item.id)}>归档</button>
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="rail-footer"><span className="rail-footer-dot" />本地工作区</div>
    </aside>
  )
}

function ConversationPane({ session, draft, liveText, loading, pendingProposal, businesses, messageBusinessId, onMessageBusinessChange, onDraftChange, onSubmit, onProposal }: {
  session: SessionDetail | null
  draft: string
  liveText: string
  loading: boolean
  pendingProposal?: ProposalLike
  businesses: Business[]
  messageBusinessId: string
  onMessageBusinessChange: (id: string) => void
  onDraftChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
  onProposal: (proposal: ProposalLike, confirmed: boolean) => void
}) {
  const messages = session?.messages ?? []
  return (
    <section className="conversation-pane">
      <header className="conversation-header">
        <div><span className="eyebrow">Session</span><h2>{session?.session.title || '选择一个会话'}</h2><p>和 Agent 讨论业务目标，确认后再进入对应工作区。</p></div>
        <span className="session-id">{session?.session.id || '—'}</span>
      </header>
      <div className="conversation-scroll">
        {!session && <EmptyState title="选择一个会话" detail="左侧会话列表会显示已持久化的工作。" />}
        {session && messages.length === 0 && <EmptyState title="从业务意图开始" detail="例如：帮我处理一张销售发票。" />}
        {messages.map((message) => <MessageRow key={message.id} message={message} />)}
        {liveText && <article className="message assistant live-message"><span className="avatar agent-avatar">A</span><div><div className="message-meta"><strong>Agent</strong><span>实时回复</span></div><p>{liveText}</p></div></article>}
        {pendingProposal && <ProposalCard proposal={pendingProposal} disabled={loading} onDecision={onProposal} />}
      </div>
      <form className="composer" onSubmit={onSubmit}>
        <textarea value={draft} onChange={(event) => onDraftChange(event.target.value)} disabled={!session || loading} placeholder={session ? '告诉 Agent 你要处理的业务…' : '先选择或创建一个会话'} aria-label="会话消息" />
        <div className="composer-footer">
          <div className="composer-context"><label htmlFor="message-business-target">发送到</label><select id="message-business-target" value={messageBusinessId} onChange={(event) => onMessageBusinessChange(event.target.value)} disabled={!session || loading}><option value="__new__">新业务意图（创建工作区）</option>{businesses.map((business) => <option key={business.id} value={business.id}>{business.title || '未命名业务'} · {labelFor(businessStatusLabel, business.status)}</option>)}</select><span>发送后需在右侧明确开始或继续执行。</span></div>
          <button type="submit" disabled={!session || loading || !draft.trim()}>{loading ? '处理中…' : '发送'}</button>
        </div>
      </form>
    </section>
  )
}

function MessageRow({ message }: { message: Message }) {
  const role = message.role === 'user' ? 'user' : message.role === 'system' ? 'system' : 'assistant'
  return <article className={`message ${role}`}><span className={`avatar ${role === 'user' ? 'user-avatar' : role === 'system' ? 'system-avatar' : 'agent-avatar'}`}>{role === 'user' ? '你' : role === 'system' ? '·' : 'A'}</span><div><div className="message-meta"><strong>{role === 'user' ? '你' : role === 'system' ? '系统' : 'Agent'}</strong><span>{formatInstant(message.created_at)}</span></div><p>{message.text}</p></div></article>
}

function ProposalCard({ proposal, disabled, onDecision }: { proposal: ProposalLike; disabled: boolean; onDecision: (proposal: ProposalLike, confirmed: boolean) => void }) {
  return <section className="proposal-card"><div className="proposal-kicker">发现新的业务意图</div><h3>{proposal.title}</h3><p>{proposal.goal}</p><div className="proposal-actions"><button className="secondary-button" disabled={disabled} onClick={() => onDecision(proposal, false)}>暂不创建</button><button className="primary-button" disabled={disabled} onClick={() => onDecision(proposal, true)}>创建业务工作区</button></div></section>
}

function BusinessWorkspace({ session, activeBusiness, detail, tab, trace, traceLoading, businessLoading, loading, selectedRunId, onBusinessSelect, onTabChange, onRunSelect, onRefresh, onStart, onCancel, onApproval }: {
  session: SessionDetail | null
  activeBusiness: Business | null
  detail: BusinessDetail | null
  tab: BusinessTab
  trace: TraceBundle | null
  traceLoading: boolean
  businessLoading: boolean
  loading: boolean
  selectedRunId: string
  onBusinessSelect: (id: string) => void
  onTabChange: (tab: BusinessTab) => void
  onRunSelect: (id: string) => void
  onRefresh: () => void
  onStart: () => void
  onCancel: (run: Run) => void
  onApproval: (approval: Approval, decision: 'approve' | 'reject') => void
}) {
  const businessList = session?.businesses ?? []
  const activeRun = detail?.runs.find((run) => run.id === selectedRunId) ?? detail?.runs.find((run) => ['running', 'awaiting_approval', 'cancel_requested'].includes(run.status))
  return (
    <aside className="business-workspace">
      <div className="business-tabs-bar">
        <div className="business-tabs-heading"><span className="eyebrow">Business workspace</span><strong>{businessList.length ? `${businessList.length} 个业务` : '业务页'}</strong></div>
        <div className="business-tabs" role="tablist" aria-label="业务工作区">
          {businessList.map((business) => <button role="tab" aria-selected={business.id === activeBusiness?.id} key={business.id} className={business.id === activeBusiness?.id ? 'active' : ''} onClick={() => onBusinessSelect(business.id)}>{business.title || '销售发票'}<span>{labelFor(businessStatusLabel, business.status)}</span></button>)}
        </div>
      </div>
      {!activeBusiness && <EmptyState title="等待业务工作区" detail="在会话中确认一个业务意图后，这里会打开对应工作区。" />}
      {activeBusiness && <>
        <header className="business-header"><div><span className="eyebrow">{activeBusiness.type}</span><h2>{activeBusiness.title}</h2><p tabIndex={0} aria-label="业务目标">{activeBusiness.goal || '暂无业务目标描述'}</p></div><span className={`state-badge state-${activeBusiness.status}`}>{labelFor(businessStatusLabel, activeBusiness.status)}</span></header>
        <nav className="business-page-tabs" role="tablist" aria-label="业务页面">
          {tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'active' : ''} onClick={() => onTabChange(item.id)}>{item.label}{item.id === 'approvals' && detail?.approvals.filter(isPendingApproval).length ? <b>{detail.approvals.filter(isPendingApproval).length}</b> : null}</button>)}
        </nav>
        <div className="business-content">
          {businessLoading && <div className="loading-line">正在读取业务状态…</div>}
          {!businessLoading && tab === 'overview' && <OverviewPage detail={detail} activeRun={activeRun} onRefresh={onRefresh} onStart={onStart} onCancel={onCancel} />}
          {!businessLoading && tab === 'documents' && <DocumentsPage documents={detail?.documents ?? []} stale={detail?.stale ?? false} />}
          {!businessLoading && tab === 'approvals' && <ApprovalsPage approvals={detail?.approvals ?? []} disabled={loading || businessLoading} onDecision={onApproval} />}
          {!businessLoading && tab === 'verification' && <VerificationPage checks={detail?.checks ?? []} observedAt={detail?.observed_at} stale={detail?.stale ?? false} />}
          {!businessLoading && tab === 'trace' && <TracePage trace={trace} runs={detail?.runs ?? []} selectedRunId={selectedRunId} loading={traceLoading} onRunSelect={onRunSelect} />}
        </div>
      </>}
    </aside>
  )
}

function OverviewPage({ detail, activeRun, onRefresh, onStart, onCancel }: { detail: BusinessDetail | null; activeRun?: Run; onRefresh: () => void; onStart: () => void; onCancel: (run: Run) => void }) {
  const canStart = !activeRun || !['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status)
  const runActionLabel = activeRun?.status === 'completed' ? '继续执行' : activeRun?.status === 'failed' ? '重新执行' : '开始执行'
  return (
    <div className="page-stack">
      <div className="action-row">
        <button className="secondary-button" onClick={onRefresh}>读取最新状态</button>
        {activeRun && ['running', 'awaiting_approval'].includes(activeRun.status)
          ? <button className="danger-button" onClick={() => onCancel(activeRun)}>取消运行</button>
          : <button className="primary-button" disabled={!canStart} onClick={onStart}>{runActionLabel}</button>}
      </div>
      <BusinessFacts documents={detail?.documents ?? []} />
      <section className="status-table-section">
        <div className="section-heading">
          <div><span className="eyebrow">Current state</span><h3>业务状态</h3></div>
          <span>{detail?.stale ? '数据可能已过期' : `观测于 ${formatInstant(detail?.observed_at)}`}</span>
        </div>
        <div className="fact-table">
          <div><span>工作区状态</span><strong>{labelFor(businessStatusLabel, detail?.business.status)}</strong></div>
          <div><span>当前运行</span><strong>{activeRun ? labelFor(runStatusLabel, activeRun.status) : '没有运行'}</strong></div>
          <div><span>回读核验</span><strong>{labelFor({passed: '通过', failed: '未通过', unknown: '未知'}, activeRun?.verification_status)}</strong></div>
          <div><span>最近摘要</span><strong>{detail?.summary || '暂无主机摘要'}</strong></div>
        </div>
      </section>
      <section className="run-summary">
        <div className="section-heading">
          <div><span className="eyebrow">Run ledger</span><h3>最近运行</h3></div>
          <span>{detail?.runs.length ?? 0} 次</span>
        </div>
        {detail?.runs.length
          ? detail.runs.slice(0, 4).map((run) => <RunRow key={run.id} run={run} />)
          : <EmptyState title="还没有运行" detail="读取状态不会触发模型；点击开始执行才会创建运行。" />}
      </section>
    </div>
  )
}

function BusinessFacts({ documents }: { documents: Document[] }) {
  const order = documents.find((document) => document.model === 'sale.order')
  const invoice = documents.find((document) => document.model === 'account.move')
  if (!order && !invoice) return null
  const fact = (document: Document | undefined, keys: string[]) => {
    if (!document) return '未观测'
    const value = keys.map((key) => document.fields[key]).find((candidate) => candidate !== undefined && candidate !== null && candidate !== '')
    return readableValue(value)
  }
  return (
    <section className="business-facts">
      <div className="section-heading"><div><span className="eyebrow">Business records</span><h3>业务关键事实</h3></div><span>来自已观测单据</span></div>
      <div className="business-facts-grid">
        {order && <>
          <div><span>销售订单</span><strong>{order.name || order.id}</strong></div>
          <div><span>客户</span><strong>{fact(order, ['partner_name', 'customer', 'partner_id'])}</strong></div>
          <div><span>订单金额</span><strong>{fact(order, ['amount_total', 'total'])} {fact(order, ['currency', 'currency_name', 'currency_id'])}</strong></div>
          <div><span>订单状态</span><strong>{fact(order, ['state'])} · 开票 {fact(order, ['invoice_status'])}</strong></div>
          <div><span>交付状态</span><strong>{fact(order, ['delivery_status', 'delivery_state'])}</strong></div>
        </>}
        {invoice && <>
          <div><span>客户发票</span><strong>{invoice.name || invoice.id}</strong></div>
          <div><span>发票状态</span><strong>{fact(invoice, ['state'])}</strong></div>
          <div><span>发票金额</span><strong>{fact(invoice, ['amount_total', 'total'])} {fact(invoice, ['currency', 'currency_name', 'currency_id'])}</strong></div>
          <div><span>已付/未付余额</span><strong>{fact(invoice, ['amount_residual', 'residual'])}</strong></div>
          <div><span>发票日期</span><strong>{fact(invoice, ['invoice_date', 'date'])}</strong></div>
        </>}
      </div>
    </section>
  )
}

function RunRow({ run }: { run: Run }) { return <div className="run-row"><div><strong>{run.id}</strong><span>{formatInstant(run.started_at)} · {formatDuration(run.elapsed_seconds)}</span></div><div className="run-row-meta"><span className={`state-badge state-${run.status}`}>{labelFor(runStatusLabel, run.status)}</span><span>{formatCount(run.tool_count)} 工具</span></div></div> }

function DocumentsPage({ documents, stale }: { documents: Document[]; stale: boolean }) {
  return (
    <div className="page-stack">
      <div className="page-intro"><div><span className="eyebrow">Observed records</span><h3>业务单据</h3></div>{stale && <span className="warning-text">数据可能已过期</span>}</div>
      {documents.length === 0 ? <EmptyState title="还没有单据回执" detail="单据将在主机完成只读读取后出现在这里。" /> : (
        <div className="document-table-wrap">
          <table className="document-table">
            <thead><tr><th>类型</th><th>业务对象</th><th>状态</th><th>关键事实</th><th>观测时间</th></tr></thead>
            <tbody>{documents.map((document) => <tr key={`${document.model}:${document.id}`}>
              <td>{document.model}</td>
              <td><strong>{document.name || document.id}</strong><details><summary>字段</summary><pre>{jsonText(document.fields)}</pre></details></td>
              <td><span className={`state-badge state-${document.state}`}>{document.state || '未知'}</span></td>
              <td>{documentFact(document)}</td>
              <td>{formatInstant(document.observed_at)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function documentFact(document: Document) {
  const fields = document.fields
  const entries = document.model === 'sale.order'
    ? [['客户', fields.partner_name ?? fields.customer ?? fields.partner_id], ['金额', fields.amount_total ?? fields.total], ['开票', fields.invoice_status], ['交付', fields.delivery_status ?? fields.delivery_state]]
    : document.model === 'account.move'
      ? [['客户', fields.partner_name ?? fields.customer ?? fields.partner_id], ['金额', fields.amount_total ?? fields.total], ['付款', fields.payment_state], ['余额', fields.amount_residual ?? fields.residual]]
      : Object.entries(fields).slice(0, 3).map(([key, value]) => [key, value])
  return entries.filter(([, value]) => value !== undefined && value !== null && value !== '').map(([key, value]) => `${key}: ${readableValue(value)}`).join(' · ') || '没有可显示的关键字段'
}

function ApprovalsPage({ approvals, disabled, onDecision }: { approvals: Approval[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void }) {
  return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">Controlled writes</span><h3>业务审批</h3></div><span>{approvals.filter(isPendingApproval).length} 项待处理</span></div>{approvals.length === 0 ? <EmptyState title="没有审批记录" detail="主机产生需要人工确认的业务动作后，审批卡会保留在这里。" /> : <div className="approval-list">{approvals.map((approval) => <ApprovalRow key={approval.action_id} approval={approval} disabled={disabled} onDecision={onDecision} />)}</div>}</div>
}

function ApprovalRow({ approval, disabled, onDecision }: { approval: Approval; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void }) {
  const pending = isPendingApproval(approval)
  const expired = isExpired(approval.expires_at)
  const title = readableApprovalTitle(approval)
  return (
    <article className={`approval-row ${pending ? 'pending' : ''}`}>
      <div className="approval-row-head">
        <div><strong>{title}</strong><span>{modelLabel(approval.model)} · {operationLabel(approval.operation)} · {approval.model} · {approval.action_id}</span></div>
        <span className={`state-badge state-${expired ? 'expired' : approval.status}`}>{expired ? '已过期' : approvalStatusLabel(approval.status)}</span>
      </div>
      <div className="approval-facts"><span>记录 ID {approval.record_ids.length ? approval.record_ids.join(', ') : '未知'}</span><span>{formatExpiry(approval.expires_at)}</span></div>
      <details>
        <summary>查看拟提交值与执行前状态</summary>
        <div className="json-columns"><div><small>拟提交值</small><pre>{jsonText(approval.values)}</pre></div><div><small>执行前状态</small><pre>{jsonText(approval.prestate)}</pre></div></div>
      </details>
      {pending && !expired && <div className="approval-actions"><button className="danger-button" disabled={disabled} onClick={() => onDecision(approval, 'reject')}>拒绝</button><button className="primary-button" disabled={disabled} onClick={() => onDecision(approval, 'approve')}>批准这项业务动作</button></div>}
      {approval.result != null && <details className={`receipt receipt-details ${pending ? 'receipt-preflight' : ''}`}><summary>{approvalResultLabel(approval.status)}</summary><pre>{jsonText(approval.result)}</pre></details>}
    </article>
  )
}

function VerificationPage({ checks, observedAt, stale }: { checks: Check[]; observedAt?: string; stale: boolean }) { return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">Independent readback</span><h3>业务核验</h3></div><span>{stale ? '可能过期' : `读取于 ${formatInstant(observedAt)}`}</span></div>{checks.length === 0 ? <EmptyState title="核验结果未知" detail="主机尚未提供独立业务检查回执。" /> : <div className="check-table">{checks.map((check) => <div className="check-row" key={check.name}><span className={`check-mark check-${check.status}`}>{check.status === 'passed' ? '✓' : check.status === 'failed' ? '!' : '?'}</span><div><strong>{check.label || check.name}</strong><span>{check.detail || '没有详细说明'}</span></div><span className={`state-badge state-${check.status}`}>{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '未知'}</span></div>)}</div>}</div> }

function TracePage({ trace, runs, selectedRunId, loading, onRunSelect }: { trace: TraceBundle | null; runs: Run[]; selectedRunId: string; loading: boolean; onRunSelect: (id: string) => void }) {
  const toolsById = new Map((trace?.tools ?? []).map((tool) => [tool.id, tool]))
  return (
    <div className="trace-page">
      <div className="trace-toolbar">
        <label>运行<select value={selectedRunId} onChange={(event) => onRunSelect(event.target.value)}><option value="">选择运行</option>{runs.map((run) => <option key={run.id} value={run.id}>{run.id} · {labelFor(runStatusLabel, run.status)}</option>)}</select></label>
        {trace?.run && <div className="trace-metrics"><span>{formatCount(trace.run.model_rounds)} 轮</span><span>{formatCount(trace.run.tool_count)} 工具</span><UsageBreakdown usage={trace.run.usage} /></div>}
      </div>
      {loading && <div className="loading-line">正在读取 Trace…</div>}
      {!loading && !trace && <EmptyState title="选择一次运行" detail="Trace 只读取已持久化的回执，不会重新执行模型。" />}
      {trace && <div className="trace-body trace-body-single">
        {trace.run?.error && <details className="round-card"><summary>失败详情 · {trace.run.error}</summary><pre>{trace.run.error_detail || trace.run.error}</pre></details>}
        <div className="round-list"><h3>轮次与工具回执</h3>
          {trace.rounds.length === 0 && <p className="muted">没有公开轮次回执。</p>}
          {trace.rounds.map((round) => <details className="round-card" key={round.index} open={round.index === 0}>
            <summary><span>第 {round.index} 轮</span><b>{round.status}</b><small>{formatDuration(round.elapsed_seconds)} · {formatCount(round.usage?.total)} tokens</small></summary>
            <p>{round.text || '没有公开摘要；隐藏思维不会在工作台展示。'}</p>
            <UsageBreakdown usage={round.usage} />
            <div className="round-tools">
              {round.tool_ids.length === 0 && <span className="muted">本轮没有工具请求</span>}
              {round.tool_ids.map((id) => {
                const tool = toolsById.get(id)
                return tool ? <ToolReceiptRow key={id} tool={tool} /> : <span key={id} className="warning-text">缺少回执：{id}</span>
              })}
            </div>
          </details>)}
        </div>
      </div>}
    </div>
  )
}

function UsageBreakdown({ usage }: { usage?: Run['usage'] }) {
  if (!usage) return <div className="usage-breakdown"><span>用量未知</span></div>
  return <div className="usage-breakdown" aria-label="Token 用量"><span>未缓存输入 {formatCount(usage.input)}</span><span>缓存命中 {formatCount(usage.cache_read)}</span><span>输出（含推理） {formatCount(usage.output)}</span><span>推理 {formatCount(usage.reasoning)}</span><span>总计 {formatCount(usage.total)}</span></div>
}

function ToolReceiptRow({ tool }: { tool: ToolReceipt }) { return <details className="tool-row"><summary><span className={`tool-status tool-${tool.status}`}>{tool.status}</span><strong>{tool.name}</strong><small>{tool.round ? `第 ${tool.round} 轮 · ` : ''}{formatDuration(tool.elapsed_seconds)}</small></summary><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div>{tool.action_id && <span className="receipt-link">关联审批：{tool.action_id}</span>}</details> }

function EmptyState({ title, detail }: { title: string; detail: string }) { return <div className="empty-state"><span className="empty-glyph">○</span><strong>{title}</strong><p>{detail}</p></div> }

function messageForError(reason: unknown) {
  const message = reason instanceof Error ? reason.message : String(reason)
  return message.includes('CONFIG_BUSY') ? '当前有业务正在执行或等待审批，请结束后再修改连接设置。' : message
}
function connectionLabel(state: ConnectionState) { return state === 'connected' ? '主机已连接' : state === 'checking' ? '正在连接主机' : state === 'crashed' ? '主机已崩溃' : state === 'protocol_error' ? '主机协议错误' : '主机断开' }
function healthLabel(status?: string) { return status === 'connected' || status === 'ready' || status === 'ok' ? '已连接' : status === 'configured' ? '已配置' : status === 'disconnected' ? '断开' : '状态未知' }
function isPendingApproval(approval: Approval) { return approval.status === 'pending' || approval.status === 'pending_approval' }
function approvalStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '待审批', pending_approval: '待审批', approved: '已批准 / 待执行', rejected: '已拒绝',
    verified: '已核验', known_failed: '已知失败', not_executed: '未执行', interrupted: '已中断',
    stale: '已失效', expired: '已过期', needs_reconciliation: '需对账', executing: '执行中', executed: '已执行', failed: '执行失败'
  }
  return labels[status] || status
}
function operationLabel(operation: string) {
  const labels: Record<string, string> = { create: '创建', write: '修改', unlink: '删除', action_confirm: '确认', create_invoices: '创建发票', action_post: '过账' }
  return labels[operation] || operation
}
function modelLabel(model: string) { return model === 'sale.order' ? '销售订单' : model === 'account.move' ? '客户发票' : model === 'res.partner' ? '客户' : model }
function readableApprovalTitle(approval: Approval) {
  const title = approval.title?.trim()
  const generic = !title || ['erp write approval', 'write approval', 'approval required', 'business approval', 'action approval'].includes(title.toLowerCase())
  return generic ? `${operationLabel(approval.operation)}${modelLabel(approval.model)}` : title
}
function approvalResultLabel(status: string) {
  return status === 'pending' || status === 'pending_approval' ? '预检 / 动作回执（未执行）' : status === 'approved' ? '已批准 / 待执行' : status === 'rejected' || status === 'not_executed' ? '审批结果（未执行）' : ['verified', 'executed', 'completed', 'posted'].includes(status) ? '动作执行回执' : '动作回执'
}
function environmentLabel(environment?: string) { return environment === 'configured' ? '已配置环境' : environment === 'demo' ? '演示环境' : '未知' }
function readableValue(value: unknown): string {
  if (value == null || value === '') return '未知'
  if (Array.isArray(value)) return value.map((item) => readableValue(item)).join(', ')
  if (typeof value === 'object') return jsonText(value).replace(/\s+/g, ' ')
  return String(value)
}

function SettingsDialog({ settings, draft, saving, onChange, onClose, onSave }: { settings: Settings | null; draft: Record<string, string>; saving: boolean; onChange: (key: string, value: string) => void; onClose: () => void; onSave: () => void }) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  useEffect(() => {
    closeButtonRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button, input, select, textarea, [tabindex]:not([tabindex="-1"])')).filter((element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true')
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])
  const field = (key: string, label: string, type = 'text', placeholder = '') => <label className="settings-field"><span>{label}</span><input type={type} value={draft[key] || ''} placeholder={placeholder} onChange={(event) => onChange(key, event.target.value)} /></label>
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section ref={dialogRef} className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header><div><span className="eyebrow">Connection settings</span><h2 id="settings-title">连接设置</h2></div><button ref={closeButtonRef} className="modal-close" onClick={onClose} aria-label="关闭设置">×</button></header>
        <p className="settings-note">配置模型与 Odoo 连接。密钥留空表示保留已有密钥。</p>
        <div className="settings-grid">{field('model', '模型')}{field('base_url', '模型地址')}{field('odoo_url', 'Odoo 地址')}{field('odoo_db', 'Odoo 数据库')}{field('odoo_username', 'Odoo 用户名')}{field('model_key', settings?.has_model_key ? '模型密钥（留空保留）' : '模型密钥', 'password')}{field('odoo_key', settings?.has_odoo_key ? 'Odoo 密钥（留空保留）' : 'Odoo 密钥', 'password')}</div>
        <div className="settings-footer"><span>环境：{environmentLabel(settings?.environment)}</span><div><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving} onClick={onSave}>{saving ? '保存中…' : '保存连接设置'}</button></div></div>
      </section>
    </div>
  )
}
