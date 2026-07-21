import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'

import type {
  AgentConnectionState,
  AgentConversationSnapshot,
  AgentStreamUpdate,
  AgentTurn,
  AgentTurnMutation,
  ConversationEvent
} from '../../shared/contracts'

export interface AgentConversationState {
  conversationId: string | null
  entries: ConversationEvent[]
  activeTurn: AgentTurn | null
  connection: AgentConnectionState
  restoring: boolean
  restoredEventId: number | null
  error: string | null
}

type AgentConversationAction =
  | { type: 'hydrate'; snapshot: AgentConversationSnapshot }
  | { type: 'event'; event: ConversationEvent }
  | { type: 'connection'; state: AgentConnectionState }
  | { type: 'mutation'; mutation: AgentTurnMutation; status: 'running' | 'cancel-result' }
  | { type: 'failure'; message: string }
  | { type: 'restore-failure'; message: string }

export const initialAgentConversationState: AgentConversationState = {
  conversationId: null,
  entries: [],
  activeTurn: null,
  connection: 'connecting',
  restoring: true,
  restoredEventId: null,
  error: null
}

function mergeEntries(left: ConversationEvent[], right: ConversationEvent[]): ConversationEvent[] {
  const merged = new Map<number, ConversationEvent>()
  for (const entry of [...left, ...right]) merged.set(entry.eventId, entry)
  return [...merged.values()].sort((a, b) => a.eventId - b.eventId)
}

function activeTurnAfterEvent(activeTurn: AgentTurn | null, event: ConversationEvent): AgentTurn | null {
  if (!activeTurn || event.turnId !== activeTurn.turnId) return activeTurn
  const terminalTurnEvent = event.type === 'assistant_message' || event.type === 'error' || event.type === 'status'
  if (terminalTurnEvent && (event.state === 'completed' || event.state === 'failed' || event.state === 'interrupted')) return null
  if (event.state === 'waiting') return { ...activeTurn, status: 'waiting' }
  if (event.state === 'working') return { ...activeTurn, status: 'running' }
  return activeTurn
}

function connectionAfterEvent(connection: AgentConnectionState, event: ConversationEvent): AgentConnectionState {
  const next = event.detail.agent_connection
  return next === 'online' || next === 'connecting' || next === 'offline' ? next : connection
}

export function agentConversationReducer(state: AgentConversationState, action: AgentConversationAction): AgentConversationState {
  switch (action.type) {
    case 'hydrate':
      const eventsAfterSnapshot = state.entries.filter(entry => entry.eventId > action.snapshot.latestEventId)
      return {
        conversationId: action.snapshot.conversationId,
        entries: mergeEntries(action.snapshot.entries, state.entries),
        activeTurn: eventsAfterSnapshot.reduce(activeTurnAfterEvent, action.snapshot.activeTurn),
        connection: eventsAfterSnapshot.reduce(connectionAfterEvent, action.snapshot.connection),
        restoring: false,
        restoredEventId: action.snapshot.latestEventId,
        error: null
      }
    case 'event':
      return {
        ...state,
        entries: mergeEntries(state.entries, [action.event]),
        activeTurn: activeTurnAfterEvent(state.activeTurn, action.event),
        connection: connectionAfterEvent(state.connection, action.event),
        restoring: state.restoring
      }
    case 'connection':
      return { ...state, connection: action.state }
    case 'mutation':
      if (action.status === 'cancel-result') {
        const terminal = ['completed', 'failed', 'interrupted'].includes(action.mutation.status ?? '')
        return {
          ...state,
          activeTurn: terminal ? null : state.activeTurn && {
            ...state.activeTurn,
            status: action.mutation.status === 'waiting' ? 'waiting' : 'running',
            cancelRequested: true
          },
          error: null
        }
      }
      return {
        ...state,
        activeTurn: { turnId: action.mutation.turnId, status: 'running', cancelRequested: false },
        error: null
      }
    case 'failure':
      return { ...state, restoring: false, error: action.message }
    case 'restore-failure':
      return {
        ...state,
        restoring: false,
        restoredEventId: state.entries.reduce(
          (latest, entry) => Math.max(latest, entry.eventId),
          0
        ),
        error: action.message,
        connection: 'offline'
      }
  }
}

interface BaseProjectedItem {
  id: string
  eventId: number
  turnId: string | null
  state: ConversationEvent['state']
  occurredAt: string
}

export type ProjectedConversationItem =
  | BaseProjectedItem & { kind: 'user'; text: string }
  | BaseProjectedItem & { kind: 'assistant'; text: string }
  | BaseProjectedItem & { kind: 'activity'; activityId: string; label: string; detail: ConversationEvent['detail'] }
  | BaseProjectedItem & { kind: 'status'; label: string; retryable: boolean }
  | BaseProjectedItem & { kind: 'error'; label: string; retryable: boolean }

function detailString(event: ConversationEvent, key: string): string | undefined {
  const value = event.detail[key]
  return typeof value === 'string' ? value : undefined
}

export function projectConversation(entries: ConversationEvent[]): ProjectedConversationItem[] {
  const projected: ProjectedConversationItem[] = []
  const activities = new Map<string, number>()
  const assistants = new Map<string, number>()
  for (const entry of [...entries].sort((a, b) => a.eventId - b.eventId)) {
    const base: BaseProjectedItem = {
      id: `event-${entry.eventId}`,
      eventId: entry.eventId,
      turnId: entry.turnId,
      state: entry.state,
      occurredAt: entry.occurredAt
    }
    if (entry.type === 'user_message') {
      projected.push({ ...base, kind: 'user', text: entry.text ?? entry.summary })
      continue
    }
    if (entry.type === 'activity') {
      const activityId = detailString(entry, 'activity_id') ?? `event-${entry.eventId}`
      const existing = activities.get(activityId)
      const item: ProjectedConversationItem = { ...base, id: `activity-${activityId}`, kind: 'activity', activityId, label: entry.summary, detail: entry.detail }
      if (existing === undefined) {
        activities.set(activityId, projected.length)
        projected.push(item)
      } else {
        const first = projected[existing]!
        projected[existing] = { ...item, eventId: first.eventId, occurredAt: first.occurredAt }
      }
      continue
    }
    if (entry.type === 'assistant_message') {
      const key = entry.turnId ?? `event-${entry.eventId}`
      const existing = assistants.get(key)
      const phase = detailString(entry, 'type')
      const nextText = phase === 'message.start' ? '' : entry.summary
      if (existing === undefined) {
        assistants.set(key, projected.length)
        projected.push({ ...base, id: `assistant-${key}`, kind: 'assistant', text: nextText })
      } else {
        const current = projected[existing]
        if (!current || current.kind !== 'assistant') continue
        const text = phase === 'message.complete' || ['completed', 'failed', 'interrupted'].includes(entry.state)
          ? entry.summary
          : `${current.text}${entry.summary}`
        projected[existing] = { ...current, state: entry.state, text }
      }
      continue
    }
    if (entry.type === 'error') {
      projected.push({ ...base, kind: 'error', label: entry.summary, retryable: entry.detail.retry === true || entry.detail.actionable === true })
      continue
    }
    if (entry.type === 'status' && (entry.state === 'waiting' || entry.state === 'interrupted' || entry.state === 'failed')) {
      projected.push({ ...base, kind: 'status', label: entry.summary, retryable: entry.detail.retry === true })
    }
  }
  return projected.sort((a, b) => a.eventId - b.eventId)
}

function identifier(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `desktop-${prefix}${random}`
}

function safeError(_error: unknown, fallback: string): string {
  return fallback
}

export function useAgentConversation() {
  const [state, dispatch] = useReducer(agentConversationReducer, initialAgentConversationState)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    const bridge = window.jobos?.agent
    if (!bridge) {
      dispatch({ type: 'restore-failure', message: 'Agent is available in the desktop app' })
      return
    }
    const unsubscribe = bridge.subscribe((update: AgentStreamUpdate) => {
      if (update.kind === 'event') dispatch({ type: 'event', event: update.event })
      else dispatch({ type: 'connection', state: update.state })
    })
    void bridge.get()
      .then(value => dispatch({ type: 'hydrate', snapshot: value }))
      .catch(error => dispatch({ type: 'restore-failure', message: safeError(error, 'Conversation could not be restored') }))
    return unsubscribe
  }, [])

  const send = useCallback(async () => {
    const text = draft.trim()
    if (!text || state.activeTurn || !window.jobos?.agent) return
    try {
      const mutation = await window.jobos.agent.send(text, identifier('message-'))
      dispatch({ type: 'mutation', mutation, status: 'running' })
      setDraft('')
    } catch (error) {
      dispatch({ type: 'failure', message: safeError(error, 'Message could not be sent') })
    }
  }, [draft, state.activeTurn])

  const stop = useCallback(async () => {
    if (!state.activeTurn || !window.jobos?.agent) return
    try {
      const mutation = await window.jobos.agent.cancel(state.activeTurn.turnId)
      dispatch({ type: 'mutation', mutation, status: 'cancel-result' })
    } catch (error) {
      dispatch({ type: 'failure', message: safeError(error, 'Turn could not be stopped') })
    }
  }, [state.activeTurn])

  const retry = useCallback(async (turnId: string) => {
    if (state.activeTurn || !window.jobos?.agent) return
    try {
      const mutation = await window.jobos.agent.retry(turnId, identifier('retry-'))
      dispatch({ type: 'mutation', mutation, status: 'running' })
    } catch (error) {
      dispatch({ type: 'failure', message: safeError(error, 'Turn could not be retried') })
    }
  }, [state.activeTurn])

  const items = useMemo(() => projectConversation(state.entries), [state.entries])
  return { ...state, items, draft, setDraft, send, stop, retry }
}
