import { Button as RadixButton,IconButton as RadixIconButton,Tooltip as RadixTooltip } from '@radix-ui/themes'
import { Activity,ArrowUpRight,ExternalLink,LoaderCircle,Minus,Settings2,X } from 'lucide-react'
import { type CSSProperties } from 'react'
import { BusinessWorkspace } from './features/business/BusinessWorkspace'
import { ArchiveDialog,BlockedSendDialog,ConversationPane,SessionRail } from './features/conversation/ConversationPane'
import { ConnectionDetailsDialog,SettingsDialog } from './features/settings/SettingsDialog'
import { connectionLabel,healthLabel,isPendingApproval,odooHealthStatus } from './presentation'
import { useWorkbench } from './useWorkbench'
import { MaterialRecord } from './view-types'

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
  } = useWorkbench()


  return (
    <div className="app-shell">
      <header className="window-bar">
        <div className="brand-lockup"><span className="brand-mark"><Activity size={16} strokeWidth={2.5} /></span><span>ERP-agent</span><small>Odoo 业务执行</small></div>
        <button className="window-bar-state" onClick={() => setConnectionDetailsOpen(true)} aria-label="查看连接状态"><span className={`connection-dot ${connection}`} />{connectionLabel(connection)}<span className="health-separator">·</span><span className={`odoo-health odoo-${odooHealthStatus(health)}`}>Odoo {healthLabel(odooHealthStatus(health))}</span><ArrowUpRight size={13} /></button>
        <RadixTooltip content="配置模型与 Odoo 连接"><RadixButton ref={settingsButtonRef} className="settings-button" variant="soft" onClick={() => void openSettings()}><Settings2 size={15} />连接设置</RadixButton></RadixTooltip>
        <RadixButton className="open-odoo-button" variant="soft" disabled={openingOdoo} onClick={() => void openOdoo()}>{openingOdoo ? <LoaderCircle className="spin" size={15} /> : <ExternalLink size={15} />}打开 Odoo</RadixButton>
        <div className="window-actions">
          <RadixTooltip content="最小化"><RadixIconButton variant="ghost" aria-label="最小化" onClick={() => void window.workbench?.windowControl('minimize')}><Minus size={16} /></RadixIconButton></RadixTooltip>
          <RadixTooltip content="最大化"><RadixIconButton variant="ghost" aria-label="最大化" onClick={() => void window.workbench?.windowControl('maximize')}><span className="window-maximize-glyph" /></RadixIconButton></RadixTooltip>
          <RadixTooltip content="关闭"><RadixIconButton variant="ghost" className="close" aria-label="关闭" onClick={() => void window.workbench?.windowControl('close')}><X size={16} /></RadixIconButton></RadixTooltip>
        </div>
      </header>

      {settingsOpen && <SettingsDialog settings={settings} draft={settingsDraft} saving={settingsSaving} onChange={(key, value) => setSettingsDraft((current) => ({ ...current, [key]: value }))} onClose={closeSettings} onSave={() => void saveSettings()} />}
      <ConnectionDetailsDialog health={health} connection={connection} busy={hasActiveExecution} open={connectionDetailsOpen} onOpenChange={setConnectionDetailsOpen} onRetry={() => void checkConnection(true)} />
      <ArchiveDialog open={Boolean(archiveTarget)} onOpenChange={(open) => { if (!open) setArchiveTarget('') }} onConfirm={() => { const id = archiveTarget; setArchiveTarget(''); void archiveSession(id) }} />
      <BlockedSendDialog message={blockedSend} onClose={() => setBlockedSend('')} />

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
          liveMessages={liveMessages}
          tab={tab}
          trace={trace}
          traceTarget={traceTarget}
          traceLoading={traceLoading}
          onLoadTraceDetail={loadTraceDetail}
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
          onRequestRevision={requestApprovalRevision}
          onReconcile={(approval) => void reconcileApproval(approval)}
          onTraceTarget={openTraceTarget}
          onToggleConversation={toggleConversation}
          conversationOpen={conversationOpen}
          onExport={(runId) => void exportBusiness(runId)}
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
          proposalBusy={proposalBusy}
          proposalUnavailable={proposalUnavailable}
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
          onOpenBusiness={chooseBusiness}
          onCancelConversation={(run) => void cancelConversation(run)}
        />}
      </main>
    </div>
  )
}
