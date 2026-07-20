import { ArrowLeft, ArrowRight, GripVertical, PanelLeftClose } from 'lucide-react'
import { useState } from 'react'

import type { PanelId, WorkspaceSnapshot } from '../workspaceLayout'
import { panelNames } from '../workspaceLayout'

interface WorkbenchLayoutProps {
  workspace: WorkspaceSnapshot
  onCollapse: (panel: PanelId, collapsed: boolean) => void
  onMove: (panel: PanelId, targetIndex: number) => void
  onResize: (before: PanelId, after: PanelId, delta: number) => void
  jobs: React.ReactNode
  center: React.ReactNode
  agent: React.ReactNode
}

export function WorkbenchLayout(props: WorkbenchLayoutProps) {
  const [insertionTarget, setInsertionTarget] = useState<PanelId | null>(null)
  const layout = props.workspace.layouts[props.workspace.selectedPreset]
  const content: Record<PanelId, React.ReactNode> = { jobs: props.jobs, center: props.center, agent: props.agent }
  const visibleOrder = layout.order.filter(panel => !layout.collapsed.includes(panel))

  const panel = (panelId: PanelId) => {
    const index = layout.order.indexOf(panelId)
    const visibleIndex = visibleOrder.indexOf(panelId)
    const previous = visibleIndex > 0 ? visibleOrder[visibleIndex - 1] : undefined
    return (
      <section
        className={`workbench-panel${insertionTarget === panelId ? ' insertion-target' : ''}`}
        data-testid={`panel-${panelId}`}
        hidden={layout.collapsed.includes(panelId)}
        key={panelId}
        onDragOver={event => { event.preventDefault(); setInsertionTarget(panelId) }}
        onDrop={event => {
          event.preventDefault()
          const source = event.dataTransfer.getData('application/x-jobos-panel') as PanelId
          if (source && source !== panelId) props.onMove(source, index)
          setInsertionTarget(null)
        }}
        style={{ order: index, flexBasis: `${layout.widths[panelId]}px` }}
      >
        {previous ? (
          <ResizeHandle before={previous} after={panelId} onResize={props.onResize} widths={layout.widths} />
        ) : null}
        <div className="panel-layout-controls">
          <button
            aria-label={`Move ${panelNames[panelId]} left`}
            className="panel-control"
            disabled={index === 0}
            onClick={() => props.onMove(panelId, index - 1)}
            type="button"
          ><ArrowLeft aria-hidden="true" size={13} /></button>
          <button
            aria-label={`Reorder ${panelNames[panelId]}`}
            className="panel-control drag-control"
            draggable
            onDragStart={event => {
              event.dataTransfer.effectAllowed = 'move'
              event.dataTransfer.setData('application/x-jobos-panel', panelId)
            }}
            title={`Drag to reorder ${panelNames[panelId]}`}
            type="button"
          ><GripVertical aria-hidden="true" size={14} /></button>
          <button
            aria-label={`Move ${panelNames[panelId]} right`}
            className="panel-control"
            disabled={index === layout.order.length - 1}
            onClick={() => props.onMove(panelId, index + 1)}
            type="button"
          ><ArrowRight aria-hidden="true" size={13} /></button>
          <button
            aria-expanded="true"
            aria-label={`Collapse ${panelNames[panelId]}`}
            className="panel-control"
            onClick={() => props.onCollapse(panelId, true)}
            type="button"
          ><PanelLeftClose aria-hidden="true" size={14} /></button>
        </div>
        {content[panelId]}
      </section>
    )
  }

  return (
    <div className="workbench-wrap">
      {layout.collapsed.length ? (
        <nav aria-label="Collapsed panels" className="collapsed-panels">
          {layout.order.filter(item => layout.collapsed.includes(item)).map(panelId => (
            <button aria-expanded="false" key={panelId} onClick={() => props.onCollapse(panelId, false)} type="button">
              Reopen {panelNames[panelId]}
            </button>
          ))}
        </nav>
      ) : null}
      <div className="workbench" onDragLeave={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node)) setInsertionTarget(null)
      }}>
        {panel('jobs')}
        {panel('center')}
        {panel('agent')}
      </div>
    </div>
  )
}

function ResizeHandle({ before, after, onResize, widths }: {
  before: PanelId
  after: PanelId
  onResize: (before: PanelId, after: PanelId, delta: number) => void
  widths: Record<PanelId, number>
}) {
  const start = (startX: number) => {
    let lastX = startX
    const move = (event: PointerEvent) => {
      const delta = event.clientX - lastX
      lastX = event.clientX
      if (delta) onResize(before, after, delta)
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop, { once: true })
  }
  return (
    <div
      aria-label={`Resize ${panelNames[before]} and ${panelNames[after]}`}
      aria-orientation="vertical"
      aria-valuemax={1600}
      aria-valuemin={220}
      aria-valuenow={widths[before]}
      className="panel-resize-handle"
      onKeyDown={event => {
        if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
          event.preventDefault()
          onResize(before, after, event.key === 'ArrowRight' ? 20 : -20)
        }
      }}
      onPointerDown={event => { event.currentTarget.setPointerCapture?.(event.pointerId); start(event.clientX) }}
      role="separator"
      tabIndex={0}
      title={`Drag or use arrow keys to resize ${panelNames[before]}`}
    />
  )
}
