import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentConversationSnapshot, AgentStreamUpdate, ConversationEvent } from '../../shared/contracts'
import {
  agentConversationReducer,
  initialAgentConversationState,
  projectConversation,
  useAgentConversation
} from './useAgentConversation'

afterEach(cleanup)

const event = (eventId: number, overrides: Partial<ConversationEvent> = {}): ConversationEvent => ({
  eventId,
  turnId: 'turn-1',
  type: 'activity',
  state: 'working',
  summary: `Action ${eventId}`,
  detail: { activity_id: `tool-${eventId}`, phase: 'start' },
  occurredAt: `2026-07-20T10:00:${String(eventId).padStart(2, '0')}Z`,
  ...overrides
})

const snapshot = (entries: ConversationEvent[] = []): AgentConversationSnapshot => ({
  conversationId: 'conv-current', entries, activeTurn: null,
  connection: 'online', latestEventId: entries.at(-1)?.eventId ?? 0
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(next => { resolve = next })
  return { promise, resolve }
}

test('hydration merges snapshot and early stream delivery once by durable event id', () => {
  const streamed = agentConversationReducer(initialAgentConversationState, { type: 'event', event: event(3) })
  const hydrated = agentConversationReducer(streamed, { type: 'hydrate', snapshot: snapshot([event(1), event(2), event(3)]) })
  const duplicate = agentConversationReducer(hydrated, { type: 'event', event: event(3) })

  expect(duplicate.entries.map(item => item.eventId)).toEqual([1, 2, 3])
  expect(duplicate.conversationId).toBe('conv-current')
  expect(duplicate.restoring).toBe(false)
})

test('activity completion does not clear the active Hermes turn', () => {
  const running = { ...initialAgentConversationState, activeTurn: { turnId: 'turn-1', status: 'running' as const, cancelRequested: false } }
  const next = agentConversationReducer(running, { type: 'event', event: event(4, { type: 'activity', state: 'completed' }) })

  expect(next.activeTurn).toEqual(running.activeTurn)
})

test('an advisory waiting activity does not pause the active Hermes turn', () => {
  const running = { ...initialAgentConversationState, activeTurn: { turnId: 'turn-1', status: 'running' as const, cancelRequested: false } }
  const next = agentConversationReducer(running, {
    type: 'event',
    event: event(4, {
      type: 'activity',
      state: 'waiting',
      detail: { activity_id: 'tool-1', type: 'tool.output_risk', risk: 'high' }
    })
  })

  expect(next.activeTurn).toEqual(running.activeTurn)
})

test('hydration applies terminal events streamed after its snapshot cursor', () => {
  const completed = event(4, { type: 'assistant_message', state: 'completed', summary: 'Done' })
  const streamed = agentConversationReducer(initialAgentConversationState, { type: 'event', event: completed })
  const staleSnapshot = {
    ...snapshot([event(1), event(2)]),
    activeTurn: { turnId: 'turn-1', status: 'running' as const, cancelRequested: false }
  }
  const hydrated = agentConversationReducer(streamed, { type: 'hydrate', snapshot: staleSnapshot })

  expect(hydrated.activeTurn).toBeNull()
  expect(hydrated.entries.map(item => item.eventId)).toEqual([1, 2, 4])
})

test('hydration preserves a newer durable gateway connectivity transition', () => {
  const offline = event(4, {
    turnId: null, type: 'status', state: 'working', summary: 'Agent offline',
    detail: { agent_connection: 'offline' }
  })
  const streamed = agentConversationReducer(initialAgentConversationState, { type: 'event', event: offline })
  const hydrated = agentConversationReducer(streamed, {
    type: 'hydrate', snapshot: { ...snapshot([event(1), event(2)]), connection: 'online' }
  })

  expect(hydrated.connection).toBe('offline')
})

test('a completed tool-heavy turn projects activity before one final assistant response', () => {
  const entries = [
    event(1, { type: 'assistant_message', summary: 'Agent response', detail: { type: 'message.start', text: '' } }),
    ...Array.from({ length: 3 }, (_, index) => [
      event(index * 2 + 2, { summary: `Action ${index + 1}`, detail: { activity_id: `tool-${index}`, phase: 'start' } }),
      event(index * 2 + 3, { summary: `Action ${index + 1}`, state: 'completed', detail: { activity_id: `tool-${index}`, phase: 'complete' } })
    ]).flat(),
    event(8, { type: 'assistant_message', state: 'completed', summary: 'Finished once.', detail: { type: 'message.complete', text: 'Finished once.' } })
  ]

  const [turn] = projectConversation(entries)
  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state: 'completed',
    activities: [
      expect.objectContaining({ label: 'Action 1' }),
      expect.objectContaining({ label: 'Action 2' }),
      expect.objectContaining({ label: 'Action 3' })
    ],
    assistant: expect.objectContaining({ text: 'Finished once.', state: 'completed' })
  }))
})

test('fifteen distinct tool calls project to fifteen concise chronological activities', () => {
  const entries = Array.from({ length: 15 }, (_, index) => [
    event(index * 3 + 1, { summary: `Action ${index + 1}`, detail: { activity_id: `tool-${index}`, phase: 'start' } }),
    event(index * 3 + 2, { summary: `Action ${index + 1}`, detail: { activity_id: `tool-${index}`, phase: 'progress' } }),
    event(index * 3 + 3, { summary: `Action ${index + 1}`, state: 'completed', detail: { activity_id: `tool-${index}`, phase: 'complete' } })
  ]).flat()

  const [turn] = projectConversation(entries)
  if (!turn || turn.kind !== 'agent-turn') throw new Error('Expected an agent turn')
  expect(turn.activities).toHaveLength(15)
  expect(turn.activities.map(item => item.label)).toEqual(Array.from({ length: 15 }, (_, index) => `Action ${index + 1}`))
  expect(turn.activities.every(item => item.state === 'completed')).toBe(true)
})

test('assistant deltas form one streaming response and completion replaces it without duplicate final text', () => {
  const [turn] = projectConversation([
    event(1, { type: 'assistant_message', summary: 'Agent response', detail: { type: 'message.start', text: '' } }),
    event(2, { type: 'assistant_message', summary: 'Hello ', detail: { type: 'message.delta', text: 'Hello ' } }),
    event(3, { type: 'assistant_message', summary: 'there', detail: { type: 'message.delta', text: 'there' } }),
    event(4, { type: 'assistant_message', state: 'completed', summary: 'Hello there', detail: { type: 'message.complete', text: 'Hello there' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    assistant: expect.objectContaining({ text: 'Hello there', state: 'completed' })
  }))
})

test.each([
  ['running', event(2, { type: 'assistant_message', state: 'working', summary: 'Drafting', detail: { type: 'message.delta' } })],
  ['waiting', event(2, { type: 'status', state: 'waiting', summary: 'Choose one', detail: { actionable: true } })],
  ['interrupted', event(2, { type: 'status', state: 'interrupted', summary: 'Stopped', detail: { retry: true } })],
  ['failed', event(2, { type: 'error', state: 'failed', summary: 'Failed', detail: { retry: true } })]
] as const)('projects a %s turn without mislabeling its state', (_label, terminalEvent) => {
  const [turn] = projectConversation([event(1), terminalEvent])
  expect(turn).toEqual(expect.objectContaining({ kind: 'agent-turn', state: terminalEvent.state }))
  if (terminalEvent.type === 'status' || terminalEvent.type === 'error') {
    expect(turn).toEqual(expect.objectContaining({ terminal: expect.objectContaining({ state: terminalEvent.state }) }))
  }
})

test.each(['failed', 'interrupted'] as const)('projects a terminal assistant completion as explicit %s state with retry', state => {
  const [turn] = projectConversation([
    event(1, { type: 'assistant_message', state: 'working', summary: 'Partial response', detail: { type: 'message.delta' } }),
    event(2, { type: 'assistant_message', state, summary: `Response ${state}`, detail: { type: 'message.complete', status: state } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state,
    terminal: expect.objectContaining({ state, retryable: true }),
    assistant: expect.objectContaining({ state, text: `Response ${state}` })
  }))
})

test('clears a resolved waiting notice when the turn later completes', () => {
  const [turn] = projectConversation([
    event(1, { type: 'status', state: 'waiting', summary: 'Choose one', detail: { actionable: true } }),
    event(2, { type: 'assistant_message', state: 'completed', summary: 'Finished after the choice', detail: { type: 'message.complete' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state: 'completed',
    terminal: null,
    assistant: expect.objectContaining({ text: 'Finished after the choice' })
  }))
})

test('keeps waiting authoritative across a late completed activity', () => {
  const [turn] = projectConversation([
    event(1, { type: 'status', state: 'waiting', summary: 'Choose one', detail: { actionable: true } }),
    event(2, { state: 'completed', summary: 'Late activity', detail: { activity_id: 'late', phase: 'complete' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state: 'waiting',
    terminal: expect.objectContaining({ state: 'waiting' }),
    activities: [expect.objectContaining({ state: 'completed' })]
  }))
})

test('clears a waiting notice when activity resumes the turn', () => {
  const [turn] = projectConversation([
    event(1, { type: 'status', state: 'waiting', summary: 'Choose one', detail: { actionable: true } }),
    event(2, { state: 'working', summary: 'Resumed work', detail: { activity_id: 'resumed', phase: 'start' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state: 'working',
    terminal: null
  }))
})

test.each(['failed', 'interrupted'] as const)('lets a later completed terminal event supersede %s', state => {
  const terminal = state === 'failed'
    ? event(1, { type: 'error', state, summary: 'Failed', detail: { retry: true } })
    : event(1, { type: 'status', state, summary: 'Interrupted', detail: { retry: true } })
  const [turn] = projectConversation([
    terminal,
    event(2, { type: 'assistant_message', state: 'completed', summary: 'Recovered and finished', detail: { type: 'message.complete' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state: 'completed',
    terminal: null
  }))
})

test.each(['failed', 'interrupted'] as const)('preserves %s terminal state across a late activity update', state => {
  const terminal = state === 'failed'
    ? event(2, { type: 'error', state, summary: 'Failed', detail: { retry: true } })
    : event(2, { type: 'status', state, summary: 'Interrupted', detail: { retry: true } })
  const [turn] = projectConversation([
    event(1),
    terminal,
    event(3, { state: 'completed', summary: 'Late activity update', detail: { activity_id: 'late', phase: 'complete' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    state,
    terminal: expect.objectContaining({ state })
  }))
})

test('keeps activities with matching IDs isolated between turns', () => {
  const projected = projectConversation([
    event(1, { turnId: 'turn-1', summary: 'First turn action', detail: { activity_id: 'shared', phase: 'start' } }),
    event(2, { turnId: 'turn-2', summary: 'Second turn action', detail: { activity_id: 'shared', phase: 'start' } })
  ])

  expect(projected).toHaveLength(2)
  expect(projected.map(item => item.kind === 'agent-turn' ? item.activities[0]?.label : null)).toEqual([
    'First turn action',
    'Second turn action'
  ])
})

test('preserves and deduplicates valid ownerless activity records', () => {
  const projected = projectConversation([
    event(1, { turnId: null, summary: 'Saved browser job', state: 'working', detail: { activity_id: 'mcp-save', phase: 'start' } }),
    event(2, { turnId: null, summary: 'Saved browser job', state: 'completed', detail: { activity_id: 'mcp-save', phase: 'complete' } })
  ])

  expect(projected).toEqual([
    expect.objectContaining({
      kind: 'activity',
      turnId: null,
      activityId: 'mcp-save',
      eventId: 1,
      state: 'completed'
    })
  ])
})

test('ownerless assistant events from a broken restart are not rendered as messages', () => {
  const projected = projectConversation([
    event(1, { turnId: null, type: 'assistant_message', summary: 'f', detail: { type: 'message.delta', text: 'f' } }),
    event(2, { turnId: null, type: 'assistant_message', summary: 'ca', detail: { type: 'message.delta', text: 'ca' } })
  ])

  expect(projected).toEqual([])
})

test('the hook restores, subscribes, preserves drafts while running, and serializes submission', async () => {
  let stream: ((update: any) => void) | undefined
  const send = vi.fn().mockResolvedValue({ turnId: 'turn-2', messageId: 'message-2', status: 'running' })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent: {
      get: vi.fn().mockResolvedValue(snapshot()), send,
      cancel: vi.fn(), retry: vi.fn(),
      subscribe: vi.fn((listener: (update: any) => void) => { stream = listener; return () => undefined })
    }
  } })
  const { result } = renderHook(() => useAgentConversation())
  await waitFor(() => expect(result.current.restoring).toBe(false))
  act(() => result.current.setDraft('A message in progress'))
  await act(async () => result.current.send())
  expect(send).toHaveBeenCalledWith('A message in progress', expect.stringMatching(/^desktop-/))
  expect(result.current.draft).toBe('')
  act(() => result.current.setDraft('Next thought'))
  await act(async () => result.current.send())
  expect(send).toHaveBeenCalledTimes(1)
  expect(result.current.draft).toBe('Next thought')
  act(() => stream?.({ kind: 'connection', state: 'reconnecting' }))
  expect(result.current.connection).toBe('reconnecting')
})

test('an initially offline agent can reconnect through Send while the API remains available', async () => {
  const send = vi.fn().mockResolvedValue({ turnId: 'turn-2', status: 'running' })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent: {
      get: vi.fn().mockResolvedValue({ ...snapshot(), connection: 'offline' }), send,
      cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn(() => () => undefined)
    }
  } })
  const { result } = renderHook(() => useAgentConversation())
  await waitFor(() => expect(result.current.restoring).toBe(false))
  act(() => result.current.setDraft('Reconnect and continue'))
  await act(async () => result.current.send())

  expect(send).toHaveBeenCalledOnce()
  expect(result.current.activeTurn?.turnId).toBe('turn-2')
})

test('starting a new session clears the transcript and ignores buffered events from the old session', async () => {
  let stream: ((update: AgentStreamUpdate) => void) | undefined
  const reset = vi.fn().mockResolvedValue({
    ...snapshot(), conversationId: 'conv-fresh', entries: [], latestEventId: 7
  })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent: {
      get: vi.fn().mockResolvedValue(snapshot([event(1)])), reset,
      send: vi.fn(), cancel: vi.fn(), retry: vi.fn(),
      subscribe: vi.fn(listener => { stream = listener; return () => undefined })
    }
  } })
  const { result } = renderHook(() => useAgentConversation())
  await waitFor(() => expect(result.current.restoring).toBe(false))
  act(() => result.current.setDraft('Do not carry this into fresh context'))

  await act(async () => result.current.reset())
  act(() => stream?.({ kind: 'event', event: event(6, { summary: 'Old buffered context' }) }))

  expect(reset).toHaveBeenCalledOnce()
  expect(result.current.conversationId).toBe('conv-fresh')
  expect(result.current.entries).toEqual([])
  expect(result.current.draft).toBe('')
})

test('a new-session response preserves newer streamed events that arrived while reset was pending', async () => {
  const pendingReset = deferred<AgentConversationSnapshot>()
  let stream: ((update: AgentStreamUpdate) => void) | undefined
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent: {
      get: vi.fn().mockResolvedValue(snapshot([event(1)])),
      reset: vi.fn(() => pendingReset.promise),
      send: vi.fn(), cancel: vi.fn(), retry: vi.fn(),
      subscribe: vi.fn(listener => { stream = listener; return () => undefined })
    }
  } })
  const { result } = renderHook(() => useAgentConversation())
  await waitFor(() => expect(result.current.restoring).toBe(false))

  let resetResult!: Promise<boolean>
  act(() => { resetResult = result.current.reset() })
  act(() => stream?.({
    kind: 'event',
    event: event(8, { type: 'turn', state: 'working', turnId: 'turn-fresh', summary: 'Fresh turn started' })
  }))
  await act(async () => {
    pendingReset.resolve({ ...snapshot(), conversationId: 'conv-fresh', latestEventId: 7 })
    await resetResult
  })

  expect(result.current.entries.map(item => item.eventId)).toEqual([8])
  expect(result.current.activeTurn?.turnId).toBe('turn-fresh')
})

test('an in-flight send cannot overlap a reset before the send mutation is visible', async () => {
  const pendingSend = deferred<{ turnId: string; status: 'running' }>()
  const reset = vi.fn()
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent: {
      get: vi.fn().mockResolvedValue(snapshot()), reset,
      send: vi.fn(() => pendingSend.promise), cancel: vi.fn(), retry: vi.fn(),
      subscribe: vi.fn(() => () => undefined)
    }
  } })
  const { result } = renderHook(() => useAgentConversation())
  await waitFor(() => expect(result.current.restoring).toBe(false))
  act(() => result.current.setDraft('Send without racing reset'))

  let sendResult!: Promise<void>
  act(() => { sendResult = result.current.send() })
  await act(async () => expect(await result.current.reset()).toBe(false))
  expect(reset).not.toHaveBeenCalled()
  await act(async () => {
    pendingSend.resolve({ turnId: 'turn-fresh', status: 'running' })
    await sendResult
  })

  expect(result.current.activeTurn?.turnId).toBe('turn-fresh')
})

test('stop and retry invoke only the active or actionable turn and keep failures visible', async () => {
  const cancel = vi.fn().mockResolvedValue({ turnId: 'turn-1', status: 'interrupted' })
  const retry = vi.fn().mockResolvedValue({ turnId: 'turn-2', sourceTurnId: 'turn-1', status: 'running' })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent: {
      get: vi.fn().mockResolvedValue({ ...snapshot([event(2, { type: 'error', state: 'failed', summary: 'Agent unavailable', detail: { actionable: true, retry: true } })]), activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: false } }),
      send: vi.fn(), cancel, retry, subscribe: vi.fn(() => () => undefined)
    }
  } })
  const { result } = renderHook(() => useAgentConversation())
  await waitFor(() => expect(result.current.restoring).toBe(false))
  await act(async () => result.current.stop())
  await act(async () => result.current.retry('turn-1'))
  expect(cancel).toHaveBeenCalledWith('turn-1')
  expect(retry).toHaveBeenCalledWith('turn-1', expect.stringMatching(/^desktop-retry-/))
  expect(projectConversation(result.current.entries)).toEqual(expect.arrayContaining([
    expect.objectContaining({
      kind: 'agent-turn',
      terminal: expect.objectContaining({ kind: 'error', label: 'Agent unavailable', retryable: true })
    })
  ]))
})

test.each(['running', 'waiting'] as const)(
  'stop retains the active turn when remote cleanup remains %s',
  async status => {
    const cancel = vi.fn().mockResolvedValue({ turnId: 'turn-1', status })
    Object.defineProperty(window, 'jobos', { configurable: true, value: {
      agent: {
        get: vi.fn().mockResolvedValue({
          ...snapshot(),
          activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: false }
        }),
        send: vi.fn(), cancel, retry: vi.fn(), subscribe: vi.fn(() => () => undefined)
      }
    } })
    const { result } = renderHook(() => useAgentConversation())
    await waitFor(() => expect(result.current.restoring).toBe(false))

    await act(async () => result.current.stop())

    expect(result.current.activeTurn).toEqual({
      turnId: 'turn-1', status, cancelRequested: true
    })
  }
)
