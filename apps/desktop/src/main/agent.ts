import {
  conversationArchiveV1ConversationsConversationIdDelete,
  conversationCancelV1ConversationsConversationIdTurnsTurnIdCancelPost,
  conversationCreateV1ConversationsPost,
  conversationGetV1ConversationsConversationIdGet,
  conversationRetryV1ConversationsConversationIdTurnsTurnIdRetryPost,
  conversationSendV1ConversationsConversationIdMessagesPost,
  conversationsListV1ConversationsGet,
  createJobOsApiClient
} from '@jobos/contracts'
import type { ConversationResponse, ConversationSummary, TurnMutationResponse } from '@jobos/contracts'

import type {
  AgentConversationSnapshot,
  AgentConnectionState,
  AgentRecoveryState,
  AgentSessionStreamUpdate,
  AgentSessionSummary,
  AgentTurn,
  AgentTurnMutation,
  ConversationEntryState,
  ConversationEntryType,
  ConversationEvent,
  SafeConversationDetailValue
} from '../shared/contracts.js'

export interface AgentConfig {
  baseUrl: string
  deviceToken: string
  fetch?: typeof fetch
}

interface ApiResult<T> {
  data?: T
  error?: unknown
  response?: Response
}

const entryTypes = new Set<ConversationEntryType>(['user_message', 'turn', 'activity', 'assistant_message', 'status', 'error'])
const entryStates = new Set<ConversationEntryState>(['queued', 'working', 'waiting', 'completed', 'failed', 'interrupted'])
const normalizedDetailKeys = new Set([
  'activity_id', 'phase', 'type', 'name', 'tool_name', 'status', 'operation', 'path', 'file',
  'artifact', 'command', 'result', 'message', 'question', 'prompt', 'choices', 'actionable',
  'retry', 'redacted', 'redactions', 'transport_confirmed', 'text',
  'agent_connection', 'recovery_pending'
])
const MAX_ASSISTANT_TRANSCRIPT_DETAIL = 100_001
const conversationPattern = /^conv_[A-Za-z0-9_-]{1,128}$/
const turnPattern = /^turn_[A-Za-z0-9_-]{1,128}$/
const messagePattern = /^msg_[A-Za-z0-9_-]{1,128}$/

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function safeValue(value: unknown, depth = 0): SafeConversationDetailValue | undefined {
  if (typeof value === 'string') return value.slice(0, 2_000)
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'boolean' || value === null) return value
  if (depth >= 4) return undefined
  if (Array.isArray(value)) {
    return value.slice(0, 50).map(item => safeValue(item, depth + 1)).filter(item => item !== undefined) as SafeConversationDetailValue[]
  }
  if (isRecord(value)) {
    const result: Record<string, SafeConversationDetailValue> = {}
    for (const [key, item] of Object.entries(value).slice(0, 50)) {
      const safe = safeValue(item, depth + 1)
      if (safe !== undefined) result[key.slice(0, 100)] = safe
    }
    return result
  }
  return undefined
}

function normalizedDetail(
  value: unknown,
  eventType: ConversationEntryType
): Record<string, SafeConversationDetailValue> | null {
  if (!isRecord(value)) return null
  const result: Record<string, SafeConversationDetailValue> = {}
  const isAssistantCompletion = eventType === 'assistant_message' && value.type === 'message.complete'
  for (const [key, item] of Object.entries(value)) {
    if (!normalizedDetailKeys.has(key)) continue
    const safe = isAssistantCompletion && key === 'text' && typeof item === 'string'
      ? item.slice(0, MAX_ASSISTANT_TRANSCRIPT_DETAIL)
      : safeValue(item)
    if (safe !== undefined) result[key] = safe
  }
  return result
}

export function normalizeConversationEvent(value: unknown): ConversationEvent | null {
  if (!isRecord(value)
    || !Number.isInteger(value.event_id) || Number(value.event_id) <= 0
    || (value.turn_id !== null && (typeof value.turn_id !== 'string' || !turnPattern.test(value.turn_id)))
    || typeof value.type !== 'string' || !entryTypes.has(value.type as ConversationEntryType)
    || typeof value.state !== 'string' || !entryStates.has(value.state as ConversationEntryState)
    || typeof value.summary !== 'string' || typeof value.occurred_at !== 'string') return null
  const detail = normalizedDetail(value.detail, value.type as ConversationEntryType)
  if (!detail) return null
  const normalized: ConversationEvent = {
    eventId: Number(value.event_id),
    turnId: value.turn_id,
    type: value.type as ConversationEntryType,
    state: value.state as ConversationEntryState,
    summary: value.summary.slice(0, 2_000),
    detail,
    occurredAt: value.occurred_at
  }
  if (typeof value.message_id === 'string' && messagePattern.test(value.message_id)) normalized.messageId = value.message_id
  if (typeof value.text === 'string') normalized.text = value.text.slice(0, 12_000)
  if ((typeof value.source_turn_id === 'string' && turnPattern.test(value.source_turn_id)) || value.source_turn_id === null) normalized.sourceTurnId = value.source_turn_id
  return normalized
}

function normalizeTurn(value: unknown): AgentTurn | null {
  if (!isRecord(value) || typeof value.turn_id !== 'string' || !turnPattern.test(value.turn_id)
    || !['queued', 'running', 'waiting'].includes(String(value.status))
    || typeof value.cancel_requested !== 'boolean') return null
  return { turnId: value.turn_id, status: value.status as AgentTurn['status'], cancelRequested: value.cancel_requested }
}

function validConversationId(value: unknown): value is string {
  return typeof value === 'string' && conversationPattern.test(value)
}

function normalizeRecoveryState(value: unknown): AgentRecoveryState {
  if (value === 'ready' || value === 'recovering' || value === 'quarantined') return value
  throw new Error('Conversation unavailable')
}

function normalizeSummary(value: ConversationSummary): AgentSessionSummary {
  if (!isRecord(value) || !validConversationId(value.conversation_id)
    || !Number.isInteger(value.position) || Number(value.position) < 1 || Number(value.position) > 5
    || typeof value.title !== 'string' || value.title.length > 100 || typeof value.created_at !== 'string'
    || !Number.isInteger(value.latest_event_id) || !isRecord(value.connection)
    || !['online', 'connecting', 'offline'].includes(String(value.connection.state))) {
    throw new Error('Conversation unavailable')
  }
  return {
    conversationId: value.conversation_id,
    position: value.position,
    title: value.title,
    createdAt: value.created_at,
    activeTurn: value.active_turn === null ? null : normalizeTurn(value.active_turn),
    connection: value.connection.state,
    recoveryState: normalizeRecoveryState(value.recovery_state),
    latestEventId: value.latest_event_id
  }
}

function normalizeSnapshot(value: ConversationResponse): AgentConversationSnapshot {
  if (!isRecord(value) || typeof value.conversation_id !== 'string' || !Array.isArray(value.entries)
    || !validConversationId(value.conversation_id) || !Number.isInteger(value.position)
    || typeof value.title !== 'string' || typeof value.created_at !== 'string'
    || !Number.isInteger(value.latest_event_id) || !isRecord(value.connection)
    || !['online', 'connecting', 'offline'].includes(String(value.connection.state))) {
    throw new Error('Conversation unavailable')
  }
  const entries = value.entries.map(normalizeConversationEvent).filter((entry): entry is ConversationEvent => entry !== null)
  return {
    conversationId: value.conversation_id,
    position: value.position,
    title: value.title.slice(0, 100),
    createdAt: value.created_at,
    entries,
    activeTurn: value.active_turn === null ? null : normalizeTurn(value.active_turn),
    connection: value.connection.state,
    recoveryState: normalizeRecoveryState(value.recovery_state),
    latestEventId: value.latest_event_id
  }
}

function errorMessage(error: unknown, fallback: string): string {
  return isRecord(error) && typeof error.detail === 'string' ? error.detail.slice(0, 500) : fallback
}

function unwrap<T>(result: ApiResult<T>, statuses: number[], fallback: string): T {
  if (result.response && statuses.includes(result.response.status) && result.data !== undefined) return result.data
  throw new Error(errorMessage(result.error, fallback))
}

function toMutation(value: TurnMutationResponse): AgentTurnMutation {
  return {
    turnId: value.turn_id,
    messageId: value.message_id,
    sourceTurnId: value.source_turn_id,
    status: value.status
  }
}

export function createMainAgentClient(config: AgentConfig) {
  return createScopedMainAgentClient(config)
}

export class AgentConversationRegistry {
  private readonly ids = new Set<string>()
  private readonly listeners = new Set<() => void>()
  private tail: Promise<void> = Promise.resolve()

  values(): IterableIterator<string> {
    return this.ids.values()
  }

  has(conversationId: string): boolean {
    return this.ids.has(conversationId)
  }

  replace(conversationIds: Iterable<string>): void {
    this.ids.clear()
    for (const conversationId of conversationIds) this.ids.add(conversationId)
    this.notify()
  }

  add(conversationId: string): void {
    this.ids.add(conversationId)
    this.notify()
  }

  delete(conversationId: string): void {
    this.ids.delete(conversationId)
    this.notify()
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private notify(): void {
    for (const listener of this.listeners) {
      try { listener() } catch { continue }
    }
  }

  runExclusive<T>(operation: () => Promise<T>, commit: (value: T) => void): Promise<T> {
    const task = this.tail.then(async () => {
      const value = await operation()
      commit(value)
      return value
    })
    this.tail = task.then(() => undefined, () => undefined)
    return task
  }
}

export function createScopedMainAgentClient(config: AgentConfig, registry?: AgentConversationRegistry) {
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)
  if (config.fetch) client.setConfig({ fetch: config.fetch })
  return {
    async list(): Promise<AgentSessionSummary[]> {
      const request = async () => {
        const result = await conversationsListV1ConversationsGet({ client })
        const value = unwrap(result, [200], 'Conversations unavailable')
        return value.conversations.map(normalizeSummary).sort((left, right) => left.position - right.position)
      }
      if (!registry) return request()
      return registry.runExclusive(request, summaries => registry.replace(summaries.map(summary => summary.conversationId)))
    },
    async create(): Promise<AgentConversationSnapshot> {
      const request = async () => {
        const result = await conversationCreateV1ConversationsPost({ client })
        return normalizeSnapshot(unwrap(result, [201], 'New session could not be started'))
      }
      if (!registry) return request()
      return registry.runExclusive(request, snapshot => registry.add(snapshot.conversationId))
    },
    async get(conversationId: string): Promise<AgentConversationSnapshot> {
      if (!validConversationId(conversationId)) throw new Error('Invalid agent conversation')
      const result = await conversationGetV1ConversationsConversationIdGet({ client, path: { conversation_id: conversationId } })
      const snapshot = normalizeSnapshot(unwrap(result, [200], 'Conversation unavailable'))
      if (snapshot.conversationId !== conversationId) throw new Error('Conversation identity mismatch')
      return snapshot
    },
    async archive(conversationId: string): Promise<void> {
      if (!validConversationId(conversationId)) throw new Error('Invalid agent conversation')
      const request = async () => {
        const result = await conversationArchiveV1ConversationsConversationIdDelete({ client, path: { conversation_id: conversationId } })
        if (!result.response || result.response.status !== 204) throw new Error(errorMessage(result.error, 'Session could not be closed'))
      }
      if (!registry) return request()
      return registry.runExclusive(request, () => registry.delete(conversationId))
    },
    async send(conversationId: string, text: string, idempotencyKey: string): Promise<AgentTurnMutation> {
      if (!validConversationId(conversationId)) throw new Error('Invalid agent conversation')
      const result = await conversationSendV1ConversationsConversationIdMessagesPost({ client, path: { conversation_id: conversationId }, body: { text, idempotency_key: idempotencyKey } })
      return toMutation(unwrap(result, [201], 'Message could not be sent'))
    },
    async cancel(conversationId: string, turnId: string): Promise<AgentTurnMutation> {
      if (!validConversationId(conversationId)) throw new Error('Invalid agent conversation')
      const result = await conversationCancelV1ConversationsConversationIdTurnsTurnIdCancelPost({ client, path: { conversation_id: conversationId, turn_id: turnId } })
      return toMutation(unwrap(result, [200], 'Turn could not be stopped'))
    },
    async retry(conversationId: string, turnId: string, idempotencyKey: string): Promise<AgentTurnMutation> {
      if (!validConversationId(conversationId)) throw new Error('Invalid agent conversation')
      const result = await conversationRetryV1ConversationsConversationIdTurnsTurnIdRetryPost({ client, path: { conversation_id: conversationId, turn_id: turnId }, body: { idempotency_key: idempotencyKey } })
      return toMutation(unwrap(result, [201], 'Turn could not be retried'))
    }
  }
}

export class AgentEventDecoder {
  private buffer = ''

  push(chunk: string): Extract<AgentSessionStreamUpdate, { kind: 'event' }>[] {
    this.buffer += chunk.replaceAll('\r\n', '\n').replaceAll('\r', '\n')
    const blocks = this.buffer.split('\n\n')
    this.buffer = blocks.pop() ?? ''
    const events: Extract<AgentSessionStreamUpdate, { kind: 'event' }>[] = []
    for (const block of blocks) {
      const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
      if (!data) continue
      try {
        const envelope: unknown = JSON.parse(data)
        if (!isRecord(envelope) || !validConversationId(envelope.conversation_id)) continue
        const event = normalizeConversationEvent(envelope.event)
        if (event) events.push({
          kind: 'event', conversationId: envelope.conversation_id,
          recoveryState: normalizeRecoveryState(envelope.recovery_state), event
        })
      } catch {
        continue
      }
    }
    return events
  }
}

interface AgentStreamTarget {
  isDestroyed: () => boolean
  send: (channel: string, update: AgentSessionStreamUpdate) => void
}

interface AgentStreamOptions {
  after?: number
  connectedState?: Exclude<AgentConnectionState, 'reconnecting'>
  fetch?: typeof fetch
  wait?: (milliseconds: number) => Promise<void>
  conversationIds?: string[]
  knownConversationIds?: AgentConversationRegistry
}

export function startAgentEventStream(target: AgentStreamTarget, config: AgentConfig, options: AgentStreamOptions): () => void {
  const controller = new AbortController()
  const fetcher = options.fetch ?? fetch
  const wait = options.wait ?? (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)))
  let cursor = Math.max(0, options.after ?? 0)
  let attempt = 0
  const staticConversationIds = new Set((options.conversationIds ?? []).filter(validConversationId))
  const conversationIds = () => options.knownConversationIds?.values() ?? staticConversationIds.values()
  const hasConversation = (conversationId: string) => options.knownConversationIds?.has(conversationId) ?? staticConversationIds.has(conversationId)
  const pending = new Map<string, Extract<AgentSessionStreamUpdate, { kind: 'event' }>[]>()
  let pendingCount = 0
  const lastAgentStates = new Map<string, Exclude<AgentConnectionState, 'reconnecting'>>()
  for (const conversationId of conversationIds()) lastAgentStates.set(conversationId, options.connectedState ?? 'offline')
  const sendConnection = (state: AgentConnectionState) => {
    if (target.isDestroyed()) return
    for (const conversationId of conversationIds()) target.send('jobos:agent:event', { kind: 'connection', conversationId, state })
  }
  const restoreConnections = () => {
    if (target.isDestroyed()) return
    for (const conversationId of conversationIds()) {
      target.send('jobos:agent:event', {
        kind: 'connection', conversationId, state: lastAgentStates.get(conversationId) ?? options.connectedState ?? 'offline'
      })
    }
  }
  const deliver = (update: Extract<AgentSessionStreamUpdate, { kind: 'event' }>) => {
    const { event, conversationId } = update
    const agentConnection = event.detail.agent_connection
    if (agentConnection === 'online' || agentConnection === 'connecting' || agentConnection === 'offline') {
      lastAgentStates.set(conversationId, agentConnection)
      target.send('jobos:agent:event', { kind: 'connection', conversationId, state: agentConnection })
    }
    target.send('jobos:agent:event', update)
  }
  const reconcilePending = () => {
    if (target.isDestroyed()) return
    for (const [conversationId, updates] of pending) {
      if (!hasConversation(conversationId)) continue
      pending.delete(conversationId)
      pendingCount -= updates.length
      for (const update of updates) deliver(update)
    }
  }
  const unsubscribeRegistry = options.knownConversationIds?.subscribe(reconcilePending)
  const connect = async () => {
    while (!controller.signal.aborted && !target.isDestroyed()) {
      try {
        if (attempt > 0) sendConnection('reconnecting')
        const url = new URL('/v1/conversations/events/stream', config.baseUrl)
        url.searchParams.set('after', String(cursor))
        const response = await fetcher(url, { headers: { Authorization: `Bearer ${config.deviceToken}`, Accept: 'text/event-stream' }, signal: controller.signal })
        if (!response.ok || !response.body) throw new Error('Conversation stream unavailable')
        restoreConnections()
        attempt = 0
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        const eventDecoder = new AgentEventDecoder()
        while (!controller.signal.aborted && !target.isDestroyed()) {
          const { value, done } = await reader.read()
          if (done) break
          for (const update of eventDecoder.push(decoder.decode(value, { stream: true }))) {
            const { event, conversationId } = update
            if (event.eventId <= cursor) continue
            cursor = event.eventId
            if (!hasConversation(conversationId)) {
              if (options.knownConversationIds && pendingCount < 1_000) {
                const queued = pending.get(conversationId) ?? []
                queued.push(update)
                pending.set(conversationId, queued)
                pendingCount += 1
              }
              continue
            }
            deliver(update)
          }
        }
      } catch {
        if (controller.signal.aborted || target.isDestroyed()) return
        sendConnection('reconnecting')
      }
      attempt += 1
      await wait(Math.min(500 * 2 ** Math.min(attempt - 1, 4), 8_000))
    }
  }
  void connect()
  return () => {
    unsubscribeRegistry?.()
    controller.abort()
  }
}
