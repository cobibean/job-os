import { ArrowDown, Bot, BriefcaseBusiness, CircleAlert, LoaderCircle, MessageSquarePlus, RotateCcw, Send, Square, UserRound, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import type { ConnectivityState } from '../../shared/contracts'
import { useAgentConversation } from '../hooks/useAgentConversation'
import { AgentActivityGroup } from './AgentActivityGroup'
import { ActivityRow } from './ActivityRow'
import { AssistantMarkdown } from './AssistantMarkdown'

interface AgentPanelProps {
  contextLabel: string
  apiState?: ConnectivityState
  onArtifactRendered?: (jobId?: string) => void
  onModalOpenChange?: (open: boolean) => void
}

function ConnectionNotice({ apiState, connection }: { apiState: ConnectivityState; connection: ReturnType<typeof useAgentConversation>['connection'] }) {
  if (apiState === 'disconnected' || apiState === 'degraded') {
    return <div className="agent-connection offline" role="status"><WifiOff aria-hidden="true" size={14} /> JobOS API offline</div>
  }
  if (connection === 'reconnecting' || connection === 'connecting') {
    return <div className="agent-connection reconnecting" role="status"><LoaderCircle aria-hidden="true" className="spin" size={14} /> Reconnecting to agent…</div>
  }
  if (connection === 'offline') {
    return <div className="agent-connection offline" role="status"><WifiOff aria-hidden="true" size={14} /> Agent offline</div>
  }
  return null
}

export function AgentPanel({ contextLabel, apiState = 'connected', onArtifactRendered, onModalOpenChange }: AgentPanelProps) {
  const conversation = useAgentConversation()
  const [confirmingReset, setConfirmingReset] = useState(false)
  const [preparingReset, setPreparingReset] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const newSessionButtonRef = useRef<HTMLButtonElement>(null)
  const cancelResetButtonRef = useRef<HTMLButtonElement>(null)
  const resetDialogRef = useRef<HTMLElement>(null)
  const resetDialogWasOpen = useRef(false)
  const pinnedToBottom = useRef(true)
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true)
  const observedEventId = useRef<number | null>(null)
  const canSend = Boolean(
    conversation.draft.trim()
    && !conversation.activeTurn
    && !conversation.restoring
    && !conversation.operationPending
    && !confirmingReset
    && apiState === 'connected'
  )
  const canReset = !conversation.activeTurn && !conversation.restoring && !conversation.operationPending && !preparingReset && apiState === 'connected'

  useEffect(() => {
    return () => onModalOpenChange?.(false)
  }, [onModalOpenChange])

  const openResetDialog = async () => {
    if (!canReset) return
    onModalOpenChange?.(true)
    const browser = window.jobos?.browser
    if (!browser) {
      setConfirmingReset(true)
      return
    }
    setPreparingReset(true)
    try {
      await browser.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
      setConfirmingReset(true)
    } catch {
      onModalOpenChange?.(false)
    } finally {
      setPreparingReset(false)
    }
  }

  const closeResetDialog = () => {
    if (conversation.resetting) return
    setConfirmingReset(false)
    onModalOpenChange?.(false)
  }

  const trapResetDialogFocus = (event: globalThis.KeyboardEvent) => {
    if (event.key !== 'Tab') return
    const focusable = [...(resetDialogRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])]
    const first = focusable.at(0)
    const last = focusable.at(-1)
    if (!first || !last) {
      event.preventDefault()
      return
    }
    if (focusable.length === 1) {
      event.preventDefault()
      first.focus()
      return
    }
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  useEffect(() => {
    const appShell = document.querySelector<HTMLElement>('.app-shell')
    if (confirmingReset) {
      resetDialogWasOpen.current = true
      appShell?.setAttribute('inert', '')
      if (conversation.resetting) cancelResetButtonRef.current?.focus()
      const handleKeyDown = (event: globalThis.KeyboardEvent) => {
        if (event.key === 'Escape' && !conversation.resetting) {
          event.preventDefault()
          setConfirmingReset(false)
          onModalOpenChange?.(false)
          return
        }
        trapResetDialogFocus(event)
      }
      document.addEventListener('keydown', handleKeyDown)
      return () => {
        document.removeEventListener('keydown', handleKeyDown)
        appShell?.removeAttribute('inert')
      }
    }
    if (!resetDialogWasOpen.current) return
    resetDialogWasOpen.current = false
    newSessionButtonRef.current?.focus()
  }, [confirmingReset, conversation.resetting])


  useEffect(() => {
    if (conversation.restoring || conversation.restoredEventId === null) return
    if (observedEventId.current === null) {
      observedEventId.current = conversation.restoredEventId
    }
    const latestEventId = conversation.entries.reduce(
      (latest, entry) => Math.max(latest, entry.eventId),
      0
    )
    const rendered = conversation.entries.find(entry => (
      entry.eventId > (observedEventId.current ?? 0)
      && entry.type === 'activity'
      && entry.state === 'completed'
      && ['document.render', 'document.publish'].includes(String(entry.detail.command))
    ))
    observedEventId.current = Math.max(observedEventId.current, latestEventId)
    if (rendered) {
      const jobId = typeof rendered.detail.job_id === 'string' ? rendered.detail.job_id : undefined
      onArtifactRendered?.(jobId)
    }
  }, [conversation.entries, conversation.restoredEventId, conversation.restoring, onArtifactRendered])

  const scrollToLatest = useCallback((focusTranscript = false) => {
    const transcript = scrollRef.current
    if (!transcript) return
    transcript.scrollTop = transcript.scrollHeight
    pinnedToBottom.current = true
    setIsPinnedToBottom(true)
    if (focusTranscript) transcript.focus({ preventScroll: true })
  }, [])

  useLayoutEffect(() => {
    if (pinnedToBottom.current) scrollToLatest()
  }, [conversation.items, scrollToLatest])

  const handleScroll = () => {
    const transcript = scrollRef.current
    if (!transcript) return
    const pinned = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight <= 64
    pinnedToBottom.current = pinned
    setIsPinnedToBottom(pinned)
  }

  const handleTurnLayoutChange = useCallback(() => {
    if (pinnedToBottom.current) scrollToLatest()
  }, [scrollToLatest])

  const activePresentation = conversation.activeTurn
    ? conversation.items.find(item => item.kind === 'agent-turn' && item.turnId === conversation.activeTurn?.turnId)
    : undefined
  const activeActionCount = activePresentation?.kind === 'agent-turn' ? activePresentation.activities.length : 0
  const latestTurn = [...conversation.items].reverse().find(item => item.kind === 'agent-turn')
  const activeStatus = conversation.activeTurn?.cancelRequested
    ? 'Stopping agent…'
    : conversation.activeTurn?.status === 'waiting'
      ? 'Agent waiting for you'
      : `Agent working · ${activeActionCount} ${activeActionCount === 1 ? 'action' : 'actions'}`
  const announcement = conversation.connection === 'reconnecting'
    ? 'Reconnecting to agent'
    : conversation.activeTurn?.cancelRequested
      ? 'Stopping agent'
      : conversation.activeTurn?.status === 'waiting'
        ? 'Agent waiting for you'
        : conversation.activeTurn
          ? 'Agent working'
          : latestTurn?.kind === 'agent-turn' && latestTurn.state === 'failed'
            ? 'Agent turn failed'
            : latestTurn?.kind === 'agent-turn' && latestTurn.state === 'interrupted'
              ? 'Agent turn interrupted'
              : latestTurn?.kind === 'agent-turn' && latestTurn.state === 'completed'
                ? 'Agent response completed'
                : ''

  return (
    <aside aria-label="Agent chat" className="agent-panel panel-region">
      <div className="agent-context">
        <span title={contextLabel}><BriefcaseBusiness aria-hidden="true" size={16} strokeWidth={1.5} /> <span>{contextLabel}</span></span>
        <button
          aria-label="Start new agent session"
          className="new-session-button"
          disabled={!canReset}
          onClick={() => void openResetDialog()}
          ref={newSessionButtonRef}
          title={conversation.activeTurn ? 'Finish or stop the active turn first' : 'Clear this conversation and start with fresh context'}
          type="button"
        >
          <MessageSquarePlus aria-hidden="true" size={14} strokeWidth={1.6} /> New session
        </button>
      </div>

      {confirmingReset && createPortal(
        <div className="new-session-overlay" onClick={closeResetDialog} role="presentation">
          <section
            aria-labelledby="new-session-title"
            aria-modal="true"
            className="new-session-confirm"
            onClick={event => event.stopPropagation()}
            ref={resetDialogRef}
            role="alertdialog"
          >
            <div>
              <strong id="new-session-title">Start with fresh context?</strong>
              <p>This clears the visible conversation and starts a new agent session. Your selected job stays attached.</p>
            </div>
            <div className="new-session-actions">
              <button onClick={closeResetDialog} ref={cancelResetButtonRef} type="button">Cancel</button>
              <button
                aria-label="Confirm new session"
                autoFocus
                className="confirm"
                disabled={!canReset}
                onClick={() => void conversation.reset().then(reset => {
                  if (!reset) return
                  scrollToLatest()
                  setConfirmingReset(false)
                  onModalOpenChange?.(false)
                })}
                type="button"
              >
                {conversation.resetting && <LoaderCircle aria-hidden="true" className="spin" size={13} />}
                {conversation.resetting ? 'Starting…' : 'New session'}
              </button>
            </div>
          </section>
        </div>,
        document.body
      )}

      <div className="agent-body" onScroll={handleScroll} ref={scrollRef} tabIndex={-1}>
        <ConnectionNotice apiState={apiState} connection={conversation.connection} />
        {conversation.restoring && <div className="agent-restore"><LoaderCircle aria-hidden="true" className="spin" size={17} /> Restoring conversation…</div>}
        {!conversation.restoring && conversation.items.length === 0 && !conversation.error && (
          <section className="agent-empty">
            <span className="agent-avatar"><Bot aria-hidden="true" size={22} strokeWidth={1.45} /></span>
            <h2>Fresh conversation</h2>
            <p>Ask the agent to research, tailor, or review. The selected job is included automatically.</p>
          </section>
        )}
        {conversation.items.length > 0 && (
          <div aria-label="Conversation transcript" className="agent-transcript">
            {conversation.items.map(item => {
              if (item.kind === 'activity') return <ActivityRow item={item} key={item.id} />
              if (item.kind === 'user') return (
                <article className="message user-message" key={item.id}>
                  <header><UserRound aria-hidden="true" size={16} /> You</header>
                  <p>{item.text}</p>
                </article>
              )
              if (item.kind === 'agent-turn') {
                const active = item.turnId === conversation.activeTurn?.turnId
                const terminal = item.terminal
                const waiting = terminal?.state === 'waiting' && active
                const assistant = item.assistant
                const streaming = Boolean(assistant && assistant.state === 'working' && active && !terminal)
                const visualState = terminal?.state ?? (assistant?.state === 'working' && !streaming ? 'completed' : assistant?.state)
                return (
                  <section className={`agent-turn ${item.state}`} data-testid="agent-turn" key={item.id}>
                    {item.activities.length > 0 && (
                      <AgentActivityGroup
                        active={active}
                        activities={item.activities}
                        onLayoutChange={handleTurnLayoutChange}
                        state={item.state}
                        working={active && conversation.activeTurn?.status === 'running' && !conversation.activeTurn.cancelRequested}
                      />
                    )}
                    {terminal && (
                      <article className={`agent-notice ${terminal.kind} ${waiting ? 'waiting' : terminal.state}`}>
                        <strong><CircleAlert aria-hidden="true" size={14} /> {waiting ? 'Waiting for you' : terminal.state === 'waiting' ? 'Turn paused' : terminal.state === 'interrupted' ? 'Turn interrupted' : 'Turn failed'}</strong>
                        <p>{terminal.label}</p>
                        {terminal.retryable && !conversation.activeTurn && (
                          <button aria-label="Retry turn" className="retry-button" onClick={() => void conversation.retry(item.turnId)} type="button">
                            <RotateCcw aria-hidden="true" size={13} /> Retry
                          </button>
                        )}
                      </article>
                    )}
                    {assistant && assistant.text && (
                      <article className={`message assistant-message ${visualState}`}>
                        <header><Bot aria-hidden="true" size={16} /> Agent {streaming && <span>Streaming</span>}</header>
                        <AssistantMarkdown>{assistant.text}</AssistantMarkdown>
                      </article>
                    )}
                  </section>
                )
              }
              const noticeLabel = item.state === 'waiting'
                ? 'Turn paused'
                : item.state === 'interrupted'
                  ? 'Turn interrupted'
                  : 'Turn failed'
              return (
                <article className={`agent-notice ${item.kind} ${item.state}`} key={item.id}>
                  <strong><CircleAlert aria-hidden="true" size={14} /> {noticeLabel}</strong>
                  <p>{item.label}</p>
                  {item.retryable && !conversation.activeTurn && (
                    <button aria-label="Retry turn" className="retry-button" onClick={() => void conversation.retry(item.turnId ?? '')} type="button">
                      <RotateCcw aria-hidden="true" size={13} /> Retry
                    </button>
                  )}
                </article>
              )
            })}
          </div>
        )}
        {conversation.error && <p className="agent-inline-error" role="alert">{conversation.error}</p>}
      </div>
      {!isPinnedToBottom && (
        <button aria-label="Jump to latest" className="jump-to-latest" onClick={() => scrollToLatest(true)} type="button">
          <ArrowDown aria-hidden="true" size={13} /> Jump to latest
        </button>
      )}
      {conversation.activeTurn && (
        <div aria-label="Agent turn status" className="agent-turn-status">
          {conversation.activeTurn.status === 'running' && !conversation.activeTurn.cancelRequested && <LoaderCircle aria-hidden="true" className="spin" size={13} />}
          {activeStatus}
        </div>
      )}

      <form className="composer" onSubmit={event => { event.preventDefault(); if (canSend) void conversation.send() }}>
        <label className="sr-only" htmlFor="agent-message">Message the agent</label>
        <textarea
          aria-describedby="composer-status"
          id="agent-message"
          maxLength={12_000}
          onChange={event => conversation.setDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && canSend) {
              event.preventDefault()
              void conversation.send()
            }
          }}
          placeholder="Message the agent…"
          rows={3}
          value={conversation.draft}
        />
        <div className="composer-footer">
          <span id="composer-status">
            {conversation.activeTurn ? 'Draft freely · send after this turn' : conversation.connection !== 'online' ? 'Send to reconnect the agent' : 'Enter to send · Shift+Enter for a new line'}
          </span>
          <div className="composer-actions">
            {conversation.activeTurn && (
              <button aria-label="Stop agent turn" className="stop-button" onClick={() => void conversation.stop()} type="button">
                <Square aria-hidden="true" size={13} /> Stop
              </button>
            )}
            <button aria-label="Send message" className="send-button" disabled={!canSend} title={conversation.activeTurn ? 'Finish or stop the active turn before sending' : 'Send message'} type="submit">
              <Send aria-hidden="true" size={16} strokeWidth={1.5} />
            </button>
          </div>
        </div>
      </form>
      <p aria-atomic="true" aria-live="polite" className="sr-only">{announcement}</p>
    </aside>
  )
}
