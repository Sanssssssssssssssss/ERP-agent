import { ChevronRight } from 'lucide-react'
import type { CSSProperties, ReactNode } from 'react'

/* Adapted from Langfuse VirtualizedTreeNodeWrapper.tsx (MIT, Copyright (c) 2023-2026 ClickHouse, Inc.).
 * Source: https://github.com/langfuse/langfuse/blob/593677cd4de5fb78dbdf1ca448de89478413229c/web/src/features/traces/components/VirtualizedTreeNodeWrapper.tsx
 * Retains capped visual depth, ancestor connectors, last-sibling branch and
 * independent collapse control; uses existing native button/CSS, no runtime.
 */
export function TraceTreeRow({ depth, treeLines, lastSibling, hasChildren, collapsed, selected, issue, title, onToggle, children }: {
  depth: number; treeLines: boolean[]; lastSibling: boolean; hasChildren: boolean;
  collapsed: boolean; selected: boolean; issue: boolean; title: string;
  onToggle: () => void; children: ReactNode
}) {
  const visualDepth = Math.min(depth, 4)
  const childrenAreCapped = depth >= 4
  return <div className={`trace-tree-row ${selected ? 'trace-selected-row' : ''} ${issue ? 'trace-row-issue' : ''}`} style={{ '--trace-depth': visualDepth } as CSSProperties}>
    <div className="trace-connectors" aria-hidden="true">
      {Array.from({ length: Math.max(0, visualDepth - 1) }, (_, index) => <span className="trace-indent" key={index}>{treeLines[index] && <i className="trace-ancestor-line" />}</span>)}
      {visualDepth > 0 && <span className={`trace-indent trace-branch ${lastSibling ? 'trace-last-branch' : ''}`} />}
    </div>
    <div className={`trace-collapse-slot ${hasChildren && !collapsed && !childrenAreCapped ? 'trace-child-spine' : ''}`}>
      {hasChildren ? <button className="trace-collapse" type="button" aria-label={`${collapsed ? '展开' : '折叠'} ${title}`} aria-expanded={!collapsed} onClick={onToggle}><ChevronRight size={13} style={{ transform: collapsed ? undefined : 'rotate(90deg)' }} /></button> : <span className="trace-collapse-spacer"><i className="trace-leaf-dot" /></span>}
    </div>
    <div className="trace-node-body">{children}</div>
  </div>
}
