import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { AlertDialog as RadixAlertDialog, Badge as RadixBadge, Button as RadixButton, Dialog as RadixDialog, IconButton as RadixIconButton, Tabs as RadixTabs, Tooltip as RadixTooltip } from '@radix-ui/themes'
import { Activity, Archive, ArrowUpRight, Check as CheckIcon, CircleAlert, CircleCheck, CircleDashed, Clock3, Download, ExternalLink, FilePlus2, FileText, FolderPlus, LoaderCircle, MessageSquare, Minus, PanelLeftClose, PanelLeftOpen, Play, RefreshCw, Search, Send, Settings2, Square, Trash2, Upload, X } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { WorkbenchMethod } from '../shared/protocol'
import {
  Approval,
  BusinessArtifact,
  Business,
  BusinessDetail,
  BusinessDetailProjection,
  BusinessEvidence,
  BusinessTab,
  Check,
  ConversationRun,
  Document,
  HostEvent,
  Health,
  Message,
  LiveMessage,
  Round,
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
type BusinessTypeCode = 'sale_invoice' | 'purchase' | 'sale_purchase_invoice'
type MaterialRecord = { id: string; session_id: string; name: string; size: number; sha256: string; created_at: string; row_count?: number; preview?: string; media_type?: string }
type MessageWithMaterials = Message & { material_ids?: string[] }
type BusinessWithType = Omit<Business, 'type'> & { type: BusinessTypeCode | string; completion_target?: string }
type DetailWithMaterials = BusinessDetailProjection & { materials?: MaterialRecord[] }
type DownloadReceipt = { status: 'downloading' | 'cancelled' | 'completed' | 'failed'; path?: string; artifact?: BusinessArtifact; error?: string }
type ApprovalProgress = { key: string; businessId: string; status: 'submitting' | 'failed'; detail?: string }

const liveMessageKey = (message: Pick<LiveMessage, 'session_id' | 'business_id' | 'run_id' | 'id'>) => `${message.session_id}:${message.business_id ?? '__conversation__'}:${message.run_id}:${message.id}`

const tabs: Array<{ id: BusinessTab; label: string }> = [
  { id: 'execution', label: '执行台' },
  { id: 'documents', label: '单据与文件' },
  { id: 'approvals', label: '变更与审批' },
  { id: 'trace', label: '运行详情' }
]

export default function App() {
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

  const exportBusiness = async () => {
    if (!selectedSessionId || !selectedBusinessId || exporting) return
    const requestSessionId = selectedSessionId
    const requestBusinessId = selectedBusinessId
    setExporting(true)
    setExportPath('')
    try {
      const result = await call<{ cancelled: boolean; path?: string }>('export_business_report', { session_id: requestSessionId, business_id: requestBusinessId, ...(selectedRunId ? { run_id: selectedRunId } : {}) })
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
  const pendingProposal = visibleMessages.find((message) => message.proposal?.status === 'pending')?.proposal

  return (
    <div className="app-shell">
      <header className="window-bar">
        <div className="brand-lockup"><span className="brand-mark"><Activity size={16} strokeWidth={2.5} /></span><span>业务工作台</span><small>Odoo 业务执行</small></div>
        <button className="window-bar-state" onClick={() => setConnectionDetailsOpen(true)} aria-label="查看连接状态"><span className={`connection-dot ${connection}`} />{connectionLabel(connection)}<span className="health-separator">·</span><span className={`odoo-health odoo-${odooHealthStatus(health)}`}>Odoo {healthLabel(odooHealthStatus(health))}</span><ArrowUpRight size={13} /></button>
        <RadixTooltip content="配置模型与 Odoo 连接"><RadixButton ref={settingsButtonRef} className="settings-button" variant="soft" onClick={() => void openSettings()}><Settings2 size={15} />连接设置</RadixButton></RadixTooltip>
        <div className="window-actions">
          <RadixTooltip content="最小化"><RadixIconButton variant="ghost" aria-label="最小化" onClick={() => void window.workbench?.windowControl('minimize')}><Minus size={16} /></RadixIconButton></RadixTooltip>
          <RadixTooltip content="最大化"><RadixIconButton variant="ghost" aria-label="最大化" onClick={() => void window.workbench?.windowControl('maximize')}><span className="window-maximize-glyph" /></RadixIconButton></RadixTooltip>
          <RadixTooltip content="关闭"><RadixIconButton variant="ghost" className="close" aria-label="关闭" onClick={() => void window.workbench?.windowControl('close')}><X size={16} /></RadixIconButton></RadixTooltip>
        </div>
      </header>

      {settingsOpen && <SettingsDialog settings={settings} draft={settingsDraft} saving={settingsSaving} onChange={(key, value) => setSettingsDraft((current) => ({ ...current, [key]: value }))} onClose={closeSettings} onSave={() => void saveSettings()} />}
      <ConnectionDetailsDialog health={health} connection={connection} busy={hasActiveExecution} open={connectionDetailsOpen} onOpenChange={setConnectionDetailsOpen} onRetry={() => void checkConnection(true)} />
      <ArchiveDialog open={Boolean(archiveTarget)} onOpenChange={(open) => { if (!open) setArchiveTarget('') }} onConfirm={() => { const id = archiveTarget; setArchiveTarget(''); void archiveSession(id) }} />

      {error && <div className="global-alert" role="alert"><span>{error}</span>{connection !== 'connected' && <button onClick={() => void retryHealth()}>重试连接</button>}<button onClick={() => setError('')}>关闭</button></div>}
      {notice && <div className="global-notice" role="status"><span>{notice}</span><button onClick={() => setNotice('')}>关闭</button></div>}

      <main className={`workspace-grid ${conversationOpen ? '' : 'conversation-hidden'} ${conversationOpen && !activeBusiness ? 'conversation-focus' : ''} ${railCollapsed ? 'rail-collapsed' : ''}`} style={{ '--business-width': `${businessWidth}px` } as CSSProperties}>
        <SessionRail
          sessions={sessions}
          selectedId={selectedSessionId}
          loading={loading}
          renamingId={renamingId}
          onSelect={chooseSession}
          onCreate={() => void createSession()}
          onArchive={setArchiveTarget}
          onRenameStart={setRenamingId}
          onRename={renameSession}
          query={sessionQuery}
          onQueryChange={setSessionQuery}
          collapsed={railCollapsed}
          onToggle={() => setRailCollapsed((value) => !value)}
        />
        <BusinessWorkspace
          session={session}
          activeBusiness={activeBusiness}
          detail={businessDetail}
          tab={tab}
          trace={trace}
          traceTarget={traceTarget}
          traceLoading={traceLoading}
          businessLoading={businessLoading}
          loading={loading}
          selectedRunId={selectedRunId}
          onBusinessSelect={chooseBusiness}
          onTabChange={setTab}
          onRunSelect={(id) => { setTraceTarget(null); setSelectedRunId(id) }}
          onRefresh={() => void refreshBusiness()}
          onStart={() => void startRun()}
          onCancel={(run) => void cancelRun(run)}
          onApproval={(approval, decision) => void decideApproval(approval, decision)}
          onReconcile={(approval) => void reconcileApproval(approval)}
          onTraceTarget={openTraceTarget}
          onToggleConversation={toggleConversation}
          conversationOpen={conversationOpen}
          onExport={() => void exportBusiness()}
          exporting={exporting}
          exportPath={exportPath}
          onOpenDocument={(document) => void openOdooRecord(document)}
          onDownloadDocument={(document, format) => void downloadDocument(document, format)}
          documentDownloads={documentDownloads}
          selectedDocumentKey={selectedDocumentKey}
          onSelectedDocumentKey={setSelectedDocumentKey}
          onOpenArtifact={(artifact) => void openArtifact(artifact)}
          onRevealArtifact={(artifact) => void openArtifact(artifact, true)}
          approvalProgress={approvalProgress}
          onOpenApprovals={() => setTab('approvals')}
        />
        {conversationOpen && <div className="workspace-divider" role="separator" tabIndex={0} aria-label="调整会话辅助面板宽度" onPointerDown={resizeBusiness} onKeyDown={(event) => { const maxWidth = Math.max(320, window.innerWidth - 240 - 520 - 5); if (event.key === 'ArrowLeft') setBusinessWidth((width) => Math.min(maxWidth, 640, width + 24)); if (event.key === 'ArrowRight') setBusinessWidth((width) => Math.max(320, width - 24)) }} />}
        {conversationOpen && <ConversationPane
          session={session}
          draft={draft}
          liveMessages={liveMessages}
          conversationRuns={conversationRunsRef.current}
          thinkingRun={thinkingRun}
          selectedBusinessId={selectedBusinessId}
          loading={loading}
          pendingProposal={pendingProposal}
          pendingApprovals={(businessDetail?.approvals ?? []).filter(isPendingApproval)}
          approvalBusinessName={activeBusiness?.title || '当前业务'}
          approvalProgress={approvalProgress}
          onOpenApprovals={() => setTab('approvals')}
          onOpenExecution={() => setTab('execution')}
          approvalActivity={businessDetail?.activity}
          businesses={session?.businesses ?? []}
          messageBusinessId={messageBusinessId || '__conversation__'}
          onMessageBusinessChange={setMessageBusinessId}
          onDraftChange={setDraft}
          onSubmit={sendMessage}
          pendingMaterials={pendingMaterials}
          reusedMaterials={(() => {
            const contextId = messageBusinessId === '__conversation__' ? '' : messageBusinessId || selectedBusinessId
            const contextBusiness = session?.businesses.find((business) => business.id === contextId)
            const materialIds = contextBusiness?.material_ids?.length ? contextBusiness.material_ids : (session?.session.pending_material_ids ?? [])
            return (session?.materials ?? []).filter((material) => materialIds.includes(material.id)) as MaterialRecord[]
          })()}
          materialsBusy={materialsBusy}
          onFiles={(files) => void importMaterials(files)}
          onRemoveMaterial={(id) => setPendingMaterials((current) => current.filter((material) => material.id !== id))}
          onStarter={(goal) => setDraft(goal)}
          onProposal={(proposal, confirmed) => void confirmProposal(proposal, confirmed)}
          onCancelConversation={(run) => void cancelConversation(run)}
        />}
      </main>
    </div>
  )
}

type ProposalLike = { id: string; title: string; goal: string; type: string; completion_target?: string; material_ids?: string[]; existing_business_id?: string }

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
  onQueryChange,
  collapsed,
  onToggle
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
  collapsed: boolean
  onToggle: () => void
}) {
  const visibleSessions = sessions.filter((item) => `${item.title} ${item.id}`.toLowerCase().includes(query.trim().toLowerCase()))
  return (
    <aside className={`session-rail ${collapsed ? 'collapsed' : ''}`}>
      <div className="rail-heading"><div><span className="eyebrow">工作区</span><h1>{collapsed ? '会' : '会话'}</h1></div><div className="rail-heading-actions"><RadixTooltip content={collapsed ? '展开会话栏' : '收起会话栏'}><RadixIconButton variant="ghost" aria-label={collapsed ? '展开会话栏' : '收起会话栏'} onClick={onToggle}>{collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}</RadixIconButton></RadixTooltip>{!collapsed && <RadixTooltip content="新建会话"><RadixButton className="new-button" onClick={onCreate} disabled={loading}><FolderPlus size={15} />新建</RadixButton></RadixTooltip>}</div></div>
      {!collapsed && <label className="session-search"><Search size={15} aria-hidden="true" /><span className="sr-only">搜索会话</span><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索会话" aria-label="搜索会话" /></label>}
      {!collapsed && <div className="rail-summary"><span>{query ? `${visibleSessions.length} / ${sessions.length} 个会话` : `${sessions.length} 个活跃会话`}</span><span className="quiet-rule" /></div>}
      <div className="session-list">
        {sessions.length === 0 && !collapsed && <EmptyState title="还没有会话" detail="创建会话后，从一句业务意图开始。" />}
        {sessions.length > 0 && visibleSessions.length === 0 && !collapsed && <EmptyState title="没有匹配会话" detail="换一个名称或会话 ID 试试。" />}
        {visibleSessions.map((item) => (
          <div key={item.id} className={`session-row ${item.id === selectedId ? 'active' : ''}`} title={collapsed ? item.title : undefined}>
            <button className="session-select" aria-label={item.title || '未命名会话'} onClick={() => onSelect(item.id)}>
              <span className={`session-status-dot status-${item.status}`} />
              {!collapsed && <span className="session-copy"><strong>{item.title || '未命名会话'}</strong><small>{formatInstant(item.updated_at)}</small></span>}
            </button>
            {item.id === selectedId && !collapsed && (
              <div className="session-row-actions">
                {renamingId === item.id ? (
                  <input autoFocus defaultValue={item.title} aria-label="会话名称" onKeyDown={(event) => { if (event.key === 'Enter') onRename(item.id, event.currentTarget.value); if (event.key === 'Escape') onRenameStart('') }} />
                ) : <RadixTooltip content="重命名"><RadixIconButton variant="ghost" size="1" aria-label="重命名" onClick={() => onRenameStart(item.id)}><Settings2 size={14} /></RadixIconButton></RadixTooltip>}
                <RadixTooltip content="归档"><RadixIconButton variant="ghost" size="1" aria-label="归档" onClick={() => onArchive(item.id)}><Archive size={14} /></RadixIconButton></RadixTooltip>
              </div>
            )}
          </div>
        ))}
      </div>
      {!collapsed && <div className="rail-footer"><span className="rail-footer-dot" />本地工作区</div>}
    </aside>
  )
}

function ConversationPane({ session, draft, liveMessages, conversationRuns, thinkingRun, selectedBusinessId, loading, pendingProposal, pendingApprovals, approvalBusinessName, approvalProgress, onOpenApprovals, onOpenExecution, approvalActivity, businesses, messageBusinessId, onMessageBusinessChange, onDraftChange, onSubmit, onProposal, onCancelConversation, pendingMaterials, reusedMaterials, materialsBusy, onFiles, onRemoveMaterial, onStarter }: {
  session: SessionDetail | null
  draft: string
  liveMessages: LiveMessage[]
  conversationRuns: ConversationRun[]
  thinkingRun: { sessionId: string; runId: string } | null
  selectedBusinessId: string
  loading: boolean
  pendingProposal?: ProposalLike
  pendingApprovals: Approval[]
  approvalBusinessName: string
  approvalProgress: ApprovalProgress | null
  onOpenApprovals: () => void
  onOpenExecution: () => void
  approvalActivity?: NonNullable<BusinessDetail['activity']>
  businesses: Business[]
  messageBusinessId: string
  onMessageBusinessChange: (id: string) => void
  onDraftChange: (value: string) => void
  onSubmit: (event: FormEvent) => void
  onProposal: (proposal: ProposalLike, confirmed: boolean) => void
  onCancelConversation: (run: ConversationRun) => void
  pendingMaterials: MaterialRecord[]
  reusedMaterials: MaterialRecord[]
  materialsBusy: boolean
  onFiles: (files: File[]) => void
  onRemoveMaterial: (id: string) => void
  onStarter: (goal: string) => void
}) {
  const messages = session?.messages ?? []
  const scrollRef = useRef<HTMLDivElement>(null)
  const [atLatest, setAtLatest] = useState(true)
  const [hasNew, setHasNew] = useState(false)
  const latestConversation = [...conversationRuns].filter((run) => run.business_id == null).sort((left, right) => String(right.started_at || '').localeCompare(String(left.started_at || '')))[0]
  const activeConversation = latestConversation && ['running', 'cancel_requested'].includes(latestConversation.status) ? latestConversation : undefined
  const terminalConversation = latestConversation && ['failed', 'cancelled', 'interrupted'].includes(latestConversation.status) ? latestConversation : undefined
  const visibleLiveMessages = liveMessages.filter((message) => message.session_id === session?.session.id && (message.business_id == null || message.business_id === selectedBusinessId))
  const persistedMessageKeys = new Set(messages.map((message) => `${message.business_id ?? '__conversation__'}:${message.id}`))
  const orderedMessages = [
    ...messages.filter((message) => message.text.trim() && !message.proposal).map((message) => ({ kind: 'message' as const, message, created_at: message.created_at })),
    ...visibleLiveMessages.filter((message) => !persistedMessageKeys.has(`${message.business_id ?? '__conversation__'}:${message.id}`)).map((message) => ({ kind: 'live' as const, message, created_at: message.created_at || '' }))
  ].sort((left, right) => left.created_at.localeCompare(right.created_at))
  const localThinking = Boolean(thinkingRun && thinkingRun.sessionId === session?.session.id && (!thinkingRun.runId || latestConversation?.id === thinkingRun.runId))
  const activePublicText = activeConversation && (
    visibleLiveMessages.some((message) => message.run_id === activeConversation.id && message.text.trim())
    || messages.some((message) => message.role === 'assistant' && message.run_id === activeConversation.id && message.text.trim())
  )
  const localThinkingForRun = Boolean(
    localThinking
    && !(thinkingRun?.runId && latestConversation?.id === thinkingRun.runId && latestConversation.status === 'cancel_requested')
  )
  const showThinking = Boolean(!activePublicText && (localThinkingForRun || activeConversation?.status === 'running'))
  const reusedMaterialIds = new Set(pendingMaterials.map((material) => material.id))
  const inheritedMaterials = reusedMaterials.filter((material) => !reusedMaterialIds.has(material.id))
  const materialSeen = new Set<string>()
  const scrollToLatest = () => {
    const element = scrollRef.current
    if (!element) return
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
    setAtLatest(true)
    setHasNew(false)
  }
  useEffect(() => {
    const element = scrollRef.current
    if (!element || atLatest) {
      if (element) element.scrollTop = element.scrollHeight
      return
    }
    setHasNew(true)
  }, [atLatest, liveMessages, messages, pendingApprovals.length, approvalProgress?.status, showThinking])
  return (
    <section className={`conversation-pane ${terminalConversation ? 'has-run-status' : ''}`}>
      <header className="conversation-header">
        <div><span className="eyebrow">会话</span><h2>{session?.session.title || '选择一个会话'}</h2><p>先和 Agent 讨论目标、能力和范围；确认业务后才会进入执行台。</p></div>
        <div className="conversation-header-meta"><span className="session-id" title={session?.session.id || undefined}>会话详情</span>{activeConversation && <RadixButton className="conversation-cancel" variant="soft" disabled={loading || activeConversation.status === 'cancel_requested'} onClick={() => onCancelConversation(activeConversation)}><Square size={13} />{activeConversation.status === 'cancel_requested' ? '正在停止…' : '停止对话'}</RadixButton>}</div>
      </header>
      {terminalConversation && <div className={`conversation-run-status status-${terminalConversation.status}`} role="status"><strong>{terminalConversation.status === 'failed' ? '对话失败，可继续输入' : terminalConversation.status === 'cancelled' ? '对话已停止，可继续输入' : '对话已中断，可继续输入'}</strong>{(terminalConversation.error || terminalConversation.error_detail) && <details><summary>查看错误详情</summary><code>{terminalConversation.error || terminalConversation.error_detail}</code>{terminalConversation.error_detail && terminalConversation.error_detail !== terminalConversation.error && <p>{terminalConversation.error_detail}</p>}</details>}</div>}
      <div ref={scrollRef} className="conversation-scroll" onScroll={(event) => { const element = event.currentTarget; const latest = element.scrollHeight - element.scrollTop - element.clientHeight < 24; setAtLatest(latest); if (latest) setHasNew(false) }}>
        {!session && <EmptyState title="选择一个会话" detail="左侧会话列表会显示已持久化的工作。" />}
        {session && orderedMessages.length === 0 && visibleLiveMessages.length === 0 && !pendingProposal && !showThinking && <ConversationWelcome onStarter={onStarter} />}
        {orderedMessages.map((item) => item.kind === 'message' ? (() => { const ids = (item.message as MessageWithMaterials).material_ids ?? []; const inherited = ids.filter((id) => materialSeen.has(id)); ids.forEach((id) => materialSeen.add(id)); return <MessageRow key={`message:${item.message.id}`} message={item.message} inheritedMaterialIds={inherited} /> })() : <article className="message assistant live-message" key={`live:${item.message.run_id}:${item.message.id}`}><span className="avatar agent-avatar">A</span><div><div className="message-meta"><strong>Agent</strong><span>{item.message.status === 'ended' ? '回复完成' : item.message.status === 'interrupted' || item.message.status === 'failed' ? '已停止 · 回复未完成' : '实时回复'}</span></div><MessageText text={item.message.text} collapsible={false} /></div></article>)}
        {showThinking && <article className="message assistant thinking-message" aria-live="polite"><span className="avatar agent-avatar">A</span><div><div className="message-meta"><strong>Agent</strong></div><p className="thinking-copy">正在思考…</p></div></article>}
        {(pendingApprovals.length > 0 || approvalProgress?.businessId === selectedBusinessId || Boolean(selectedBusinessId && approvalActivity && approvalActivity.phase !== 'idle')) && <ApprovalInboxCard approvals={pendingApprovals} businessName={approvalBusinessName} progress={approvalProgress} activity={approvalActivity} onOpenApprovals={onOpenApprovals} onOpenExecution={onOpenExecution} />}
        {pendingProposal && <ProposalCard proposal={pendingProposal} disabled={loading} onDecision={onProposal} />}
        {hasNew && <button className="new-message-indicator" type="button" onClick={scrollToLatest}>有新消息 · 回到最新</button>}
      </div>
      <form className="composer" onSubmit={onSubmit} onDragOver={(event) => { event.preventDefault(); event.currentTarget.classList.add('drop-active') }} onDragLeave={(event) => event.currentTarget.classList.remove('drop-active')} onDrop={(event) => { event.preventDefault(); event.currentTarget.classList.remove('drop-active'); onFiles(Array.from(event.dataTransfer.files)) }}>
        <MaterialTray materials={pendingMaterials} busy={materialsBusy} onRemove={onRemoveMaterial} onFiles={onFiles} />
        {inheritedMaterials.length > 0 && <MaterialReuseTray materials={inheritedMaterials} hasNewMaterials={pendingMaterials.length > 0} />}
        <textarea value={draft} onChange={(event) => onDraftChange(event.target.value)} disabled={!session || loading} placeholder={session ? '和 Agent 讨论目标、能力或业务范围…' : '先选择或创建一个会话'} aria-label="会话消息" />
        <div className="composer-footer">
          <div className="composer-context"><label htmlFor="message-business-target">讨论范围</label><select id="message-business-target" value={messageBusinessId} onChange={(event) => onMessageBusinessChange(event.target.value)} disabled={!session || loading}><option value="__conversation__">整个会话（普通讨论）</option>{businesses.map((business) => <option key={business.id} value={business.id}>{business.title || '未命名业务'} · {labelFor(businessStatusLabel, business.status)}</option>)}</select><span>普通发送只会话，不会自动开始业务执行。</span></div>
        <div className="composer-actions"><label className="material-picker"><FilePlus2 size={15} />添加材料<input type="file" accept=".csv,.txt,text/csv,text/plain" multiple disabled={!session || loading || materialsBusy} onChange={(event) => { onFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} /></label><RadixButton type="submit" disabled={!session || loading || materialsBusy || (!draft.trim() && !pendingMaterials.length)}>{loading ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}{loading ? '处理中…' : '发送'}</RadixButton></div>
        </div>
      </form>
    </section>
  )
}

function MessageText({ text, collapsible = true }: { text: string; collapsible?: boolean }) {
  const value = text || '（空消息）'
  const content = <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt || '图片'}</span>, a: ({ children }) => <span>{children}</span>, table: ({ children }) => <div className="message-table-wrap"><table>{children}</table></div> }}>{value}</Markdown>
  if (value.length <= 900 || !collapsible) return <div className="message-text message-markdown">{content}</div>
  return <div><MessageText text={`${value.slice(0, 360)}…`} collapsible={false} /><details className="message-full"><summary>查看完整消息（{value.length.toLocaleString('zh-CN')} 字）</summary><div className="message-text message-markdown">{content}</div></details></div>
}

function MessageRow({ message, inheritedMaterialIds = [] }: { message: Message; inheritedMaterialIds?: string[] }) {
  const role = message.role === 'user' ? 'user' : message.role === 'system' ? 'system' : 'assistant'
  const materialIds = (message as MessageWithMaterials).material_ids ?? []
  const isProposalEnvelope = Boolean(message.proposal)
  return <article className={`message ${role}`}><span className={`avatar ${role === 'user' ? 'user-avatar' : role === 'system' ? 'system-avatar' : 'agent-avatar'}`}>{role === 'user' ? '你' : role === 'system' ? '·' : 'A'}</span><div><div className="message-meta">{role !== 'user' && <strong>{role === 'system' ? '系统' : 'Agent'}</strong>}<span>{formatInstant(message.created_at)}</span></div>{!isProposalEnvelope && <MessageText text={message.text} />}{materialIds.length > 0 && <span className="message-materials"><FileText size={12} />{inheritedMaterialIds.length === materialIds.length ? `沿用 ${materialIds.length} 个业务材料` : inheritedMaterialIds.length > 0 ? `新附 ${materialIds.length - inheritedMaterialIds.length} 个，沿用 ${inheritedMaterialIds.length} 个材料` : `已附 ${materialIds.length} 个业务材料`}</span>}</div></article>
}

function ConversationWelcome({ onStarter }: { onStarter: (goal: string) => void }) {
  const starters = [
    { type: 'sale_invoice', title: '销售与开票', detail: '整理已有客户的销售订单，确认后继续开票。', goal: '我想为已有客户整理销售订单，确认后继续开票。' },
    { type: 'purchase', title: '采购', detail: '先梳理采购需求与供应商信息，再确认执行范围。', goal: '我想整理一笔采购需求，请先告诉我需要补充哪些信息。' },
    { type: 'sale_purchase_invoice', title: '销售 → 采购 → 开票', detail: '把销售需求、采购环节和开票关系放在同一条业务链里。', goal: '我想梳理销售、采购到开票的完整业务链，请先说明需要补充的信息。' }
  ]
  return <section className="conversation-welcome" aria-label="开始一个业务讨论"><div className="welcome-kicker">从一句自然语言开始</div><h3>你想先处理哪类业务？</h3><p>选择一个入口只会把目标填入输入框；发送后由 Agent 先澄清范围，不会自动写入 Odoo。</p><div className="welcome-actions">{starters.map((starter) => <button key={starter.type} type="button" className="welcome-card" onClick={() => onStarter(starter.goal)}><span className="welcome-card-title">{starter.title}</span><span>{starter.detail}</span><small>仅预填目标</small></button>)}</div></section>
}

function MaterialTray({ materials, busy, onRemove, onFiles }: { materials: MaterialRecord[]; busy: boolean; onRemove: (id: string) => void; onFiles: (files: File[]) => void }) {
  if (!materials.length && !busy) return <div className="material-drop-hint"><Upload size={14} />拖入 CSV/TXT，或点击“添加材料”（每个文件 ≤ 2 MiB，最多 3 个）</div>
  return <div className="material-tray" aria-label="本次消息材料"><div className="material-tray-head"><span><FileText size={14} />本次消息材料</span>{busy && <span className="material-uploading"><LoaderCircle className="spin" size={13} />正在上传与解析…</span>}</div>{materials.map((material) => <div className="material-chip" key={material.id}><div><strong>{material.name}</strong><span>{materialRowLabel(material)} · {material.preview || '暂无预览'}</span></div><button type="button" aria-label={`移除 ${material.name}`} onClick={() => onRemove(material.id)}><Trash2 size={14} /></button></div>)}{!busy && materials.length < 3 && <label className="material-inline-drop">继续添加<input type="file" accept=".csv,.txt,text/csv,text/plain" multiple onChange={(event) => { onFiles(Array.from(event.currentTarget.files ?? [])); event.currentTarget.value = '' }} /></label>}</div>
}

function MaterialReuseTray({ materials, hasNewMaterials }: { materials: MaterialRecord[]; hasNewMaterials: boolean }) {
  return <details className="material-reuse-tray"><summary><span><FileText size={14} />沿用历史材料</span><small>{materials.length} 个文件 · {hasNewMaterials ? '本轮不附加' : '发送时按业务上下文沿用'}</small></summary><div className="material-reuse-list">{materials.map((material) => <div className="material-reuse-row" key={material.id}><strong>{material.name}</strong><span>{materialRowLabel(material)} · {material.preview || '暂无预览'}</span></div>)}</div></details>
}

function ApprovalInboxCard({ approvals, businessName, progress, activity, onOpenApprovals, onOpenExecution }: { approvals: Approval[]; businessName: string; progress: ApprovalProgress | null; activity?: NonNullable<BusinessDetail['activity']>; onOpenApprovals: () => void; onOpenExecution: () => void }) {
  const activeProgress = Boolean(progress && (approvals.length === 0 || approvals.some((approval) => approval.business_id === progress.businessId)))
  const summary = approvals.length ? `${approvals.slice(0, 2).map((approval) => readableApprovalTitle(approval)).join('、')}${approvals.length > 2 ? ` 等 ${approvals.length} 项` : ''}` : activity ? `${activity.label || '当前执行状态'}：${activity.detail || '正在读取最新状态。'}` : '正在读取最新执行状态。'
  return <section className="approval-inbox-card" role="status"><div className="approval-inbox-icon"><Clock3 size={18} /></div><div className="approval-inbox-copy"><div className="approval-inbox-kicker">{approvals.length ? `需要人工审批 · ${businessName}` : `业务执行状态 · ${businessName}`}</div><strong>{approvals.length ? `${approvals.length} 项业务动作等待确认` : (activity?.label || '业务执行状态')}</strong><span>{summary}</span>{activeProgress && <small>{progress?.status === 'submitting' ? '正在提交审批决定…' : (progress?.detail || '审批状态未生效，请查看执行详情。')}</small>}</div><RadixButton className="primary-button approval-inbox-button" onClick={approvals.length ? onOpenApprovals : onOpenExecution}>{approvals.length ? <><CheckIcon size={15} />查看并审批</> : '查看执行状态'}</RadixButton></section>
}

function ProposalCard({ proposal, disabled, onDecision }: { proposal: ProposalLike; disabled: boolean; onDecision: (proposal: ProposalLike, confirmed: boolean) => void }) {
  const continuesBusiness = Boolean(proposal.existing_business_id)
  return <section className="proposal-card"><div className="proposal-icon"><FolderPlus size={18} /></div><div className="proposal-kicker">{continuesBusiness ? '延续当前业务' : '发现新的业务意图'} · {businessTypeMeta(proposal.type).title}</div><h3>{proposal.title}</h3><p>{proposal.goal}</p><div className="proposal-target">完成目标：{completionTargetLabel(proposal.completion_target, proposal.type)}</div><div className="proposal-actions"><RadixButton className="secondary-button" variant="soft" disabled={disabled} onClick={() => onDecision(proposal, false)}>{continuesBusiness ? '暂不更新' : '暂不创建'}</RadixButton><RadixButton className="primary-button" disabled={disabled} onClick={() => onDecision(proposal, true)}><FolderPlus size={15} />{continuesBusiness ? '更新业务目标' : '创建业务工作区'}</RadixButton></div></section>
}

function BusinessWorkspace({ session, activeBusiness, detail, tab, trace, traceTarget, traceLoading, businessLoading, loading, selectedRunId, onBusinessSelect, onTabChange, onRunSelect, onRefresh, onStart, onCancel, onApproval, onReconcile, onTraceTarget, onToggleConversation, conversationOpen, onExport, exporting, exportPath, onOpenDocument, onDownloadDocument, documentDownloads, selectedDocumentKey, onSelectedDocumentKey, onOpenArtifact, onRevealArtifact, approvalProgress, onOpenApprovals }: {
  session: SessionDetail | null
  activeBusiness: Business | null
  detail: BusinessDetailProjection | null
  tab: BusinessTab
  trace: TraceBundle | null
  traceTarget: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null
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
  onReconcile: (approval: Approval) => void
  onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void
  onToggleConversation: () => void
  conversationOpen: boolean
  onExport: () => void
  exporting: boolean
  exportPath: string
  onOpenDocument: (document: Document) => void
  onDownloadDocument: (document: Document, format: 'pdf' | 'csv') => void
  documentDownloads: Record<string, DownloadReceipt>
  selectedDocumentKey: string
  onSelectedDocumentKey: (key: string) => void
  onOpenArtifact: (artifact: BusinessArtifact) => void
  onRevealArtifact: (artifact: BusinessArtifact) => void
  approvalProgress: ApprovalProgress | null
  onOpenApprovals: () => void
}) {
  const businessList = session?.businesses ?? []
  const businessInfo = activeBusiness as BusinessWithType | null
  const activeRun = detail?.runs?.find((run) => run.id === detail.business.active_run_id)
    ?? detail?.runs?.find((run) => ['running', 'awaiting_approval', 'cancel_requested'].includes(run.status))
    ?? detail?.runs?.[0]
  const pendingApprovals = detail?.approvals?.filter(isPendingApproval) ?? []
  return (
    <main className="business-workspace">
      <div className="business-tabs-bar">
        <div className="business-tabs-heading"><div><span className="eyebrow">业务工作区</span><strong>{businessList.length ? `${businessList.length} 个业务` : '业务页'}</strong></div><RadixButton className="conversation-toggle" variant="soft" aria-label={conversationOpen ? '收起会话' : '打开会话'} onClick={onToggleConversation}><MessageSquare size={15} />{conversationOpen ? '收起会话' : '打开会话'}</RadixButton></div>
        <div className="business-tabs" role="tablist" aria-label="业务工作区">
          {businessList.map((business) => <button role="tab" aria-selected={business.id === activeBusiness?.id} key={business.id} className={business.id === activeBusiness?.id ? 'active' : ''} onClick={() => onBusinessSelect(business.id)}>{business.title || businessTypeMeta(business.type).title}<span>{businessTypeMeta(business.type).short} · {labelFor(businessStatusLabel, business.status)}</span></button>)}
        </div>
      </div>
      {!activeBusiness && <EmptyState title="等待业务工作区" detail="在会话中确认一个业务意图后，这里会打开对应工作区。" />}
      {activeBusiness && <>
        <header className="business-header"><div><span className="eyebrow">{businessTypeMeta(activeBusiness.type).title}</span><h2>{activeBusiness.title}</h2><p tabIndex={0} aria-label="业务目标">{compactGoal(activeBusiness.goal, detail?.documents ?? [])}</p><div className="business-target-line"><span>完成目标</span><strong>{completionTargetLabel(businessInfo?.completion_target, activeBusiness.type)}</strong></div><details className="goal-details"><summary>查看原始指令</summary><p>{activeBusiness.goal || '未知'}</p></details></div><StatusBadge status={activeBusiness.status} label={labelFor(businessStatusLabel, activeBusiness.status)} /></header>
        {approvalProgress && approvalProgress.businessId === activeBusiness.id && <div className={`approval-progress approval-progress-${approvalProgress.status}`} role="status"><LoaderCircle className={approvalProgress.status === 'submitting' ? 'spin' : ''} size={15} /><span>{approvalProgress.status === 'submitting' ? '正在提交审批决定…' : (approvalProgress.detail || '审批状态未生效，请查看执行详情。')}</span></div>}
        <RadixTabs.Root className="business-tabs-root" value={tab} onValueChange={(value) => onTabChange(value as BusinessTab)}>
          <RadixTabs.List className="business-page-tabs" aria-label="业务页面">
            {tabs.map((item) => <RadixTabs.Trigger key={item.id} value={item.id}>{item.label}{item.id === 'approvals' && detail?.approvals?.filter(isPendingApproval).length ? <b>{detail.approvals.filter(isPendingApproval).length}</b> : null}</RadixTabs.Trigger>)}
          </RadixTabs.List>
          <div className="business-content">
          {businessLoading && <div className="loading-line"><LoaderCircle className="spin" size={16} />正在读取业务状态…</div>}
          <RadixTabs.Content value="execution">{!businessLoading && <ExecutionPage detail={detail} activeRun={activeRun} pendingApprovals={pendingApprovals} onOpenApprovals={onOpenApprovals} onRefresh={onRefresh} onStart={onStart} onCancel={onCancel} onEvidence={onTraceTarget} />}</RadixTabs.Content>
           <RadixTabs.Content value="documents">{!businessLoading && <DocumentsPage documents={detail?.documents ?? []} materials={(detail as DetailWithMaterials | null)?.materials ?? []} artifacts={detail?.artifacts ?? []} goal={activeBusiness.goal} stale={detail?.stale ?? false} onExport={onExport} exporting={exporting} exportPath={exportPath} onOpenDocument={onOpenDocument} onDownloadDocument={onDownloadDocument} documentDownloads={documentDownloads} selectedDocumentKey={selectedDocumentKey} onSelectedDocumentKey={onSelectedDocumentKey} onOpenArtifact={onOpenArtifact} onRevealArtifact={onRevealArtifact} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="approvals">{!businessLoading && <ApprovalsPage approvals={detail?.approvals ?? []} documents={detail?.documents ?? []} disabled={loading || businessLoading} onDecision={onApproval} onReconcile={onReconcile} onTraceTarget={onTraceTarget} />}</RadixTabs.Content>
          <RadixTabs.Content value="trace">{!businessLoading && <TracePage trace={trace} runs={detail?.runs ?? []} readback={detail?.business.readback} selectedRunId={selectedRunId} loading={traceLoading} target={traceTarget} onRunSelect={onRunSelect} />}</RadixTabs.Content>
          </div>
        </RadixTabs.Root>
      </>}
    </main>
  )
}

function ExecutionPage({ detail, activeRun, pendingApprovals, onOpenApprovals, onRefresh, onStart, onCancel, onEvidence }: { detail: BusinessDetailProjection | null; activeRun?: Run; pendingApprovals: Approval[]; onOpenApprovals: () => void; onRefresh: () => void; onStart: () => void; onCancel: (run: Run) => void; onEvidence: (evidence: BusinessEvidence) => void }) {
  const hasUnknownWrite = Boolean(detail?.approvals?.some((approval) => approval.status === 'needs_reconciliation') || detail?.runs?.some((run) => run.status === 'needs_reconciliation') || detail?.business.status === 'blocked')
  const canStart = !hasUnknownWrite && (!activeRun || !['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))
  const runActionLabel = activeRun?.status === 'completed' ? '继续执行' : activeRun?.status === 'failed' ? '重新执行' : '开始执行'
  return (
    <div className="page-stack">
      <div className="action-row">
        <RadixButton className="secondary-button" variant="soft" disabled={Boolean(activeRun && ['running', 'awaiting_approval', 'cancel_requested'].includes(activeRun.status))} title="读取 Odoo 最新状态不会重复写入" onClick={onRefresh}><RefreshCw size={15} />读取最新状态</RadixButton>
        {activeRun && ['running', 'awaiting_approval'].includes(activeRun.status)
          ? <RadixButton className="danger-button" variant="soft" onClick={() => onCancel(activeRun)}><Square size={14} />取消运行</RadixButton>
          : <RadixButton className="primary-button" disabled={!canStart} title={hasUnknownWrite ? '存在待核对写入，请先在变更与审批中核对' : undefined} onClick={onStart}><Play size={15} />{hasUnknownWrite ? '先核对写入' : runActionLabel}</RadixButton>}
      </div>
      {pendingApprovals.length > 0 && <section className="approval-execution-cta" role="status"><div><strong>运行已暂停，等待人工审批</strong><span>{pendingApprovals.length} 项动作需要确认后才会继续。</span></div><RadixButton className="primary-button" onClick={onOpenApprovals}><CheckIcon size={15} />查看并审批</RadixButton></section>}
      {detail?.activity && <ActivityCard activity={detail.activity} />}
      <ExecutionStages execution={detail?.execution} runStatus={activeRun?.status} onEvidence={onEvidence} />
      <BusinessFacts documents={detail?.documents ?? []} />
      <section className="outcome-summary">
        <div className="section-heading"><div><span className="eyebrow">业务结果</span><h3>{detail?.outcome?.label || '结果状态未知'}</h3></div><span>{outcomeStatusLabel(detail?.outcome?.status)}</span></div>
        <p>{detail?.outcome?.detail || '主机尚未提供业务结果说明。'}</p>
        {detail?.outcome?.scope && <small>范围：{outcomeScopeLabel(detail.outcome.scope)}</small>}
      </section>
      <details className="verification-fold"><summary>查看独立回读核验 · {detail?.checks?.length ?? 0} 项</summary><VerificationPage checks={detail?.checks ?? []} observedAt={detail?.observed_at} stale={detail?.stale ?? false} /></details>
      <details className="lower-facts"><summary>查看状态与最近运行</summary><section className="status-table-section">
        <div className="section-heading">
          <div><span className="eyebrow">状态与回执</span><h3>业务状态</h3></div>
          <span>{detail?.stale ? '数据可能已过期' : `观测于 ${formatInstant(detail?.observed_at)}`}</span>
        </div>
        <div className="fact-table">
          <div><span>工作区状态</span><strong>{labelFor(businessStatusLabel, detail?.business.status)}</strong></div>
          <div><span>当前运行</span><strong>{activeRun ? runDisplayLabel(activeRun.status) : '没有运行'}</strong></div>
          <div><span>回读核验</span><strong>{labelFor({passed: '通过', failed: '未通过', unknown: '未知'}, activeRun?.verification_status)}</strong></div>
          <div><span>最近摘要</span><strong>{detail?.summary || '暂无主机摘要'}</strong></div>
        </div>
      </section><section className="run-summary">
        <div className="section-heading">
          <div><span className="eyebrow">执行记录</span><h3>最近运行</h3></div>
          <span>{detail?.runs?.length ?? 0} 次</span>
        </div>
        {detail?.runs?.length
          ? detail.runs.slice(0, 4).map((run) => <RunRow key={run.id} run={run} />)
          : <EmptyState title="还没有运行" detail="读取状态不会触发模型；点击开始执行才会创建运行。" />}
      </section></details>
    </div>
  )
}

function ExecutionStages({ execution, runStatus, onEvidence }: { execution?: BusinessDetailProjection['execution']; runStatus?: string; onEvidence: (evidence: BusinessEvidence) => void }) {
  const stages = execution?.stages ?? []
  const [selectedStageId, setSelectedStageId] = useState('')
  useEffect(() => { if (!selectedStageId || !stages.some((stage) => stage.id === selectedStageId)) setSelectedStageId(execution?.current_stage_id || stages[0]?.id || '') }, [execution?.current_stage_id, selectedStageId, stages])
  const selectedStage = stages.find((stage) => stage.id === selectedStageId) ?? stages[0]
  const stageCaption = runStatus === 'completed' ? '本轮已结束 · 可查看各阶段证据' : execution?.current_stage_id ? `当前：${stageLabel(execution.current_stage_id)}` : '当前阶段未知'
  if (!stages.length) return <section className="stage-panel"><div className="section-heading"><div><span className="eyebrow">执行阶段</span><h3>阶段状态未知</h3></div><span>主机尚未提供阶段计划</span></div><p className="muted">等待业务执行投影。</p></section>
  return <section className="stage-panel"><div className="section-heading"><div><span className="eyebrow">执行阶段</span><h3>当前业务进度</h3></div><span>{stageCaption}</span></div><div className="stage-layout"><nav className="stage-list" aria-label="业务执行阶段">{stages.map((stage) => <button className={`stage-row stage-${stage.status || 'unknown'} ${stage.id === execution?.current_stage_id ? 'current' : ''} ${stage.id === selectedStage?.id ? 'selected' : ''}`} type="button" key={stage.id} onClick={() => setSelectedStageId(stage.id)}><span className="stage-number" aria-hidden="true">{stage.id === execution?.current_stage_id ? '●' : '○'}</span><span><strong>{stage.label || stageLabel(stage.id)}</strong><small>{stageStatusLabel(stage.status)}</small></span></button>)}</nav><article className="stage-detail"><div className="stage-row-head"><strong>{selectedStage?.label || stageLabel(selectedStage?.id)}</strong><span>{stageStatusLabel(selectedStage?.status)}</span></div><p>{selectedStage?.detail || '没有阶段详情。'}</p>{selectedStage?.evidence?.length ? <div className="evidence-list">{selectedStage.evidence.map((evidence, index) => { const kind = (evidence as BusinessEvidence & { kind?: string }).kind; return <button className="evidence-link" type="button" key={`${evidence.run_id || 'run'}:${evidence.tool_id || index}`} onClick={() => onEvidence(evidence)}>{kind === 'readback' ? '查看独立回读快照' : (evidence.label || '查看执行证据')} · {evidence.observed_at ? formatInstant(evidence.observed_at) : '未知时间'}</button> })}</div> : <span className="muted">暂无关联证据</span>}</article></div></section>
}

function BusinessFacts({ documents }: { documents: Document[] }) {
  const currentDocuments = documents.filter((document) => !document.is_reference && document.document_scope !== 'reference')
  const orders = currentDocuments.filter((document) => document.model === 'sale.order')
  const purchaseOrders = currentDocuments.filter((document) => document.model === 'purchase.order')
  const invoices = currentDocuments.filter((document) => document.model === 'account.move')
  const pickings = currentDocuments.filter((document) => document.model === 'stock.picking')
  const fact = (document: Document | undefined, keys: string[]) => {
    if (!document) return '未观测'
    const value = keys.map((key) => key === 'state' ? document.state : document.fields[key]).find((candidate) => candidate !== undefined && candidate !== null && candidate !== '')
    return readableValue(value)
  }
  return (
    <section className="business-facts">
      <div className="section-heading"><div><span className="eyebrow">业务记录</span><h3>业务关键事实</h3></div><span>来自已观测单据</span></div>
      {orders.length === 0 && purchaseOrders.length === 0 && invoices.length === 0 && <div className="facts-empty"><FileText size={16} /><span>尚未观察到销售订单、采购订单或发票</span></div>}
      {orders.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>销售订单</strong><span>{orders.length} 张</span></div>{orders.map((order) => <div className="business-facts-grid" key={`order:${order.id}`}><div><span>订单</span><strong>{order.name || order.id}</strong></div><div><span>客户</span><strong>{fact(order, ['partner_name', 'customer', 'partner_id'])}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(order, ['amount_total', 'total']), fact(order, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>状态</span><strong>{documentStateLabel(order.model, order.state)} · 开票 {invoiceStatusLabel(fact(order, ['invoice_status']))}</strong></div></div>)}</div>}
      {purchaseOrders.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>采购订单</strong><span>{purchaseOrders.length} 张</span></div>{purchaseOrders.map((order) => <div className="business-facts-grid" key={`purchase:${order.id}`}><div><span>采购单</span><strong>{order.name || order.id}</strong></div><div><span>供应商</span><strong>{fact(order, ['partner_name', 'vendor', 'partner_id'])}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(order, ['amount_total', 'total']), fact(order, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>状态</span><strong>{documentStateLabel(order.model, order.state)}</strong></div></div>)}</div>}
      {invoices.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>客户发票</strong><span>{invoices.length} 张</span></div>{invoices.map((invoice) => <div className="business-facts-grid" key={`invoice:${invoice.id}`}><div><span>发票</span><strong>{invoice.name || invoice.id}</strong></div><div><span>状态</span><strong>{documentStateLabel(invoice.model, invoice.state)}</strong></div><div><span>金额</span><strong>{amountWithCurrency(fact(invoice, ['amount_total', 'total']), fact(invoice, ['currency', 'currency_name', 'currency_id']))}</strong></div><div><span>未付余额</span><strong>{fact(invoice, ['amount_residual', 'residual'])}</strong></div><div><span>付款状态</span><strong>{paymentStatusLabel(fact(invoice, ['payment_state']))}</strong></div></div>)}</div>}
      {pickings.length > 0 && <div className="record-fact-block"><div className="record-fact-heading"><strong>出库状态</strong><span>{pickings.length} 张</span></div>{pickings.map((picking) => <div className="business-facts-grid" key={`picking:${picking.id}`}><div><span>出库单</span><strong>{picking.name || picking.id}</strong></div><div><span>状态</span><strong>{documentStateLabel(picking.model, picking.state)}</strong></div></div>)}</div>}
    </section>
  )
}

function ActivityCard({ activity }: { activity: NonNullable<BusinessDetail['activity']> }) {
  const phase = activity.phase || 'unknown'
  const moving = ['model', 'tool', 'cancelling'].includes(phase)
  const intent = (activity as NonNullable<BusinessDetail['activity']> & { intent?: string }).intent?.trim()
  const intentPreview = intent && intent.length > 220 ? `${intent.slice(0, 217)}…` : intent
  return <section key={phase} className={`activity-card activity-phase-${phase}`} aria-label="当前动作">
    <div className="activity-icon">{moving ? <LoaderCircle className="spin" size={17} /> : phase === 'approval' ? <Clock3 size={17} /> : <Activity size={17} />}</div>
    <div className="activity-copy"><span className="eyebrow">当前动作</span><strong>{activity.label || '读取状态中'}</strong><p>{activity.detail || '暂无动作详情'}</p>{intent && <div className="activity-intent"><span>Agent 当前说明</span><MessageText text={intentPreview || ''} collapsible={false} />{intent.length > 220 && <details><summary>查看完整说明</summary><MessageText text={intent} collapsible={false} /></details>}</div>}<div className="activity-meta"><span>{activityPhaseLabel(activity.phase)}</span>{activity.tool_name && <span title={activity.tool_name}>{toolLabel(activity.tool_name)}</span>}{activity.round != null && <span>第 {activity.round} 轮</span>}{activity.tool_count != null && <span>{activity.tool_count} 个工具</span>}{activity.model_rounds != null && <span>{activity.model_rounds} 轮模型</span>}{activity.at && <span>{formatInstant(activity.at)}</span>}</div></div>
  </section>
}

function StatusBadge({ status, label }: { status?: string; label: string }) {
  const neutral = label === '—（不适用）'
  const icon = neutral ? <Minus size={13} /> : status === 'running' || status === 'awaiting_approval' || status === 'pending' || status === 'pending_approval'
    ? <Clock3 size={13} />
    : status === 'failed' || status === 'rejected' || status === 'expired' || status === 'known_failed'
      ? <CircleAlert size={13} />
    : status === 'passed' || status === 'verified' || status === 'approved'
        ? <CircleCheck size={13} />
        : <CircleDashed size={13} />
  return <RadixBadge className={`state-badge ${neutral ? 'state-neutral' : `state-${status || 'unknown'}`}`} variant="soft">{icon}{label}</RadixBadge>
}

function RunRow({ run }: { run: Run }) { return <div className="run-row"><div><strong>{run.id}</strong><span>{formatInstant(run.started_at)} · {formatDuration(run.elapsed_seconds)}</span></div><div className="run-row-meta"><StatusBadge status={run.status} label={runDisplayLabel(run.status)} /><span>{formatCount(run.tool_count)} 工具</span></div></div> }

function DocumentsPage({ documents, materials, artifacts, goal, stale, onExport, exporting, exportPath, onOpenDocument, onDownloadDocument, documentDownloads, selectedDocumentKey, onSelectedDocumentKey, onOpenArtifact, onRevealArtifact, onTraceTarget }: { documents: Document[]; materials: MaterialRecord[]; artifacts: BusinessArtifact[]; goal?: string; stale: boolean; onExport: () => void; exporting: boolean; exportPath: string; onOpenDocument: (document: Document) => void; onDownloadDocument: (document: Document, format: 'pdf' | 'csv') => void; documentDownloads: Record<string, DownloadReceipt>; selectedDocumentKey: string; onSelectedDocumentKey: (key: string) => void; onOpenArtifact: (artifact: BusinessArtifact) => void; onRevealArtifact: (artifact: BusinessArtifact) => void; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const selectedKey = selectedDocumentKey
  useEffect(() => { const current = documents.filter((document) => !isReferenceDocument(document)); const preferred = current.find(isDownloadableDocument) ?? current[0] ?? documents[0]; const selected = documents.find((document) => documentKey(document) === selectedKey); if (!preferred) { if (selectedKey) onSelectedDocumentKey(''); return } if (!selected) onSelectedDocumentKey(documentKey(preferred)) }, [documents, onSelectedDocumentKey, selectedKey])
  const selected = documents.find((document) => documentKey(document) === selectedKey)
  const resourceGroups = [
    { label: '业务单据', items: documents.filter((document) => !isReferenceDocument(document) && ['sale.order', 'purchase.order', 'account.move', 'stock.picking'].includes(document.model)) },
    { label: '往来单位与明细', items: documents.filter((document) => !isReferenceDocument(document) && (document.model === 'res.partner' || document.model.endsWith('.line'))) },
    { label: '参考记录', items: documents.filter((document) => isReferenceDocument(document) || (!['sale.order', 'purchase.order', 'account.move', 'stock.picking', 'res.partner'].includes(document.model) && !document.model.endsWith('.line'))) }
  ].filter((group) => group.items.length > 0)
  return (
    <div className="page-stack">
      <div className="page-intro"><div><span className="eyebrow">已观测记录</span><h3>单据与文件</h3></div><div className="page-actions"><RadixButton className="secondary-button" variant="soft" disabled={exporting} onClick={onExport}>{exporting ? <LoaderCircle className="spin" size={15} /> : <FileText size={15} />}{exporting ? '导出中…' : '导出业务回执'}</RadixButton>{stale && <span className="warning-text">数据可能已过期</span>}</div></div>
      {exportPath && <div className="export-receipt" role="status"><strong>导出路径</strong><span>{exportPath}</span></div>}
      {materials.length > 0 && <section className="material-history" aria-label="业务材料"><div className="section-heading"><div><span className="eyebrow">输入来源</span><h3>业务材料</h3></div><span>{materials.length} 个文件</span></div><div className="material-history-list">{materials.map((material) => <article className="material-history-row" key={material.id}><div><strong>{material.name}</strong><span>{materialRowLabel(material)} · {material.preview || '暂无预览'}</span></div><small>{material.media_type || '文本材料'}</small></article>)}</div></section>}
      <section className="artifact-section" aria-label="业务文件"><div className="section-heading"><div><span className="eyebrow">持久化产物</span><h3>文件与回执</h3></div><span>{artifacts.length} 项</span></div>{artifacts.length === 0 ? <p className="muted">当前业务没有已保存的文件产物。</p> : <div className="artifact-list">{artifacts.map((artifact) => <article className={`artifact-row ${artifact.available === false ? 'artifact-missing' : ''}`} key={artifact.id}><div><strong>{artifact.name}</strong><span>{artifactKindLabel(artifact.kind)} · {formatInstant(artifact.created_at)}{artifact.run_id ? ` · 运行 ${artifact.run_id}` : ''}</span>{artifact.available === false && <small>{artifact.error || '文件不可用'}</small>}</div><div className="artifact-actions"><RadixButton className="inline-action" variant="ghost" disabled={artifact.available === false} onClick={() => onOpenArtifact(artifact)}>打开文件</RadixButton><RadixButton className="inline-action" variant="ghost" disabled={artifact.available === false} onClick={() => onRevealArtifact(artifact)}>显示位置</RadixButton></div></article>)}</div>}</section>
      {documents.length === 0 ? <><details className="goal-details resource-goal"><summary>查看原始目标输入</summary><p>{goal || '未知'}</p></details><EmptyState title="还没有单据回执" detail="单据将在主机完成只读读取后出现在这里。" /></> : <div className="resource-layout"><nav className="resource-list" aria-label="已观测单据">{resourceGroups.map((group) => <section className="resource-group" key={group.label}><h4>{group.label}</h4>{group.items.map((document) => <button className={`resource-row ${documentKey(document) === selectedKey ? 'active' : ''}`} type="button" key={documentKey(document)} onClick={() => onSelectedDocumentKey(documentKey(document))}><span className="resource-icon"><FileText size={14} /></span><span><strong>{document.name || String(document.id)}</strong><small>{documentModelLabel(document.model)} · {documentStateLabel(document.model, document.state)}</small></span><span className="resource-type">{documentSourceLabel(document.source)}</span></button>)}</section>)}</nav><section className="resource-preview">{selected ? <><div className="preview-head"><div><h3>{selected.name || String(selected.id)}</h3><p>{documentModelLabel(selected.model)} · 记录 {selected.id}</p></div><div className="preview-actions"><RadixButton className="secondary-button" variant="soft" onClick={() => onOpenDocument(selected)}><ExternalLink size={14} />在 Odoo 打开</RadixButton><DocumentDownloadActions document={selected} receipts={documentDownloads} onDownload={onDownloadDocument} onOpenArtifact={onOpenArtifact} onRevealArtifact={onRevealArtifact} /></div></div><dl className="preview-meta"><dt>状态</dt><dd><StatusBadge status={selected.state} label={documentStateLabel(selected.model, selected.state)} /></dd><dt>关键事实</dt><dd>{documentFact(selected)}</dd><dt>来源</dt><dd>{documentSource(selected)}</dd><dt>刷新观测时间</dt><dd>{formatInstant(selected.observed_at)}</dd></dl><details className="goal-details resource-goal"><summary>查看原始目标输入</summary><p>{goal || '未知'}</p></details><div className="resource-receipt-actions"><RadixButton className="inline-action" variant="ghost" disabled={!documentSourceRun(selected) || !documentSourceTool(selected)} onClick={() => onTraceTarget({ run_id: documentSourceRun(selected), tool_id: documentSourceTool(selected) })}>查看原始读取回执</RadixButton><span>{documentSourceRun(selected) && documentSourceTool(selected) ? `原始读取时间：${formatInstant(documentSourceObservedAt(selected))}` : '原始读取回执不可用'}</span></div>{resourceText(selected.fields, 'goal') && <div className="resource-note"><strong>单据返回的目标上下文</strong><p>{resourceText(selected.fields, 'goal')}</p></div>}<details className="resource-fields"><summary>查看原始字段</summary><pre>{jsonText(selected.fields)}</pre></details>{resourceText(selected.fields, 'sop') && <div className="resource-note"><strong>已返回 SOP / 知识</strong><p>{resourceText(selected.fields, 'sop')}</p></div>}{exportPath && <div className="export-receipt"><strong>生成的业务回执</strong><span> {exportPath}</span></div>}</> : <EmptyState title="选择一项单据" detail="从左侧选择已观测的 Odoo 记录。" />}</section></div>}
    </div>
  )
}

function isReferenceDocument(document: Document) { return Boolean(document.is_reference || document.document_scope === 'reference') }

function documentFact(document: Document) {
  const fields = document.fields
  const entries = document.model === 'sale.order'
    ? [['客户', fields.partner_name ?? fields.customer ?? fields.partner_id], ['金额', fields.amount_total ?? fields.total], ['开票', invoiceStatusLabel(String(fields.invoice_status ?? '未知'))]]
    : document.model === 'account.move'
      ? [['客户', fields.partner_name ?? fields.customer ?? fields.partner_id], ['金额', fields.amount_total ?? fields.total], ['付款', paymentStatusLabel(String(fields.payment_state ?? '未知'))], ['余额', fields.amount_residual ?? fields.residual], ['正式 PDF', invoicePdfStatus(fields)]]
      : document.model === 'mail.message'
        ? [['主题', fields.subject], ['作者', fields.author_name ?? fields.author_id], ['留言', fields.body ?? fields.message]]
        : document.model === 'sale.order.line'
          ? [['产品', fields.product_name ?? fields.product_id], ['数量', fields.product_uom_qty ?? fields.quantity], ['单价', fields.price_unit]]
          : document.model === 'account.move.line'
            ? [['科目', fields.account_name ?? fields.account_id], ['借方', fields.debit], ['贷方', fields.credit]]
            : document.model === 'account.payment.term'
              ? [['付款条件', fields.name ?? fields.note], ['说明', fields.description]]
      : Object.entries(fields).slice(0, 3).map(([key, value]) => [key, value])
  return entries.filter(([, value]) => value !== undefined && value !== null && value !== '').map(([key, value]) => `${key}:${readableValue(value)}`).join(' · ') || '没有可显示的关键字段'
}

function invoicePdfStatus(fields: Record<string, unknown>) {
  if (!Object.prototype.hasOwnProperty.call(fields, 'invoice_pdf_report_id')) return '未观测'
  return fields.invoice_pdf_report_id ? '已生成' : '待生成'
}

function DocumentDownloadActions({ document, receipts, onDownload, onOpenArtifact, onRevealArtifact }: { document: Document; receipts: Record<string, DownloadReceipt>; onDownload: (document: Document, format: 'pdf' | 'csv') => void; onOpenArtifact: (artifact: BusinessArtifact) => void; onRevealArtifact: (artifact: BusinessArtifact) => void }) {
  if (!isDownloadableDocument(document)) return null
  const documentName = document.name || `${documentModelLabel(document.model)} ${String(document.id)}`
  const action = (format: 'pdf' | 'csv') => receipts[`${documentKey(document)}:${format}`]
  const renderAction = (format: 'pdf' | 'csv', label: string) => {
    const receipt = action(format)
    return <div className="document-download-action"><RadixButton className="inline-action" variant="ghost" aria-label={`${format === 'pdf' ? '下载' : '导出'} ${documentName} 的 ${format.toUpperCase()}`} disabled={receipt?.status === 'downloading'} onClick={() => onDownload(document, format)}>{receipt?.status === 'downloading' ? <LoaderCircle className="spin" size={13} /> : <Download size={13} />}{receipt?.status === 'downloading' ? '生成中…' : label}</RadixButton>{receipt?.status === 'cancelled' && <small>已取消，可重试</small>}{receipt?.status === 'failed' && <small className="download-error">{receipt.error || '下载失败'}</small>}{receipt?.status === 'completed' && receipt.artifact && <span className="download-receipt"><strong>{receipt.artifact.name}</strong><button type="button" onClick={() => onOpenArtifact(receipt.artifact!)}>打开</button><button type="button" onClick={() => onRevealArtifact(receipt.artifact!)}>位置</button></span>}{receipt?.status === 'completed' && !receipt.artifact && receipt.path && <span className="download-receipt"><strong>{receipt.path}</strong></span>}</div>
  }
  return <div className="document-download-actions" aria-label="单据下载">{renderAction('pdf', '下载 PDF')}{renderAction('csv', '导出明细 CSV')}</div>
}

function documentSource(document: Document) {
  const fields = document.fields as Record<string, unknown>
  const run = document.source_run_id ?? fields.source_run_id ?? fields.run_id
  const tool = document.source_tool_id ?? fields.source_tool_id ?? fields.tool_id
  return [document.source || '未知来源', run ? `运行 ${String(run)}` : '', tool ? `工具 ${String(tool)}` : ''].filter(Boolean).join(' · ')
}

function documentSourceObservedAt(document: Document) {
  const fields = document.fields as Record<string, unknown>
  const value = (document as Document & { source_observed_at?: string }).source_observed_at ?? fields.source_observed_at
  return typeof value === 'string' ? value : undefined
}
function documentSourceRun(document: Document) { return document.source_run_id || String(document.fields.source_run_id || '') || undefined }
function documentSourceTool(document: Document) { return document.source_tool_id || String(document.fields.source_tool_id || '') || undefined }

function resourceText(fields: Record<string, unknown>, key: string) {
  const value = fields[key] ?? fields[`${key}_text`] ?? fields[`${key}_content`]
  return value == null || value === '' ? '' : readableValue(value)
}

function artifactKindLabel(kind?: string) { return ({ business_receipt: '业务回执', odoo_pdf: 'PDF 单据', odoo_csv: '明细 CSV', document_pdf: 'PDF 单据', document_csv: '明细 CSV' } as Record<string, string>)[kind || ''] || kind || '文件' }
function documentModelLabel(model: string) { return ({ 'sale.order': '销售订单', 'purchase.order': '采购订单', 'account.move': '客户发票', 'stock.picking': '出库单', 'res.partner': '往来单位', 'mail.message': '业务留言', 'sale.order.line': '销售明细', 'purchase.order.line': '采购明细', 'account.move.line': '会计分录', 'account.payment.term': '付款条件', 'product.template': '产品', 'product.product': '商品', 'account.journal': '会计日记账', 'sale.advance.payment.inv': '开票向导', 'account.move.send.wizard': '发票PDF向导', 'ir.attachment': '附件' } as Record<string, string>)[model] || model }
function documentSourceLabel(source?: string) { return ({ odoo: 'Odoo 观测', odoo_rpc: 'Odoo 观测', refresh_native_read: '独立回读', native_read_receipt: '原始读取回执', native_action_readback: '动作回读', agent: 'Agent', host: '本地 host' } as Record<string, string>)[source || ''] || '其他来源' }
function isDownloadableDocument(document: Document) { return ['sale.order', 'purchase.order', 'account.move'].includes(document.model) }
function documentKey(document: Document) { return `${document.model}:${String(document.id)}` }
function documentStateLabel(model: string, state?: string) {
  const labels: Record<string, Record<string, string>> = {
    'sale.order': { draft: '草稿', sent: '已发送', sale: '已确认', cancel: '已取消' },
    'purchase.order': { draft: '询价草稿', sent: '已发询价', 'to approve': '待二次确认', to_approve: '待二次确认', purchase: '已确认', done: '已锁定', cancel: '已取消' },
    'account.move': { draft: '草稿', posted: '已过账', cancel: '已取消' },
    'stock.picking': { draft: '草稿', waiting: '等待', confirmed: '待处理', assigned: '已分配', done: '已完成', cancel: '已取消' }
  }
  const noIndependentState = ['res.partner', 'mail.message', 'sale.order.line', 'purchase.order.line', 'account.move.line', 'account.payment.term', 'product.template', 'product.product', 'account.journal', 'sale.advance.payment.inv', 'account.move.send.wizard', 'ir.attachment', 'product.supplierinfo', 'account.tax'].includes(model)
  return (state && labels[model]?.[state]) || (state ? '状态未知' : noIndependentState ? '—（不适用）' : '未知')
}
function invoiceStatusLabel(value: string) { return ({ invoiced: '已开票', 'to invoice': '待开票', to_invoice: '待开票', no: '无需开票' } as Record<string, string>)[value] || value }
function paymentStatusLabel(value: string) { return ({ paid: '已付款', not_paid: '未付款', partial: '部分付款', in_payment: '付款处理中', reversed: '已冲销' } as Record<string, string>)[value] || value }
function amountWithCurrency(amount: string, currency: string) { return currency === '未知' || currency === '未观测' || currency === '—（不适用）' ? amount : `${amount} ${currency}` }

function ApprovalsPage({ approvals, documents, disabled, onDecision, onReconcile, onTraceTarget }: { approvals: Approval[]; documents: Document[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void; onReconcile: (approval: Approval) => void; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const orderedApprovals = [...approvals].sort(compareApprovals)
  return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">需要确认</span><h3>变更与审批</h3></div><span>{approvals.filter(isPendingApproval).length} 项待处理</span></div>{approvals.length === 0 ? <EmptyState title="没有审批记录" detail="主机产生需要人工确认的业务动作后，审批卡会保留在这里。" /> : <div className="approval-list">{orderedApprovals.map((approval) => <ApprovalRow key={approval.action_id} approval={approval} documents={documents} disabled={disabled} onDecision={onDecision} onReconcile={onReconcile} onTraceTarget={onTraceTarget} />)}</div>}</div>
}

function compareApprovals(left: Approval, right: Approval) {
  const priority = (approval: Approval) => isPendingApproval(approval) ? 0 : approval.status === 'needs_reconciliation' ? 1 : 2
  const priorityDifference = priority(left) - priority(right)
  if (priorityDifference) return priorityDifference
  const createdAt = (approval: Approval) => {
    const value = (approval as Approval & { created_at?: string | number }).created_at
    if (typeof value === 'number') return value
    const timestamp = value ? Date.parse(value) : 0
    return Number.isNaN(timestamp) ? 0 : timestamp
  }
  const createdDifference = createdAt(right) - createdAt(left)
  return createdDifference || right.action_id.localeCompare(left.action_id)
}

function ApprovalRow({ approval, documents, disabled, onDecision, onReconcile, onTraceTarget }: { approval: Approval; documents: Document[]; disabled: boolean; onDecision: (approval: Approval, decision: 'approve' | 'reject') => void; onReconcile: (approval: Approval) => void; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  const pending = isPendingApproval(approval)
  const expired = pending && isExpired(approval.expires_at)
  const title = readableApprovalTitle(approval)
  return (
    <article className={`approval-row ${pending ? 'pending' : ''}`}>
      <div className="approval-row-head">
        <div><strong>{title}</strong><span>{modelLabel(approval.model)} · {operationLabel(approval.operation)} · {approval.model} · {approval.action_id}</span></div>
        <StatusBadge status={expired ? 'expired' : approval.status} label={expired ? '已过期' : approvalStatusLabel(approval.status)} />
      </div>
      <div className="approval-facts"><span>{approvalRecordText(approval, documents)}</span><span>{pending ? formatExpiry(approval.expires_at) : '审批已结束'}</span></div>
      <ApprovalFieldDiff approval={approval} documents={documents} />
      <details>
        <summary>查看拟提交值与执行前状态</summary>
        <div className="json-columns"><div><small>拟提交值</small><pre>{jsonText(approval.values)}</pre></div><div><small>执行前状态</small><pre>{approvalPrestateText(approval)}</pre></div></div>
      </details>
      {pending && !expired && <div className="approval-actions"><RadixButton className="danger-button" variant="soft" disabled={disabled} onClick={() => onDecision(approval, 'reject')}><X size={15} />拒绝</RadixButton><RadixButton className="primary-button" disabled={disabled} onClick={() => onDecision(approval, 'approve')}><CheckIcon size={15} />批准这项业务动作</RadixButton></div>}
      {approval.status === 'needs_reconciliation' && <div className="approval-actions"><small>写入结果不确定；核对现有 Odoo 状态后才能继续。</small><RadixButton className="secondary-button" variant="soft" disabled={disabled} onClick={() => onReconcile(approval)}><RefreshCw size={15} />核对不确定写入</RadixButton></div>}
      <button className="receipt-link receipt-button" type="button" onClick={() => onTraceTarget({ run_id: approval.run_id, action_id: approval.action_id })}>查看关联运行回执</button>
      {approval.result != null && <details className={`receipt receipt-details ${pending ? 'receipt-preflight' : ''}`}><summary>{approvalResultLabel(approval.status)}</summary><pre>{jsonText(approval.result)}</pre></details>}
    </article>
  )
}

function ApprovalFieldDiff({ approval, documents }: { approval: Approval; documents: Document[] }) {
  const values = approval.values || {}
  const prestate = approvalPrestateView(approval)
  const before = prestate.fields
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(values)]))
  if (!keys.length) return prestate.kind === 'multiple' ? <div className="field-diff"><p className="field-diff-note">执行前状态包含多条记录，无法压缩为单条字段对比；原始结构保留在下方。</p></div> : <p className="muted">没有可展示的字段前后值。</p>
  const beforeText = (key: string) => prestate.kind === 'new' ? '新建 / 无前态' : prestate.kind === 'multiple' ? '多条记录（见下方原始状态）' : prestate.kind === 'unknown' ? '未知' : approvalValueText(approval.model, key, before[key], documents)
  const row = (key: string) => <div className="field-diff-row" key={key}><span>{approvalFieldLabel(key, approval.model)}</span><span className="diff-value">{beforeText(key)}</span><span className="diff-value after">{approvalValueText(approval.model, key, values[key], documents)}</span></div>
  const visible = keys.slice(0, 8)
  const remaining = keys.slice(8)
  return <div className="field-diff"><div className="field-diff-row field-diff-head"><span>字段</span><span>执行前</span><span>拟提交</span></div>{visible.map(row)}{remaining.length > 0 && <details className="field-diff-more"><summary>查看其余字段（共 {keys.length} 项）</summary>{remaining.map(row)}</details>}</div>
}

function approvalPrestateView(approval: Approval): { kind: 'new' | 'unknown' | 'fields' | 'multiple'; fields: Record<string, unknown> } {
  const raw = approval.prestate
  if (raw == null || raw === '') return { kind: approval.operation === 'create' ? 'new' : 'unknown', fields: {} }
  let records: unknown[] | null = null
  if (Array.isArray(raw)) records = raw
  else if (raw && typeof raw === 'object' && Array.isArray((raw as Record<string, unknown>).records)) records = (raw as Record<string, unknown>).records as unknown[]
  if (records) {
    if (records.length === 0) return { kind: approval.operation === 'create' ? 'new' : 'unknown', fields: {} }
    if (records.length === 1 && records[0] && typeof records[0] === 'object' && !Array.isArray(records[0])) return { kind: 'fields', fields: records[0] as Record<string, unknown> }
    return { kind: 'multiple', fields: {} }
  }
  return raw && typeof raw === 'object' && !Array.isArray(raw) ? { kind: 'fields', fields: raw as Record<string, unknown> } : { kind: 'unknown', fields: {} }
}
function approvalPrestateText(approval: Approval) {
  return approval.prestate == null || approval.prestate === '' ? (approval.operation === 'create' ? '新建 / 无前态' : '未知（未返回执行前状态）') : jsonText(approval.prestate)
}
function approvalRecordText(approval: Approval, documents: Document[]) {
  if (!approval.record_ids.length) return approval.operation === 'create' ? '新建，尚未生成编号' : '记录 ID 未知'
  const records = approval.record_ids.map((id) => {
    const document = documents.find((item) => item.model === approval.model && String(item.id) === String(id))
    return document?.name ? `${document.name}（ID ${String(id)}）` : `ID ${String(id)}`
  })
  return `记录 ${records.join('、')}`
}

function approvalFieldLabel(key: string, model?: string) {
  const labels: Record<string, string> = { partner_id: model === 'purchase.order' ? '供应商' : '客户', payment_term_id: '付款条件', partner_shipping_id: '收货地址', partner_invoice_id: '开票地址', date_order: '下单日期', invoice_status: '开票状态', invoice_line_ids: '发票明细', order_line: '订单明细', commitment_date: '承诺日期', client_order_ref: '客户参考', records: '记录' }
  return labels[key] ? `${labels[key]}（${key}）` : key
}

function approvalReferenceText(models: string[], value: unknown, documents: Document[], fallback: string) {
  const id = Array.isArray(value) ? value[0] : value
  if (id == null || id === '') return '未知'
  const inlineName = Array.isArray(value) && typeof value[1] === 'string' ? value[1] : ''
  const document = documents.find((item) => models.includes(item.model) && String(item.id) === String(id))
  const name = document?.name || inlineName
  return name ? `${name}（ID ${String(id)}）` : `${fallback} ID ${String(id)}`
}

function approvalLineText(value: unknown, documents: Document[]) {
  if (!Array.isArray(value)) return readableValue(value)
  const rows = value.map((line) => {
    const data = Array.isArray(line) ? line[line.length - 1] : line
    if (!data || typeof data !== 'object' || Array.isArray(data)) return readableValue(line)
    const row = data as Record<string, unknown>
    const product = approvalReferenceText(['product.product', 'product.template'], row.product_id, documents, '商品')
    const quantity = row.product_uom_qty ?? row.product_qty ?? row.quantity ?? row.qty
    const price = row.price_unit ?? row.price
    return [product, quantity == null ? '' : `数量 ${readableValue(quantity)}`, price == null ? '' : `单价 ${readableValue(price)}`].filter(Boolean).join(' · ')
  }).filter(Boolean)
  return rows.length ? rows.join('；') : readableValue(value)
}

function approvalValueText(model: string, key: string, value: unknown, documents: Document[]) {
  if (key === 'partner_id' || key === 'partner_shipping_id' || key === 'partner_invoice_id') return approvalReferenceText(['res.partner'], value, documents, key === 'partner_id' ? (model === 'purchase.order' ? '供应商' : '客户') : '往来单位')
  if (key === 'payment_term_id') return approvalReferenceText(['account.payment.term'], value, documents, '付款条件')
  if (key === 'product_id') return approvalReferenceText(['product.product', 'product.template'], value, documents, '商品')
  if (key === 'order_line' || key === 'invoice_line_ids') return approvalLineText(value, documents)
  return readableValue(value)
}

function VerificationPage({ checks, observedAt, stale }: { checks: Check[]; observedAt?: string; stale: boolean }) { return <div className="page-stack"><div className="page-intro"><div><span className="eyebrow">独立回读</span><h3>业务核验</h3></div><span>{stale ? '可能过期' : `读取于 ${formatInstant(observedAt)}`}</span></div>{checks.length === 0 ? <EmptyState title="核验结果未知" detail="主机尚未提供独立业务检查回执。" /> : <div className="check-table">{checks.map((check) => <div className="check-row" key={check.name}><span className={`check-mark check-${check.status}`}>{check.status === 'passed' ? '✓' : check.status === 'failed' ? '!' : '?'}</span><div><strong>{check.label || check.name}</strong><span>{check.detail || '没有详细说明'}</span></div><span className={`state-badge state-${check.status}`}>{check.status === 'passed' ? '通过' : check.status === 'failed' ? '失败' : '未知'}</span></div>)}</div>}</div> }

function TracePage({ trace, runs, readback, selectedRunId, loading, target, onRunSelect }: { trace: TraceBundle | null; runs: Run[]; readback?: BusinessDetailProjection['business']['readback']; selectedRunId: string; loading: boolean; target: { runId?: string; toolId?: string; actionId?: string; kind?: string } | null; onRunSelect: (id: string) => void }) {
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

function RunDetail({ trace }: { trace: TraceBundle }) { if (!trace.run) return <EmptyState title="运行状态未知" detail="主机尚未返回运行摘要。" />; return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{runDisplayLabel(trace.run.status)}</strong></div><div><span>轮次</span><strong>{formatCount(trace.run.model_rounds)}</strong></div><div><span>工具</span><strong>{formatCount(trace.run.tool_count)}</strong></div><div><span>耗时</span><strong>{formatDuration(trace.run.elapsed_seconds)}</strong></div></div><UsageBreakdown usage={trace.run.usage} />{trace.run.error && <div className="notice red"><CircleAlert size={15} /><span>{trace.run.error_detail || trace.run.error}</span></div>}</div> }
function RoundDetail({ round }: { round: Round }) { return <div className="trace-detail-content"><MessageText text={round.text || '没有公开摘要；隐藏思维不会在工作台展示。'} /><UsageBreakdown usage={round.usage} /><div className="trace-label">状态</div><p>{roundStatusLabel(round.status)} · {formatDuration(round.elapsed_seconds)}</p></div> }
function ToolDetail({ tool }: { tool: ToolReceipt }) { return <div className="trace-detail-content"><div className="fact-table"><div><span>状态</span><strong>{toolStatusLabel(tool.status)}</strong></div><div><span>轮次</span><strong>{tool.round == null ? '未知' : `第 ${tool.round} 轮`}</strong></div><div><span>耗时</span><strong>{formatDuration(tool.elapsed_seconds)}</strong></div><div><span>审批</span><strong>{tool.action_id || '未知'}</strong></div></div><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div></div> }
function ReadbackDetail({ readback }: { readback?: BusinessDetailProjection['business']['readback'] }) { return <div className="trace-detail-content"><div className="notice blue"><CircleCheck size={15} /><span>这是独立 Odoo 回读快照，时间与原始工具调用分开记录。</span></div>{readback ? <><div className="fact-table"><div><span>快照时间</span><strong>{formatInstant(readback.observed_at)}</strong></div><div><span>来源</span><strong>独立 Odoo 回读</strong></div><div><span>核验项</span><strong>{readback.checks?.length ?? 0}</strong></div><div><span>状态</span><strong>{readback.stale ? '可能已过期' : '已返回'}</strong></div></div><details className="resource-fields"><summary>查看回读核验</summary><pre>{jsonText(readback.checks ?? [])}</pre></details></> : <div className="empty-state"><strong>快照详情不可用</strong><p>当前运行没有匹配的独立回读快照。</p></div>}</div> }

function UsageBreakdown({ usage }: { usage?: Run['usage'] }) {
  if (!usage) return <div className="usage-breakdown"><span>用量未知</span></div>
  return <div className="usage-breakdown" aria-label="Token 用量"><span>未缓存输入 {formatCount(usage.input)}</span><span>缓存命中 {formatCount(usage.cache_read)}</span><span>输出（含推理） {formatCount(usage.output)}</span><span>推理 {formatCount(usage.reasoning)}</span><span>总计 {formatCount(usage.total)}</span>{usage.reported_total != null && <span>已报告 {formatCount(usage.reported_total)}{usage.missing_usage_rounds ? `，${usage.missing_usage_rounds} 轮未报告` : ''}</span>}</div>
}

function ToolReceiptRow({ tool }: { tool: ToolReceipt }) { return <details className="tool-row"><summary><span className={`tool-status tool-${tool.status}`}>{toolStatusLabel(tool.status)}</span><strong>{tool.name}</strong><small>{tool.round ? `第 ${tool.round} 轮 · ` : ''}{formatDuration(tool.elapsed_seconds)}</small></summary><div className="json-columns"><div><small>请求参数</small><pre>{jsonText(tool.arguments)}</pre></div><div><small>结果回执</small><pre>{tool.result == null ? '未知' : jsonText(tool.result)}</pre></div></div>{tool.action_id && <span className="receipt-link">关联审批：{tool.action_id}</span>}</details> }

function EmptyState({ title, detail }: { title: string; detail: string }) { return <div className="empty-state"><span className="empty-glyph">○</span><strong>{title}</strong><p>{detail}</p></div> }

function messageForError(reason: unknown) {
  let message = reason instanceof Error ? reason.message : String(reason)
  message = message.replace(/^Error invoking remote method ['"]workbench:call['"]:\s*Error:\s*/i, '')
  const knownCodes = new Set(['VALUEERROR', 'CONFIG_BUSY', 'CONNECTION_CHECK_BUSY', 'EXPORT_CANCELLED', 'ARTIFACT_FILE_MISSING', 'ARTIFACT_NOT_FOUND', 'ARTIFACT_FORMAT_INVALID', 'ARTIFACT_OPEN_FAILED', 'ARTIFACT_INDEX_FAILED', 'DOCUMENT_PDF_UNAVAILABLE', 'PDF_UNAVAILABLE', 'DOCUMENT_DOWNLOAD_FAILED', 'MATERIAL_TOO_LARGE', 'MATERIAL_UNSUPPORTED', 'MATERIAL_PARSE_FAILED', 'ODOO_RECORD_NOT_FOUND', 'ODOO_OPEN_UNAVAILABLE', 'ODOO_ORIGIN_MISMATCH'])
  const codePrefix = message.match(/^\[([A-Z0-9_]+)\]\s*/)
  if (codePrefix && knownCodes.has(codePrefix[1])) message = message.slice(codePrefix[0].length)
  return message.includes('CONFIG_BUSY') ? '当前有业务正在执行或等待审批，请结束后再修改连接设置。' : message.includes('CONNECTION_CHECK_BUSY') ? '执行期间显示最近检查结果，结束后可重新检查。' : message.includes('EXPORT_CANCELLED') ? '已取消导出业务回执。' : message.includes('ARTIFACT_FILE_MISSING') ? '文件已移动或删除，请重新导出。' : message.includes('ARTIFACT_NOT_FOUND') ? '当前业务没有此文件。' : message.includes('ARTIFACT_FORMAT_INVALID') ? '仅支持本业务已登记的 JSON 回执。' : message.includes('ARTIFACT_OPEN_FAILED') ? '系统无法打开文件，可尝试显示位置。' : message.includes('ARTIFACT_INDEX_FAILED') ? message : message.includes('DOCUMENT_PDF_UNAVAILABLE') || message.includes('PDF_UNAVAILABLE') ? '当前单据没有可用 PDF，请改用导出明细 CSV。' : message.includes('DOCUMENT_DOWNLOAD_FAILED') ? '单据下载失败，请稍后重试。' : message.includes('MATERIAL_TOO_LARGE') ? '材料超过 2 MiB 限制。' : message.includes('MATERIAL_UNSUPPORTED') ? '仅支持 CSV 或 TXT 材料。' : message.includes('MATERIAL_PARSE_FAILED') ? '材料解析失败，请检查文件内容。' : message.includes('ODOO_RECORD_NOT_FOUND') ? '该 Odoo 记录已不存在或不属于当前业务。' : message.includes('ODOO_OPEN_UNAVAILABLE') ? '当前无法打开 Odoo 记录，请检查 Odoo 连接。' : message.includes('ODOO_ORIGIN_MISMATCH') ? '该记录不属于当前配置的 Odoo 地址。' : message
}
function connectionLabel(state: ConnectionState) { return state === 'connected' ? '主机已连接' : state === 'checking' ? '正在连接主机' : state === 'crashed' ? '主机已崩溃' : state === 'protocol_error' ? '主机协议错误' : '主机断开' }
function odooHealthStatus(health: Health | null) { return health?.odoo?.status || health?.odoo_status || 'unchecked' }
function healthLabel(status?: string) { return status === 'connected' || status === 'ready' || status === 'ok' ? '已连接' : status === 'configured' ? '已配置' : status === 'unconfigured' ? '未配置' : status === 'unavailable' ? '不可用' : status === 'permission_denied' ? '无权限' : status === 'error' ? '检查失败' : status === 'unchecked' ? '未检查' : status === 'disconnected' ? '断开' : '状态未知' }
function runDisplayLabel(status?: string) { return status === 'completed' ? '本轮结束' : labelFor(runStatusLabel, status) }
function roundStatusLabel(status?: string) { return labelFor({ completed: '已完成', running: '进行中', pending: '待处理', failed: '失败', interrupted: '已中断', cancelled: '已取消', awaiting_approval: '等待审批' }, status) }
function toolStatusLabel(status?: string) { return labelFor({ completed: '已完成', running: '进行中', error: '错误', failed: '失败', executed: '已执行', awaiting_approval: '等待审批', pending: '待处理', interrupted: '已中断', cancelled: '已取消', unknown: '未知' }, status) }
function activityPhaseLabel(phase?: string) { return ({ idle: '待执行', planning: '准备中', model: '分析业务目标', reading: '读取业务数据', tool: '调用业务工具', approval: '等待确认', executing: '执行中', cancelling: '正在取消', verifying: '回读核验', completed: '本轮结束', failed: '执行失败', interrupted: '已中断', cancelled: '已取消', reconciliation: '等待对账', unknown: '状态未知' } as Record<string, string>)[phase || ''] || '状态未知' }
function businessTypeMeta(type?: string) { return ({ sale_invoice: { title: '销售与开票', short: '销售发票' }, purchase: { title: '采购', short: '采购流程' }, sale_purchase_invoice: { title: '销售 → 采购 → 开票', short: '业务链' } } as Record<string, { title: string; short: string }>)[type || ''] || { title: '业务工作区', short: '业务' } }
function completionTargetLabel(target?: string, type?: string) { const effective = target || (type === 'purchase' ? 'confirmed' : 'posted'); return ({ read_only: '只读浏览', draft: '保留草稿', confirmed: '完成确认', posted: '发票已过账' } as Record<string, string>)[effective] || '完成目标未知' }
function materialRowLabel(material: MaterialRecord) { if (material.row_count == null) return '行数未知'; return material.media_type === 'text/csv' ? `${Math.max(0, material.row_count - 1)} 条数据` : `${material.row_count} 行`; }
function stageLabel(stage?: string) { return ({ material: '材料', read: '读取', quote: '报价', sales: '销售', confirm: '确认', purchase: '采购', invoice: '开票', verify: '核验' } as Record<string, string>)[stage || ''] || '未知阶段' }
function stageStatusLabel(status?: string) { return ({ pending: '待处理', active: '进行中', awaiting_approval: '等待审批', observed: '已观测', verified: '已核验', failed: '失败', unknown: '未知' } as Record<string, string>)[status || ''] || '未知' }
function outcomeScopeLabel(scope?: string) { return ({
  sale_invoice_read_only_checks: '销售订单与客户发票只读浏览',
  sale_invoice_draft_checks: '销售订单与客户发票草稿核验',
  sale_invoice_confirmed_checks: '销售订单与客户发票确认核验',
  sale_invoice_basic_checks: '销售订单与客户发票基础核验',
  purchase_read_only_checks: '采购订单只读浏览',
  purchase_draft_checks: '采购订单草稿核验',
  purchase_confirmed_checks: '采购订单确认核验',
  sale_purchase_invoice_read_only_checks: '销售、采购与开票只读浏览',
  sale_purchase_invoice_draft_checks: '销售、采购与开票草稿核验',
  sale_purchase_invoice_confirmed_checks: '销售、采购与开票确认核验',
  sale_purchase_invoice_posted_checks: '销售、采购与开票基础核验'
} as Record<string, string>)[scope || ''] || '业务范围未知' }
function outcomeStatusLabel(status?: string) { return ({ unknown: '未知', passed: '通过', failed: '失败' } as Record<string, string>)[status || ''] || '状态未知' }
function toolLabel(tool?: string) { return ({ mcp_odoo_read_record: '读取业务记录', mcp_odoo_read: '读取业务记录', mcp_odoo_validate_write: '预检业务动作', execute_approved_write: '执行已批准动作', refresh_business: '读取最新状态' } as Record<string, string>)[tool || ''] || '业务工具' }
function isPendingApproval(approval: Approval) { return approval.status === 'pending' || approval.status === 'pending_approval' }
function approvalStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '待审批', pending_approval: '待审批', approved: '已批准 / 待执行', rejected: '已拒绝',
    verified: '已核验', known_failed: '已知失败', not_executed: '未执行', interrupted: '已中断',
    stale: '已失效', expired: '已过期', needs_reconciliation: '需对账', executing: '执行中', executed: '已执行', failed: '执行失败'
  }
  return labels[status] || '状态未知'
}
function operationLabel(operation: string) {
  const labels: Record<string, string> = { create: '创建', write: '修改', unlink: '删除', action_confirm: '确认', button_confirm: '确认采购订单', button_approve: '批准采购订单', create_invoices: '创建发票', action_post: '过账', action_send_and_print: '生成正式发票文件' }
  return labels[operation] || operation
}
function modelLabel(model: string) { return model === 'sale.order' ? '销售订单' : model === 'purchase.order' ? '采购订单' : model === 'account.move' ? '客户发票' : model === 'account.move.send.wizard' ? '发票文件向导' : model === 'res.partner' ? '往来单位' : model }
function readableApprovalTitle(approval: Approval) {
  const title = approval.title?.trim()
  const generic = !title || ['erp write approval', 'write approval', 'approval required', 'business approval', 'action approval'].includes(title.toLowerCase())
  if (!generic) return title
  if (approval.operation === 'button_confirm' || approval.operation === 'button_approve') return operationLabel(approval.operation)
  return `${operationLabel(approval.operation)}${modelLabel(approval.model)}`
}
function approvalResultLabel(status: string) {
  return status === 'pending' || status === 'pending_approval' ? '预检 / 动作回执（未执行）' : status === 'approved' ? '已批准 / 待执行' : status === 'rejected' || status === 'not_executed' ? '审批结果（未执行）' : ['verified', 'executed', 'completed', 'posted'].includes(status) ? '动作执行回执' : '动作回执'
}
function environmentLabel(environment?: string) { return environment === 'configured' ? '已配置环境' : environment === 'demo' ? '演示环境' : '未知' }
function readableValue(value: unknown): string {
  if (value == null || value === '') return '未知'
  if (Array.isArray(value)) {
    if (value.length === 2 && typeof value[0] === 'number' && typeof value[1] === 'string') return value[1]
    return value.map((item) => readableValue(item)).join(', ')
  }
  if (typeof value === 'object') return jsonText(value).replace(/\s+/g, ' ')
  return String(value)
}

function compactGoal(goal?: string, documents: Document[] = []) {
  const value = goal?.trim()
  if (!value) return '按已确认目标处理销售订单与客户发票'
  if (/you may act autonomously|autonomous|authorization/i.test(value)) {
    const order = documents.find((document) => document.model === 'sale.order')
    return order?.name ? `处理销售订单 ${order.name} 与客户发票` : '按已确认目标处理销售订单与客户发票'
  }
  return value.length > 180 ? `${value.slice(0, 177)}…` : value
}

function ConnectionDetailsDialog({ health, connection, busy, open, onOpenChange, onRetry }: { health: Health | null; connection: ConnectionState; busy: boolean; open: boolean; onOpenChange: (open: boolean) => void; onRetry: () => void }) {
  const odoo = health?.odoo
  const checking = connection === 'checking'
  const odooProblem = ['unavailable', 'permission_denied', 'error'].includes(odooHealthStatus(health))
  return <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
    <RadixDialog.Content className="connection-dialog" maxWidth="440px">
      <RadixDialog.Title>连接状态</RadixDialog.Title>
      <RadixDialog.Description>查看桌面工作台与 Odoo 的最近一次只读检查。</RadixDialog.Description>
      <div className="connection-detail-list">
        <div><span>本地主机</span><strong>{connectionLabel(connection)}</strong></div>
        <div><span>Odoo 读取</span><strong>{healthLabel(odooHealthStatus(health))}</strong></div>
        <div><span>地址 / 数据库</span><strong>{odoo?.endpoint || '未配置'}{odoo?.database ? ` · ${odoo.database}` : ''}</strong></div>
        <div><span>配置账号</span><strong>{odoo?.account || '未检查'}</strong></div>
        <div><span>本地数据目录</span><strong>{health?.data_dir || '未提供'}</strong></div>
        <div><span>模型配置</span><strong>{health?.model_configured ? '已配置' : '未配置'}</strong></div>
        <div><span>最近检查</span><strong>{formatInstant(odoo?.checked_at)}{odoo?.latency_ms != null ? ` · ${odoo.latency_ms} ms` : ''}</strong></div>
      </div>
      {odoo?.detail && <p className={odooProblem ? 'connection-detail-error' : 'connection-detail-note'}>{odoo.detail}</p>}
      {busy && <p className="connection-busy-note">执行期间显示最近检查结果，结束后可重新检查。</p>}
      <div className="connection-dialog-footer"><RadixButton variant="soft" onClick={() => onOpenChange(false)}>关闭</RadixButton><RadixButton disabled={busy || checking} onClick={onRetry}>{checking ? <LoaderCircle className="spin" size={15} /> : <RefreshCw size={15} />}{checking ? '检查中…' : '重新检查'}</RadixButton></div>
    </RadixDialog.Content>
  </RadixDialog.Root>
}

function ArchiveDialog({ open, onOpenChange, onConfirm }: { open: boolean; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  return <RadixAlertDialog.Root open={open} onOpenChange={onOpenChange}>
    <RadixAlertDialog.Content maxWidth="420px">
      <RadixAlertDialog.Title>归档会话</RadixAlertDialog.Title>
      <RadixAlertDialog.Description>归档后会话从活跃列表中隐藏，业务记录仍会保留。</RadixAlertDialog.Description>
      <div className="connection-dialog-footer"><RadixAlertDialog.Cancel onClick={() => onOpenChange(false)}><RadixButton variant="soft">取消</RadixButton></RadixAlertDialog.Cancel><RadixAlertDialog.Action onClick={onConfirm}><RadixButton color="red"><Archive size={15} />归档会话</RadixButton></RadixAlertDialog.Action></div>
    </RadixAlertDialog.Content>
  </RadixAlertDialog.Root>
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
