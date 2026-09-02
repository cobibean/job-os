import type {
  AgentConnectionState,
  AgentConversationSnapshot,
  AgentTurn,
  AgentTurnMutation,
  ConversationEvent
} from '../../../shared/contracts'

export interface AgentConversationState {
  conversationId: string | null
  entries: ConversationEvent[]
  activeTurn: AgentTurn | null
  connection: AgentConnectionState
  restoring: boolean
  restoredEventId: number | null
  error: string | null
}

export type AgentConversationAction =
  | { type: 'hydrate'; snapshot: AgentConversationSnapshot }
  | { type: 'reset'; snapshot: AgentConversationSnapshot }
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
  if (!activeTurn) {
    if ((event.type === 'turn' || event.type === 'status') && event.turnId && ['queued', 'working', 'waiting'].includes(event.state)) {
      return {
        turnId: event.turnId,
        status: event.state === 'waiting' ? 'waiting' : 'running',
        cancelRequested: false
      }
    }
    return null
  }
  if (event.turnId !== activeTurn.turnId) return activeTurn
  const terminalTurnEvent = event.type === 'turn' || event.type === 'assistant_message' || event.type === 'error' || event.type === 'status'
  if (terminalTurnEvent && (event.state === 'completed' || event.state === 'failed' || event.state === 'interrupted')) return null
  // Activity state describes one tool call, not the whole turn. Only an
  // explicit runtime status event may pause or resume the active turn.
  if (event.type === 'status' && event.state === 'waiting') return { ...activeTurn, status: 'waiting' }
  if (event.type === 'status' && event.state === 'working') return { ...activeTurn, status: 'running' }
  return activeTurn
}

function isTerminalTurnState(state: string | null | undefined): boolean {
  return state === 'completed' || state === 'failed' || state === 'interrupted' || state === 'cancelled'
}

function hasTerminalTurnEvent(entries: ConversationEvent[], turnId: string): boolean {
  return entries.some(event => (
    event.turnId === turnId
    && (event.type === 'turn' || event.type === 'assistant_message' || event.type === 'error' || event.type === 'status')
    && isTerminalTurnState(event.state)
  ))
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
    case 'reset':
      const eventsAfterReset = state.entries.filter(entry => entry.eventId > action.snapshot.latestEventId)
      return {
        conversationId: action.snapshot.conversationId,
        entries: mergeEntries(action.snapshot.entries, eventsAfterReset),
        activeTurn: eventsAfterReset.reduce(activeTurnAfterEvent, action.snapshot.activeTurn),
        connection: eventsAfterReset.reduce(connectionAfterEvent, action.snapshot.connection),
        restoring: false,
        restoredEventId: action.snapshot.latestEventId,
        error: null
      }
    case 'event':
      if (state.restoredEventId !== null && action.event.eventId <= state.restoredEventId) return state
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
        const terminal = isTerminalTurnState(action.mutation.status)
          || hasTerminalTurnEvent(state.entries, action.mutation.turnId)
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
      const terminal = isTerminalTurnState(action.mutation.status)
        || hasTerminalTurnEvent(state.entries, action.mutation.turnId)
      return {
        ...state,
        activeTurn: terminal ? null : { turnId: action.mutation.turnId, status: 'running', cancelRequested: false },
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

export interface ProjectedUserItem extends BaseProjectedItem {
  kind: 'user'
  text: string
}

export interface AssistantItem extends BaseProjectedItem {
  kind: 'assistant'
  text: string
}

export interface ActivityItem extends BaseProjectedItem {
  kind: 'activity'
  activityId: string
  label: string
  detail: ConversationEvent['detail']
}

export interface StatusOrErrorItem extends BaseProjectedItem {
  kind: 'status' | 'error'
  label: string
  retryable: boolean
  detail: ConversationEvent['detail']
}

export interface ProjectedAgentTurn {
  kind: 'agent-turn'
  id: string
  turnId: string
  eventId: number
  state: ConversationEvent['state']
  occurredAt: string
  activities: ActivityItem[]
  assistant: AssistantItem | null
  terminal: StatusOrErrorItem | null
}

export type ProjectedConversationItem = ProjectedUserItem | ProjectedAgentTurn | ActivityItem | StatusOrErrorItem

function detailString(event: ConversationEvent, key: string): string | undefined {
  const value = event.detail[key]
  return typeof value === 'string' ? value : undefined
}

function projectedBase(entry: ConversationEvent): BaseProjectedItem {
  return {
    id: `event-${entry.eventId}`,
    eventId: entry.eventId,
    turnId: entry.turnId,
    state: entry.state,
    occurredAt: entry.occurredAt
  }
}

function statusOrErrorItem(entry: ConversationEvent): StatusOrErrorItem {
  return {
    ...projectedBase(entry),
    kind: entry.type === 'error' ? 'error' : 'status',
    label: entry.summary,
    retryable: entry.detail.retry === true || (entry.type === 'error' && entry.detail.actionable === true),
    detail: entry.detail
  }
}

const destructiveRedactionPlaceholders = new Set([
  '[protected path]'
])

function terminalAssistantText(streamedText: string, terminalText: string): string {
  const streamed = streamedText.trim()
  const terminal = terminalText.trim()
  if (
    streamed
    && !destructiveRedactionPlaceholders.has(streamed)
    && destructiveRedactionPlaceholders.has(terminal)
  ) return streamedText
  return terminalText
}

export function projectConversation(entries: ConversationEvent[]): ProjectedConversationItem[] {
  const projected: ProjectedConversationItem[] = []
  const turns = new Map<string, ProjectedAgentTurn>()
  const activityIndexes = new Map<string, Map<string, number>>()

  const terminalStates = new Map<string, ConversationEvent['state']>()

  for (const entry of [...entries].sort((a, b) => a.eventId - b.eventId)) {
    const base = projectedBase(entry)
    if (entry.type === 'user_message') {
      projected.push({ ...base, kind: 'user', text: entry.text ?? entry.summary })
      continue
    }

    if (!entry.turnId) {
      if (entry.type === 'error') {
        projected.push(statusOrErrorItem(entry))
      } else if (entry.type === 'status' && ['waiting', 'interrupted', 'failed'].includes(entry.state)) {
        projected.push(statusOrErrorItem(entry))
      }
      continue
    }

    let turn = turns.get(entry.turnId)
    if (!turn) {
      turn = {
        kind: 'agent-turn',
        id: `turn-${entry.turnId}`,
        turnId: entry.turnId,
        eventId: entry.eventId,
        state: entry.state,
        occurredAt: entry.occurredAt,
        activities: [],
        assistant: null,
        terminal: null
      }
      turns.set(entry.turnId, turn)
      activityIndexes.set(entry.turnId, new Map())
      projected.push(turn)
    }

    const isTerminalEntry = (
      ['assistant_message', 'error', 'status'].includes(entry.type)
      && ['completed', 'failed', 'interrupted'].includes(entry.state)
    )
    if (isTerminalEntry) {
      terminalStates.set(entry.turnId, entry.state)
      turn.state = entry.state
      if (entry.state === 'completed') turn.terminal = null
    } else if (
      !terminalStates.has(entry.turnId)
      && !(turn.terminal?.state === 'waiting' && entry.state !== 'working')
    ) {
      turn.state = entry.state
    }

    const resolvesWaiting = turn.terminal?.state === 'waiting' && entry.state === 'working'
    if (resolvesWaiting) turn.terminal = null

    if (entry.type === 'activity') {
      const activityId = detailString(entry, 'activity_id') ?? `event-${entry.eventId}`
      const indexes = activityIndexes.get(entry.turnId)!
      const existing = indexes.get(activityId)
      const item: ActivityItem = {
        ...base,
        id: `activity-${entry.turnId}-${activityId}`,
        kind: 'activity',
        activityId,
        label: entry.summary,
        detail: entry.detail
      }
      if (existing === undefined) {
        indexes.set(activityId, turn.activities.length)
        turn.activities.push(item)
      } else {
        const first = turn.activities[existing]!
        turn.activities[existing] = { ...item, eventId: first.eventId, occurredAt: first.occurredAt }
      }
      continue
    }

    if (entry.type === 'assistant_message') {
      const phase = detailString(entry, 'type')
      const eventText = detailString(entry, 'text') ?? entry.summary
      const nextText = phase === 'message.start' ? '' : eventText
      if (!turn.assistant) {
        turn.assistant = { ...base, id: `assistant-${entry.turnId}`, kind: 'assistant', text: nextText }
      } else {
        const text = phase === 'message.complete' || ['completed', 'failed', 'interrupted'].includes(entry.state)
          ? terminalAssistantText(turn.assistant.text, eventText)
          : `${turn.assistant.text}${eventText}`
        turn.assistant = { ...turn.assistant, state: entry.state, text }
      }
      if (entry.state === 'failed' || entry.state === 'interrupted') {
        turn.terminal = {
          ...base,
          kind: 'status',
          label: entry.summary,
          retryable: true,
          detail: entry.detail
        }
      }
      continue
    }

    if (entry.type === 'error') {
      turn.terminal = {
        ...base,
        kind: 'error',
        label: entry.summary,
        retryable: entry.detail.retry === false
          ? false
          : entry.detail.retry === true || entry.detail.actionable === true,
        detail: entry.detail
      }
      continue
    }

    if (entry.type === 'status' && ['waiting', 'interrupted', 'failed'].includes(entry.state)) {
      turn.terminal = {
        ...base,
        kind: 'status',
        label: entry.summary,
        retryable: entry.detail.retry === true,
        detail: entry.detail
      }
    }
  }

  return projected.sort((left, right) => left.eventId - right.eventId)
}
