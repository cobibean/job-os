import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentConversationSnapshot, AgentSessionStreamUpdate, AgentSessionSummary, ConversationEvent } from '../../shared/contracts'
import { useAgentSessions } from './useAgentSessions'

afterEach(() => { cleanup(); localStorage.clear() })

const summary = (position: number): AgentSessionSummary => ({
  conversationId: `conv_${position}`, position, title: `Session ${position}`,
  createdAt: '2026-08-16T10:00:00Z', activeTurn: null, connection: 'online', recoveryState: 'ready', latestEventId: 0
})
const snapshot = (position: number, entries: ConversationEvent[] = []): AgentConversationSnapshot => ({
  ...summary(position), entries, latestEventId: entries.at(-1)?.eventId ?? 0
})
const event = (eventId: number, state: ConversationEvent['state'] = 'working'): ConversationEvent => ({
  eventId, turnId: 'turn-1', type: state === 'failed' ? 'error' : state === 'completed' ? 'assistant_message' : 'activity',
  state, summary: `Event ${eventId}`, detail: state === 'completed' ? { type: 'message.complete', text: 'Done' } : {},
  occurredAt: '2026-08-16T10:00:00Z'
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(next => { resolve = next })
  return { promise, resolve }
}

function install(summaries: AgentSessionSummary[], snapshots = summaries.map(item => snapshot(item.position))) {
  let listener: (update: AgentSessionStreamUpdate) => void = () => undefined
  const agent = {
    list: vi.fn().mockResolvedValue(summaries),
    get: vi.fn((id: string) => Promise.resolve(snapshots.find(item => item.conversationId === id)!)),
    create: vi.fn(), archive: vi.fn().mockResolvedValue(undefined), send: vi.fn(), cancel: vi.fn(), retry: vi.fn(),
    subscribe: vi.fn((next: typeof listener) => { listener = next; return () => undefined })
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent } })
  return { agent, emit: (update: AgentSessionStreamUpdate) => listener(update) }
}

test('subscribes before hydration and reconciles early scoped events exactly once', async () => {
  const pending = deferred<AgentConversationSnapshot>()
  const { agent, emit } = install([summary(1)])
  agent.get.mockImplementation(() => pending.promise)
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(agent.get).toHaveBeenCalled())
  act(() => emit({ kind: 'event', conversationId: 'conv_1', recoveryState: 'ready', event: event(2, 'completed') }))
  await act(async () => pending.resolve(snapshot(1, [event(1), event(2, 'completed')])))
  await waitFor(() => expect(result.current.activeConversation?.restoring).toBe(false))
  expect(result.current.activeConversation?.entries.map(item => item.eventId)).toEqual([1, 2])
})

test('interleaved events and drafts update only their owning sessions', async () => {
  const { emit } = install([summary(1), summary(2)])
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(2))
  act(() => {
    result.current.setDraft('conv_1', 'first draft')
    result.current.setDraft('conv_2', 'second draft')
    emit({ kind: 'event', conversationId: 'conv_2', recoveryState: 'ready', event: event(3, 'failed') })
  })
  expect(result.current.sessions.conv_1?.draft).toBe('first draft')
  expect(result.current.sessions.conv_2?.draft).toBe('second draft')
  expect(result.current.sessions.conv_1?.conversation.entries).toEqual([])
  expect(result.current.sessions.conv_2?.conversation.entries.map(item => item.eventId)).toEqual([3])
  expect(result.current.sessions.conv_2?.unreadTerminal).toBe(true)
  act(() => result.current.select('conv_2'))
  expect(result.current.sessions.conv_2?.unreadTerminal).toBe(false)
})

test('live recovery updates quarantine close and re-enable it on return to ready', async () => {
  const { agent, emit } = install([summary(1), summary(2)])
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(2))

  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'quarantined',
    event: event(1, 'failed')
  }))
  expect(result.current.sessions.conv_1?.summary.recoveryState).toBe('quarantined')
  await act(async () => { expect(await result.current.archive('conv_1')).toBe(false) })
  expect(agent.archive).not.toHaveBeenCalled()

  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'ready', event: event(2)
  }))
  expect(result.current.sessions.conv_1?.summary.recoveryState).toBe('ready')
  await act(async () => { expect(await result.current.archive('conv_1')).toBe(true) })
  expect(agent.archive).toHaveBeenCalledWith('conv_1')
})

test('scroll and pinned state survive switching without lifecycle calls', async () => {
  const { agent } = install([summary(1), summary(2)])
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(2))
  act(() => { result.current.saveScroll('conv_1', 420, false); result.current.select('conv_2'); result.current.select('conv_1') })
  expect(result.current.sessions.conv_1).toMatchObject({ scrollTop: 420, pinnedToBottom: false })
  expect(agent.cancel).not.toHaveBeenCalled()
  expect(agent.subscribe).toHaveBeenCalledOnce()
})

test('two sends may remain pending in different sessions while each session stays locked', async () => {
  const first = deferred<{ turnId: string; status: string }>()
  const second = deferred<{ turnId: string; status: string }>()
  const { agent } = install([summary(1), summary(2)])
  agent.send.mockImplementation((id: string) => id === 'conv_1' ? first.promise : second.promise)
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(2))
  act(() => { result.current.setDraft('conv_1', 'one'); result.current.setDraft('conv_2', 'two') })
  let sendOne!: Promise<void>
  let sendTwo!: Promise<void>
  act(() => { sendOne = result.current.send('conv_1'); sendTwo = result.current.send('conv_2') })
  await waitFor(() => expect(agent.send).toHaveBeenCalledTimes(2))
  expect(result.current.sessions.conv_1?.operation).toBe('send')
  expect(result.current.sessions.conv_2?.operation).toBe('send')
  await act(async () => { first.resolve({ turnId: 'turn-1', status: 'running' }); second.resolve({ turnId: 'turn-2', status: 'running' }); await Promise.all([sendOne, sendTwo]) })
})

test('a bridge mutation failure preserves its exact safe message', async () => {
  const { agent } = install([summary(1)])
  agent.send.mockRejectedValue(new Error('Agent connection unavailable'))
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.activeConversation?.restoring).toBe(false))
  act(() => result.current.setDraft('conv_1', 'send this'))
  await act(async () => { await result.current.send('conv_1') })
  expect(result.current.activeConversation?.error).toBe('Agent connection unavailable')
})

test('a terminal SSE crossing the send response barrier cannot resurrect the completed turn', async () => {
  const response = deferred<{ turnId: string; status: string }>()
  const { agent, emit } = install([summary(1)])
  agent.send.mockReturnValue(response.promise)
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.activeConversation?.restoring).toBe(false))
  act(() => result.current.setDraft('conv_1', 'finish deterministically'))
  let sending!: Promise<void>
  act(() => { sending = result.current.send('conv_1') })
  await waitFor(() => expect(agent.send).toHaveBeenCalledOnce())

  act(() => emit({ kind: 'event', conversationId: 'conv_1', recoveryState: 'ready', event: event(1, 'completed') }))
  expect(result.current.sessions.conv_1?.conversation.activeTurn).toBeNull()
  await act(async () => { response.resolve({ turnId: 'turn-1', status: 'running' }); await sending })

  expect(result.current.sessions.conv_1?.conversation.activeTurn).toBeNull()
  expect(result.current.sessions.conv_1?.operation).toBeNull()
})

test('a terminal SSE crossing the retry response barrier cannot resurrect the failed retry turn', async () => {
  const response = deferred<{ turnId: string; sourceTurnId: string; status: string }>()
  const failed = { ...event(1, 'failed'), turnId: 'turn-source' }
  const { agent, emit } = install([summary(1)], [snapshot(1, [failed])])
  agent.retry.mockReturnValue(response.promise)
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.activeConversation?.restoring).toBe(false))
  let retrying!: Promise<void>
  act(() => { retrying = result.current.retry('conv_1', 'turn-source') })
  await waitFor(() => expect(agent.retry).toHaveBeenCalledOnce())

  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'ready',
    event: { ...event(2, 'failed'), turnId: 'turn-retry' }
  }))
  expect(result.current.sessions.conv_1?.conversation.activeTurn).toBeNull()
  await act(async () => {
    response.resolve({ turnId: 'turn-retry', sourceTurnId: 'turn-source', status: 'running' })
    await retrying
  })

  expect(result.current.sessions.conv_1?.conversation.activeTurn).toBeNull()
  expect(result.current.sessions.conv_1?.operation).toBeNull()
})

test('selection persists only a valid opaque id and otherwise falls back to position one', async () => {
  localStorage.setItem('jobos.agent.activeConversationId', 'conv_2')
  install([summary(1), summary(2)])
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.activeId).toBe('conv_2'))
  expect(Object.keys(localStorage)).toEqual(['jobos.agent.activeConversationId'])
  cleanup()
  localStorage.setItem('jobos.agent.activeConversationId', 'conv_missing')
  install([summary(1), summary(2)])
  const secondRender = renderHook(() => useAgentSessions())
  await waitFor(() => expect(secondRender.result.current.activeId).toBe('conv_1'))
})

test('the sixth session is blocked locally with the exact announcement', async () => {
  const { agent } = install(Array.from({ length: 5 }, (_, index) => summary(index + 1)))
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(5))
  await act(async () => { expect(await result.current.create()).toBe(true) })
  expect(agent.create).not.toHaveBeenCalled()
  expect(result.current.announcement).toBe('Maximum 5 sessions.')
})

test('rapid create requests serialize and stop at exactly five sessions', async () => {
  const { agent } = install([summary(1)])
  agent.create.mockImplementation(async () => snapshot(agent.create.mock.calls.length + 1))
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(1))
  await act(async () => { await Promise.all(Array.from({ length: 6 }, () => result.current.create())) })
  expect(result.current.order).toHaveLength(5)
  expect(agent.create).toHaveBeenCalledTimes(4)
  expect(result.current.announcement).toBe('Maximum 5 sessions.')
})

test('archive compacts order and ignores late events for the archived id', async () => {
  const { agent, emit } = install([summary(1), summary(2), summary(3)])
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.order).toHaveLength(3))
  act(() => result.current.select('conv_2'))
  await act(async () => { expect(await result.current.archive('conv_2')).toBe(true) })
  expect(agent.archive).toHaveBeenCalledWith('conv_2')
  expect(result.current.order).toEqual(['conv_1', 'conv_3'])
  expect(result.current.activeId).toBe('conv_3')
  act(() => emit({ kind: 'event', conversationId: 'conv_2', recoveryState: 'ready', event: event(9, 'completed') }))
  expect(result.current.sessions.conv_2).toBeUndefined()
})

test('an initial list failure retries and restores sessions without reloading or resubscribing', async () => {
  const { agent } = install([summary(1)])
  agent.list.mockRejectedValueOnce(new Error('temporarily unavailable'))
  const { result } = renderHook(() => useAgentSessions())
  await waitFor(() => expect(result.current.announcement).toBe('Conversations could not be restored. Retrying…'))
  await waitFor(() => expect(result.current.order).toEqual(['conv_1']), { timeout: 2_000 })
  expect(agent.list).toHaveBeenCalledTimes(2)
  expect(agent.subscribe).toHaveBeenCalledOnce()
})

test('buffered terminal overlap uses the live transition for unread, announcement, and deduplication', async () => {
  const pendingList = deferred<AgentSessionSummary[]>()
  const { agent, emit } = install([summary(1), summary(2)], [snapshot(1), snapshot(2, [event(7, 'completed')])])
  agent.list.mockImplementation(() => pendingList.promise)
  localStorage.setItem('jobos.agent.activeConversationId', 'conv_1')
  const { result } = renderHook(() => useAgentSessions())
  act(() => emit({ kind: 'event', conversationId: 'conv_2', recoveryState: 'ready', event: event(7, 'completed') }))
  await act(async () => pendingList.resolve([summary(1), summary(2)]))
  await waitFor(() => expect(result.current.sessions.conv_2?.conversation.restoring).toBe(false))
  expect(result.current.sessions.conv_2?.conversation.entries.map(item => item.eventId)).toEqual([7])
  expect(result.current.sessions.conv_2?.unreadTerminal).toBe(true)
  expect(result.current.announcement).toBe('Session 2 completed')
})
