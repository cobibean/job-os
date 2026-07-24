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

function installAgent(
  snapshot: AgentConversationSnapshot,
  overrides: Record<string, unknown> = {},
  bridgeOverrides: Record<string, unknown> = {}
) {
  const agent = {
    get: vi.fn().mockResolvedValue(snapshot), send: vi.fn().mockResolvedValue({ turnId: 'turn-new', status: 'running' }),
    reset: vi.fn().mockResolvedValue({ ...snapshot, conversationId: 'conv-fresh', entries: [] }),
    cancel: vi.fn().mockResolvedValue({ turnId: 'turn-1', status: 'interrupted' }),
    retry: vi.fn().mockResolvedValue({ turnId: 'turn-retry', status: 'running' }),
    subscribe: vi.fn(() => () => undefined), ...overrides
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent, ...bridgeOverrides } })
  return agent
}

test('starts a fresh agent session only after an explicit inline confirmation', async () => {
  const agent = installAgent({
    conversationId: 'conv-current', activeTurn: null, connection: 'online', latestEventId: 1,
    entries: [event(1, { type: 'assistant_message', summary: 'Old context', detail: { type: 'message.complete' } })]
  })
  render(<div className="app-shell"><AgentPanel apiState="connected" contextLabel="Northstar" /></div>)

  expect(await screen.findByText('Old context')).not.toBeNull()
  const transcript = document.querySelector<HTMLElement>('.agent-body')!
  let scrollTop = 0
  Object.defineProperties(transcript, {
    clientHeight: { configurable: true, get: () => 300 },
    scrollHeight: { configurable: true, get: () => 1_000 },
    scrollTop: { configurable: true, get: () => scrollTop, set: value => { scrollTop = Number(value) } }
  })
  fireEvent.scroll(transcript)
  expect(screen.getByRole('button', { name: 'Jump to latest' })).not.toBeNull()
  fireEvent.change(screen.getByRole('textbox', { name: 'Message the agent' }), { target: { value: 'Draft for next session' } })
  expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(false)
  const startButton = screen.getByRole('button', { name: 'Start new agent session' })
  fireEvent.click(startButton)
  expect(agent.reset).not.toHaveBeenCalled()
  expect(screen.getByText('Start with fresh context?')).not.toBeNull()
  const dialog = screen.getByRole('alertdialog')
  const confirmButton = screen.getByRole('button', { name: 'Confirm new session' })
  const cancelButton = screen.getByRole('button', { name: 'Cancel' })
  expect(dialog.closest('.agent-body')).toBeNull()
  expect(dialog.getAttribute('aria-modal')).toBe('true')
  expect(dialog.closest('.new-session-overlay')).not.toBeNull()
  expect(document.querySelector('.app-shell')?.hasAttribute('inert')).toBe(true)
  expect(document.activeElement).toBe(confirmButton)
  fireEvent.keyDown(confirmButton, { key: 'Tab' })
  expect(document.activeElement).toBe(cancelButton)
  fireEvent.keyDown(cancelButton, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(confirmButton)
  fireEvent.keyDown(dialog, { key: 'Escape' })
  expect(screen.queryByRole('alertdialog')).toBeNull()
  expect(document.activeElement).toBe(startButton)
  fireEvent.click(startButton)
  expect((screen.getByRole('button', { name: 'Send message' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm new session' }))

  await waitFor(() => expect(agent.reset).toHaveBeenCalledOnce())
  expect(screen.queryByText('Old context')).toBeNull()
  expect(screen.getByText('Fresh conversation')).not.toBeNull()
  expect(screen.queryByRole('button', { name: 'Jump to latest' })).toBeNull()
  expect(scrollTop).toBe(1_000)
})

test('keeps keyboard focus inside the modal while a new session is starting', async () => {
  let resolveReset!: (snapshot: AgentConversationSnapshot) => void
  const reset = vi.fn(() => new Promise<AgentConversationSnapshot>(resolve => { resolveReset = resolve }))
  installAgent(
    { conversationId: 'conv-current', activeTurn: null, connection: 'online', latestEventId: 0, entries: [] },
    { reset }
  )
  render(<div className="app-shell"><AgentPanel apiState="connected" contextLabel="Northstar" /></div>)

  await screen.findByRole('textbox', { name: 'Message the agent' })
  fireEvent.click(screen.getByRole('button', { name: 'Start new agent session' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm new session' }))
  await waitFor(() => expect(reset).toHaveBeenCalledOnce())

  const cancelButton = screen.getByRole('button', { name: 'Cancel' })
  const confirmButton = screen.getByRole('button', { name: 'Confirm new session' }) as HTMLButtonElement
  expect(confirmButton.disabled).toBe(true)
  await waitFor(() => expect(document.activeElement).toBe(cancelButton))
  fireEvent.keyDown(cancelButton, { key: 'Tab' })
  expect(document.activeElement).toBe(cancelButton)
  fireEvent.keyDown(cancelButton, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(cancelButton)

  resolveReset({ conversationId: 'conv-fresh', activeTurn: null, connection: 'online', latestEventId: 0, entries: [] })
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
})

test('groups a completed turn, starts collapsed, and keeps the final answer last', async () => {
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
  const disclosure = screen.getByRole('button', { name: 'Show agent activity: 15 actions completed' })
  expect(disclosure.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryAllByTestId('agent-activity-row')).toHaveLength(0)

  fireEvent.click(disclosure)
  const rows = screen.getAllByTestId('agent-activity-row')
  expect(rows).toHaveLength(15)
  expect(rows.map(row => row.textContent)).toEqual(Array.from({ length: 15 }, (_, index) => expect.stringContaining(`Action ${index + 2}`)))
  const rowDisclosure = screen.getByRole('button', { name: 'Show details for Action 2' })
  fireEvent.click(rowDisclosure)
  expect(screen.getByText('Safe operation 2')).not.toBeNull()
  expect(screen.getByText('Sensitive detail was redacted.')).not.toBeNull()

  const turn = screen.getByTestId('agent-turn')
  const group = disclosure.closest('.agent-activity-group')!
  const answer = screen.getByText('Finished.').closest('.assistant-message')!
  expect(turn.lastElementChild).toBe(answer)
  expect(group.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('does not label unfinished activities as completed when the turn finishes', async () => {
  installAgent({
    conversationId: 'conv-current', activeTurn: null, connection: 'online', latestEventId: 3,
    entries: [
      event(1),
      event(2, { state: 'working' }),
      event(3, { type: 'assistant_message', state: 'completed', summary: 'Finished with one action unresolved', detail: { type: 'message.complete' } })
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const disclosure = await screen.findByRole('button', { name: 'Show agent activity: 2 actions · 1 completed' })
  const group = disclosure.closest('.agent-activity-group')!
  expect(group.classList.contains('paused')).toBe(true)
  expect(group.querySelector('.activity-group-state .lucide-pause')).not.toBeNull()
  expect(group.querySelector('.activity-group-state .lucide-check')).toBeNull()
})

test('keeps an active turn expanded with a persistent working status and respects manual collapse', async () => {
  let stream!: (update: unknown) => void
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 4,
    activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: false },
    entries: [
      event(1),
      event(2),
      event(3),
      event(4, { state: 'working' })
    ]
  }, {
    subscribe: vi.fn((listener: (update: unknown) => void) => { stream = listener; return () => undefined })
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const disclosure = await screen.findByRole('button', { name: 'Hide agent activity: 4 actions · 3 completed' })
  expect(disclosure.getAttribute('aria-expanded')).toBe('true')
  expect(screen.getAllByTestId('agent-activity-row')).toHaveLength(4)
  const workingStatus = screen.getByText('Agent working · 4 actions').closest('.agent-turn-status')!
  expect(workingStatus.getAttribute('role')).toBeNull()
  expect(workingStatus.nextElementSibling?.classList.contains('composer')).toBe(true)
  expect(workingStatus.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  expect(screen.getByRole('button', { name: 'Stop agent turn' })).not.toBeNull()

  fireEvent.click(disclosure)
  expect(screen.queryAllByTestId('agent-activity-row')).toHaveLength(0)
  act(() => stream({
    kind: 'event',
    event: event(5, { state: 'completed', detail: { activity_id: 'tool-4', operation: 'Updated operation', redacted: true } })
  }))
  expect(screen.getByRole('button', { name: 'Show agent activity: 4 actions · 4 completed' }).getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryAllByTestId('agent-activity-row')).toHaveLength(0)
})

test('shows stopping rather than completed activity state while cancellation is pending', async () => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 1,
    activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: true },
    entries: [event(1)]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const disclosure = await screen.findByRole('button', { name: 'Hide agent activity: 1 action · stopping' })
  const group = disclosure.closest('.agent-activity-group')!
  expect(group.classList.contains('stopping')).toBe(true)
  expect(group.querySelector('.activity-group-state .lucide-pause')).not.toBeNull()
  expect(group.querySelector('.activity-group-state .lucide-check')).toBeNull()
  expect(screen.getByLabelText('Agent turn status').textContent).toBe('Stopping agent…')
})

test('preserves ownerless activity and never marks an inactive unfinished group complete', async () => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 2, activeTurn: null,
    entries: [
      event(1, { turnId: null, summary: 'Saved browser job', detail: { activity_id: 'mcp-save', phase: 'complete' } }),
      event(2, { turnId: 'turn-unsettled', state: 'working', summary: 'Unsettled action' })
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  expect(await screen.findByText('Saved browser job')).not.toBeNull()
  const disclosure = screen.getByRole('button', { name: 'Show agent activity: 1 action · 0 completed' })
  const group = disclosure.closest('.agent-activity-group')!
  expect(group.classList.contains('paused')).toBe(true)
  expect(group.querySelector('.activity-group-state .lucide-pause')).not.toBeNull()
  expect(group.querySelector('.activity-group-state .lucide-check')).toBeNull()
})

test.each([
  ['waiting', 'Turn paused'],
  ['interrupted', 'Turn interrupted'],
  ['failed', 'Turn failed']
] as const)('renders ownerless %s state truthfully and preserves Retry', async (state, label) => {
  const agent = installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 1, activeTurn: null,
    entries: [event(1, {
      turnId: null,
      type: state === 'failed' ? 'error' : 'status',
      state,
      summary: `Ownerless ${state}`,
      detail: { retry: true }
    })]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  expect(await screen.findByText(label)).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry turn' }))
  await waitFor(() => expect(agent.retry).toHaveBeenCalledWith('', expect.stringMatching(/^desktop-retry/)))
})

test('keeps a waiting turn open without showing working activity state', async () => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 3,
    activeTurn: { turnId: 'turn-1', status: 'waiting', cancelRequested: false },
    entries: [
      event(1),
      event(2, { type: 'status', state: 'waiting', summary: 'Choose a direction', detail: { actionable: true } }),
      event(3)
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const disclosure = await screen.findByRole('button', { name: 'Hide agent activity: 2 actions · waiting' })
  const group = disclosure.closest('.agent-activity-group')!
  expect(group.classList.contains('waiting')).toBe(true)
  expect(group.classList.contains('working')).toBe(false)
  expect(group.querySelector('.activity-group-state .spin')).toBeNull()
  expect(screen.getByLabelText('Agent turn status').textContent).toBe('Agent waiting for you')
})

test('collapses untouched activity once when the turn completes', async () => {
  let stream!: (update: unknown) => void
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 2,
    activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: false },
    entries: [event(1), event(2, { state: 'working' })]
  }, {
    subscribe: vi.fn((listener: (update: unknown) => void) => { stream = listener; return () => undefined })
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)
  expect((await screen.findByRole('button', { name: 'Hide agent activity: 2 actions · 1 completed' })).getAttribute('aria-expanded')).toBe('true')

  act(() => stream({
    kind: 'event',
    event: event(3, { type: 'assistant_message', state: 'completed', summary: 'Final answer', detail: { type: 'message.complete' } })
  }))

  const collapsed = await screen.findByRole('button', { name: 'Show agent activity: 2 actions · 1 completed' })
  expect(collapsed.getAttribute('aria-expanded')).toBe('false')
  expect(screen.queryAllByTestId('agent-activity-row')).toHaveLength(0)
  expect(screen.getByTestId('agent-turn').lastElementChild).toBe(screen.getByText('Final answer').closest('.assistant-message'))
})

test('announces turn state changes without announcing each tool update', async () => {
  let stream!: (update: unknown) => void
  installAgent({
    conversationId: 'conv-current', connection: 'online', latestEventId: 1,
    activeTurn: { turnId: 'turn-1', status: 'running', cancelRequested: false },
    entries: [event(1, { state: 'working' })]
  }, {
    subscribe: vi.fn((listener: (update: unknown) => void) => { stream = listener; return () => undefined })
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const liveRegion = document.querySelector('[aria-live="polite"]')!
  await waitFor(() => expect(liveRegion.textContent).toBe('Agent working'))
  act(() => stream({ kind: 'event', event: event(2, { state: 'working' }) }))
  expect(liveRegion.textContent).toBe('Agent working')
  act(() => stream({ kind: 'event', event: event(3, { type: 'status', state: 'waiting', summary: 'Need a choice', detail: { actionable: true } }) }))
  expect(liveRegion.textContent).toBe('Agent waiting for you')
  act(() => stream({ kind: 'event', event: event(4, { type: 'assistant_message', state: 'completed', summary: 'Done', detail: { type: 'message.complete' } }) }))
  expect(liveRegion.textContent).toBe('Agent response completed')
  expect(screen.queryByText('Turn paused')).toBeNull()
  expect(screen.queryByText('Need a choice')).toBeNull()
  expect(screen.getByTestId('agent-turn').lastElementChild).toBe(screen.getByText('Done').closest('.assistant-message'))
})

test('follows the bottom until the user detaches, then offers Jump to latest', async () => {
  let resolveSnapshot!: (snapshot: AgentConversationSnapshot) => void
  let stream!: (update: unknown) => void
  installAgent({} as AgentConversationSnapshot, {
    get: vi.fn(() => new Promise<AgentConversationSnapshot>(resolve => { resolveSnapshot = resolve })),
    subscribe: vi.fn((listener: (update: unknown) => void) => { stream = listener; return () => undefined })
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)
  const transcript = document.querySelector<HTMLElement>('.agent-body')!
  let scrollHeight = 1_000
  let scrollTop = 0
  Object.defineProperties(transcript, {
    clientHeight: { configurable: true, get: () => 300 },
    scrollHeight: { configurable: true, get: () => scrollHeight },
    scrollTop: { configurable: true, get: () => scrollTop, set: value => { scrollTop = Number(value) } }
  })

  await act(async () => resolveSnapshot({
    conversationId: 'conv-current', connection: 'online', activeTurn: null, latestEventId: 1,
    entries: [event(1, { type: 'assistant_message', state: 'completed', summary: 'Latest answer', detail: { type: 'message.complete' } })]
  }))
  expect(scrollTop).toBe(1_000)

  scrollTop = 500
  fireEvent.scroll(transcript)
  const jump = screen.getByRole('button', { name: 'Jump to latest' })
  expect(jump.tagName).toBe('BUTTON')
  scrollHeight = 1_200
  act(() => stream({ kind: 'event', event: event(2, { turnId: 'turn-2', type: 'assistant_message', state: 'completed', summary: 'New answer', detail: { type: 'message.complete' } }) }))
  expect(scrollTop).toBe(500)

  fireEvent.click(jump)
  expect(scrollTop).toBe(1_200)
  expect(screen.queryByRole('button', { name: 'Jump to latest' })).toBeNull()
  expect(document.activeElement).toBe(transcript)

  scrollHeight = 1_400
  act(() => stream({ kind: 'event', event: event(3, { turnId: 'turn-3', type: 'assistant_message', state: 'completed', summary: 'Newest answer', detail: { type: 'message.complete' } }) }))
  expect(scrollTop).toBe(1_400)
})

test('renders assistant Markdown safely while keeping user messages literal', async () => {
  const markdown = [
    '**Important**',
    '',
    '- First item',
    '- Second item',
    '',
    '[JobOS docs](https://example.com/docs)',
    '',
    '[Unsafe link](javascript:alert(1))',
    '',
    '![Tracking image](https://tracker.example/pixel.png)',
    '',
    '```ts',
    'const ready = true',
    '```',
    '',
    '<button data-testid="unsafe-html">Do not mount</button>'
  ].join('\n')
  const openExternal = vi.fn().mockResolvedValue(undefined)
  installAgent({
    conversationId: 'conv-current', activeTurn: null, connection: 'online', latestEventId: 2,
    entries: [
      event(1, { type: 'user_message', summary: markdown, text: markdown, detail: {} }),
      event(2, { type: 'assistant_message', summary: markdown, text: markdown, detail: { type: 'message.complete' } })
    ]
  }, {}, { shell: { openExternal } })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  const assistant = (await screen.findByText('Important')).closest('.assistant-markdown')
  expect(assistant).not.toBeNull()
  expect(assistant?.querySelector('strong')?.textContent).toBe('Important')
  expect(assistant?.querySelectorAll('li')).toHaveLength(2)
  expect(assistant?.querySelector('pre code')?.textContent).toContain('const ready = true')
  const link = assistant?.querySelector<HTMLAnchorElement>('a[href="https://example.com/docs"]')
  expect(link?.getAttribute('target')).toBe('_blank')
  expect(link?.getAttribute('rel')).toBe('noreferrer noopener')
  fireEvent.click(link as HTMLAnchorElement)
  expect(openExternal).toHaveBeenCalledWith('https://example.com/docs')
  expect(assistant?.querySelector('a[href^="javascript:"]')).toBeNull()
  expect(assistant?.querySelector('img')).toBeNull()
  expect(assistant?.textContent).toContain('[Image: Tracking image]')
  expect(assistant?.querySelector('[data-testid="unsafe-html"]')).toBeNull()
  expect(assistant?.textContent).toContain('<button data-testid="unsafe-html">Do not mount</button>')

  const userMessage = document.querySelector('.user-message p')
  expect(userMessage?.textContent).toBe(markdown)
  expect(document.querySelector('.user-message strong')).toBeNull()
  expect(document.querySelector('.user-message a')).toBeNull()
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

test.each(['failed', 'interrupted'] as const)('renders terminal assistant completion as explicit %s notice with Retry', async state => {
  installAgent({
    conversationId: 'conv-current', connection: 'online', activeTurn: null, latestEventId: 2,
    entries: [
      event(1, { type: 'assistant_message', state: 'working', summary: 'Partial response', detail: { type: 'message.delta' } }),
      event(2, { type: 'assistant_message', state, summary: `Response ${state}`, detail: { type: 'message.complete', status: state } })
    ]
  })
  render(<AgentPanel apiState="connected" contextLabel="Northstar" />)

  expect(await screen.findByText(state === 'failed' ? 'Turn failed' : 'Turn interrupted')).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Retry turn' })).not.toBeNull()
  const finalAssistant = screen.getByTestId('agent-turn').lastElementChild
  expect(finalAssistant?.classList.contains('assistant-message')).toBe(true)
  expect(finalAssistant?.textContent).toContain(`Response ${state}`)
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

test('focuses a live resume render that arrives before hydration resolves', async () => {
  let stream!: (update: unknown) => void
  let resolveSnapshot!: (snapshot: AgentConversationSnapshot) => void
  installAgent({} as AgentConversationSnapshot, {
    get: vi.fn(() => new Promise<AgentConversationSnapshot>(resolve => { resolveSnapshot = resolve })),
    subscribe: vi.fn((listener: (update: unknown) => void) => { stream = listener; return () => undefined })
  })
  const onArtifactRendered = vi.fn()
  render(<AgentPanel apiState="connected" contextLabel="Northstar" onArtifactRendered={onArtifactRendered} />)

  act(() => stream({
    kind: 'event',
    event: event(2, { detail: { command: 'document.render', outcome: 'completed' } })
  }))
  await act(async () => resolveSnapshot({
    conversationId: 'conv-current', connection: 'online', activeTurn: null, latestEventId: 1,
    entries: [event(1)]
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
  const activityGroup = screen.getByRole('button', { name: 'Show agent activity: 1 action · interrupted' }).closest('.agent-activity-group')!
  const terminal = screen.getByText('Turn interrupted').closest('.agent-notice')!
  expect(screen.queryByText('Streaming')).toBeNull()
  expect(screen.getByRole('button', { name: 'Retry turn' })).not.toBeNull()
  expect(activityGroup.compareDocumentPosition(terminal) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(terminal.compareDocumentPosition(draft.closest('.assistant-message')!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
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
