import { Button as RadixButton } from '@radix-ui/themes'
import { Download, ExternalLink, FileText, LoaderCircle } from 'lucide-react'
import { Fragment, useEffect } from 'react'
import { EmptyState, StatusBadge } from '../../components/common'
import { artifactKindLabel, documentAmount, documentCurrencyFields, documentFacts, documentKey, documentLines, documentModelLabel, documentMoney, documentQuantity, documentSource, documentSourceObservedAt, documentSourceRun, documentSourceTool, documentStateLabel, documentTypeLabel, isDownloadableDocument, isReferenceDocument, materialRowLabel, readableValue } from '../../presentation'
import { BusinessArtifact, Document, formatInstant, jsonText } from '../../protocol'
import { DownloadReceipt, MaterialRecord } from '../../view-types'

export function DocumentsPage({ documents, materials, artifacts, goal, stale, onExport, exporting, exportPath, onOpenDocument, onDownloadDocument, documentDownloads, selectedDocumentKey, onSelectedDocumentKey, onOpenArtifact, onRevealArtifact, onTraceTarget }: { documents: Document[]; materials: MaterialRecord[]; artifacts: BusinessArtifact[]; goal?: string; stale: boolean; onExport: () => void; exporting: boolean; exportPath: string; onOpenDocument: (document: Document) => void; onDownloadDocument: (document: Document, format: 'pdf' | 'csv') => void; documentDownloads: Record<string, DownloadReceipt>; selectedDocumentKey: string; onSelectedDocumentKey: (key: string) => void; onOpenArtifact: (artifact: BusinessArtifact) => void; onRevealArtifact: (artifact: BusinessArtifact) => void; onTraceTarget: (target: { run_id?: string; tool_id?: string; action_id?: string; kind?: string }) => void }) {
  useEffect(() => {
    const current = documents.filter((document) => !isReferenceDocument(document))
    const preferred = current.find(isDownloadableDocument) ?? current[0] ?? documents[0]
    if (!preferred) { if (selectedDocumentKey) onSelectedDocumentKey(''); return }
    if (!documents.some((document) => documentKey(document) === selectedDocumentKey)) onSelectedDocumentKey(documentKey(preferred))
  }, [documents, onSelectedDocumentKey, selectedDocumentKey])
  const selected = documents.find((document) => documentKey(document) === selectedDocumentKey)
  const businessModels = ['sale.order', 'purchase.order', 'account.move', 'stock.picking', 'mrp.production', 'account.payment', 'account.bank.statement.line']
  const resourceGroups = [
    { label: '业务单据', items: documents.filter((document) => !isReferenceDocument(document) && businessModels.includes(document.model)) },
    { label: '往来单位与明细', items: documents.filter((document) => !isReferenceDocument(document) && !businessModels.includes(document.model) && (document.model === 'res.partner' || document.model.endsWith('.line') || document.model === 'stock.move')) },
    { label: '参考记录', items: documents.filter((document) => isReferenceDocument(document) || (!businessModels.includes(document.model) && !['res.partner', 'stock.move'].includes(document.model) && !document.model.endsWith('.line'))) }
  ].filter((group) => group.items.length > 0)
  const facts = selected ? documentFacts(selected, documents) : []
  const lines = selected ? documentLines(selected, documents) : []
  const hasQuantity = lines.some((line) => Boolean(documentQuantity(line.fields)))
  const hasUnitPrice = lines.some((line) => typeof line.fields.price_unit === 'number')
  const hasSubtotal = lines.some((line) => typeof line.fields.price_subtotal === 'number')
  return <div className="page-stack">
    <div className="page-intro">
      <h3>单据与文件</h3>
      <div className="page-actions"><RadixButton className="secondary-button" variant="soft" disabled={exporting} onClick={onExport}>{exporting ? <LoaderCircle className="spin" size={15} /> : <FileText size={15} />}{exporting ? '导出中…' : '导出业务回执'}</RadixButton>{stale && <span className="warning-text">数据可能已过期，请读取最新状态</span>}</div>
    </div>
    {exportPath && <div className="export-receipt" role="status"><strong>回执已导出</strong><span>{exportPath}</span></div>}
    {documents.length === 0 ? <EmptyState title="暂无单据" detail="读取或处理业务后，相关单据会显示在这里。" /> : <div className="resource-layout">
      <nav className="resource-list" aria-label="业务记录">
        {resourceGroups.map((group) => <section className="resource-group" key={group.label}>
          <h4>{group.label}</h4>
          {group.items.map((document) => <button className={`resource-row ${documentKey(document) === selectedDocumentKey ? 'active' : ''}`} type="button" key={documentKey(document)} aria-pressed={documentKey(document) === selectedDocumentKey} onClick={() => onSelectedDocumentKey(documentKey(document))}>
            <span className="resource-icon"><FileText size={15} /></span>
            <span><strong>{document.name || `${documentTypeLabel(document)} ${document.id}`}</strong><small className="resource-row-summary">{documentTypeLabel(document)}{document.state ? ` · ${documentStateLabel(document.model, document.state)}` : ''}</small>{documentAmount(document, documents) && <small className="resource-row-amount">{documentAmount(document, documents)}</small>}</span>
          </button>)}
        </section>)}
      </nav>
      <section className="resource-preview">
        {selected ? <>
          <div className="preview-head">
            <div><h3>{selected.name || `${documentTypeLabel(selected)} ${selected.id}`}</h3><p>{documentTypeLabel(selected)}{isReferenceDocument(selected) ? ' · 参考记录' : ''}</p></div>
            <div className="preview-actions">
              <RadixButton className="secondary-button" variant="soft" onClick={() => onOpenDocument(selected)}><ExternalLink size={15} />在 Odoo 打开</RadixButton>
              <DocumentDownloadActions document={selected} receipts={documentDownloads} onDownload={onDownloadDocument} onOpenArtifact={onOpenArtifact} onRevealArtifact={onRevealArtifact} />
            </div>
          </div>
          {(selected.state || facts.length > 0) && <dl className="preview-meta document-facts">
            {selected.state && <><dt>状态</dt><dd><StatusBadge status={selected.state} label={documentStateLabel(selected.model, selected.state)} /></dd></>}
            {facts.map(({ label, value }) => <Fragment key={label}><dt>{label}</dt><dd className={['含税金额', '金额', '未付余额'].includes(label) ? 'document-fact-amount' : undefined}>{value}</dd></Fragment>)}
          </dl>}
          {!selected.state && facts.length === 0 && <p className="muted">详细信息尚未读取，可在 Odoo 查看。</p>}
          {lines.length > 0 && <div className="document-lines">
            <table><caption>已读取明细 · {lines.length} 条</caption><thead><tr><th scope="col">产品 / 明细</th>{hasQuantity && <th scope="col">数量</th>}{hasUnitPrice && <th scope="col">单价</th>}{hasSubtotal && <th scope="col">未税小计</th>}<th scope="col">操作</th></tr></thead>
              <tbody>{lines.map((line) => {
                const fields = documentCurrencyFields(line, documents)
                return <tr key={documentKey(line)}>
                  <td>{readableValue(line.fields.product_name ?? line.fields.product_id ?? line.name)}</td>
                  {hasQuantity && <td>{documentQuantity(line.fields) || '未读取'}</td>}
                  {hasUnitPrice && <td>{documentMoney(line.fields.price_unit, fields) || '未读取'}</td>}
                  {hasSubtotal && <td>{documentMoney(line.fields.price_subtotal, fields) || '未读取'}</td>}
                  <td><RadixButton className="inline-action" variant="ghost" aria-label={`在 Odoo 打开 ${line.name || line.id}`} onClick={() => onOpenDocument(line)}><ExternalLink size={14} />打开</RadixButton></td>
                </tr>
              })}</tbody>
            </table>
          </div>}
          {selected.observed_at && <p className="document-refresh">读取于 {formatInstant(selected.observed_at)}</p>}
          <details className="document-audit resource-fields">
            <summary>数据与回执</summary>
            <dl className="preview-meta"><dt>记录</dt><dd>{selected.model} · {selected.id}</dd><dt>数据来源</dt><dd>{documentSource(selected)}</dd></dl>
            {documentSourceRun(selected) && documentSourceTool(selected) && <div className="resource-receipt-actions"><RadixButton className="inline-action" variant="ghost" onClick={() => onTraceTarget({ run_id: documentSourceRun(selected), tool_id: documentSourceTool(selected) })}>查看原始读取回执</RadixButton><span>读取时间：{formatInstant(documentSourceObservedAt(selected))}</span></div>}
            {goal && <details className="goal-details resource-goal"><summary>查看原始目标输入</summary><p>{goal}</p></details>}
            <details className="resource-fields"><summary>查看原始字段</summary><pre>{jsonText(selected.fields)}</pre></details>
          </details>
        </> : <EmptyState title="选择一项单据" detail="选择左侧记录查看详情。" />}
      </section>
    </div>}
    {artifacts.length > 0 && <section className="artifact-section" aria-label="业务文件">
      <div className="section-heading"><h3>文件与回执</h3><span>{artifacts.length} 项</span></div>
      <div className="artifact-list">{artifacts.map((artifact) => <article className={`artifact-row ${artifact.available === false ? 'artifact-missing' : ''}`} key={artifact.id}>
        <div><strong>{artifact.name}</strong><span>{artifactKindLabel(artifact.kind)} · {formatInstant(artifact.created_at)}</span>{artifact.available === false && <small>{artifact.error || '文件不可用'}</small>}</div>
        <div className="artifact-actions"><RadixButton className="inline-action" variant="ghost" disabled={artifact.available === false} onClick={() => onOpenArtifact(artifact)}>打开文件</RadixButton><RadixButton className="inline-action" variant="ghost" disabled={artifact.available === false} onClick={() => onRevealArtifact(artifact)}>显示位置</RadixButton>{artifact.run_id && <RadixButton className="inline-action" variant="ghost" onClick={() => onTraceTarget({ run_id: artifact.run_id })}>查看生成记录</RadixButton>}</div>
      </article>)}</div>
    </section>}
    {materials.length > 0 && <section className="material-history" aria-label="业务材料">
      <div className="section-heading"><h3>上传材料</h3><span>{materials.length} 个文件</span></div>
      <div className="material-history-list">{materials.map((material) => <article className="material-history-row" key={material.id}><div><strong>{material.name}</strong><span>{materialRowLabel(material)} · {material.preview || '暂无预览'}</span></div><small>{material.media_type || '文本材料'}</small></article>)}</div>
    </section>}
  </div>
}

export function DocumentDownloadActions({ document, receipts, onDownload, onOpenArtifact, onRevealArtifact }: { document: Document; receipts: Record<string, DownloadReceipt>; onDownload: (document: Document, format: 'pdf' | 'csv') => void; onOpenArtifact: (artifact: BusinessArtifact) => void; onRevealArtifact: (artifact: BusinessArtifact) => void }) {
  if (!isDownloadableDocument(document)) return null
  const documentName = document.name || `${documentModelLabel(document.model)} ${String(document.id)}`
  const action = (format: 'pdf' | 'csv') => receipts[`${documentKey(document)}:${format}`]
  const renderAction = (format: 'pdf' | 'csv', label: string) => {
    const receipt = action(format)
    return <div className="document-download-action"><RadixButton className="inline-action" variant="ghost" aria-label={`${format === 'pdf' ? '下载' : '导出'} ${documentName} 的 ${format.toUpperCase()}`} disabled={receipt?.status === 'downloading'} onClick={() => onDownload(document, format)}>{receipt?.status === 'downloading' ? <LoaderCircle className="spin" size={13} /> : <Download size={13} />}{receipt?.status === 'downloading' ? '生成中…' : label}</RadixButton>{receipt?.status === 'cancelled' && <small>已取消，可重试</small>}{receipt?.status === 'failed' && <small className="download-error">{receipt.error || '下载失败'}</small>}{receipt?.status === 'completed' && receipt.artifact && <span className="download-receipt"><strong>{receipt.artifact.name}</strong><button type="button" onClick={() => onOpenArtifact(receipt.artifact!)}>打开</button><button type="button" onClick={() => onRevealArtifact(receipt.artifact!)}>位置</button></span>}{receipt?.status === 'completed' && !receipt.artifact && receipt.path && <span className="download-receipt"><strong>{receipt.path}</strong></span>}</div>
  }
  return <div className="document-download-actions" aria-label="单据下载">{renderAction('pdf', '下载 PDF')}{renderAction('csv', '导出明细 CSV')}</div>
}
