import { Button as RadixButton,Dialog as RadixDialog } from '@radix-ui/themes'
import { LoaderCircle,RefreshCw } from 'lucide-react'
import { useEffect,useRef } from 'react'
import { connectionLabel,environmentLabel,healthLabel,odooHealthStatus } from '../../presentation'
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

export function SettingsDialog({ settings, draft, saving, onChange, onClose, onSave }: { settings: Settings | null; draft: Record<string, string>; saving: boolean; onChange: (key: string, value: string) => void; onClose: () => void; onSave: () => void }) {
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
