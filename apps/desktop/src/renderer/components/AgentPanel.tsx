import { Bot, BriefcaseBusiness, ChevronDown, CircleAlert, LoaderCircle, RotateCcw, Send, SlidersHorizontal, Square, UserRound, WifiOff } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react'

import type { ConnectivityState, ConversationEntryState, ConversationEvent } from '../../shared/contracts'
import { useAgentConversation } from '../hooks/useAgentConversation'
import { ActivityRow } from './ActivityRow'

interface AgentPanelProps {
  contextLabel: string
  apiState?: ConnectivityState
  onArtifactRendered?: () => void
}

type TerminalState = Extract<ConversationEntryState, 'completed' | 'failed' | 'interrupted'>

function terminalStateByTurn(entries: ConversationEvent[]): Map<string, { eventId: number; state: TerminalState }> {
  const terminalByTurn = new Map<string, { eventId: number; state: TerminalState }>()
  for (const entry of [...entries].sort((left, right) => left.eventId - right.eventId)) {
    if (!entry.turnId || !['completed', 'failed', 'interrupted'].includes(entry.state)) continue
    const isTerminalEntry = entry.type === 'assistant_message' || entry.type === 'error' || entry.type === 'status'
    if (isTerminalEntry) terminalByTurn.set(entry.turnId, { eventId: entry.eventId, state: entry.state as TerminalState })
  }
  return terminalByTurn
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

export function AgentPanel({ contextLabel, apiState = 'connected', onArtifactRendered }: AgentPanelProps) {
  const conversation = useAgentConversation()
  const terminalByTurn = useMemo(() => terminalStateByTurn(conversation.entries), [conversation.entries])
  const scrollRef = useRef<HTMLDivElement>(null)
  const pinnedToBottom = useRef(true)
  const observedEventId = useRef<number | null>(null)
  const canSend = Boolean(
    conversation.draft.trim()
    && !conversation.activeTurn
    && !conversation.restoring
    && apiState === 'connected'
  )

  useEffect(() => {
    if (conversation.restoring || conversation.restoredEventId === null) return
    if (observedEventId.current === null) {
      observedEventId.current = conversation.restoredEventId
    }
    const latestEventId = conversation.entries.reduce(
      (latest, entry) => Math.max(latest, entry.eventId),
      0
    )
    const rendered = conversation.entries.some(entry => (
      entry.eventId > (observedEventId.current ?? 0)
      && entry.type === 'activity'
      && entry.state === 'completed'
      && entry.detail.command === 'document.render'
    ))
    observedEventId.current = Math.max(observedEventId.current, latestEventId)
    if (rendered) onArtifactRendered?.()
  }, [conversation.entries, conversation.restoredEventId, conversation.restoring, onArtifactRendered])

  useLayoutEffect(() => {
    const transcript = scrollRef.current
    if (transcript && pinnedToBottom.current) transcript.scrollTop = transcript.scrollHeight
  }, [conversation.items.length, conversation.items.at(-1)])

  const handleScroll = () => {
    const transcript = scrollRef.current
    if (!transcript) return
    pinnedToBottom.current = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 64
  }
  const latestItem = conversation.items.at(-1)
  const latestItemIsActivelyWaiting = latestItem?.state === 'waiting' && latestItem.turnId === conversation.activeTurn?.turnId
  const announcement = conversation.connection === 'reconnecting'
    ? 'Reconnecting to agent'
    : latestItemIsActivelyWaiting
      ? latestItem?.kind === 'assistant' ? 'Agent is waiting' : 'Agent is waiting for you'
      : latestItem?.state === 'failed'
        ? 'Agent turn failed'
        : latestItem?.state === 'completed'
          ? 'Agent response completed'
          : ''

  return (
    <aside aria-label="Agent chat" className="agent-panel panel-region">
      <div className="agent-context">
        <span title={contextLabel}><BriefcaseBusiness aria-hidden="true" size={16} strokeWidth={1.5} /> <span>{contextLabel}</span></span>
        <ChevronDown aria-hidden="true" size={14} strokeWidth={1.5} />
        <button aria-label="Agent context settings" className="icon-button context-settings placeholder-control" disabled title="Context follows the selected job" type="button">
          <SlidersHorizontal aria-hidden="true" size={16} strokeWidth={1.5} />
        </button>
      </div>

      <div className="agent-body" onScroll={handleScroll} ref={scrollRef}>
        <ConnectionNotice apiState={apiState} connection={conversation.connection} />
        {conversation.restoring && <div className="agent-restore"><LoaderCircle aria-hidden="true" className="spin" size={17} /> Restoring conversation…</div>}
        {!conversation.restoring && conversation.items.length === 0 && !conversation.error && (
          <section className="agent-empty">
            <span className="agent-avatar"><Bot aria-hidden="true" size={22} strokeWidth={1.45} /></span>
            <h2>One continuous conversation</h2>
            <p>Ask the agent to research, tailor, or review. This conversation stays with you as jobs change.</p>
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
              if (item.kind === 'assistant') {
                const terminal = item.turnId ? terminalByTurn.get(item.turnId) : undefined
                const laterTerminal = terminal && terminal.eventId > item.eventId ? terminal : undefined
                const streaming = item.state === 'working' && item.turnId === conversation.activeTurn?.turnId && !laterTerminal
                const visualState = laterTerminal?.state ?? (item.state === 'working' && !streaming ? 'completed' : item.state)
                return (
                  <article className={`message assistant-message ${visualState}`} key={item.id}>
                    <header><Bot aria-hidden="true" size={16} /> Agent {streaming && <span>Streaming</span>}</header>
                    <p>{item.text}</p>
                  </article>
                )
              }
              const waiting = item.kind === 'status' && item.state === 'waiting' && item.turnId === conversation.activeTurn?.turnId
              const noticeState = item.state === 'waiting' && !waiting ? 'completed' : item.state
              return (
                <article className={`agent-notice ${item.kind} ${noticeState}`} key={item.id}>
                  <strong><CircleAlert aria-hidden="true" size={14} /> {waiting ? 'Waiting for you' : item.state === 'waiting' ? 'Turn paused' : item.state === 'interrupted' ? 'Turn interrupted' : 'Turn failed'}</strong>
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
