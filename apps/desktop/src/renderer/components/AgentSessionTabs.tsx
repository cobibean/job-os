import { Circle, LoaderCircle, Plus, X } from 'lucide-react'
import { useRef, type CSSProperties } from 'react'

import type { AgentSessionsController, AgentSessionViewState } from '../hooks/useAgentSessions'

export function visibleState(session: AgentSessionViewState): 'recovering' | 'quarantined' | 'working' | 'needs-you' | 'done' | 'failed' | 'interrupted' | 'idle' {
  if (session.summary.recoveryState === 'recovering') return 'recovering'
  if (session.summary.recoveryState === 'quarantined') return 'quarantined'
  if (session.conversation.activeTurn?.status === 'waiting') return 'needs-you'
  if (session.conversation.activeTurn) return 'working'
  const terminal = [...session.conversation.entries].reverse().find(event => (
    (event.type === 'assistant_message' || event.type === 'error' || event.type === 'status')
    && ['completed', 'failed', 'interrupted'].includes(event.state)
  ))
  if (terminal?.state === 'failed' && session.unreadTerminal) return 'failed'
  if (terminal?.state === 'completed' && session.unreadTerminal) return 'done'
  if (terminal?.state === 'interrupted' && session.unreadTerminal) return 'interrupted'
  return 'idle'
}

const stateLabel = {
  recovering: 'Recovering',
  quarantined: 'Quarantined',
  working: 'Working',
  'needs-you': 'Needs you',
  done: 'Done',
  failed: 'Failed',
  interrupted: 'Interrupted',
  idle: 'Idle'
} as const

interface AgentSessionTabsProps {
  controller: AgentSessionsController
  onNewChat?: () => void
}

export function AgentSessionTabs({ controller, onNewChat }: AgentSessionTabsProps) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])
  const moveFocus = (index: number) => {
    const count = controller.order.length
    if (!count) return
    const next = (index + count) % count
    const id = controller.order[next]
    if (!id) return
    controller.select(id)
    tabRefs.current[next]?.focus()
  }
  const activeIndex = controller.activeId ? controller.order.indexOf(controller.activeId) : -1
  const activeSession = controller.activeId ? controller.sessions[controller.activeId] : undefined
  const cannotClose = controller.order.length === 1
    || controller.creating
    || activeSession?.summary.recoveryState === 'recovering'
    || activeSession?.summary.recoveryState === 'quarantined'
    || Boolean(activeSession?.conversation.activeTurn)
    || activeSession?.operation !== null
  const closeTitle = controller.order.length === 1
    ? 'At least one session must remain'
    : activeSession?.summary.recoveryState === 'recovering'
        ? 'Wait for remote recovery to finish before closing'
        : activeSession?.summary.recoveryState === 'quarantined'
          ? 'Recover quarantined remote work before closing'
      : activeSession?.conversation.activeTurn
        ? 'Stop this session before closing'
      : controller.creating
        ? 'Wait for the new session to finish opening'
        : activeSession?.operation
          ? `Wait for the pending ${activeSession.operation} operation`
      : 'Close and archive this session'
  return (
    <div aria-label="Agent session controls" className="agent-session-toolbar" role="toolbar">
      <div
        aria-label="Agent sessions"
        className="agent-session-tabs"
        role="tablist"
        style={{ '--agent-session-count': controller.order.length } as CSSProperties}
      >
        {controller.order.map((id, index) => {
          const session = controller.sessions[id]!
          const state = visibleState(session)
          const selected = id === controller.activeId
          return (
            <button
              aria-controls={`agent-session-panel-${id}`}
              aria-label={`Session ${index + 1}, ${stateLabel[state]}`}
              aria-selected={selected}
              className={`agent-session-tab ${selected ? 'selected' : ''}`}
              id={`agent-session-tab-${id}`}
              key={id}
              onClick={() => controller.select(id)}
              onKeyDown={event => {
                if (event.key === 'ArrowRight') { event.preventDefault(); moveFocus(index + 1) }
                else if (event.key === 'ArrowLeft') { event.preventDefault(); moveFocus(index - 1) }
                else if (event.key === 'Home') { event.preventDefault(); moveFocus(0) }
                else if (event.key === 'End') { event.preventDefault(); moveFocus(controller.order.length - 1) }
              }}
              ref={element => { tabRefs.current[index] = element }}
              role="tab"
              tabIndex={selected ? 0 : -1}
              type="button"
              title={`Session ${index + 1} — ${stateLabel[state]}`}
            >
              <span className="agent-session-name"><span className="agent-session-name-full">Session </span>{index + 1}</span>
              {state !== 'idle' && (
                <span className={`agent-session-state ${state}`}>
                  {state === 'working'
                    ? <LoaderCircle aria-hidden="true" className="spin" size={14} />
                    : <Circle aria-hidden="true" fill="currentColor" size={10} />}
                  <span className="agent-session-state-label">{stateLabel[state]}</span>
                </span>
              )}
            </button>
          )
        })}
      </div>
      <div className="agent-session-actions">
        <button
          aria-label={activeIndex >= 0 ? `Close Session ${activeIndex + 1}` : 'Close session'}
          className="agent-session-close"
          disabled={cannotClose || !activeSession}
          onClick={() => { if (controller.activeId) void controller.archive(controller.activeId) }}
          title={closeTitle}
          type="button"
        ><X aria-hidden="true" size={16} /></button>
        <button
          aria-label="New agent session"
          className="agent-session-add"
          disabled={!controller.available || controller.creating}
          onClick={() => onNewChat?.()}
          title={controller.atMaximum ? 'Maximum 5 sessions' : 'New agent session (⌘N)'}
          type="button"
        ><Plus aria-hidden="true" size={17} /></button>
      </div>
    </div>
  )
}
