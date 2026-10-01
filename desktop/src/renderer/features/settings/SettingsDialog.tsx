import { Button as RadixButton,Dialog as RadixDialog } from '@radix-ui/themes'
import { ExternalLink,FolderOpen,LoaderCircle,RefreshCw,X } from 'lucide-react'
import { useRef,useState } from 'react'
import { connectionLabel,healthLabel,odooHealthStatus } from '../../presentation'
import {
Health,
Settings,
formatInstant
} from '../../protocol'
import { ConnectionState } from '../../view-types'

export function ConnectionDetailsDialog({ health, connection, busy, open, onOpenChange, onRetry }: { health: Health | null; connection: ConnectionState; busy: boolean; open: boolean; onOpenChange: (open: boolean) => void; onRetry: () => void }) {
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

export function SettingsDialog({ settings, draft, saving, error, onDismissError, onChooseDirectory, onChange, onClose, onSave, onOpenOdoo, openingOdoo = false, appearance = 'light', onAppearanceChange }: { error?: string; onDismissError?: () => void; onChooseDirectory: () => Promise<void>; appearance?: 'light' | 'dark'; onAppearanceChange?: (appearance: 'light' | 'dark') => void; onOpenOdoo?: () => void; openingOdoo?: boolean; settings: Settings | null; draft: Record<string, string>; saving: boolean; onChange: (key: string, value: string) => void; onClose: () => void; onSave: () => void }) {
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const [choosing, setChoosing] = useState(false)
  const busy = saving || choosing
  const field = (key: string, label: string, type = 'text') => <label className="settings-field"><span>{label}</span><input type={type} value={draft[key] || ''} disabled={busy} onChange={(event) => onChange(key, event.target.value)} /></label>
  return <RadixDialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose() }}>
    <RadixDialog.Content className="settings-dialog" maxWidth="780px" onOpenAutoFocus={(event) => { event.preventDefault(); closeButtonRef.current?.focus() }} onEscapeKeyDown={(event) => { if (busy) event.preventDefault() }} onPointerDownOutside={(event) => { if (busy) event.preventDefault() }}>
      <header><RadixDialog.Title>连接设置</RadixDialog.Title><button ref={closeButtonRef} className="modal-close" disabled={busy} onClick={onClose} aria-label="关闭设置"><X size={19} /></button></header>
      <div className="settings-body">
        <RadixDialog.Description className="settings-description">连接、聊天存储与外观。密钥留空保留。</RadixDialog.Description>
        {error && <div className="settings-error" role="alert"><span>{error}</span><button aria-label="关闭错误提示" onClick={onDismissError}><X size={16} /></button></div>}
        <section className="settings-section"><h3>模型</h3><div className="settings-grid">{field('model', '模型')}{field('base_url', '模型地址')}{field('model_key', settings?.has_model_key ? '模型密钥（留空保留）' : '模型密钥', 'password')}</div></section>
        <section className="settings-section"><div className="settings-section-heading"><h3>Odoo</h3>{onOpenOdoo && <RadixButton variant="ghost" size="1" disabled={busy || openingOdoo} onClick={onOpenOdoo}><ExternalLink size={14} />在浏览器登录 Odoo</RadixButton>}</div><div className="settings-grid">{field('odoo_url', 'Odoo 地址')}{field('odoo_db', 'Odoo 数据库')}{field('odoo_username', 'Odoo 用户名')}{field('odoo_key', settings?.has_odoo_key ? 'Odoo 密钥（留空保留）' : 'Odoo 密钥', 'password')}</div></section>
        <section className="settings-section"><h3>聊天数据</h3><div className="settings-storage"><label className="settings-field"><span>存储目录</span><input aria-label="聊天数据存储目录" readOnly value={draft.data_dir || ''} title={draft.data_dir} /></label><RadixButton variant="soft" disabled={busy} onClick={async () => { setChoosing(true); try { await onChooseDirectory() } finally { setChoosing(false) } }}><FolderOpen size={15} />选择目录</RadixButton></div><p className="settings-help">使用所选位置下的 ERP-agent-data 文件夹。保存时复制聊天、材料和运行记录，原目录保留；目标文件夹需尚未存在。</p>{draft.data_dir && draft.data_dir !== settings?.data_dir && <p className="settings-storage-pending">保存后切换到此目录</p>}</section>
        <section className="settings-section settings-preferences"><h3>偏好</h3>{onAppearanceChange && <label><input type="checkbox" checked={appearance === 'dark'} disabled={busy} onChange={(event) => onAppearanceChange(event.target.checked ? 'dark' : 'light')} />深色外观</label>}<label><input type="checkbox" checked={draft.long_term_memory === 'on'} disabled={busy} onChange={(event) => onChange('long_term_memory', event.target.checked ? 'on' : 'off')} aria-describedby="memory-setting-help" />启用长期记忆（Mem0）</label><p className="settings-help" id="memory-setting-help">结束后自动学习，会额外调用模型。关闭后保留已有记忆。</p></section>
      </div>
      <footer className="settings-footer"><span>{saving ? '正在保存，请稍候…' : ''}</span><div><button className="secondary-button" disabled={busy} onClick={onClose}>取消</button><button className="primary-button" disabled={busy} onClick={onSave}>{saving ? '保存中…' : '保存连接设置'}</button></div></footer>
    </RadixDialog.Content>
  </RadixDialog.Root>
}
