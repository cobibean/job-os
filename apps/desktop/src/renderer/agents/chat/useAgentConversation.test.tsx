import { cleanup } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'

import type { AgentConversationSnapshot, ConversationEvent } from '../../../shared/contracts'
import {
  agentConversationReducer,
  initialAgentConversationState,
  projectConversation
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
  conversationId: 'conv-current', position: 1, title: 'Session 1', createdAt: '', entries, activeTurn: null,
  connection: 'online', recoveryState: 'ready', latestEventId: entries.at(-1)?.eventId ?? 0,
  binding: { connectedAgentId: 'agent-hermes', provider: 'hermes', modelId: 'default', reasoningEffort: 'medium' },
  availability: { state: 'ready', reason: null },
  jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 }
})

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

test('assistant completion renders full transcript text instead of its bounded event summary', () => {
  const fullText = `Long response ${'x'.repeat(2_000)}`
  const [turn] = projectConversation([
    event(1, { type: 'assistant_message', summary: 'Long ', detail: { type: 'message.delta', text: 'Long ' } }),
    event(2, {
      type: 'assistant_message',
      state: 'completed',
      summary: `${fullText.slice(0, 500)}…`,
      detail: { type: 'message.complete', text: fullText }
    })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    assistant: expect.objectContaining({ text: fullText, state: 'completed' })
  }))
})

test('[protected path] cannot erase an already streamed assistant response', () => {
  const placeholder = '[protected path]'
  const visible = 'Saved the cover letter to /Users/example/.hermes/workspaces/example/cover-letter.pdf.'
  const [turn] = projectConversation([
    event(1, { type: 'assistant_message', summary: visible, detail: { type: 'message.delta', text: visible } }),
    event(2, { type: 'assistant_message', state: 'completed', summary: placeholder, detail: { type: 'message.complete', text: placeholder } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    assistant: expect.objectContaining({ text: visible, state: 'completed' })
  }))
})

test('terminal signed URL redaction replaces streamed chunks that could reconstruct a credential', () => {
  const [turn] = projectConversation([
    event(1, { type: 'assistant_message', summary: 'https://example.test/file?x-amz-sign', detail: { type: 'message.delta', text: 'https://example.test/file?x-amz-sign' } }),
    event(2, { type: 'assistant_message', summary: 'ature=SECRET', detail: { type: 'message.delta', text: 'ature=SECRET' } }),
    event(3, { type: 'assistant_message', state: 'completed', summary: '[protected signed URL]', detail: { type: 'message.complete', text: '[protected signed URL]' } })
  ])

  expect(turn).toEqual(expect.objectContaining({
    kind: 'agent-turn',
    assistant: expect.objectContaining({ text: '[protected signed URL]', state: 'completed' })
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

test('drops ownerless activity records from agent chat', () => {
  const projected = projectConversation([
    event(1, { turnId: null, summary: 'Saved browser job', state: 'working', detail: { activity_id: 'mcp-save', phase: 'start' } }),
    event(2, { turnId: null, summary: 'Saved browser job', state: 'completed', detail: { activity_id: 'mcp-save', phase: 'complete' } })
  ])

  expect(projected).toEqual([])
})
test('ownerless assistant events from a broken restart are not rendered as messages', () => {
  const projected = projectConversation([
    event(1, { turnId: null, type: 'assistant_message', summary: 'f', detail: { type: 'message.delta', text: 'f' } }),
    event(2, { turnId: null, type: 'assistant_message', summary: 'ca', detail: { type: 'message.delta', text: 'ca' } })
  ])

  expect(projected).toEqual([])
})
