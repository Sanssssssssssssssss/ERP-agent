import { Badge as RadixBadge } from '@radix-ui/themes'
import { CircleAlert,CircleCheck,CircleDashed,Clock3,Minus } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useState } from 'react'


export function MessageText({ text, collapsible = true }: { text: string; collapsible?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const value = text || '（空消息）'
  const canCollapse = collapsible && value.length > 900
  const content = <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt || '图片'}</span>, a: ({ children }) => <span>{children}</span>, table: ({ children }) => <div className="message-table-wrap"><table>{children}</table></div> }}>{canCollapse && !expanded ? `${value.slice(0, 360)}…` : value}</Markdown>
  return <div><div className="message-text message-markdown">{content}</div>{canCollapse && <button type="button" className="message-full receipt-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? '收起消息' : `查看完整消息（${value.length.toLocaleString('zh-CN')} 字）`}</button>}</div>
}

export function StatusBadge({ status, label }: { status?: string; label: string }) {
  const neutral = label === '—（不适用）'
  const icon = neutral ? <Minus size={13} /> : status === 'running' || status === 'awaiting_approval' || status === 'awaiting_input' || status === 'pending' || status === 'pending_approval'
    ? <Clock3 size={13} />
    : status === 'failed' || status === 'rejected' || status === 'expired' || status === 'known_failed'
      ? <CircleAlert size={13} />
    : status === 'passed' || status === 'verified' || status === 'approved'
        ? <CircleCheck size={13} />
        : <CircleDashed size={13} />
  return <RadixBadge className={`state-badge ${neutral ? 'state-neutral' : `state-${status || 'unknown'}`}`} variant="soft">{icon}{label}</RadixBadge>
}

export function EmptyState({ title, detail }: { title: string; detail: string }) { return <div className="empty-state"><strong>{title}</strong><p>{detail}</p></div> }
