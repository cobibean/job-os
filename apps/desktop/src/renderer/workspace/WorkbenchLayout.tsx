import { ArrowLeft, ArrowRight, GripVertical, PanelLeftClose } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { PanelId, WorkspaceSnapshot } from './workspaceLayout'
import { panelNames } from './workspaceLayout'

interface WorkbenchLayoutProps {
  workspace: WorkspaceSnapshot
  onCollapse: (panel: PanelId, collapsed: boolean) => void
  onReorderInteractionChange?: (active: boolean) => boolean | void | Promise<boolean | void>
  onMove: (panel: PanelId, targetIndex: number) => void
  onResize: (before: PanelId, after: PanelId, delta: number) => void
  jobs: React.ReactNode
  center: React.ReactNode
  agent: React.ReactNode
}

export function WorkbenchLayout(props: WorkbenchLayoutProps) {
  const [insertionTarget, setInsertionTarget] = useState<PanelId | null>(null)
  const collapseControls = useRef<Partial<Record<PanelId, HTMLButtonElement | null>>>({})
  const panelElements = useRef<Partial<Record<PanelId, HTMLElement | null>>>({})
  const recoveryControls = useRef<Partial<Record<PanelId, HTMLButtonElement | null>>>({})
  const reorderPointer = useRef<{
    pointerId: number
    source: PanelId
    target: PanelId | null
    startX: number
    startY: number
    clientX: number
    clientY: number
    moved: boolean
    ready: boolean
    suppressClickAfterDrag: boolean
  } | null>(null)
  const reorderReleaseCleanup = useRef<(() => void) | null>(null)
  const suppressedArrangeClick = useRef<PanelId | null>(null)
  const layout = props.workspace.layouts[props.workspace.selectedPreset]
  const content: Record<PanelId, React.ReactNode> = { jobs: props.jobs, center: props.center, agent: props.agent }
  const visibleOrder = layout.order.filter(panel => !layout.collapsed.includes(panel))

  useEffect(() => () => {
    reorderPointer.current = null
    reorderReleaseCleanup.current?.()
    reorderReleaseCleanup.current = null
    setInsertionTarget(null)
    void props.onReorderInteractionChange?.(false)
  }, [props.onReorderInteractionChange])

  const targetAt = (clientX: number, clientY: number) => visibleOrder.find(panelId => {
    const rect = panelElements.current[panelId]?.getBoundingClientRect()
    return Boolean(
      rect
      && clientX >= rect.left
      && clientX <= rect.right
      && clientY >= rect.top
      && clientY <= rect.bottom
    )
  }) ?? null

  const updateReorderTarget = (clientX: number, clientY: number) => {
    const gesture = reorderPointer.current
    if (!gesture) return
    gesture.clientX = clientX
    gesture.clientY = clientY
    if (!gesture.moved) {
      gesture.moved = Math.hypot(clientX - gesture.startX, clientY - gesture.startY) >= 4
    }
    if (!gesture.ready || !gesture.moved) return
    gesture.target = targetAt(clientX, clientY)
    setInsertionTarget(gesture.target)
  }

  const finishReorderInteraction = (commit: boolean) => {
    const gesture = reorderPointer.current
    reorderPointer.current = null
    reorderReleaseCleanup.current?.()
    reorderReleaseCleanup.current = null
    setInsertionTarget(null)
    if (commit && gesture?.moved && gesture.suppressClickAfterDrag) suppressedArrangeClick.current = gesture.source
    if (commit && gesture?.ready && gesture.moved && gesture.target && gesture.target !== gesture.source) {
      props.onMove(gesture.source, layout.order.indexOf(gesture.target))
    }
    void props.onReorderInteractionChange?.(false)
  }

  const prepareReorderInteraction = (
    event: React.PointerEvent<HTMLElement>,
    source: PanelId,
    options: { preserveClick?: boolean } = {}
  ) => {
    if (event.button !== 0) return
    if (!options.preserveClick) event.preventDefault()
    reorderReleaseCleanup.current?.()
    const pointerId = event.pointerId
    const gesture = {
      pointerId,
      source,
      target: null,
      startX: event.clientX,
      startY: event.clientY,
      clientX: event.clientX,
      clientY: event.clientY,
      moved: false,
      suppressClickAfterDrag: Boolean(options.preserveClick),
      ready: false
    }
    reorderPointer.current = gesture
    event.currentTarget.setPointerCapture?.(pointerId)
    const move = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId === pointerId) updateReorderTarget(pointerEvent.clientX, pointerEvent.clientY)
    }
    const release = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId !== pointerId) return
      updateReorderTarget(pointerEvent.clientX, pointerEvent.clientY)
      finishReorderInteraction(true)
    }
    const cancel = (pointerEvent: PointerEvent) => {
      if (pointerEvent.pointerId === pointerId) finishReorderInteraction(false)
    }
    const cancelWithKeyboard = (keyboardEvent: KeyboardEvent) => {
      if (keyboardEvent.key === 'Escape' && reorderPointer.current === gesture) {
        keyboardEvent.preventDefault()
        finishReorderInteraction(false)
      }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', release)
    window.addEventListener('pointercancel', cancel)
    window.addEventListener('keydown', cancelWithKeyboard)
    reorderReleaseCleanup.current = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', release)
      window.removeEventListener('pointercancel', cancel)
      window.removeEventListener('keydown', cancelWithKeyboard)
    }
    void Promise.resolve(props.onReorderInteractionChange?.(true)).then(approved => {
      if (reorderPointer.current !== gesture) return
      if (approved === false) {
        finishReorderInteraction(false)
        return
      }
      gesture.ready = true
      updateReorderTarget(gesture.clientX, gesture.clientY)
    }).catch(() => {
      if (reorderPointer.current === gesture) finishReorderInteraction(false)
    })
  }

  const panel = (panelId: PanelId) => {
    const index = layout.order.indexOf(panelId)
    const visibleIndex = visibleOrder.indexOf(panelId)
    const previous = visibleIndex > 0 ? visibleOrder[visibleIndex - 1] : undefined
    return (
      <section
        className={`workbench-panel${insertionTarget === panelId ? ' insertion-target' : ''}`}
        data-testid={`panel-${panelId}`}
        hidden={layout.collapsed.includes(panelId)}
        id={`workbench-panel-${panelId}`}
        key={panelId}
        ref={element => { panelElements.current[panelId] = element }}
        style={{ flexBasis: `${layout.widths[panelId]}px` }}
      >
        {previous ? (
          <ResizeHandle before={previous} after={panelId} onResize={props.onResize} widths={layout.widths} />
        ) : null}
        <div aria-label={`${panelNames[panelId]} layout controls`} className="panel-layout-controls">
          <span className="sr-only">{panelNames[panelId]}</span>
          <details className="panel-arrange-menu">
            <summary
              aria-label={`Arrange ${panelNames[panelId]}`}
              className="panel-control drag-control"
              onClick={event => {
                if (suppressedArrangeClick.current !== panelId) return
                event.preventDefault()
                suppressedArrangeClick.current = null
              }}
              onPointerDown={event => prepareReorderInteraction(event, panelId, { preserveClick: true })}
              title={`Drag to reorder ${panelNames[panelId]}; click for move controls`}
            ><GripVertical aria-hidden="true" size={14} /></summary>
            <span className="panel-arrange-actions" role="toolbar" aria-label={`Arrange ${panelNames[panelId]}`}>
              <button
                aria-label={`Move ${panelNames[panelId]} left`}
                className="panel-control"
                disabled={index === 0}
                onClick={() => props.onMove(panelId, index - 1)}
                type="button"
              ><ArrowLeft aria-hidden="true" size={13} /></button>

              <button
                aria-label={`Move ${panelNames[panelId]} right`}
                className="panel-control"
                disabled={index === layout.order.length - 1}
                onClick={() => props.onMove(panelId, index + 1)}
                type="button"
              ><ArrowRight aria-hidden="true" size={13} /></button>
            </span>
          </details>
          <button
            aria-controls={`workbench-panel-${panelId}`}
            aria-expanded="true"
            aria-label={`Collapse ${panelNames[panelId]}`}
            className="panel-control"
            onClick={() => {
              props.onCollapse(panelId, true)
              window.setTimeout(() => recoveryControls.current[panelId]?.focus(), 0)
            }}
            ref={control => { collapseControls.current[panelId] = control }}
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
            <button
              aria-controls={`workbench-panel-${panelId}`}
              aria-expanded="false"
              key={panelId}
              onClick={() => {
                props.onCollapse(panelId, false)
                window.setTimeout(() => collapseControls.current[panelId]?.focus(), 0)
              }}
              ref={control => { recoveryControls.current[panelId] = control }}
              type="button"
            >
              Reopen {panelNames[panelId]}
            </button>
          ))}
        </nav>
      ) : null}
      <div className="workbench">
        {layout.order.map(panel)}
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
