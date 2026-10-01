import { Button as RadixButton,IconButton as RadixIconButton,Tooltip as RadixTooltip,Theme } from '@radix-ui/themes'
import { Activity,ArrowUpRight,ExternalLink,LoaderCircle,Minus,Settings2,X } from 'lucide-react'
import { useEffect,useState,type CSSProperties } from 'react'
import { BusinessWorkspace } from './features/business/BusinessWorkspace'
import { ArchiveDialog,BlockedSendDialog,ConversationPane,SessionRail } from './features/conversation/ConversationPane'
import { ConnectionDetailsDialog,SettingsDialog } from './features/settings/SettingsDialog'
import { connectionLabel,healthLabel,isPendingApproval,odooHealthStatus } from './presentation'
import { useWorkbench } from './useWorkbench'
import { MaterialRecord } from './view-types'
import { transitionView } from './view-transition'

export default function App() {
  const {
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
    startingBusinessId,
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
    resolvedMessageBusinessId,
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
    chooseDataDirectory,
    retryHealth,
    exportBusiness,
    openSessionSnapshot,
    snapshotOpening,
    openOdooRecord,
    openOdoo,
    openArtifact,
    openTraceTarget,
    resizeBusiness,
    pendingProposal,
    proposalBusy,
    proposalUnavailable
  } = useWorkbench()

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 3000)
    return () => window.clearTimeout(timer)
  }, [notice, setNotice])

  const [focusedApprovalId, setFocusedApprovalId] = useState('')
  const [focusedApprovalRequest, setFocusedApprovalRequest] = useState(0)
  const openApprovals = (actionId?: string) => {
    const approval = businessDetail?.approvals.find(item => item.action_id === actionId)
      ?? (!actionId ? businessDetail?.approvals.find(item => isPendingApproval(item) || item.status === 'needs_reconciliation') : undefined)
    setFocusedApprovalId(approval?.action_id || actionId || '')
    setFocusedApprovalRequest(value => value + 1)
    setTab(approval && !isPendingApproval(approval) && approval.status !== 'needs_reconciliation' ? 'approvals' : 'execution')
  }
  const [openMenu, setOpenMenu] = useState('')
  const [appearance, setAppearance] = useState<'light' | 'dark'>(() => {
    try { return localStorage.getItem('workbench.appearance') === 'dark' ? 'dark' : 'light' } catch { return 'light' }
  })
  useEffect(() => {
    document.documentElement.dataset.appearance = appearance
    try { localStorage.setItem('workbench.appearance', appearance) } catch { /* Session appearance still works without storage. */ }
  }, [appearance])

  useEffect(() => window.workbench?.subscribe(event => {
    if (event.event !== 'ui_command') return
    switch (event.data.command) {
      case 'new_chat': void createSession(); break
      case 'settings': void openSettings(); break
      case 'toggle_sidebar': transitionView(() => setRailCollapsed(value => !value)); break
      case 'toggle_chat': transitionView(toggleConversation); break
      case 'toggle_appearance': setAppearance(value => value === 'dark' ? 'light' : 'dark'); break
      case 'connection_status': setConnectionDetailsOpen(true); break
    }
  }), [createSession, openSettings, setRailCollapsed, toggleConversation, setConnectionDetailsOpen])

  return (
    <Theme appearance={appearance} accentColor={appearance === 'dark' ? 'gray' : 'teal'} grayColor={appearance === 'dark' ? 'gray' : 'slate'} radius="large" scaling="100%" style={{ height: '100%' }}><div className="app-shell">
      <header className="window-bar">
        <div className="brand-lockup"><span className="brand-mark"><Activity size={14} strokeWidth={2.2} /></span><span>ERP-agent</span></div>
        <nav className="desktop-menubar" aria-label="应用菜单">{(['文件', '编辑', '视图', '帮助'] as const).map(name => <button key={name} type="button" aria-haspopup="menu" aria-expanded={openMenu === name} onMouseDown={event => event.preventDefault()} onClick={async event => {
          const bounds = event.currentTarget.getBoundingClientRect()
          setOpenMenu(name)
          try { await window.workbench?.showMenu?.(name, bounds.left, bounds.bottom) }
          catch { setError('无法打开菜单，请重新打开工作台。') }
          finally { setOpenMenu('') }
        }}>{name}</button>)}</nav>
        <div className="window-actions">
          <RadixTooltip content="最小化"><RadixIconButton variant="ghost" aria-label="最小化" onClick={() => void window.workbench?.windowControl('minimize')}><Minus size={16} /></RadixIconButton></RadixTooltip>
          <RadixTooltip content="最大化"><RadixIconButton variant="ghost" aria-label="最大化" onClick={() => void window.workbench?.windowControl('maximize')}><span className="window-maximize-glyph" /></RadixIconButton></RadixTooltip>
          <RadixTooltip content="关闭"><RadixIconButton variant="ghost" className="close" aria-label="关闭" onClick={() => void window.workbench?.windowControl('close')}><X size={16} /></RadixIconButton></RadixTooltip>
        </div>
      </header>

      {settingsOpen && <SettingsDialog error={error} onDismissError={() => setError('')} onChooseDirectory={chooseDataDirectory} appearance={appearance} onAppearanceChange={setAppearance} onOpenOdoo={() => void openOdoo()} openingOdoo={openingOdoo} settings={settings} draft={settingsDraft} saving={settingsSaving} onChange={(key, value) => setSettingsDraft((current) => ({ ...current, [key]: value }))} onClose={closeSettings} onSave={() => void saveSettings()} />}
      <ConnectionDetailsDialog health={health} connection={connection} busy={hasActiveExecution} open={connectionDetailsOpen} onOpenChange={setConnectionDetailsOpen} onRetry={() => void checkConnection(true)} />
      <ArchiveDialog open={Boolean(archiveTarget)} onOpenChange={(open) => { if (!open) setArchiveTarget('') }} onConfirm={() => { const id = archiveTarget; setArchiveTarget(''); void archiveSession(id) }} />
      <BlockedSendDialog message={blockedSend} onClose={() => setBlockedSend('')} />

      {error && !settingsOpen && <div className="global-alert" role="alert"><span>{error}</span>{connection !== 'connected' && <button onClick={() => void retryHealth()}>重试连接</button>}<button onClick={() => setError('')}>关闭</button></div>}
      {notice && <div className="global-notice" role="status"><span>{notice}</span><button onClick={() => setNotice('')}>关闭</button></div>}

      <main className={`workspace-grid ${conversationOpen ? '' : 'conversation-hidden'} ${conversationOpen && !activeBusiness ? 'conversation-focus' : ''} ${railCollapsed ? 'rail-collapsed' : ''}`} style={{ '--business-width': `${businessWidth}px` } as CSSProperties}>
        <SessionRail
          sessions={sessions}
          selectedId={selectedSessionId}
          loading={loading}
          renamingId={renamingId}
          onSelect={(id) => { if (id !== selectedSessionId) transitionView(() => chooseSession(id)) }}
          onCreate={() => void createSession()}
          onArchive={setArchiveTarget}
          onRenameStart={setRenamingId}
          onRename={renameSession}
          query={sessionQuery}
          onQueryChange={setSessionQuery}
          collapsed={railCollapsed}
          onToggle={() => transitionView(() => setRailCollapsed((value) => !value))}
          footer={<div className="rail-connections"><button className="rail-connection-state" onClick={() => setConnectionDetailsOpen(true)} aria-label="查看连接状态"><span className={`connection-dot ${connection}`} />{connectionLabel(connection)}<span className="health-separator">·</span><span className={`odoo-health odoo-${odooHealthStatus(health)}`}>Odoo {healthLabel(odooHealthStatus(health))}</span><ArrowUpRight size={13} /></button>
        <RadixTooltip content="配置模型与 Odoo 连接"><RadixButton aria-label="连接设置" ref={settingsButtonRef} className="settings-button" variant="soft" onClick={() => void openSettings()}><Settings2 size={15} />连接设置</RadixButton></RadixTooltip>
        <RadixButton className="open-odoo-button" aria-label="打开 Odoo" variant="soft" disabled={openingOdoo} onClick={() => void openOdoo()}>{openingOdoo ? <LoaderCircle className="spin" size={15} /> : <ExternalLink size={15} />}打开 Odoo</RadixButton></div>}
        />
        <BusinessWorkspace
          session={session}
          activeBusiness={activeBusiness}
          detail={businessDetail}
          liveMessages={liveMessages}
          tab={tab}
          trace={trace}
          traceTarget={traceTarget}
          traceLoading={traceLoading}
          onLoadTraceDetail={loadTraceDetail}
          businessLoading={businessLoading}
          loading={loading}
          starting={startingBusinessId === selectedBusinessId}
          focusedApprovalId={focusedApprovalId}
          focusedApprovalRequest={focusedApprovalRequest}
          selectedRunId={selectedRunId}
          onBusinessSelect={(id) => { if (id !== selectedBusinessId) transitionView(() => chooseBusiness(id)); else chooseBusiness(id) }}
          onTabChange={(value) => { if (value !== tab) transitionView(() => setTab(value)) }}
          onRunSelect={(id) => { setTraceTarget(null); setSelectedRunId(id) }}
          onRefresh={() => void refreshBusiness()}
          onReconcileBusiness={() => void refreshBusiness(true)}
          onStart={() => void startRun()}
          onCancel={(run) => void cancelRun(run)}
          onApproval={(approval, decision) => void decideApproval(approval, decision)}
          onRequestRevision={requestApprovalRevision}
          onReconcile={(approval) => void reconcileApproval(approval)}
          onTraceTarget={openTraceTarget}
          onToggleConversation={() => transitionView(toggleConversation)}
          conversationOpen={conversationOpen}
          onExport={(runId) => void exportBusiness(runId)}
          onSnapshot={() => void openSessionSnapshot()}
          snapshotOpening={snapshotOpening}
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
          onOpenApprovals={openApprovals}
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
          proposalBusy={proposalBusy}
          proposalUnavailable={proposalUnavailable}
          pendingApprovals={(businessDetail?.approvals ?? []).filter(isPendingApproval)}
          approvalBusinessName={activeBusiness?.title || '当前业务'}
          approvalProgress={approvalProgress}
          onOpenApprovals={openApprovals}
          onOpenExecution={() => transitionView(() => setTab('execution'))}
          approvalActivity={businessDetail?.activity}
          businesses={session?.businesses ?? []}
          messageBusinessId={messageBusinessId}
          onMessageBusinessChange={setMessageBusinessId}
          onDraftChange={setDraft}
          onSubmit={sendMessage}
          pendingMaterials={pendingMaterials}
          reusedMaterials={(() => {
            const contextBusiness = session?.businesses.find((business) => business.id === resolvedMessageBusinessId)
            const materialIds = contextBusiness?.material_ids?.length ? contextBusiness.material_ids : (session?.session.pending_material_ids ?? [])
            return (session?.materials ?? []).filter((material) => materialIds.includes(material.id)) as MaterialRecord[]
          })()}
          materialsBusy={materialsBusy}
          onFiles={(files) => void importMaterials(files)}
          onRemoveMaterial={(id) => setPendingMaterials((current) => current.filter((material) => material.id !== id))}
          onStarter={(goal) => setDraft(goal)}
          onProposal={(proposal, confirmed) => void confirmProposal(proposal, confirmed)}
          onOpenBusiness={(id) => transitionView(() => chooseBusiness(id))}
          onCancelConversation={(run) => void cancelConversation(run)}
        />}
      </main>
    </div></Theme>
  )
}
