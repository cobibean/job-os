// @vitest-environment node

import { expect, test, vi } from 'vitest'

import type { AgentSessionStreamUpdate } from '../shared/contracts.js'
import { AgentConversationRegistry, AgentEventDecoder, createMainAgentClient, createScopedMainAgentClient, startAgentEventStream } from './agent.js'

const event = (eventId: number, overrides: Record<string, unknown> = {}) => ({
  event_id: eventId, turn_id: 'turn_1', type: 'activity', state: 'working', summary: `Action ${eventId}`,
  detail: { activity_id: `tool-${eventId}`, phase: 'start' }, occurred_at: '2026-08-16T10:00:00Z', ...overrides
})
const envelope = (conversationId: string, eventId: number, overrides: Record<string, unknown> = {}) => ({
  conversation_id: conversationId, recovery_state: 'ready', event: event(eventId, overrides)
})
const apiSnapshot = (conversationId: string, position: number) => ({
  conversation_id: conversationId, position, title: `Session ${position}`, created_at: '2026-08-16T09:00:00Z',
  entries: [], active_turn: null, connection: { state: 'online' }, recovery_state: 'ready', latest_event_id: 0
})

function streamResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(encoder.encode(chunk)); controller.close() } }), {
    status: 200, headers: { 'content-type': 'text/event-stream' }
  })
}

test('the decoder validates scoped envelopes, survives split CRLF, and strips private routing fields', () => {
  const decoder = new AgentEventDecoder()
  const payload = envelope('conv_one', 7, { detail: {
    activity_id: 'tool-7', operation: 'Read file', session_id: 'hermes-private', authorization: 'Bearer secret'
  } })
  const serialized = JSON.stringify(payload)
  expect(decoder.push(`id: 7\r\ndata: ${serialized.slice(0, 50)}`)).toEqual([])
  const [update] = decoder.push(`${serialized.slice(50)}\r\n\r\ndata: {"conversation_id":"../bad","event":{}}\r\n\r\n`)
  expect(update).toEqual(expect.objectContaining({ kind: 'event', conversationId: 'conv_one', event: expect.objectContaining({ eventId: 7 }) }))
  expect(update?.event.detail).toEqual({ activity_id: 'tool-7', operation: 'Read file' })
  expect(update?.recoveryState).toBe('ready')
  expect(JSON.stringify(update)).not.toMatch(/hermes-private|Bearer secret|authorization|session_id/)
})

test('the decoder carries quarantine and ready recovery transitions explicitly', () => {
  const decoder = new AgentEventDecoder()
  const quarantined = { ...envelope('conv_one', 8), recovery_state: 'quarantined' }
  const ready = { ...envelope('conv_one', 9), recovery_state: 'ready' }
  expect(decoder.push(`data: ${JSON.stringify(quarantined)}\n\ndata: ${JSON.stringify(ready)}\n\n`))
    .toEqual([
      expect.objectContaining({ conversationId: 'conv_one', recoveryState: 'quarantined' }),
      expect.objectContaining({ conversationId: 'conv_one', recoveryState: 'ready' })
    ])
})

test('one shared stream preserves interleaved ownership and reconnects from the global cursor without duplicates', async () => {
  const urls: string[] = []
  const fetcher = vi.fn(async (input: string | URL | Request) => {
    urls.push(String(input))
    return urls.length === 1
      ? streamResponse([
          `id: 10\ndata: ${JSON.stringify(envelope('conv_one', 10))}\n\n`,
          `id: 11\ndata: ${JSON.stringify(envelope('conv_two', 11))}\n\n`
        ])
      : streamResponse([
          `id: 11\ndata: ${JSON.stringify(envelope('conv_two', 11))}\n\n`,
          `id: 12\ndata: ${JSON.stringify(envelope('conv_one', 12, { state: 'completed' }))}\n\n`
        ])
  })
  const delivered: AgentSessionStreamUpdate[] = []
  let stop: () => void = () => undefined
  stop = startAgentEventStream({
    isDestroyed: () => delivered.filter(update => update.kind === 'event').length === 2,
    send: (_channel, update) => {
      if (update.kind === 'event') delivered.push(update)
      if (delivered.length === 2) stop()
    }
  }, { baseUrl: 'http://jobos.test', deviceToken: 'secret' }, {
    after: 10, conversationIds: ['conv_one', 'conv_two'], fetch: fetcher, wait: async () => undefined
  })
  await vi.waitFor(() => expect(delivered.map(update => update.kind === 'event' && [update.conversationId, update.event.eventId])).toEqual([
    ['conv_two', 11], ['conv_one', 12]
  ]))
  expect(new URL(urls[0]!).pathname).toBe('/v1/conversations/events/stream')
  expect(new URL(urls[0]!).searchParams.get('after')).toBe('10')
  expect(new URL(urls[1]!).searchParams.get('after')).toBe('11')
})

test('transport connection updates always carry each known conversation id', async () => {
  const updates: AgentSessionStreamUpdate[] = []
  const stop = startAgentEventStream({ isDestroyed: () => updates.length >= 2, send: (_channel, update) => updates.push(update) },
    { baseUrl: 'http://jobos.test', deviceToken: 'secret' }, {
      conversationIds: ['conv_one', 'conv_two'], fetch: vi.fn().mockRejectedValue(new Error('offline')), wait: async () => undefined
    })
  await vi.waitFor(() => expect(updates).toEqual(expect.arrayContaining([
    { kind: 'connection', conversationId: 'conv_one', state: 'reconnecting' },
    { kind: 'connection', conversationId: 'conv_two', state: 'reconnecting' }
  ])))
  stop()
})

test('a syntactically valid but unknown conversation envelope is not delivered', async () => {
  const updates: AgentSessionStreamUpdate[] = []
  let stop: () => void = () => undefined
  stop = startAgentEventStream({
    isDestroyed: () => updates.some(update => update.kind === 'event'),
    send: (_channel, update) => {
      if (update.kind === 'event') updates.push(update)
      if (updates.length) stop()
    }
  }, { baseUrl: 'http://jobos.test', deviceToken: 'secret' }, {
    conversationIds: ['conv_one'], wait: async () => undefined,
    fetch: vi.fn(async () => streamResponse([
      `id: 1\ndata: ${JSON.stringify(envelope('conv_unknown', 1))}\n\n`,
      `id: 2\ndata: ${JSON.stringify(envelope('conv_one', 2))}\n\n`
    ]))
  })
  await vi.waitFor(() => expect(updates).toEqual([
    expect.objectContaining({ kind: 'event', conversationId: 'conv_one', event: expect.objectContaining({ eventId: 2 }) })
  ]))
})

test('an event emitted before create returns is buffered and delivered when the registry commits the new id', async () => {
  const registry = new AgentConversationRegistry()
  registry.add('conv_one')
  const delivered: AgentSessionStreamUpdate[] = []
  let releaseCreate!: () => void
  const createBarrier = new Promise<void>(resolve => { releaseCreate = resolve })
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    if (request.method === 'POST') {
      await createBarrier
      return Response.json(apiSnapshot('conv_two', 2), { status: 201 })
    }
    return streamResponse([`data: ${JSON.stringify(envelope('conv_two', 1, { state: 'completed' }))}\n\n`])
  })
  const client = createScopedMainAgentClient({ baseUrl: 'http://jobos.test', deviceToken: 'secret', fetch: fetcher }, registry)
  const stop = startAgentEventStream({
    isDestroyed: () => false,
    send: (_channel, update) => { if (update.kind === 'event') delivered.push(update) }
  }, { baseUrl: 'http://jobos.test', deviceToken: 'secret' }, {
    knownConversationIds: registry, fetch: fetcher, wait: () => new Promise(() => undefined)
  })

  const creating = client.create()
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  expect(delivered).toEqual([])
  releaseCreate()
  await expect(creating).resolves.toEqual(expect.objectContaining({ conversationId: 'conv_two' }))
  await vi.waitFor(() => expect(delivered).toEqual([
    expect.objectContaining({ conversationId: 'conv_two', event: expect.objectContaining({ eventId: 1 }) })
  ]))
  stop()
})

test('typed scoped routes validate returned identity and expose no transport secrets', async () => {
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    const url = new URL(request.url)
    expect(request.headers.get('authorization')).toBe('Bearer secret')
    if (url.pathname === '/v1/conversations' && request.method === 'GET') return Response.json({ conversations: [apiSnapshot('conv_one', 1)] })
    if (url.pathname === '/v1/conversations' && request.method === 'POST') return Response.json(apiSnapshot('conv_two', 2), { status: 201 })
    if (url.pathname.endsWith('/messages')) return Response.json({ turn_id: 'turn_2', status: 'running' }, { status: 201 })
    if (request.method === 'DELETE') return new Response(null, { status: 204 })
    return Response.json(apiSnapshot('conv_one', 1))
  })
  const client = createMainAgentClient({ baseUrl: 'http://jobos.test', deviceToken: 'secret', fetch: fetcher })
  expect(await client.list()).toEqual([expect.objectContaining({ conversationId: 'conv_one', position: 1 })])
  expect(await client.create()).toEqual(expect.objectContaining({ conversationId: 'conv_two' }))
  expect(await client.get('conv_one')).toEqual(expect.objectContaining({ conversationId: 'conv_one' }))
  expect(await client.send('conv_one', 'Hello', 'idempotency-01')).toEqual({ turnId: 'turn_2', status: 'running' })
  await expect(client.archive('conv_one')).resolves.toBeUndefined()
  expect(JSON.stringify(await client.list())).not.toContain('secret')
})

test('a mismatched snapshot identity is rejected', async () => {
  const client = createMainAgentClient({
    baseUrl: 'http://jobos.test', deviceToken: 'secret', fetch: vi.fn(async () => Response.json(apiSnapshot('conv_other', 1)))
  })
  await expect(client.get('conv_one')).rejects.toThrow('Conversation identity mismatch')
})

test('a stale list and create are barrier-serialized so the created conversation remains allowed', async () => {
  let releaseList!: () => void
  let listStarted!: () => void
  const started = new Promise<void>(resolve => { listStarted = resolve })
  const barrier = new Promise<void>(resolve => { releaseList = resolve })
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    if (request.method === 'GET') {
      listStarted()
      await barrier
      return Response.json({ conversations: [apiSnapshot('conv_one', 1)] })
    }
    return Response.json(apiSnapshot('conv_two', 2), { status: 201 })
  })
  const registry = new AgentConversationRegistry()
  const client = createScopedMainAgentClient({ baseUrl: 'http://jobos.test', deviceToken: 'secret', fetch: fetcher }, registry)
  const list = client.list()
  await started
  const create = client.create()
  expect(fetcher).toHaveBeenCalledTimes(1)
  releaseList()
  await Promise.all([list, create])
  expect([...registry.values()]).toEqual(['conv_one', 'conv_two'])
})

test('a stale list and archive are barrier-serialized so an archived conversation is not re-allowed', async () => {
  let releaseList!: () => void
  let listStarted!: () => void
  const started = new Promise<void>(resolve => { listStarted = resolve })
  const barrier = new Promise<void>(resolve => { releaseList = resolve })
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    if (request.method === 'GET') {
      listStarted()
      await barrier
      return Response.json({ conversations: [apiSnapshot('conv_one', 1), apiSnapshot('conv_two', 2)] })
    }
    return new Response(null, { status: 204 })
  })
  const registry = new AgentConversationRegistry()
  const client = createScopedMainAgentClient({ baseUrl: 'http://jobos.test', deviceToken: 'secret', fetch: fetcher }, registry)
  const list = client.list()
  await started
  const archive = client.archive('conv_two')
  expect(fetcher).toHaveBeenCalledTimes(1)
  releaseList()
  await Promise.all([list, archive])
  expect([...registry.values()]).toEqual(['conv_one'])
})

test('the SSE transport starts and retries independently while initial registry hydration is unavailable', async () => {
  const registry = new AgentConversationRegistry()
  const delivered: AgentSessionStreamUpdate[] = []
  const fetcher = vi.fn()
    .mockRejectedValueOnce(new Error('initial list and stream unavailable'))
    .mockImplementationOnce(async () => {
      registry.add('conv_one')
      return streamResponse([`data: ${JSON.stringify(envelope('conv_one', 1))}\n\n`])
    })
  let stop: () => void = () => undefined
  stop = startAgentEventStream({
    isDestroyed: () => delivered.length > 0,
    send: (_channel, update) => {
      if (update.kind === 'event') delivered.push(update)
      if (delivered.length) stop()
    }
  }, { baseUrl: 'http://jobos.test', deviceToken: 'secret' }, {
    knownConversationIds: registry, fetch: fetcher, wait: async () => undefined
  })
  await vi.waitFor(() => expect(delivered).toHaveLength(1))
  expect(fetcher).toHaveBeenCalledTimes(2)
})
