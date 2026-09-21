import { Badge as RadixBadge } from '@radix-ui/themes'
import { CircleAlert,CircleCheck,CircleDashed,Clock3,Minus } from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'


export function MessageText({ text, collapsible = true }: { text: string; collapsible?: boolean }) {
  const value = text || '（空消息）'
  const content = <Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt || '图片'}</span>, a: ({ children }) => <span>{children}</span>, table: ({ children }) => <div className="message-table-wrap"><table>{children}</table></div> }}>{value}</Markdown>
  if (value.length <= 900 || !collapsible) return <div className="message-text message-markdown">{content}</div>
  return <div><MessageText text={`${value.slice(0, 360)}…`} collapsible={false} /><details className="message-full"><summary>查看完整消息（{value.length.toLocaleString('zh-CN')} 字）</summary><div className="message-text message-markdown">{content}</div></details></div>
}

export function StatusBadge({ status, label }: { status?: string; label: string }) {
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

export function EmptyState({ title, detail }: { title: string; detail: string }) { return <div className="empty-state"><span className="empty-glyph">○</span><strong>{title}</strong><p>{detail}</p></div> }
