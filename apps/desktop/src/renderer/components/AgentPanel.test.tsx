import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentConversationSnapshot, ConversationEvent } from '../../shared/contracts'
import { AgentPanel } from './AgentPanel'

afterEach(cleanup)

const event = (eventId: number, overrides: Partial<ConversationEvent> = {}): ConversationEvent => ({
  eventId, turnId: 'turn-1', type: 'activity', state: 'completed', summary: `Action ${eventId}`,
  detail: { activity_id: `tool-${eventId}`, operation: `Safe operation ${eventId}`, redacted: true },
  occurredAt: '2026-07-20T10:43:00Z', ...overrides
})

function installAgent(snapshot: AgentConversationSnapshot, overrides: Record<string, unknown> = {}) {
  const agent = {
    get: vi.fn().mockResolvedValue(snapshot), send: vi.fn().mockResolvedValue({ turnId: 'turn-new', status: 'running' }),
    cancel: vi.fn().mockResolvedValue({ turnId: 'turn-1', status: 'interrupted' }),
    retry: vi.fn().mockResolvedValue({ turnId: 'turn-retry', status: 'running' }),
    subscribe: vi.fn(() => () => undefined), ...overrides
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent } })
  return agent
}

test('renders transcript and exactly fifteen chronological compact activity rows with accessible disclosure', async () => {
  installAgent({
    conversationId: 'conv-current', activeTurn: null, connection: 'online', latestEventId: 17,
    entries: [
      event(1, { type: 'user_message', summary: 'Please update it', text: 'Please update it', detail: {} }),
      ...Array.from({ length: 15 }, (_, index) => event(index + 2)),
      event(17, { type: 'assistant_message', summary: 'Finished.', detail: { type: 'message.complete' } })
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar · Staff PM" />)

  expect(await screen.findByText('Please update it')).not.toBeNull()
  expect(screen.getByText('Finished.')).not.toBeNull()
  const rows = screen.getAllByTestId('agent-activity-row')
  expect(rows).toHaveLength(15)
  expect(rows.map(row => row.textContent)).toEqual(Array.from({ length: 15 }, (_, index) => expect.stringContaining(`Action ${index + 2}`)))
  const disclosure = screen.getByRole('button', { name: 'Show details for Action 2' })
  expect(disclosure.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(disclosure)
  expect(disclosure.getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByText('Safe operation 2')).not.toBeNull()
  expect(screen.getByText('Sensitive detail was redacted.')).not.toBeNull()
})

test('composer supports Enter submission, Shift+Enter drafting, and preserves a draft across job changes', async () => {
  const agent = installAgent({ conversationId: 'conv-current', entries: [], activeTurn: null, connection: 'online', latestEventId: 0 })
  const { rerender } = render(<AgentPanel apiState="connected" contextLabel="Northstar · Staff PM" />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Draft for this conversation' } })
  fireEvent.keyDown(composer, { key: 'Enter', shiftKey: true })
  expect(agent.send).not.toHaveBeenCalled()
  rerender(<AgentPanel apiState="connected" contextLabel="Daybreak · Platform PM" />)
  expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Draft for this conversation')
  fireEvent.keyDown(composer, { key: 'Enter' })
  await waitFor(() => expect(agent.send).toHaveBeenCalledWith('Draft for this conversation', expect.stringMatching(/^desktop-/)))
})

test('running and waiting turns keep drafting enabled while submission is serialized and Stop remains available', async () => {
  const agent = installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 1,
    activeTurn: { turnId: 'turn-1', status: 'waiting', cancelRequested: false },
    entries: [event(1, { type: 'status', state: 'waiting', summary: 'Choose an approach', detail: { actionable: true } })]
  })
  render(<AgentPanel apiState="connected" contextLabel="No active job" />)

  expect(await screen.findByText('Waiting for you')).not.toBeNull()
  expect(screen.getByText('Choose an approach')).not.toBeNull()
  const composer = screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  expect(composer.disabled).toBe(false)
  fireEvent.change(composer, { target: { value: 'A queued idea' } })
  expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Stop agent turn' }))
  expect(agent.cancel).toHaveBeenCalledWith('turn-1')
  expect(composer.value).toBe('A queued idea')
})

test('failed and interrupted turns expose Retry and offline states remain distinct', async () => {
  const failed = event(1, { type: 'error', state: 'failed', summary: 'Agent unavailable', detail: { actionable: true, retry: true } })
  const agent = installAgent({ conversationId: 'conv-current', connection: 'offline', activeTurn: null, latestEventId: 1, entries: [failed] })
  const { rerender } = render(<AgentPanel apiState="connected" contextLabel="Northstar" />)
  expect(await screen.findByText('Agent offline')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry turn' }))
  expect(agent.retry).toHaveBeenCalledWith('turn-1', expect.stringMatching(/^desktop-retry-/))

  rerender(<AgentPanel apiState="disconnected" contextLabel="Northstar" />)
  expect(screen.getByText('JobOS API offline')).not.toBeNull()
})

test('API-online users can use Send to reconnect an initially offline agent', async () => {
  const agent = installAgent({ conversationId: 'conv-current', connection: 'offline', activeTurn: null, latestEventId: 0, entries: [] })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Reconnect safely' } })
  const send = screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement
  expect(send.disabled).toBe(false)
  expect(screen.getByText('Send to reconnect the agent')).not.toBeNull()
  fireEvent.click(send)
  await waitFor(() => expect(agent.send).toHaveBeenCalledOnce())
})

test('focuses the document surface when a live resume render completes', async () => {
  let stream!: (update: unknown) => void
  installAgent(
    { conversationId: 'conv-current', connection: 'online', activeTurn: null, latestEventId: 0, entries: [] },
    { subscribe: vi.fn((listener: (update: unknown) => void) => { stream = listener; return () => undefined }) }
  )
  const onArtifactRendered = vi.fn()
  render(<AgentPanel apiState="connected" contextLabel="Northstar" onArtifactRendered={onArtifactRendered} />)
  await screen.findByRole('textbox', { name: 'Message the agent' })

  act(() => stream({
    kind: 'event',
    event: event(1, { detail: { command: 'document.render', outcome: 'completed' } })
  }))

  await waitFor(() => expect(onArtifactRendered).toHaveBeenCalledOnce())
})

test('settles a working assistant placeholder before a later interrupted terminal card', async () => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', activeTurn: null, latestEventId: 3,
    entries: [
      event(1, { type: 'assistant_message', state: 'working', summary: 'I found the role and started comparing it.', detail: { type: 'message.delta' } }),
      event(2, { summary: 'Compared the role requirements', detail: { activity_id: 'compare-role', operation: 'Compared public role details', redacted: false } }),
      event(3, { type: 'status', state: 'interrupted', summary: 'Stopped before the comparison finished.', detail: { retry: true } })
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const draft = await screen.findByText('I found the role and started comparing it.')
  const activity = screen.getByText('Compared the role requirements')
  const terminal = screen.getByText('Turn interrupted')
  expect(screen.queryByText('Streaming')).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry turn' })).not.toBeNull()
  expect(draft.compareDocumentPosition(activity) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(activity.compareDocumentPosition(terminal) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('settles a working assistant placeholder before a later failed terminal card', async () => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', activeTurn: null, latestEventId: 2,
    entries: [
      event(1, { type: 'assistant_message', state: 'working', summary: 'The useful draft remains visible.', detail: { type: 'message.delta' } }),
      event(2, { type: 'error', state: 'failed', summary: 'The agent could not finish this turn.', detail: { actionable: true, retry: true } })
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  expect(await screen.findByText('The useful draft remains visible.')).not.toBeNull()
  expect(screen.getByText('Turn failed')).not.toBeNull()
  expect(screen.queryByText('Streaming')).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry turn' })).not.toBeNull()
})

test('keeps Streaming on the working assistant placeholder for the current active turn', async () => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 1,
    activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: false },
    entries: [event(1, { type: 'assistant_message', state: 'working', summary: 'Still drafting.', detail: { type: 'message.delta' } })]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  expect(await screen.findByText('Still drafting.')).not.toBeNull()
  expect(screen.getByText('Streaming')).not.toBeNull()
})

test('restoring and reconnecting states are calm and announced without exposing token-level detail', async () => {
  let resolve!: (snapshot: AgentConversationSnapshot) => void
  installAgent({} as AgentConversationSnapshot, {
    get: vi.fn().mockReturnValue(new Promise<AgentConversationSnapshot>(value => { resolve = value })),
    subscribe: vi.fn((listener: (update: unknown) => void) => {
      listener({ kind: 'connection', state: 'reconnecting' })
      return () => undefined
    })
  })
  render(<AgentPanel apiState="connected" contextLabel="No active job" />)
  expect(screen.getByText('Restoring conversation…')).not.toBeNull()
  expect(screen.getByText('Reconnecting to agent…')).not.toBeNull()
  resolve({ conversationId: 'conv-current', entries: [], activeTurn: null, connection: 'online', latestEventId: 0 })
  expect(JSON.stringify(document.body.textContent)).not.toMatch(/authorization|bearer|token=/i)
})
