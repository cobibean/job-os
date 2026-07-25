// @vitest-environment node

import { expect, test, vi } from 'vitest'

import type { ConversationEvent } from '../shared/contracts.js'
import {
  AgentEventDecoder,
  createMainAgentClient,
  startAgentEventStream
} from './agent.js'

const event = (eventId: number, overrides: Record<string, unknown> = {}) => ({
  event_id: eventId,
  turn_id: 'turn-1',
  type: 'activity',
  state: 'working',
  summary: `Action ${eventId}`,
  detail: { activity_id: `tool-${eventId}`, phase: 'start' },
  occurred_at: '2026-07-20T10:00:00Z',
  ...overrides
})

function streamResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    }
  }), { status, headers: { 'content-type': 'text/event-stream' } })
}

test('the conversation decoder survives split CRLF chunks and ignores malformed frames', () => {
  const decoder = new AgentEventDecoder()
  const first = decoder.push('retry: 2000\r\nid: 7\r\nevent: conversation\r\ndata: {"event_id":7,"turn_id":"turn-1","type":"assistant_message",')
  const second = decoder.push('"state":"working","summary":"Hel","detail":{},"occurred_at":"2026-07-20T10:00:00Z"}\r\n\r\ndata: {"event_id":"bad"}\r\n\r\n')

  expect(first).toEqual([])
  expect(second).toEqual([expect.objectContaining({
    eventId: 7,
    turnId: 'turn-1',
    type: 'assistant_message',
    state: 'working',
    summary: 'Hel'
  })])
})

test('normalization strips Hermes routing, credentials, and unknown raw frame fields', () => {
  const decoder = new AgentEventDecoder()
  const payload = event(8, { detail: {
    activity_id: 'tool-8', operation: 'Read project file', redacted: true,
    session_id: 'live-hermes-session', authorization: 'Bearer secret', raw_frame: { token: 'secret' }
  } })
  const [normalized] = decoder.push(`data: ${JSON.stringify(payload)}\n\n`)

  expect(normalized?.detail).toEqual({ activity_id: 'tool-8', operation: 'Read project file', redacted: true })
  expect(JSON.stringify(normalized)).not.toMatch(/session_id|authorization|raw_frame|Bearer secret/)
})

test('normalization preserves bounded completion transcripts but not oversized deltas', () => {
  const decoder = new AgentEventDecoder()
  const text = 'x'.repeat(100_050)
  const complete = event(9, {
    type: 'assistant_message',
    state: 'completed',
    summary: text,
    detail: { type: 'message.complete', text }
  })
  const delta = event(10, {
    type: 'assistant_message',
    state: 'working',
    summary: text,
    detail: { type: 'message.delta', text }
  })

  const [normalizedComplete, normalizedDelta] = decoder.push(
    `data: ${JSON.stringify(complete)}\n\ndata: ${JSON.stringify(delta)}\n\n`
  )

  expect(normalizedComplete?.detail.text).toBe(text.slice(0, 100_001))
  expect(normalizedDelta?.detail.text).toBe(text.slice(0, 2_000))
  expect(normalizedComplete?.summary).toBe(text.slice(0, 2_000))
})

test('the resumable stream starts at the snapshot cursor, reconnects at the latest cursor, and dedupes overlap', async () => {
  const urls: string[] = []
  const fetcher = vi.fn(async (input: string | URL | Request) => {
    const url = String(input)
    urls.push(url)
    if (urls.length === 1) {
      return streamResponse([
        `id: 10\ndata: ${JSON.stringify(event(10))}\n\n`,
        `id: 11\ndata: ${JSON.stringify(event(11))}\n\n`
      ])
    }
    return streamResponse([
      `id: 11\ndata: ${JSON.stringify(event(11))}\n\n`,
      `id: 12\ndata: ${JSON.stringify(event(12, { state: 'completed' }))}\n\n`
    ])
  })
  const delivered: ConversationEvent[] = []
  const states: string[] = []
  let stop: () => void = () => undefined
  stop = startAgentEventStream(
    {
      isDestroyed: () => delivered.length === 2,
      send: (_channel, update) => {
        if (update.kind === 'event') delivered.push(update.event)
        else states.push(update.state)
        if (delivered.length === 2) stop()
      }
    },
    { baseUrl: 'http://jobos.test', deviceToken: 'fake-device-token' },
    { after: 10, fetch: fetcher, wait: async () => undefined }
  )

  await vi.waitFor(() => expect(delivered.map(item => item.eventId)).toEqual([11, 12]))
  expect(new URL(urls[0]!).searchParams.get('after')).toBe('10')
  expect(new URL(urls[1]!).searchParams.get('after')).toBe('11')
  expect(states).toContain('reconnecting')
})

test('startup replay from zero cannot skip an event between independent snapshots', async () => {
  const delivered: ConversationEvent[] = []
  let stop: () => void = () => undefined
  const fetcher = vi.fn(async (input: string | URL | Request) => {
    expect(new URL(String(input)).searchParams.get('after')).toBe('0')
    return streamResponse([`id: 6\ndata: ${JSON.stringify(event(6))}\n\n`])
  })
  stop = startAgentEventStream(
    {
      isDestroyed: () => delivered.length === 1,
      send: (_channel, update) => {
        if (update.kind === 'event') delivered.push(update.event)
        if (delivered.length === 1) stop()
      }
    },
    { baseUrl: 'http://jobos.test', deviceToken: 'fake-device-token' },
    { fetch: fetcher, wait: async () => undefined }
  )

  await vi.waitFor(() => expect(delivered.map(item => item.eventId)).toEqual([6]))
})

test('the typed client maps generated contracts without returning credentials or raw API fields', async () => {
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    const url = new URL(request.url)
    expect(request.headers.get('authorization')).toBe('Bearer fake-device-token')
    if (url.pathname.endsWith('/messages')) {
      return new Response(JSON.stringify({ turn_id: 'turn-2', message_id: 'message-2', status: 'running' }), { status: 201, headers: { 'content-type': 'application/json' } })
    }
    if (url.pathname.endsWith('/reset')) {
      return new Response(JSON.stringify({
        conversation_id: 'conv-fresh', entries: [], active_turn: null,
        connection: { state: 'online' }, latest_event_id: 0
      }), { status: 200, headers: { 'content-type': 'application/json' } })
    }
    return new Response(JSON.stringify({
      conversation_id: 'conv-current',
      entries: [event(4, { type: 'user_message', text: 'Hello', message_id: 'message-1' })],
      active_turn: { turn_id: 'turn-1', status: 'running', cancel_requested: false },
      connection: { state: 'online' },
      latest_event_id: 4
    }), { status: 200, headers: { 'content-type': 'application/json' } })
  })
  const client = createMainAgentClient({
    baseUrl: 'http://jobos.test',
    deviceToken: 'fake-device-token',
    fetch: fetcher
  })

  const snapshot = await client.get()
  const sent = await client.send('Hello', 'idempotency-0001')
  const reset = await client.reset()

  expect(snapshot).toMatchObject({
    conversationId: 'conv-current',
    latestEventId: 4,
    activeTurn: { turnId: 'turn-1', status: 'running' },
    entries: [{ eventId: 4, text: 'Hello' }]
  })
  expect(sent).toEqual({ turnId: 'turn-2', messageId: 'message-2', status: 'running' })
  expect(reset).toMatchObject({ conversationId: 'conv-fresh', entries: [], activeTurn: null })
  expect(JSON.stringify({ snapshot, sent, reset })).not.toContain('fake-device-token')
  expect(JSON.stringify({ snapshot, sent, reset })).not.toContain('event_id')
})
