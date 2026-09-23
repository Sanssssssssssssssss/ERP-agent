import { useCallback,useEffect,useMemo,useRef,useState,type FormEvent,type PointerEvent as ReactPointerEvent } from 'react'
import type { WorkbenchMethod } from '../shared/protocol'
import { documentKey,messageForError } from './presentation'
import {
Approval,
Business,
BusinessArtifact,
BusinessDetailProjection,
BusinessTab,
ConversationRun,
Document,
Health,
HostEvent,
LiveMessage,
Run,
SessionDetail,
SessionSummary,
Settings,
TraceBundle
} from './protocol'
import type { TraceDetail, TraceDetailKind } from './protocol'
import { ApprovalProgress,ConnectionState,DownloadReceipt,MaterialRecord,ProposalLike } from './view-types'

export const liveMessageKey = (message: Pick<LiveMessage, 'session_id' | 'business_id' | 'run_id' | 'id'>) => `${message.session_id}:${message.business_id ?? '__conversation__'}:${message.run_id}:${message.id}`

export function useWorkbench() {

  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [selectedSessionId, setSelectedSessionId] = useState('')
  const [selectedBusinessId, setSelectedBusinessId] = useState('')
  const [businessDetail, setBusinessDetail] = useState<BusinessDetailProjection | null>(null)
  const [trace, setTrace] = useState<TraceBundle | null>(null)
  const [traceTarget, setTraceTarget] = useState<{ runId?: string; toolId?: string; actionId?: string; kind?: string } | null>(null)
  const [selectedRunId, setSelectedRunId] = useState('')
  const [tab, setTab] = useState<BusinessTab>('execution')
  const [draft, setDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [businessLoading, setBusinessLoading] = useState(false)
  const [traceLoading, setTraceLoading] = useState(false)
  const [connection, setConnection] = useState<ConnectionState>('checking')
  const [health, setHealth] = useState<Health | null>(null)
  const hostReadyRef = useRef<boolean | null>(null)
  const [error, setError] = useState('')
  const [liveMessages, setLiveMessages] = useState<LiveMessage[]>([])
  const [thinkingRun, setThinkingRun] = useState<{ sessionId: string; runId: string } | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [connectionDetailsOpen, setConnectionDetailsOpen] = useState(false)
  const [archiveTarget, setArchiveTarget] = useState('')
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({})
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [blockedSend, setBlockedSend] = useState('')
  const [renamingId, setRenamingId] = useState('')
  const [messageBusinessId, setMessageBusinessId] = useState('')
  const [sessionQuery, setSessionQuery] = useState('')
  const [businessWidth, setBusinessWidth] = useState(560)
  const [conversationOpen, setConversationOpen] = useState(() => {
    try {
      const saved = window.localStorage.getItem('odoo-workbench.conversation-open')
      return saved == null ? true : saved === 'true'
    } catch {
      return true
    }
  })
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [openingOdoo, setOpeningOdoo] = useState(false)
  const [exportPath, setExportPath] = useState('')
  const [pendingMaterials, setPendingMaterials] = useState<MaterialRecord[]>([])
  const [materialsBusy, setMaterialsBusy] = useState(false)
  const [documentDownloads, setDocumentDownloads] = useState<Record<string, DownloadReceipt>>({})
  const [selectedDocumentKey, setSelectedDocumentKey] = useState('')
  const [approvalProgress, setApprovalProgress] = useState<ApprovalProgress | null>(null)
  const [traceRefreshToken, setTraceRefreshToken] = useState(0)
  const settingsButtonRef = useRef<HTMLButtonElement>(null)
  const sessionIdRef = useRef('')
  const businessIdRef = useRef('')
  const sessionRequestRef = useRef(0)
  const businessRequestRef = useRef(0)
  const traceRequestRef = useRef(0)
  const selectedRunIdRef = useRef(selectedRunId)
  selectedRunIdRef.current = selectedRunId
  const traceRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const businessRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const quietBusinessRequestRef = useRef(0)
  const messageInFlightRef = useRef(new Set<string>())
  const proposalInFlightRef = useRef(new Set<string>())
  const runStartInFlightRef = useRef(new Set<string>())
  const approvalInFlightRef = useRef(new Set<string>())
  const cancelInFlightRef = useRef(new Set<string>())
  const conversationCancelInFlightRef = useRef(new Set<string>())
  const conversationRunsRef = useRef<ConversationRun[]>([])
  const businessRunsRef = useRef<Run[]>([])
  const conversationStreamsRef = useRef(new Map<string, LiveMessage>())
  const pendingStreamEventsRef = useRef<Array<{ event: 'message_delta' | 'message_end'; sessionId: string; businessId: string | null; runId: string; messageId: string; sequence: number; text: string }>>([])
  const finalizedStreamKeysRef = useRef(new Set<string>())
  const streamRenderFrameRef = useRef<number | null>(null)
  const materialRequestRef = useRef(0)

  const publishLiveMessages = () => {
    if (streamRenderFrameRef.current !== null) return
    streamRenderFrameRef.current = window.requestAnimationFrame(() => {
      streamRenderFrameRef.current = null
      setLiveMessages([...conversationStreamsRef.current.values()])
    })
  }

  const applyStreamEvent = (event: { event: 'message_delta' | 'message_end'; sessionId: string; businessId: string | null; runId: string; messageId: string; sequence: number; text: string }) => {
    if (event.sessionId !== sessionIdRef.current) return true
    const conversationRun = conversationRunsRef.current.find((run) => run.id === event.runId && run.session_id === event.sessionId)
    const businessRun = businessRunsRef.current.find((run) => run.id === event.runId && run.session_id === event.sessionId && run.business_id === event.businessId)
    if (!conversationRun && !businessRun) return false
    const expectedBusinessId = conversationRun ? (conversationRun.business_id ?? null) : event.businessId
    if (expectedBusinessId !== event.businessId) return true
    const runStatus = conversationRun?.status ?? businessRun?.status
    if (event.event === 'message_delta' && ['completed', 'failed', 'cancelled', 'interrupted'].includes(runStatus || '')) return true
    const key = liveMessageKey({ session_id: event.sessionId, business_id: event.businessId, run_id: event.runId, id: event.messageId })
    if (finalizedStreamKeysRef.current.has(key)) return true
    const current = conversationStreamsRef.current.get(key)
    if (event.event === 'message_delta') {
      if (!current || event.sequence > current.sequence) {
        conversationStreamsRef.current.set(key, {
          id: event.messageId,
          session_id: event.sessionId,
          business_id: event.businessId,
          run_id: event.runId,
          sequence: event.sequence,
          text: `${current?.text || ''}${event.text}`,
          role: 'assistant',
          status: 'streaming',
          created_at: current?.created_at || new Date().toISOString()
        })
        publishLiveMessages()
      }
      if (event.businessId == null) setThinkingRun((current) => current && current.sessionId === event.sessionId && (!current.runId || current.runId === event.runId) ? null : current)
    } else if (!current || current.status !== 'ended') {
      conversationStreamsRef.current.set(key, {
        id: event.messageId,
        session_id: event.sessionId,
        business_id: event.businessId,
        run_id: event.runId,
        sequence: Math.max(event.sequence, current?.sequence ?? 0),
        text: event.text || current?.text || '',
        role: 'assistant',
        status: 'ended',
        created_at: current?.created_at || new Date().toISOString()
      })
      finalizedStreamKeysRef.current.add(key)
      publishLiveMessages()
      if (event.businessId == null) setThinkingRun((current) => current && current.sessionId === event.sessionId && (!current.runId || current.runId === event.runId) ? null : current)
    }
    return true
  }

  const drainPendingStreamEvents = () => {
    const pending = pendingStreamEventsRef.current
    pendingStreamEventsRef.current = pending.filter((event) => !applyStreamEvent(event))
  }

  const call = useCallback(async <T,>(method: string, params?: Record<string, unknown>) => {
    if (!window.workbench) throw new Error('桌面主机桥未连接')
    return window.workbench.call(method as WorkbenchMethod, params) as Promise<T>
  }, [])

  const loadSessions = useCallback(async (selectFirst = true) => {
    const result = await call<SessionSummary[]>('list_sessions')
    const active = result.filter((item) => !item.archived)
    setSessions(active)
    if (selectFirst && !sessionIdRef.current && active[0]) {
      sessionIdRef.current = active[0].id
      setSelectedSessionId(active[0].id)
    }
    return active
  }, [call])

  const checkConnection = useCallback(async (showError = true) => {
    setConnection('checking')
    try {
      const result = await call<Health>('check_connection')
      hostReadyRef.current = result.host_ready
      setHealth(result)
      setConnection(result.host_ready ? 'connected' : 'disconnected')
      if (showError) setError(result.host_ready ? '' : '本地 host 尚未就绪')
      return result
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason)
      if (message.includes('CONNECTION_CHECK_BUSY')) {
        try {
          const cached = await call<Health>('health')
          hostReadyRef.current = cached.host_ready
          setHealth((current) => cached.odoo ? cached : { ...cached, odoo: current?.odoo })
          setConnection(cached.host_ready ? 'connected' : 'disconnected')
          if (showError) setError('执行期间显示最近检查结果，结束后可重新检查。')
          return cached
        } catch {
          setConnection(hostReadyRef.current === true ? 'connected' : 'disconnected')
        }
        if (showError) setError('执行期间显示最近检查结果，结束后可重新检查。')
        return null
      }
      setConnection(hostReadyRef.current === true ? 'connected' : 'disconnected')
      if (showError) {
        setError(messageForError(reason))
      }
      return null
    }
  }, [call])

  const loadSession = useCallback(async (sessionId: string) => {
    if (!sessionId) return
    const requestId = ++sessionRequestRef.current
    const result = await call<SessionDetail>('get_session', { session_id: sessionId })
    if (requestId !== sessionRequestRef.current || sessionIdRef.current !== sessionId) return
    setSession(result)
    setSessions((current) => current.map((item) => item.id === sessionId ? result.session : item))
    conversationRunsRef.current = result.conversation_runs ?? []
    const latestConversation = [...conversationRunsRef.current].filter((run) => run.business_id == null).sort((left, right) => String(right.started_at || '').localeCompare(String(left.started_at || '')))[0]
    if (latestConversation && ['completed', 'failed', 'cancelled', 'interrupted'].includes(latestConversation.status)) {
      setThinkingRun((current) => current && current.runId && current.sessionId === sessionId && current.runId === latestConversation.id ? null : current)
    }
    const persistedMessageIds = new Set(result.messages.map((message) => message.id))
    for (const message of result.messages) {
      if (message.run_id) finalizedStreamKeysRef.current.add(liveMessageKey({ session_id: sessionId, business_id: message.business_id ?? null, run_id: message.run_id, id: message.id }))
    }
    for (const message of result.live_messages ?? []) {
      if (!persistedMessageIds.has(message.id) && message.session_id === sessionId && Number.isInteger(message.sequence)) {
        const key = liveMessageKey(message)
        const current = conversationStreamsRef.current.get(key)
        if (!current || message.sequence >= current.sequence) conversationStreamsRef.current.set(key, message)
        if (message.status === 'ended') finalizedStreamKeysRef.current.add(key)
      }
    }
    for (const [key, message] of conversationStreamsRef.current) {
      if (message.session_id !== sessionId || persistedMessageIds.has(message.id)) conversationStreamsRef.current.delete(key)
    }
    setLiveMessages([...conversationStreamsRef.current.values()])
    drainPendingStreamEvents()
    publishLiveMessages()
    sessionIdRef.current = sessionId
    setSelectedBusinessId((current) => {
      if (current && result.businesses.some((item) => item.id === current)) return current
      return result.businesses[0]?.id ?? ''
    })
    setMessageBusinessId((current) => {
      if (current === '__conversation__' || (current && result.businesses.some((item) => item.id === current))) return current
      return '__conversation__'
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
      const result = await call<BusinessDetailProjection>('get_business', { session_id: sessionId, business_id: businessId })
      if (requestId !== businessRequestRef.current || sessionIdRef.current !== sessionId || businessIdRef.current !== businessId) return
      setBusinessDetail(result)
      businessRunsRef.current = result.runs
      for (const message of result.live_messages ?? []) {
        if (message.session_id === sessionId && Number.isInteger(message.sequence)) {
          const key = liveMessageKey(message)
          const current = conversationStreamsRef.current.get(key)
          if (!current || message.sequence >= current.sequence) conversationStreamsRef.current.set(key, message)
          if (message.status === 'ended') finalizedStreamKeysRef.current.add(key)
        }
      }
      drainPendingStreamEvents()
      publishLiveMessages()
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

  const refreshBusinessQuiet = useCallback(async (sessionId: string, businessId: string) => {
    const requestId = ++quietBusinessRequestRef.current
    try {
      const result = await call<BusinessDetailProjection>('get_business', { session_id: sessionId, business_id: businessId })
      if (requestId !== quietBusinessRequestRef.current || sessionIdRef.current !== sessionId || businessIdRef.current !== businessId) return
      setBusinessDetail(result)
      businessRunsRef.current = result.runs
      drainPendingStreamEvents()
      publishLiveMessages()
      setSelectedRunId((current) => result.runs.some((run) => run.id === current) ? current : result.runs[0]?.id || '')
    } catch (reason) {
      if (requestId === quietBusinessRequestRef.current && sessionIdRef.current === sessionId && businessIdRef.current === businessId) setError(messageForError(reason))
    }
  }, [call])

  useEffect(() => {
    let mounted = true
    void (async () => {
      try {
        const result = await call<Health>('health')
        if (mounted) {
          hostReadyRef.current = result.host_ready
          setHealth(result)
          setConnection(result.host_ready ? 'connected' : 'disconnected')
          if (!result.host_ready) setError('本地 host 尚未就绪')
        }
        await loadSessions()
        await checkConnection(false)
      } catch (reason) {
        if (mounted) {
          setConnection('disconnected')
          setError(messageForError(reason))
        }
      }
    })()
    return () => { mounted = false }
  }, [call, loadSessions, checkConnection])

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

  useEffect(() => { setSelectedDocumentKey('') }, [selectedBusinessId, selectedSessionId])

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
      run_id: selectedRunId,
      summary_only: true
    }).then((result) => {
      if (requestId === traceRequestRef.current) setTrace(result)
    }).catch((reason) => {
      if (requestId === traceRequestRef.current) setError(messageForError(reason))
    }).finally(() => {
      if (requestId === traceRequestRef.current) setTraceLoading(false)
    })
  }, [call, selectedBusinessId, selectedRunId, selectedSessionId, tab, traceRefreshToken])

  const loadTraceDetail = useCallback(async (runId: string, kind: TraceDetailKind, id: string) => {
    const requestSessionId = sessionIdRef.current
    const requestBusinessId = businessIdRef.current
    if (!requestSessionId || !requestBusinessId || selectedRunIdRef.current !== runId) return null
    const result = await call<TraceDetail>('get_trace_detail', { session_id: requestSessionId, business_id: requestBusinessId, run_id: runId, kind, id })
    if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId || selectedRunIdRef.current !== runId) return null
    return result
  }, [call])

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
          void loadSessions(false).catch(() => undefined)
          void reloadCurrent().catch((reason) => setError(messageForError(reason)))
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
      if (event.event === 'changed' && String(data.type || '') === 'connection_changed') {
        setHealth((current) => current ? { ...current, odoo: data.odoo && typeof data.odoo === 'object' ? data.odoo as Health['odoo'] : current.odoo } : current)
        return
      }
      if (event.event === 'message_delta' || event.event === 'message_end') {
        const runId = typeof data.run_id === 'string' ? data.run_id : ''
        const messageId = typeof data.message_id === 'string' ? data.message_id : ''
        const sequence = Number(data.sequence)
        const eventText = typeof data.text === 'string' ? data.text : ''
        const eventBusiness = data.business_id == null ? null : String(data.business_id)
        if (!eventSession || eventSession !== sessionIdRef.current || !runId || !messageId || !Number.isInteger(sequence) || sequence < 0) return
        if (eventBusiness && eventBusiness !== businessIdRef.current) return
        const normalized = { event: event.event as 'message_delta' | 'message_end', sessionId: eventSession, businessId: eventBusiness, runId, messageId, sequence, text: eventText }
        if (!applyStreamEvent(normalized)) {
          pendingStreamEventsRef.current.push(normalized)
          if (pendingStreamEventsRef.current.length > 100) pendingStreamEventsRef.current.shift()
        }
        return
      }
      if (event.event === 'run_trace' || event.event === 'trace') {
        const eventRun = String(data.run_id || '')
        if (eventBusiness === businessIdRef.current && eventSession === sessionIdRef.current) {
          if (businessRefreshTimerRef.current) clearTimeout(businessRefreshTimerRef.current)
          businessRefreshTimerRef.current = setTimeout(() => { void refreshBusinessQuiet(eventSession, eventBusiness) }, 120)
        }
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
          if (eventBusiness === approvalProgress?.businessId && ['completed', 'cancelled', 'interrupted'].includes(changeStatus)) setApprovalProgress(null)
          if (eventBusiness === approvalProgress?.businessId && changeStatus === 'failed') setApprovalProgress((current) => current ? { ...current, status: 'failed' } : current)
          if (['completed', 'failed', 'cancelled', 'interrupted'].includes(changeStatus)) setLiveMessages([...conversationStreamsRef.current.values()])
        }).catch((reason) => setError(messageForError(reason)))
      }
    }
    return window.workbench.subscribe(handleEvent)
  }, [approvalProgress?.businessId, checkConnection, loadSessions, refreshBusinessQuiet, reloadCurrent, selectedBusinessId, selectedRunId, selectedSessionId, tab])

  const activeBusiness = useMemo(
    () => session?.businesses.find((item) => item.id === selectedBusinessId) ?? businessDetail?.business ?? null,
    [businessDetail?.business, selectedBusinessId, session?.businesses]
  )
  const hasActiveExecution = Boolean(
    businessDetail?.runs.some((run) => ['running', 'awaiting_approval', 'cancel_requested'].includes(run.status))
      || session?.businesses.some((business) => ['running', 'awaiting_approval', 'cancel_requested'].includes(business.status))
      || sessions.some((item) => ['running', 'awaiting_approval', 'cancel_requested'].includes(item.status))
  )

  const chooseSession = (id: string) => {
    if (sessionIdRef.current === id && selectedSessionId === id) return
    sessionIdRef.current = id
    businessIdRef.current = ''
    sessionRequestRef.current += 1
    businessRequestRef.current += 1
    quietBusinessRequestRef.current += 1
    traceRequestRef.current += 1
    setSelectedSessionId(id)
    setBlockedSend('')
    setSelectedBusinessId('')
    setBusinessDetail(null)
    setBusinessLoading(false)
    setLoading(false)
    setTrace(null)
    setTraceTarget(null)
    setExportPath('')
    setSelectedRunId('')
    setMessageBusinessId('__conversation__')
    setThinkingRun(null)
    setTab('execution')
    conversationStreamsRef.current.clear()
    conversationRunsRef.current = []
    businessRunsRef.current = []
    pendingStreamEventsRef.current = []
    finalizedStreamKeysRef.current.clear()
    setLiveMessages([])
    materialRequestRef.current += 1
    setPendingMaterials([])
    setMaterialsBusy(false)
    setDocumentDownloads({})
  }

  const importMaterials = async (files: File[]) => {
    if (!selectedSessionId || !files.length || materialsBusy) return
    const requestSessionId = selectedSessionId
    const requestId = ++materialRequestRef.current
    const available = Math.max(0, 3 - pendingMaterials.length)
    if (files.length > available) {
      setError(available ? `本次最多再添加 ${available} 个文件。` : '本次消息最多附带 3 个文件。')
      files = files.slice(0, available)
    }
    if (!files.length) return
    setMaterialsBusy(true)
    try {
      for (const file of files) {
        if (file.size > 2 * 1024 * 1024) throw new Error(`文件“${file.name}”超过 2 MiB 限制。`)
        if (!/\.(csv|txt)$/i.test(file.name) && !['text/csv', 'text/plain'].includes(file.type)) throw new Error(`文件“${file.name}”仅支持 CSV 或 TXT。`)
        const bytes = new Uint8Array(await file.arrayBuffer())
        let binary = ''
        const chunkSize = 0x8000
        for (let offset = 0; offset < bytes.length; offset += chunkSize) binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)))
        const response = await call<MaterialRecord | { material?: MaterialRecord }>('import_material', { session_id: requestSessionId, name: file.name, content_base64: btoa(binary) })
        const material = (response && 'material' in response ? response.material : response) as MaterialRecord | undefined
        if (!material?.id) throw new Error(`文件“${file.name}”解析失败，请检查内容。`)
        if (sessionIdRef.current === requestSessionId && materialRequestRef.current === requestId) setPendingMaterials((current) => current.some((item) => item.id === material.id) ? current : current.length >= 3 ? current : [...current, material])
      }
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && materialRequestRef.current === requestId) setError(messageForError(reason))
    } finally {
      if (materialRequestRef.current === requestId) setMaterialsBusy(false)
    }
  }

  const downloadDocument = async (document: Document, format: 'pdf' | 'csv') => {
    if (!selectedSessionId || !selectedBusinessId) return
    const recordId = Number(document.id)
    if (!Number.isInteger(recordId) || recordId <= 0) {
      setError('该单据没有可用的 Odoo 记录 ID。')
      return
    }
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    const key = `${documentKey(document)}:${format}`
    setDocumentDownloads((current) => ({ ...current, [key]: { status: 'downloading' } }))
    try {
      const result = await call<{ cancelled?: boolean; path?: string; artifact?: BusinessArtifact }>('download_document', { session_id: requestSessionId, business_id: requestBusinessId, model: document.model, record_id: recordId, format })
      if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      if (result.cancelled) {
        setDocumentDownloads((current) => ({ ...current, [key]: { status: 'cancelled' } }))
        return
      }
      setDocumentDownloads((current) => ({ ...current, [key]: { status: 'completed', path: result.path, artifact: result.artifact } }))
      setNotice(`${format.toUpperCase()} 已下载：${result.artifact?.name || result.path || document.name}`)
      window.setTimeout(() => setNotice(''), 2600)
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) {
        const message = messageForError(reason)
        setDocumentDownloads((current) => ({ ...current, [key]: { status: 'failed', error: message } }))
        setError(message)
      }
    }
  }

  const chooseBusiness = (id: string) => {
    if (businessIdRef.current === id && selectedBusinessId === id) return
    businessIdRef.current = id
    businessRequestRef.current += 1
    quietBusinessRequestRef.current += 1
    traceRequestRef.current += 1
    setSelectedBusinessId(id)
    setMessageBusinessId(id)
    setBusinessDetail(null)
    setTrace(null)
    setTraceTarget(null)
    setExportPath('')
    setBusinessLoading(true)
    setLoading(false)
    setTab('execution')
    setSelectedRunId('')
  }

  const toggleConversation = () => {
    setConversationOpen((value) => {
      const next = !value
      try { window.localStorage.setItem('odoo-workbench.conversation-open', String(next)) } catch { /* storage is optional */ }
      return next
    })
  }

  const createSession = async () => {
    setLoading(true)
    try {
      const created = await call<SessionSummary>('create_session')
      await loadSessions(false)
      setConversationOpen(true)
      try { window.localStorage.setItem('odoo-workbench.conversation-open', 'true') } catch { /* storage is optional */ }
      chooseSession(created.id)
    } catch (reason) {
      setError(messageForError(reason))
    } finally {
      setLoading(false)
    }
  }

  const archiveSession = async (id: string) => {
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
    const attachedMaterials = pendingMaterials
    const text = draft.trim() || (attachedMaterials.length ? '请先整理这些业务材料，说明需要补充的信息' : '')
    if (!text || !selectedSessionId || loading || materialsBusy) return
    const requestSessionId = selectedSessionId
    const messageKey = `${requestSessionId}:${text}:${attachedMaterials.map((material) => material.id).join(',')}`
    if (messageInFlightRef.current.has(messageKey)) return
    if (hasActiveExecution || conversationRunsRef.current.some((run) => ['running', 'cancel_requested'].includes(run.status))) {
      setBlockedSend('请等待当前运行结束；需要修改待审批动作，请在审批卡选择“提出修改”。输入已保留。')
      return
    }
    messageInFlightRef.current.add(messageKey)
    setLoading(true)
    setDraft('')
    setThinkingRun({ sessionId: requestSessionId, runId: '' })
    const contextBusinessId = messageBusinessId === '__conversation__' ? '' : messageBusinessId || selectedBusinessId
    try {
      const result = await call<{ ok?: boolean; run_id?: string }>('send_message', {
        session_id: requestSessionId,
        text,
        ...(attachedMaterials.length ? { material_ids: attachedMaterials.map((material) => material.id) } : {}),
        ...(contextBusinessId ? { context_business_id: contextBusinessId } : {})
      })
      if (sessionIdRef.current === requestSessionId) setThinkingRun((current) => current && current.sessionId === requestSessionId ? { ...current, runId: typeof result?.run_id === 'string' ? result.run_id : current.runId } : current)
      if (sessionIdRef.current === requestSessionId) {
        setPendingMaterials([])
        await loadSession(requestSessionId)
      }
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId) {
        setThinkingRun((current) => current?.sessionId === requestSessionId ? null : current)
        setDraft(text)
        setError(messageForError(reason))
      }
    } finally {
      messageInFlightRef.current.delete(messageKey)
      if (sessionIdRef.current === requestSessionId) setLoading(false)
    }
  }

  const confirmProposal = async (proposal: ProposalLike, confirmed: boolean) => {
    if (!selectedSessionId) return
    const message = session?.messages.find((item) => item.proposal?.id === proposal.id)
    if (message?.proposal?.status === 'confirmed' && message.business_id) {
      chooseBusiness(message.business_id)
      return
    }
    if (message?.proposal?.status !== 'pending' || proposal.id !== pendingProposal?.id || proposalUnavailable) return
    const producer = conversationRunsRef.current.find((run) => run.id === message.run_id || run.proposal_ids?.includes(proposal.id))
    if (producer && ['running', 'cancel_requested'].includes(producer.status)) return
    const requestSessionId = selectedSessionId
    const proposalKey = `${requestSessionId}:${proposal.id}`
    if (proposalInFlightRef.current.has(proposalKey)) return
    proposalInFlightRef.current.add(proposalKey)
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
      proposalInFlightRef.current.delete(proposalKey)
      if (sessionIdRef.current === requestSessionId) setLoading(false)
    }
  }

  const startRun = async () => {
    if (!selectedSessionId || !selectedBusinessId) return
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    const runKey = `${requestSessionId}:${requestBusinessId}`
    if (runStartInFlightRef.current.has(runKey)) return
    runStartInFlightRef.current.add(runKey)
    setLoading(true)
    try {
      const run = await call<Run>('start_run', { session_id: requestSessionId, business_id: requestBusinessId })
      if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      setSelectedRunId(run.id)
      await reloadCurrent()
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    } finally {
      runStartInFlightRef.current.delete(runKey)
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setLoading(false)
    }
  }

  const cancelRun = async (run: Run) => {
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    const cancelKey = `${requestSessionId}:${requestBusinessId}:${run.id}`
    if (cancelInFlightRef.current.has(cancelKey)) return
    cancelInFlightRef.current.add(cancelKey)
    try {
      await call('cancel_run', { session_id: requestSessionId, business_id: requestBusinessId, run_id: run.id })
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) await reloadCurrent()
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    } finally {
      cancelInFlightRef.current.delete(cancelKey)
    }
  }

  const cancelConversation = async (run: ConversationRun) => {
    const requestSessionId = selectedSessionId
    const key = `${requestSessionId}:${run.id}`
    if (conversationCancelInFlightRef.current.has(key)) return
    conversationCancelInFlightRef.current.add(key)
    setLoading(true)
    try {
      await call('cancel_conversation', { session_id: requestSessionId, run_id: run.id })
      if (sessionIdRef.current === requestSessionId) await loadSession(requestSessionId)
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId) setError(messageForError(reason))
    } finally {
      conversationCancelInFlightRef.current.delete(key)
      if (sessionIdRef.current === requestSessionId) setLoading(false)
    }
  }

  const decideApproval = async (approval: Approval, decision: 'approve' | 'reject') => {
    const requestSessionId = selectedSessionId
    const requestBusinessId = approval.business_id
    const approvalKey = `${requestSessionId}:${requestBusinessId}:${approval.run_id}:${approval.action_id}`
    if (approvalInFlightRef.current.has(approvalKey)) return
    approvalInFlightRef.current.add(approvalKey)
    setError('')
    setApprovalProgress({ key: approvalKey, businessId: requestBusinessId, status: 'submitting' })
    setLoading(true)
    try {
      const result = await call<{ ok?: boolean; status?: string; remaining_action_ids?: string[]; awaiting_action_ids?: string[] }>('decide_approval', {
        session_id: requestSessionId,
        business_id: requestBusinessId,
        run_id: approval.run_id,
        action_id: approval.action_id,
        decision
      })
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId && result?.ok !== true) setApprovalProgress({ key: approvalKey, businessId: requestBusinessId, status: 'failed', detail: result?.status === 'expired' ? '审批已过期。' : result?.status === 'stale' ? '业务状态已变化，审批未生效。' : '审批状态未生效。' })
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId && result?.ok === true) setApprovalProgress(null)
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) await reloadCurrent()
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) { setApprovalProgress(null); setError(messageForError(reason)) }
    } finally {
      approvalInFlightRef.current.delete(approvalKey)
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setLoading(false)
    }
  }

  const requestApprovalRevision = async (approval: Approval, text: string) => {
    const requestSessionId = selectedSessionId
    const requestBusinessId = approval.business_id
    const approvalKey = `${requestSessionId}:${requestBusinessId}:${approval.run_id}:${approval.action_id}`
    if (!text.trim() || !requestSessionId || requestBusinessId !== selectedBusinessId || approval.status !== 'pending_approval') throw new Error('请选择待审批动作并填写修改要求。')
    if (approvalInFlightRef.current.has(approvalKey)) throw new Error('该审批正在提交，请稍候。')
    approvalInFlightRef.current.add(approvalKey)
    setLoading(true)
    try {
      const result = await call<{ ok: boolean; run_id?: string }>('request_approval_revision', {
        session_id: requestSessionId, business_id: requestBusinessId,
        run_id: approval.run_id, action_id: approval.action_id, text: text.trim()
      })
      if (result.ok !== true) throw new Error('修改要求未被接受，请核对当前审批状态。')
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) {
        setApprovalProgress(null)
        setMessageBusinessId(requestBusinessId)
        setThinkingRun({ sessionId: requestSessionId, runId: result.run_id || '' })
        setConversationOpen(true)
        setTab('execution')
        await reloadCurrent().catch((reason) => setError(messageForError(reason)))
      }
    } catch (reason) {
      throw new Error(messageForError(reason))
    } finally {
      approvalInFlightRef.current.delete(approvalKey)
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setLoading(false)
    }
  }

  const reconcileApproval = async (approval: Approval) => {
    const requestSessionId = selectedSessionId
    const requestBusinessId = approval.business_id
    const approvalKey = `${requestSessionId}:${requestBusinessId}:${approval.run_id}:${approval.action_id}:reconcile`
    if (approvalInFlightRef.current.has(approvalKey)) return
    approvalInFlightRef.current.add(approvalKey)
    setLoading(true)
    try {
      const result = await call<BusinessDetailProjection>('reconcile_action', {
        session_id: requestSessionId,
        business_id: requestBusinessId,
        run_id: approval.run_id,
        action_id: approval.action_id
      })
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) {
        setBusinessDetail(result)
        await loadSession(requestSessionId)
      }
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    } finally {
      approvalInFlightRef.current.delete(approvalKey)
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
      const result = await call<BusinessDetailProjection>('refresh_business', { session_id: requestSessionId, business_id: requestBusinessId })
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
        long_term_memory: current.long_term_memory ? 'on' : 'off',
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
      const updated = await call<Settings>('save_settings', {
        ...settingsDraft,
        long_term_memory: settingsDraft.long_term_memory === 'on'
      })
      setSettings(updated)
      setSettingsDraft((current) => ({ ...current, model_key: '', odoo_key: '' }))
      const currentHealth = await checkConnection(true)
      if (!currentHealth) throw new Error('连接检查失败')
      setNotice('设置已保存，连接状态已刷新。')
      closeSettings()
    } catch (reason) {
      setError(messageForError(reason))
    } finally {
      setSettingsSaving(false)
    }
  }

  const retryHealth = async () => { await checkConnection(true) }

  const exportBusiness = async (runId = selectedRunId) => {
    if (!selectedSessionId || !selectedBusinessId || exporting) return
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    setExporting(true)
    setExportPath('')
    try {
      const result = await call<{ cancelled: boolean; path?: string }>('export_business_report', { session_id: requestSessionId, business_id: requestBusinessId, ...(runId ? { run_id: runId } : {}) })
      if (result.cancelled) return
      if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      setExportPath(result.path || '导出已完成，但主机没有返回文件路径。')
      setNotice(result.path ? '业务回执已导出。' : '业务回执已导出。')
      await loadBusiness(requestSessionId, requestBusinessId).catch(() => undefined)
      window.setTimeout(() => setNotice(''), 2600)
    } catch (reason) {
      setError(messageForError(reason))
    } finally {
      setExporting(false)
    }
  }

  const openOdoo = async () => {
    if (openingOdoo) return
    setOpeningOdoo(true)
    try {
      await call('open_odoo')
      setNotice('已请求在浏览器打开已配置的 Odoo。')
    } catch (reason) {
      if (String(reason).includes('ODOO_NOT_CONFIGURED')) {
        setNotice('请先填写 Odoo 地址与数据库，再打开 Odoo。')
        await openSettings()
      } else setError(messageForError(reason))
    } finally {
      setOpeningOdoo(false)
    }
  }

  const openOdooRecord = async (document: Document) => {
    const recordId = Number(document.id)
    if (!selectedSessionId || !selectedBusinessId || !Number.isInteger(recordId) || recordId <= 0) {
      setError('该单据没有可用的 Odoo 记录 ID。')
      return
    }
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    try {
      await call('open_odoo_record', { session_id: requestSessionId, business_id: requestBusinessId, model: document.model, record_id: recordId })
      if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      setNotice(`已请求打开 ${document.name || document.id}。`)
      window.setTimeout(() => setNotice(''), 2600)
    } catch (reason) {
      setError(messageForError(reason))
    }
  }

  const openArtifact = async (artifact: BusinessArtifact, reveal = false) => {
    if (!selectedSessionId || !selectedBusinessId) return
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    try {
      await call(reveal ? 'reveal_business_artifact' : 'open_business_artifact', {
        session_id: requestSessionId,
        business_id: requestBusinessId,
        artifact_id: artifact.id
      })
      if (sessionIdRef.current !== requestSessionId || businessIdRef.current !== requestBusinessId) return
      setNotice(reveal ? `已打开文件所在位置：${artifact.name}` : `已请求打开文件：${artifact.name}`)
      window.setTimeout(() => setNotice(''), 2600)
    } catch (reason) {
      if (sessionIdRef.current === requestSessionId && businessIdRef.current === requestBusinessId) setError(messageForError(reason))
    }
  }

  const openTraceTarget = (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => {
    const runId = target.kind === 'readback' ? businessDetail?.business.readback?.latest_run_id : target.run_id
    setTraceTarget({ runId, toolId: target.tool_id, actionId: target.action_id, kind: target.kind })
    if (runId) setSelectedRunId(runId)
    setTab('trace')
  }

  const resizeBusiness = (event: ReactPointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const move = (moveEvent: PointerEvent) => {
      const maxWidth = Math.max(360, window.innerWidth - 240 - 420 - 5)
      const width = Math.max(360, Math.min(700, maxWidth, window.innerWidth - moveEvent.clientX))
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
  const pendingProposalMessage = [...visibleMessages].reverse().find((message) => message.proposal?.status === 'pending')
  const pendingProposal = pendingProposalMessage?.proposal
  const proposalRun = conversationRunsRef.current.find((run) => run.id === pendingProposalMessage?.run_id || (pendingProposal && run.proposal_ids?.includes(pendingProposal.id)))
  const proposalBusy = Boolean(proposalRun && ['running', 'cancel_requested'].includes(proposalRun.status))
  const latestUser = [...visibleMessages].reverse().find((message) => message.role === 'user')
  const newerProposal = proposalRun?.proposal_ids?.length ? proposalRun.proposal_ids.at(-1) !== pendingProposal?.id
    : Boolean(pendingProposalMessage?.run_id && visibleMessages.slice(visibleMessages.indexOf(pendingProposalMessage) + 1).some((message) => message.run_id === pendingProposalMessage.run_id && message.proposal))
  const proposalUnavailable = newerProposal ? '该提案已被更新，请使用最新业务说明。'
    : proposalRun && !['completed', 'running', 'cancel_requested'].includes(proposalRun.status) ? '本轮回复未完成，请重新说明业务要求。'
    : pendingProposal?.source_messages?.length && latestUser && !pendingProposal.source_messages.some((message) => message.id === latestUser.id)
      ? '已有新的业务要求，请等待更新后的提案。' : ''
  return {
    sessions,
    session,
    selectedSessionId,
    selectedBusinessId,
    businessDetail,
    trace,
    traceTarget,
    setTraceTarget,
    selectedRunId,
    setSelectedRunId,
    tab,
    setTab,
    draft,
    setDraft,
    loading,
    businessLoading,
    traceLoading,
    loadTraceDetail,
    connection,
    health,
    error,
    setError,
    liveMessages,
    thinkingRun,
    settings,
    settingsOpen,
    connectionDetailsOpen,
    setConnectionDetailsOpen,
    archiveTarget,
    setArchiveTarget,
    settingsDraft,
    setSettingsDraft,
    settingsSaving,
    notice,
    setNotice,
    blockedSend,
    setBlockedSend,
    renamingId,
    setRenamingId,
    messageBusinessId,
    setMessageBusinessId,
    sessionQuery,
    setSessionQuery,
    businessWidth,
    setBusinessWidth,
    conversationOpen,
    railCollapsed,
    setRailCollapsed,
    exporting,
    openingOdoo,
    exportPath,
    pendingMaterials,
    setPendingMaterials,
    materialsBusy,
    documentDownloads,
    selectedDocumentKey,
    setSelectedDocumentKey,
    approvalProgress,
    settingsButtonRef,
    conversationRunsRef,
    checkConnection,
    activeBusiness,
    hasActiveExecution,
    chooseSession,
    importMaterials,
    downloadDocument,
    chooseBusiness,
    toggleConversation,
    createSession,
    archiveSession,
    renameSession,
    sendMessage,
    confirmProposal,
    startRun,
    cancelRun,
    cancelConversation,
    decideApproval,
    requestApprovalRevision,
    reconcileApproval,
    refreshBusiness,
    openSettings,
    closeSettings,
    saveSettings,
    retryHealth,
    exportBusiness,
    openOdooRecord,
    openOdoo,
    openArtifact,
    openTraceTarget,
    resizeBusiness,
    pendingProposal,
    proposalBusy,
    proposalUnavailable
  }
}
