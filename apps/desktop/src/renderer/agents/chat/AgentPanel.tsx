import { ArrowDown, BriefcaseBusiness, CircleAlert, LoaderCircle, RotateCcw, Send, Square, UserRound, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import type { ConnectivityState } from '../../../shared/contracts'
import { AgentAvatar } from '../avatar/AgentAvatar'
import type { AgentAvatarId } from '../avatar/agentAvatars'
import type { AgentSessionsController } from './useAgentSessions'
import { initialAgentConversationState } from './useAgentConversation'
import { AgentActivityGroup } from './AgentActivityGroup'
import { ActivityRow } from './ActivityRow'
import { AssistantMarkdown } from './AssistantMarkdown'
import { AgentSessionTabs } from './AgentSessionTabs'

function formatElapsedTime(elapsedMilliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(elapsedMilliseconds / 1_000))
  const seconds = totalSeconds % 60
  const totalMinutes = Math.floor(totalSeconds / 60)
  const minutes = totalMinutes % 60
  const hours = Math.floor(totalMinutes / 60)
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${minutes}:${String(seconds).padStart(2, '0')}`
}

function parseEventTimestamp(value: string): number {
  const sqliteUtc = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(value)
  return Date.parse(sqliteUtc ? `${value.replace(' ', 'T')}Z` : value)
}

function AgentElapsedTime({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [startedAt])

  return <time aria-label="Elapsed agent time">{formatElapsedTime(now - startedAt)}</time>
}

interface AgentPanelProps {
  agentLabel?: string
  avatarId: AgentAvatarId
  contextLabel: string
  apiState?: ConnectivityState
  onArtifactRendered?: () => void
  onNewChat?: () => void
  sessions: AgentSessionsController
}

function ConnectionNotice({ apiState, connection }: { apiState: ConnectivityState; connection: typeof initialAgentConversationState.connection }) {
  if (apiState === 'disconnected') {
    return <div className="agent-connection offline" role="status"><WifiOff aria-hidden="true" size={14} /> JobOS host unavailable</div>
  }
  if (apiState === 'degraded') {
    return <div className="agent-connection offline" role="status"><WifiOff aria-hidden="true" size={14} /> Device authentication needs attention</div>
  }
  if (connection === 'reconnecting' || connection === 'connecting') {
    return <div className="agent-connection reconnecting" role="status"><LoaderCircle aria-hidden="true" className="spin" size={14} /> Reconnecting to agent…</div>
  }
  if (connection === 'offline') {
    return <div className="agent-connection offline" role="status"><WifiOff aria-hidden="true" size={14} /> Agent offline</div>
  }
  return null
}

export function AgentPanel({ agentLabel, avatarId, contextLabel, apiState = 'connected', onArtifactRendered, onNewChat, sessions }: AgentPanelProps) {
  const conversation = sessions.activeConversation ?? {
    ...initialAgentConversationState,
    items: [],
    draft: '',
    operationPending: false
  }
  const activeId = sessions.activeId
  const activeSummary = sessions.activeSession?.summary
  const retryRequiresRecovery = activeSummary?.recoveryState === 'quarantined'
  const panelRefs = useRef(new Map<string, HTMLDivElement>())
  const lastScrollTops = useRef(new Map<string, number>())
  const pinnedToBottom = useRef(true)
  const [isPinnedToBottom, setIsPinnedToBottom] = useState(true)
  const observedEventIds = useRef(new Map<string, number>())
  const activeTurnStarts = useRef(new Map<string, { turnId: string; startedAt: number }>())
  const canSend = Boolean(
    conversation.draft.trim()
    && !conversation.activeTurn
    && !conversation.restoring
    && !conversation.operationPending
    && apiState === 'connected'
    && activeSummary?.availability?.state !== 'locked'
    && activeSummary?.recoveryState === 'ready'
  )


  useEffect(() => {
    let documentChanged = false
    for (const id of sessions.order) {
      const scoped = sessions.sessions[id]?.conversation
      if (!scoped || scoped.restoring || scoped.restoredEventId === null) continue
      if (!observedEventIds.current.has(id)) observedEventIds.current.set(id, scoped.restoredEventId)
      const observed = observedEventIds.current.get(id) ?? 0
      const latestEventId = scoped.entries.reduce((latest, entry) => Math.max(latest, entry.eventId), 0)
      documentChanged ||= scoped.entries.some(entry => (
        entry.eventId > observed
        && entry.type === 'activity'
        && entry.state === 'completed'
        && typeof entry.detail.command === 'string'
        && ['document.render', 'document.refresh', 'document.register', 'document.publish'].includes(entry.detail.command)
      ))
      observedEventIds.current.set(id, Math.max(observed, latestEventId))
    }
    if (documentChanged) onArtifactRendered?.()
  }, [onArtifactRendered, sessions.order, sessions.sessions])

  const scrollToLatest = useCallback((focusTranscript = false) => {
    const transcript = activeId ? panelRefs.current.get(activeId) : undefined
    if (!transcript) return
    transcript.scrollTop = transcript.scrollHeight
    if (activeId) lastScrollTops.current.set(activeId, transcript.scrollTop)
    pinnedToBottom.current = true
    setIsPinnedToBottom(true)
    if (focusTranscript && activeId) sessions.saveScroll(activeId, transcript.scrollTop, true)
    if (focusTranscript) transcript.focus({ preventScroll: true })
  }, [activeId, sessions.saveScroll])

  useLayoutEffect(() => {
    if (pinnedToBottom.current) scrollToLatest()
  }, [conversation.items, scrollToLatest])

  useLayoutEffect(() => {
    const transcript = activeId ? panelRefs.current.get(activeId) : undefined
    if (!transcript || !activeId) return
    const saved = sessions.sessions[activeId]
    pinnedToBottom.current = saved?.pinnedToBottom ?? true
    setIsPinnedToBottom(pinnedToBottom.current)
    transcript.scrollTop = pinnedToBottom.current ? transcript.scrollHeight : (saved?.scrollTop ?? 0)
    lastScrollTops.current.set(activeId, transcript.scrollTop)
  }, [activeId, sessions.saveScroll])

  const handleScroll = () => {
    const transcript = activeId ? panelRefs.current.get(activeId) : undefined
    if (!transcript || !activeId) return
    const previousScrollTop = lastScrollTops.current.get(activeId) ?? transcript.scrollTop
    const movedUp = transcript.scrollTop < previousScrollTop
    const distanceFromBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight
    const pinned = !movedUp && (pinnedToBottom.current ? distanceFromBottom <= 64 : distanceFromBottom <= 1)
    lastScrollTops.current.set(activeId, transcript.scrollTop)
    pinnedToBottom.current = pinned
    setIsPinnedToBottom(pinned)
    sessions.saveScroll(activeId, transcript.scrollTop, pinned)
  }

  const handleTurnLayoutChange = useCallback(() => {
    if (pinnedToBottom.current) scrollToLatest()
  }, [scrollToLatest])

  const activePresentation = conversation.activeTurn
    ? conversation.items.find(item => item.kind === 'agent-turn' && item.turnId === conversation.activeTurn?.turnId)
    : undefined
  let activeTurnStartedAt: number | null = null
  if (activeId && conversation.activeTurn) {
    const eventStart = conversation.entries
      .filter(entry => entry.turnId === conversation.activeTurn?.turnId)
      .map(entry => parseEventTimestamp(entry.occurredAt))
      .filter(Number.isFinite)
      .reduce<number | null>((earliest, timestamp) => earliest === null ? timestamp : Math.min(earliest, timestamp), null)
    const remembered = activeTurnStarts.current.get(activeId)
    activeTurnStartedAt = remembered?.turnId === conversation.activeTurn.turnId
      ? Math.min(remembered.startedAt, eventStart ?? remembered.startedAt)
      : (eventStart ?? Date.now())
    activeTurnStarts.current.set(activeId, { turnId: conversation.activeTurn.turnId, startedAt: activeTurnStartedAt })
  }
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
      </div>
      <div className="agent-session-header">
        <AgentSessionTabs controller={sessions} onNewChat={onNewChat} />
        {activeSummary?.binding ? (
          <div className="agent-binding" role="status">
            <strong>{agentLabel ?? (activeSummary.binding.provider === 'codex' ? 'ChatGPT · Codex' : 'Hermes')}</strong>
            <span>{activeSummary.binding.modelId} · {activeSummary.binding.reasoningEffort}</span>
            <small>Locked for this chat</small>
          </div>
        ) : null}
        {activeSummary?.availability?.state === 'locked' ? (
          <div className="agent-connection offline" role="status"><WifiOff aria-hidden="true" size={14} /> This chat is read-only · {activeSummary.availability.reason ?? 'Reconnect its agent to continue'}</div>
        ) : null}
      </div>

      {(sessions.order.length ? sessions.order : ['']).map(panelId => {
        const selected = !panelId || panelId === activeId
        return <div
          aria-labelledby={panelId ? `agent-session-tab-${panelId}` : undefined}
          className="agent-body"
          hidden={!selected}
          id={panelId ? `agent-session-panel-${panelId}` : undefined}
          key={panelId || 'restoring'}
          onScroll={selected ? handleScroll : undefined}
          ref={element => {
            if (!panelId) return
            if (element) panelRefs.current.set(panelId, element)
            else panelRefs.current.delete(panelId)
          }}
          role={panelId ? 'tabpanel' : undefined}
          tabIndex={selected ? -1 : undefined}
        >
        {selected && <>
        <ConnectionNotice apiState={apiState} connection={conversation.connection} />
        {conversation.restoring && <div className="agent-restore"><LoaderCircle aria-hidden="true" className="spin" size={17} /> Restoring conversation…</div>}
        {!conversation.restoring && conversation.items.length === 0 && !conversation.error && (
          <section className="agent-empty">
            <AgentAvatar avatarId={avatarId} size="empty" />
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
                const approvalId = waiting && typeof terminal?.detail.approval_id === 'string'
                  ? terminal.detail.approval_id
                  : null
                const reviewConsumed = approvalId !== null && sessions.activeSession?.consumedReviewId === approvalId
                const stopping = active && Boolean(conversation.activeTurn?.cancelRequested)
                const assistant = item.assistant
                const streaming = Boolean(assistant && assistant.state === 'working' && active && !terminal && !stopping)
                const visualState = terminal?.state ?? (assistant?.state === 'working' && !streaming ? 'completed' : assistant?.state)
                const avatarState = stopping
                  ? 'stopping'
                  : waiting
                    ? 'waiting'
                    : streaming
                      ? 'working'
                      : visualState === 'failed' || visualState === 'interrupted'
                        ? 'error'
                        : 'complete'
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
                        {approvalId && activeId && !reviewConsumed && (
                          <div className="review-actions" role="group" aria-label="Tool review">
                            <button aria-label="Approve tool" className="retry-button" disabled={conversation.operationPending} onClick={() => void sessions.review(activeId, item.turnId, approvalId, true)} type="button">
                              Approve
                            </button>
                            <button aria-label="Decline tool" className="stop-button" disabled={conversation.operationPending} onClick={() => void sessions.review(activeId, item.turnId, approvalId, false)} type="button">
                              Decline
                            </button>
                          </div>
                        )}
                        {terminal.retryable && !conversation.activeTurn && activeSummary?.availability?.state !== 'locked' && (
                          <button aria-label={retryRequiresRecovery ? 'Confirm cleanup and retry turn' : 'Retry turn'} className="retry-button" onClick={() => { if (activeId) void sessions.retry(activeId, item.turnId) }} type="button">
                            <RotateCcw aria-hidden="true" size={13} /> {retryRequiresRecovery ? 'Confirm cleanup & retry' : 'Retry'}
                          </button>
                        )}
                      </article>
                    )}
                    {assistant && assistant.text && (
                      <article className={`message assistant-message ${visualState}`}>
                        <header><AgentAvatar avatarId={avatarId} size="message" state={avatarState} /> Agent {streaming && <span>Streaming</span>}</header>
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
                  {item.retryable && !conversation.activeTurn && activeSummary?.availability?.state !== 'locked' && (
                    <button aria-label={retryRequiresRecovery ? 'Confirm cleanup and retry turn' : 'Retry turn'} className="retry-button" onClick={() => { if (activeId) void sessions.retry(activeId, item.turnId ?? '') }} type="button">
                      <RotateCcw aria-hidden="true" size={13} /> {retryRequiresRecovery ? 'Confirm cleanup & retry' : 'Retry'}
                    </button>
                  )}
                </article>
              )
            })}
          </div>
        )}
        {conversation.error && <p className="agent-inline-error" role="alert">{conversation.error}</p>}
        </>}
      </div>
      })}
      {!isPinnedToBottom && (
        <button aria-label="Jump to latest" className="jump-to-latest" onClick={() => scrollToLatest(true)} type="button">
          <ArrowDown aria-hidden="true" size={13} /> Jump to latest
        </button>
      )}
      {conversation.activeTurn && (
        <div aria-label="Agent turn status" className="agent-turn-status">
          {conversation.activeTurn.status === 'running' && !conversation.activeTurn.cancelRequested && <LoaderCircle aria-hidden="true" className="spin" size={13} />}
          <span>{activeStatus}</span>
          {activeTurnStartedAt !== null && <><span aria-hidden="true">·</span><AgentElapsedTime startedAt={activeTurnStartedAt} /></>}
        </div>
      )}

      <form className="composer" onSubmit={event => { event.preventDefault(); if (canSend && activeId) void sessions.send(activeId) }}>
        <label className="sr-only" htmlFor="agent-message">Message the agent</label>
        <textarea
          aria-describedby="composer-status"
          id="agent-message"
          maxLength={12_000}
          onChange={event => { if (activeId) sessions.setDraft(activeId, event.target.value) }}
          onKeyDown={event => {
            if (event.key === 'Enter' && !event.shiftKey && canSend) {
              event.preventDefault()
              if (activeId) void sessions.send(activeId)
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
              <button aria-label="Stop agent turn" className="stop-button" onClick={() => { if (activeId) void sessions.stop(activeId) }} type="button">
                <Square aria-hidden="true" size={13} /> Stop
              </button>
            )}
            <button aria-label="Send message" className="send-button" disabled={!canSend} title={conversation.activeTurn ? 'Finish or stop the active turn before sending' : 'Send message'} type="submit">
              <Send aria-hidden="true" size={16} strokeWidth={1.5} />
            </button>
          </div>
        </div>
      </form>
      <p aria-atomic="true" aria-live="polite" className="sr-only">{sessions.announcement || announcement}</p>
    </aside>
  )
}
