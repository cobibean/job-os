import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentConversationSnapshot, AgentSessionStreamUpdate, AgentSessionSummary, ConversationEvent } from '../../../shared/contracts'
import { useAgentSessions } from './useAgentSessions'
import { AgentPanel } from './AgentPanel'

afterEach(() => { cleanup(); window.localStorage.clear(); vi.restoreAllMocks() })

const summary = (position: number, activeTurn: AgentSessionSummary['activeTurn'] = null): AgentSessionSummary => ({
  conversationId: `conv_${position}`, position, title: `Session ${position}`, createdAt: '2026-08-16T10:00:00Z',
  activeTurn, connection: 'online', recoveryState: 'ready', latestEventId: 0,
  binding: { connectedAgentId: 'agent-hermes', provider: 'hermes', modelId: 'default', reasoningEffort: 'medium' },
  availability: { state: 'ready', reason: null },
  jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 }
})
const snapshot = (position: number, entries: ConversationEvent[] = [], activeTurn: AgentSessionSummary['activeTurn'] = null): AgentConversationSnapshot => ({
  ...summary(position, activeTurn), entries, latestEventId: entries.at(-1)?.eventId ?? 0
})
const event = (eventId: number, overrides: Partial<ConversationEvent> = {}): ConversationEvent => ({
  eventId, turnId: 'turn-1', type: 'activity', state: 'working', summary: `Action ${eventId}`,
  detail: { activity_id: `tool-${eventId}`, operation: `Safe operation ${eventId}`, redacted: true },
  occurredAt: '2026-08-16T10:00:00Z', ...overrides
})

function install(summaries = [summary(1)], snapshots = summaries.map(item => snapshot(item.position, [], item.activeTurn))) {
  let listener: (update: AgentSessionStreamUpdate) => void = () => undefined
  const agent = {
    list: vi.fn().mockResolvedValue(summaries),
    get: vi.fn((id: string) => Promise.resolve(snapshots.find(item => item.conversationId === id)!)),
    create: vi.fn().mockImplementation(() => Promise.resolve(snapshot(summaries.length + 1))),
    archive: vi.fn().mockResolvedValue(undefined),
    send: vi.fn().mockResolvedValue({ turnId: 'turn-new', status: 'running' }),
    cancel: vi.fn().mockResolvedValue({ turnId: 'turn-1', status: 'interrupted' }),
    retry: vi.fn().mockResolvedValue({ turnId: 'turn-retry', status: 'running' }),
    review: vi.fn().mockResolvedValue({ turnId: 'turn-1', status: 'running' }),
    subscribe: vi.fn((next: typeof listener) => { listener = next; return () => undefined })
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent } })
  return { agent, emit: (update: AgentSessionStreamUpdate) => listener(update) }
}

function Harness({ onNewChat }: { onNewChat?: () => void } = {}) {
  const sessions = useAgentSessions()
  return <AgentPanel avatarId="ninja" apiState="connected" contextLabel="Northstar · Staff PM" onNewChat={onNewChat} sessions={sessions} />
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(next => { resolve = next })
  return { promise, resolve }
}

function installClock(isoTime: string) {
  let now = Date.parse(isoTime)
  let tick: () => void = () => undefined
  vi.spyOn(Date, 'now').mockImplementation(() => now)
  vi.spyOn(window, 'setInterval').mockImplementation(callback => {
    tick = callback as () => void
    return 1 as unknown as ReturnType<typeof window.setInterval>
  })
  vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined)
  return {
    advance(milliseconds: number) {
      now += milliseconds
      act(() => tick())
    }
  }
}

test('renders accessible tabs and routes additive creation through the New Chat picker', async () => {
  const { agent } = install()
  const onNewChat = vi.fn()
  render(<Harness onNewChat={onNewChat} />)
  expect(await screen.findByRole('tab', { name: 'Session 1, Idle' })).not.toBeNull()
  expect(screen.getByRole('tablist', { name: 'Agent sessions' })).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'New agent session' }))
  expect(onNewChat).toHaveBeenCalledOnce()
  expect(agent.create).not.toHaveBeenCalled()
  expect(screen.queryByRole('alertdialog')).toBeNull()
})

test('keeps session binding metadata in the header above the flexible chat body', async () => {
  install()
  render(<Harness />)
  await screen.findByRole('tab', { name: 'Session 1, Idle' })

  const panel = screen.getByRole('complementary', { name: 'Agent chat' })
  const header = panel.querySelector('.agent-session-header')
  const body = screen.getByRole('tabpanel')

  expect(header).not.toBeNull()
  expect(header?.querySelector('.agent-session-toolbar')).not.toBeNull()
  expect(header?.querySelector('.agent-binding')?.textContent).toContain('Hermesdefault · mediumLocked for this chat')
  expect(header?.nextElementSibling).toBe(body)
})

test('every session tab controls a persistent panel while only the selected panel is visible', async () => {
  install([summary(1), summary(2)])
  render(<Harness />)
  const tabs = await screen.findAllByRole('tab')
  const panels = screen.getAllByRole('tabpanel', { hidden: true })
  expect(panels).toHaveLength(2)
  for (const tab of tabs) {
    const panelId = tab.getAttribute('aria-controls')
    const panel = panelId ? document.getElementById(panelId) : null
    expect(panel?.getAttribute('role')).toBe('tabpanel')
    expect(panel?.getAttribute('aria-labelledby')).toBe(tab.id)
  }
  const firstPanel = document.getElementById('agent-session-panel-conv_1')!
  const secondPanel = document.getElementById('agent-session-panel-conv_2')!
  expect(firstPanel.hidden).toBe(false)
  expect(secondPanel.hidden).toBe(true)

  fireEvent.click(await screen.findByRole('tab', { name: 'Session 2, Idle' }))
  expect(document.getElementById('agent-session-panel-conv_1')).toBe(firstPanel)
  expect(document.getElementById('agent-session-panel-conv_2')).toBe(secondPanel)
  expect(firstPanel.hidden).toBe(true)
  expect(secondPanel.hidden).toBe(false)
})

test('two session panel DOM nodes restore their distinct real scroll positions across switching', async () => {
  install([summary(1), summary(2)])
  render(<Harness />)
  await screen.findByRole('tab', { name: 'Session 2, Idle' })
  const firstPanel = document.getElementById('agent-session-panel-conv_1')!
  const secondPanel = document.getElementById('agent-session-panel-conv_2')!
  let firstScrollTop = 240
  let secondScrollTop = 0
  Object.defineProperties(firstPanel, {
    clientHeight: { configurable: true, get: () => 500 },
    scrollHeight: { configurable: true, get: () => 2_000 },
    scrollTop: { configurable: true, get: () => firstScrollTop, set: value => { firstScrollTop = Number(value) } }
  })
  Object.defineProperties(secondPanel, {
    clientHeight: { configurable: true, get: () => 500 },
    scrollHeight: { configurable: true, get: () => 2_500 },
    scrollTop: { configurable: true, get: () => secondScrollTop, set: value => { secondScrollTop = Number(value) } }
  })

  fireEvent.scroll(firstPanel)
  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Idle' }))
  secondScrollTop = 900
  fireEvent.scroll(secondPanel)
  fireEvent.click(screen.getByRole('tab', { name: 'Session 1, Idle' }))
  expect(firstScrollTop).toBe(240)
  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Idle' }))
  expect(secondScrollTop).toBe(900)
})

test('a small upward scroll stays detached across session switching and streamed updates', async () => {
  const initialAnswer = event(1, {
    type: 'assistant_message', state: 'completed', summary: 'Initial answer',
    detail: { type: 'message.complete', text: 'Initial answer' }
  })
  const { emit } = install([summary(1), summary(2)], [snapshot(1, [initialAnswer]), snapshot(2)])
  render(<Harness />)
  const panel = await screen.findByRole('tabpanel')
  let scrollHeight = 1_000
  let scrollTop = 0
  Object.defineProperties(panel, {
    clientHeight: { configurable: true, get: () => 300 },
    scrollHeight: { configurable: true, get: () => scrollHeight },
    scrollTop: {
      configurable: true,
      get: () => scrollTop,
      set: value => { scrollTop = Math.min(Number(value), scrollHeight - 300) }
    }
  })

  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'ready',
    event: event(2, { type: 'assistant_message', state: 'completed', summary: 'Followed answer', detail: { type: 'message.complete', text: 'Followed answer' } })
  }))
  expect(scrollTop).toBe(700)

  scrollTop = 650
  fireEvent.scroll(panel)
  expect(screen.getByRole('button', { name: 'Jump to latest' })).not.toBeNull()

  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Idle' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Session 1, Idle' }))
  fireEvent.scroll(panel)
  expect(screen.getByRole('button', { name: 'Jump to latest' })).not.toBeNull()

  scrollHeight = 1_200
  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'ready',
    event: event(3, { type: 'assistant_message', state: 'completed', summary: 'Newest answer', detail: { type: 'message.complete', text: 'Newest answer' } })
  }))
  expect(scrollTop).toBe(650)
})

test('a new agent turn timer starts at zero and counts upward', async () => {
  const clock = installClock('2026-08-16T10:00:00Z')
  install()
  await act(async () => { render(<Harness />); await Promise.resolve() })
  const composer = screen.getByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Start the work' } })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Send message' })); await Promise.resolve() })

  expect(screen.getByLabelText('Elapsed agent time').textContent).toBe('0:00')
  clock.advance(1_000)
  expect(screen.getByLabelText('Elapsed agent time').textContent).toBe('0:01')
})

test('each active session shows its own elapsed timer until that turn completes', async () => {
  const clock = installClock('2026-08-16T10:01:05Z')
  const firstTurn = { turnId: 'turn-1', status: 'running' as const, cancelRequested: false }
  const secondTurn = { turnId: 'turn-2', status: 'running' as const, cancelRequested: false }
  // SQLite CURRENT_TIMESTAMP is UTC without a timezone suffix. The renderer
  // must not accidentally interpret these persisted values as local time.
  const firstStart = event(1, { type: 'turn', state: 'working', occurredAt: '2026-08-16 10:00:00' })
  const secondStart = event(2, { turnId: 'turn-2', type: 'turn', state: 'working', occurredAt: '2026-08-16 10:01:00' })
  const { emit } = install(
    [summary(1, firstTurn), summary(2, secondTurn)],
    [snapshot(1, [firstStart], firstTurn), snapshot(2, [secondStart], secondTurn)]
  )

  await act(async () => { render(<Harness />); await Promise.resolve() })
  expect(screen.getByLabelText('Elapsed agent time').textContent).toBe('1:05')

  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Working' }))
  expect(screen.getByLabelText('Elapsed agent time').textContent).toBe('0:05')
  clock.advance(2_000)
  expect(screen.getByLabelText('Elapsed agent time').textContent).toBe('0:07')

  act(() => emit({
    kind: 'event', conversationId: 'conv_2', recoveryState: 'ready',
    event: event(3, {
      turnId: 'turn-2', type: 'assistant_message', state: 'completed', occurredAt: '2026-08-16T10:01:07Z',
      summary: 'Done', detail: { type: 'message.complete', text: 'Done' }
    })
  }))
  expect(screen.queryByLabelText('Elapsed agent time')).toBeNull()
})

test('close stays disabled across the pending send response gap', async () => {
  const response = deferred<{ turnId: string; status: string }>()
  const { agent } = install([summary(1), summary(2)])
  agent.send.mockReturnValue(response.promise)
  render(<Harness />)
  await waitFor(() => expect(agent.get).toHaveBeenCalledTimes(2))
  const composer = screen.getByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Pending message' } })
  await waitFor(() => expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(agent.send).toHaveBeenCalledOnce())
  expect((screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement).disabled).toBe(true)
  await act(async () => response.resolve({ turnId: 'turn-new', status: 'running' }))
})

test('close stays disabled across the pending retry response gap', async () => {
  const response = deferred<{ turnId: string; status: string }>()
  const failed = event(1, { type: 'error', state: 'failed', summary: 'Try again', detail: { retry: true } })
  const { agent } = install([summary(1), summary(2)], [snapshot(1, [failed]), snapshot(2)])
  agent.retry.mockReturnValue(response.promise)
  render(<Harness />)
  fireEvent.click(await screen.findByRole('button', { name: 'Retry turn' }))
  await waitFor(() => expect(agent.retry).toHaveBeenCalledOnce())
  expect((screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement).disabled).toBe(true)
  await act(async () => response.resolve({ turnId: 'turn-retry', status: 'running' }))
})

test('locked chat keeps retryable history readable without exposing retry mutation', async () => {
  const failed = event(1, { type: 'error', state: 'failed', summary: 'Try again', detail: { retry: true } })
  const locked = {
    ...snapshot(1, [failed]),
    availability: { state: 'locked' as const, reason: 'AGENT_DISCONNECTED' }
  }
  install([{ ...summary(1), availability: locked.availability }], [locked])
  render(<Harness />)
  expect(await screen.findByText('Try again')).not.toBeNull()
  expect(screen.queryByRole('button', { name: 'Retry turn' })).toBeNull()
})

test('runtime transport quarantine updates the tab and close gate until ready streams', async () => {
  const { emit } = install([summary(1), summary(2)])
  render(<Harness />)
  await screen.findByRole('tab', { name: 'Session 2, Idle' })

  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'quarantined',
    event: event(1, {
      type: 'error', state: 'failed', summary: 'Transport lost',
      detail: { reason: 'transport_lost', retry: true }
    })
  }))
  expect(screen.getByRole('tab', { name: 'Session 1, Quarantined' })).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.queryByRole('button', { name: 'Retry turn' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Confirm cleanup and retry turn' })).not.toBeNull()

  act(() => emit({
    kind: 'event', conversationId: 'conv_1', recoveryState: 'ready',
    event: event(2, { type: 'status', state: 'interrupted', summary: 'Recovery confirmed' })
  }))
  expect(screen.getByRole('tab', { name: 'Session 1, Idle' })).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Close Session 1' }) as HTMLButtonElement).disabled).toBe(false)
  expect(screen.queryByRole('button', { name: 'Confirm cleanup and retry turn' })).toBeNull()
})

test('quarantined stock continuation offers cleanup without resurrecting a retry turn', async () => {
  const continuationTurnId = 'turn_cont_0123456789abcdef0123456789abcdef'
  const entries = [
    event(1, {
      turnId: continuationTurnId,
      type: 'turn',
      state: 'working',
      summary: 'Agent continuing completed background work',
      detail: { context: { agent_continuation: true } }
    }),
    event(2, {
      turnId: continuationTurnId,
      type: 'error',
      state: 'failed',
      summary: 'Background work cleanup must be confirmed before new work',
      detail: {
        actionable: true,
        agent_continuation: true,
        continuation_id: 'deleg_stock_cleanup_1234',
        reason: 'transport_lost',
        retry: false
      }
    })
  ]
  const quarantinedSummary = { ...summary(1), recoveryState: 'quarantined' as const }
  const { agent } = install(
    [quarantinedSummary],
    [{ ...snapshot(1, entries), recoveryState: 'quarantined' }]
  )
  agent.retry.mockResolvedValue({ turnId: continuationTurnId, status: 'failed' })

  render(<Harness />)
  const cleanupButton = await screen.findByRole('button', { name: 'Confirm agent cleanup' })
  expect(screen.queryByRole('button', { name: 'Retry turn' })).toBeNull()
  fireEvent.click(cleanupButton)
  await waitFor(() => expect(agent.retry).toHaveBeenCalledOnce())
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Confirm agent cleanup' })).toBeNull())
  expect(screen.queryByText('Working')).toBeNull()
})

test('drafts survive switching and two sessions may send independently', async () => {
  const { agent } = install([summary(1), summary(2)])
  render(<Harness />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Draft one' } })
  await waitFor(() => expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Draft one'))
  fireEvent.click(await screen.findByRole('tab', { name: 'Session 2, Idle' }))
  await waitFor(() => expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe(''))
  fireEvent.change(composer, { target: { value: 'Draft two' } })
  await waitFor(() => expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Draft two'))
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  fireEvent.click(screen.getByRole('tab', { name: 'Session 1, Idle' }))
  await waitFor(() => expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Draft one'))
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(agent.send).toHaveBeenCalledTimes(2))
  expect(agent.send.mock.calls.map(call => call.slice(0, 2))).toEqual(expect.arrayContaining([
    ['conv_1', 'Draft one'], ['conv_2', 'Draft two']
  ]))
})

test('interleaved background completion waiting and failure states identify their owner and clear on read', async () => {
  const { emit } = install([summary(1), summary(2)])
  render(<Harness />)
  await screen.findByRole('tab', { name: 'Session 2, Idle' })
  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Idle' }))
  act(() => emit({ kind: 'event', conversationId: 'conv_1', recoveryState: 'ready', event: event(1, {
    type: 'assistant_message', state: 'completed', summary: 'Done', detail: { type: 'message.complete', text: 'Done' }
  }) }))
  expect(screen.getByRole('tab', { name: 'Session 1, Done' })).not.toBeNull()
  expect(screen.getByText('Session 1 completed')).not.toBeNull()
  fireEvent.click(screen.getByRole('tab', { name: 'Session 1, Done' }))
  expect(screen.getByRole('tab', { name: 'Session 1, Idle' })).not.toBeNull()
  act(() => emit({ kind: 'event', conversationId: 'conv_2', recoveryState: 'ready', event: event(2, {
    type: 'status', state: 'waiting', summary: 'Choose', detail: { actionable: true }
  }) }))
  expect(screen.getByRole('tab', { name: 'Session 2, Needs you' })).not.toBeNull()
  act(() => emit({ kind: 'event', conversationId: 'conv_2', recoveryState: 'ready', event: event(3, {
    type: 'error', state: 'failed', summary: 'Failed', detail: { retry: true }
  }) }))
  expect(screen.getByRole('tab', { name: 'Session 2, Failed' })).not.toBeNull()
})

test('tool review requires an explicit turn-scoped approve or decline', async () => {
  const activeTurn = { turnId: 'turn-1', status: 'running' as const, cancelRequested: false }
  const waiting = event(1, {
    type: 'status',
    state: 'waiting',
    summary: 'Allow the JobOS tool to inspect this job?',
    detail: {
      actionable: true,
      approval_id: 'approval_abcdefghijklmnop',
      tool_name: 'job_inspect'
    }
  })
  const { agent } = install([summary(1, activeTurn)], [snapshot(1, [waiting], activeTurn)])
  render(<Harness />)

  fireEvent.click(await screen.findByRole('button', { name: 'Approve tool' }))
  await waitFor(() => expect(agent.review).toHaveBeenCalledWith(
    'conv_1',
    'turn-1',
    'approval_abcdefghijklmnop',
    true
  ))

  await waitFor(() => expect(screen.queryByRole('button', { name: 'Approve tool' })).toBeNull())
  expect(screen.queryByRole('button', { name: 'Decline tool' })).toBeNull()
  expect(agent.review).toHaveBeenCalledTimes(1)
})

test('close compacts visible positions while last and running sessions cannot close', async () => {
  const running = { turnId: 'turn-2', status: 'running' as const, cancelRequested: false }
  const { agent } = install([summary(1), summary(2, running), summary(3)])
  render(<Harness />)
  await screen.findByRole('tab', { name: 'Session 3, Idle' })
  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Working' }))
  expect((screen.getByRole('button', { name: 'Close Session 2' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('tab', { name: 'Session 1, Idle' }))
  fireEvent.click(screen.getByRole('button', { name: 'Close Session 1' }))
  await waitFor(() => expect(agent.archive).toHaveBeenCalledWith('conv_1'))
  expect(screen.getByRole('tab', { name: 'Session 1, Working' })).not.toBeNull()
  expect(screen.getByRole('tab', { name: 'Session 2, Idle' })).not.toBeNull()
})

test('preserves safe activity grouping and composer Enter behavior', async () => {
  const entries = [
    event(1, { state: 'completed' }),
    event(2, { type: 'assistant_message', state: 'completed', summary: 'Finished.', detail: { type: 'message.complete', text: '**Finished.**' } })
  ]
  const { agent } = install([summary(1)], [snapshot(1, entries)])
  render(<Harness />)
  expect(await screen.findByRole('button', { name: 'Show agent activity: 1 action completed' })).not.toBeNull()
  expect(screen.getByText('Finished.').tagName).toBe('STRONG')
  const composer = screen.getByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Send me' } })
  fireEvent.keyDown(composer, { key: 'Enter' })
  await waitFor(() => expect(agent.send).toHaveBeenCalledWith('conv_1', 'Send me', expect.any(String)))
})
