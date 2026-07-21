import {
  conversationCancelV1ConversationsCurrentTurnsTurnIdCancelPost,
  conversationCurrentV1ConversationsCurrentGet,
  conversationResetV1ConversationsCurrentResetPost,
  conversationRetryV1ConversationsCurrentTurnsTurnIdRetryPost,
  conversationSendV1ConversationsCurrentMessagesPost,
  createJobOsApiClient
} from '@jobos/contracts'
import type { ConversationResponse, TurnMutationResponse } from '@jobos/contracts'

import type {
  AgentConversationSnapshot,
  AgentConnectionState,
  AgentStreamUpdate,
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

function normalizedDetail(value: unknown): Record<string, SafeConversationDetailValue> | null {
  if (!isRecord(value)) return null
  const result: Record<string, SafeConversationDetailValue> = {}
  for (const [key, item] of Object.entries(value)) {
    if (!normalizedDetailKeys.has(key)) continue
    const safe = safeValue(item)
    if (safe !== undefined) result[key] = safe
  }
  return result
}

export function normalizeConversationEvent(value: unknown): ConversationEvent | null {
  if (!isRecord(value)
    || !Number.isInteger(value.event_id) || Number(value.event_id) <= 0
    || (value.turn_id !== null && typeof value.turn_id !== 'string')
    || typeof value.type !== 'string' || !entryTypes.has(value.type as ConversationEntryType)
    || typeof value.state !== 'string' || !entryStates.has(value.state as ConversationEntryState)
    || typeof value.summary !== 'string' || typeof value.occurred_at !== 'string') return null
  const detail = normalizedDetail(value.detail)
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
  if (typeof value.message_id === 'string') normalized.messageId = value.message_id
  if (typeof value.text === 'string') normalized.text = value.text.slice(0, 12_000)
  if (typeof value.source_turn_id === 'string' || value.source_turn_id === null) normalized.sourceTurnId = value.source_turn_id
  return normalized
}

function normalizeTurn(value: unknown): AgentTurn | null {
  if (!isRecord(value) || typeof value.turn_id !== 'string'
    || !['queued', 'running', 'waiting'].includes(String(value.status))
    || typeof value.cancel_requested !== 'boolean') return null
  return { turnId: value.turn_id, status: value.status as AgentTurn['status'], cancelRequested: value.cancel_requested }
}

function normalizeSnapshot(value: ConversationResponse): AgentConversationSnapshot {
  if (!isRecord(value) || typeof value.conversation_id !== 'string' || !Array.isArray(value.entries)
    || !Number.isInteger(value.latest_event_id) || !isRecord(value.connection)
    || !['online', 'connecting', 'offline'].includes(String(value.connection.state))) {
    throw new Error('Conversation unavailable')
  }
  const entries = value.entries.map(normalizeConversationEvent).filter((entry): entry is ConversationEvent => entry !== null)
  return {
    conversationId: value.conversation_id,
    entries,
    activeTurn: value.active_turn === null ? null : normalizeTurn(value.active_turn),
    connection: value.connection.state,
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
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)
  if (config.fetch) client.setConfig({ fetch: config.fetch })
  return {
    async get(): Promise<AgentConversationSnapshot> {
      const result = await conversationCurrentV1ConversationsCurrentGet({ client })
      return normalizeSnapshot(unwrap(result, [200], 'Conversation unavailable'))
    },
    async reset(): Promise<AgentConversationSnapshot> {
      const result = await conversationResetV1ConversationsCurrentResetPost({ client })
      return normalizeSnapshot(unwrap(result, [200], 'New session could not be started'))
    },
    async send(text: string, idempotencyKey: string): Promise<AgentTurnMutation> {
      const result = await conversationSendV1ConversationsCurrentMessagesPost({ client, body: { text, idempotency_key: idempotencyKey } })
      return toMutation(unwrap(result, [201], 'Message could not be sent'))
    },
    async cancel(turnId: string): Promise<AgentTurnMutation> {
      const result = await conversationCancelV1ConversationsCurrentTurnsTurnIdCancelPost({ client, path: { turn_id: turnId } })
      return toMutation(unwrap(result, [200], 'Turn could not be stopped'))
    },
    async retry(turnId: string, idempotencyKey: string): Promise<AgentTurnMutation> {
      const result = await conversationRetryV1ConversationsCurrentTurnsTurnIdRetryPost({ client, path: { turn_id: turnId }, body: { idempotency_key: idempotencyKey } })
      return toMutation(unwrap(result, [201], 'Turn could not be retried'))
    }
  }
}

export class AgentEventDecoder {
  private buffer = ''

  push(chunk: string): ConversationEvent[] {
    this.buffer += chunk.replaceAll('\r\n', '\n').replaceAll('\r', '\n')
    const blocks = this.buffer.split('\n\n')
    this.buffer = blocks.pop() ?? ''
    const events: ConversationEvent[] = []
    for (const block of blocks) {
      const data = block.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
      if (!data) continue
      try {
        const event = normalizeConversationEvent(JSON.parse(data))
        if (event) events.push(event)
      } catch {
        continue
      }
    }
    return events
  }
}

interface AgentStreamTarget {
  isDestroyed: () => boolean
  send: (channel: string, update: AgentStreamUpdate) => void
}

interface AgentStreamOptions {
  after?: number
  connectedState?: Exclude<AgentConnectionState, 'reconnecting'>
  fetch?: typeof fetch
  wait?: (milliseconds: number) => Promise<void>
}

export function startAgentEventStream(target: AgentStreamTarget, config: AgentConfig, options: AgentStreamOptions): () => void {
  const controller = new AbortController()
  const fetcher = options.fetch ?? fetch
  const wait = options.wait ?? (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)))
  let cursor = Math.max(0, options.after ?? 0)
  let attempt = 0
  let lastAgentState = options.connectedState ?? 'offline'
  const sendConnection = (state: AgentConnectionState) => {
    if (!target.isDestroyed()) target.send('jobos:agent:event', { kind: 'connection', state })
  }
  const connect = async () => {
    while (!controller.signal.aborted && !target.isDestroyed()) {
      try {
        if (attempt > 0) sendConnection('reconnecting')
        const url = new URL('/v1/conversations/current/events/stream', config.baseUrl)
        url.searchParams.set('after', String(cursor))
        const response = await fetcher(url, { headers: { Authorization: `Bearer ${config.deviceToken}`, Accept: 'text/event-stream' }, signal: controller.signal })
        if (!response.ok || !response.body) throw new Error('Conversation stream unavailable')
        sendConnection(lastAgentState)
        attempt = 0
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        const eventDecoder = new AgentEventDecoder()
        while (!controller.signal.aborted && !target.isDestroyed()) {
          const { value, done } = await reader.read()
          if (done) break
          for (const event of eventDecoder.push(decoder.decode(value, { stream: true }))) {
            if (event.eventId <= cursor) continue
            cursor = event.eventId
            const agentConnection = event.detail.agent_connection
            if (agentConnection === 'online' || agentConnection === 'connecting' || agentConnection === 'offline') {
              lastAgentState = agentConnection
              sendConnection(agentConnection)
            }
            target.send('jobos:agent:event', { kind: 'event', event })
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
  return () => controller.abort()
}
