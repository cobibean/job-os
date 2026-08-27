import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { useConnectedAgents } from '../hooks/useConnectedAgents'
import { authTerminalNotice, ConnectedAgentsSettings } from './ConnectedAgentsSettings'

const agentId = `jagent_${'b'.repeat(32)}`

test('authentication terminal states remain visible and actionable', () => {
  expect(authTerminalNotice('expired', null)).toContain('expired')
  expect(authTerminalNotice('cancelled', null)).toContain('cancelled')
  expect(authTerminalNotice('failed', null)).toContain('failed')
  expect(authTerminalNotice('cleanup_required', 'AUTH_CLEANUP_REQUIRED')).toContain('cleaned up safely')
})

function state(): ReturnType<typeof useConnectedAgents> {
  const catalog = { live: true, models: [{ modelId: 'hermes-live', displayName: 'Hermes Live', reasoningEfforts: ['medium'] }] }
  const bridge = {
    update: vi.fn().mockResolvedValue(undefined),
    setDefault: vi.fn().mockResolvedValue(4),
    test: vi.fn(),
    impact: vi.fn().mockResolvedValue({ activeChats: 2, lockedChats: 1, defaultProfileIds: ['default'] }),
    disconnect: vi.fn().mockResolvedValue(undefined),
    createCodex: vi.fn(),
    startAuth: vi.fn(),
    readAuth: vi.fn(),
    cancelAuth: vi.fn()
  }
  return {
    bridge: bridge as never,
    snapshot: {
      registryRevision: 3,
      profileId: 'default',
      defaultConnectedAgentId: agentId,
      agents: [{
        id: agentId,
        provider: 'hermes',
        displayName: 'Hermes',
        avatarId: 'ninja',
        defaultModelId: 'hermes-live',
        defaultReasoningEffort: 'medium',
        lifecycle: 'connected',
        accountSummary: null,
        accountFingerprint: null,
        health: { state: 'ready', label: 'Ready', providerAvailable: true, toolsAvailable: true, retryAfterSeconds: null },
        activeChats: 2,
        lockedChats: 1
      }]
    },
    models: { [agentId]: catalog },
    loading: false,
    error: null,
    refresh: vi.fn().mockResolvedValue(undefined),
    loadModels: vi.fn().mockResolvedValue(catalog)
  }
}

function renderExpanded(
  value: ReturnType<typeof useConnectedAgents>,
  onAgentsChanged?: () => Promise<void>
) {
  const rendered = render(
    <ConnectedAgentsSettings onAgentsChanged={onAgentsChanged} state={value} />
  )
  const toggle = screen.getByRole('button', { name: 'Connected Agents' })
  expect(toggle.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(toggle)
  expect(toggle.getAttribute('aria-expanded')).toBe('true')
  return rendered
}

afterEach(() => { cleanup(); vi.useRealTimers() })

test('renders the roster inspector, live defaults, and immutable-chat explanation', async () => {
  renderExpanded(state())
  expect(screen.getByRole('list', { name: 'Connected Agent roster' })).toBeTruthy()
  expect(screen.getByText('Provider and JobOS tools are ready.')).toBeTruthy()
  expect(screen.queryByText(/Existing chats keep their original model/i)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Save defaults' }))
  expect(await screen.findByText(/Existing chats keep their original model/i)).toBeTruthy()
})

test('refreshes both the roster and recovered chat summaries', async () => {
  const value = state()
  const onAgentsChanged = vi.fn().mockResolvedValue(undefined)
  renderExpanded(value, onAgentsChanged)

  fireEvent.click(screen.getByRole('button', { name: 'Refresh Connected Agents' }))

  await waitFor(() => expect(value.refresh).toHaveBeenCalledTimes(1))
  expect(onAgentsChanged).toHaveBeenCalledTimes(1)
})

test('testing an agent refreshes recovered chat summaries', async () => {
  const value = state()
  const onAgentsChanged = vi.fn().mockResolvedValue(undefined)
  value.bridge!.test = vi.fn().mockResolvedValue({ health: value.snapshot!.agents[0]!.health })
  renderExpanded(value, onAgentsChanged)

  fireEvent.click(screen.getByRole('button', { name: 'Test' }))

  await waitFor(() => expect(value.bridge!.test).toHaveBeenCalledWith(agentId))
  expect(value.refresh).toHaveBeenCalledTimes(1)
  expect(onAgentsChanged).toHaveBeenCalledTimes(1)
})

test('disconnect requires impact disclosure and promises readable history', async () => {
  const value = state()
  renderExpanded(value)
  fireEvent.click(screen.getByRole('button', { name: /Disconnect/ }))
  const dialog = await screen.findByRole('alertdialog')
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Keep connected' })))
  expect(dialog.textContent).toContain('2 active chats will become read-only')
  expect(dialog.textContent).toContain('Chat history stays visible')
  expect(dialog.textContent).toContain('New Chat default for 1 profile: default')
  fireEvent.click(screen.getByRole('button', { name: 'Keep connected' }))
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
})

test('disconnect disclosure pluralizes singular impact and traps keyboard focus', async () => {
  const value = state()
  value.bridge!.impact = vi.fn().mockResolvedValue({ activeChats: 1, lockedChats: 1, defaultProfileIds: [] })
  renderExpanded(value)
  fireEvent.click(screen.getByRole('button', { name: /Disconnect/ }))

  const dialog = await screen.findByRole('alertdialog')
  expect(dialog.textContent).toContain('1 active chat will become read-only. 1 is already locked.')
  const keep = screen.getByRole('button', { name: 'Keep connected' })
  const disconnect = screen.getByRole('button', { name: 'Disconnect agent' })
  disconnect.focus()
  fireEvent.keyDown(disconnect, { key: 'Tab' })
  expect(document.activeElement).toBe(keep)
  fireEvent.keyDown(keep, { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
})

test('pending device auth can be cancelled and is cleaned up when settings closes', async () => {
  const value = state()
  const codex = value.snapshot!.agents[0]!
  codex.provider = 'codex'
  codex.accountSummary = null
  const transactionId = `jauth_${'d'.repeat(32)}`
  value.bridge!.startAuth = vi.fn().mockResolvedValue({
    transactionId,
    status: 'login_pending', userCode: '(FAKE)-CODE', verificationUrl: 'https://example.test/device', expiresAt: ''
  })
  value.bridge!.cancelAuth = vi.fn().mockResolvedValue(undefined)
  const rendered = renderExpanded(value)

  fireEvent.click(screen.getByRole('button', { name: 'Finish ChatGPT sign in' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel sign in' }))
  await waitFor(() => expect(value.bridge!.cancelAuth).toHaveBeenCalledWith(transactionId))

  fireEvent.click(screen.getByRole('button', { name: 'Finish ChatGPT sign in' }))
  await screen.findByRole('button', { name: 'Cancel sign in' })
  rendered.unmount()
  expect(value.bridge!.cancelAuth).toHaveBeenCalledTimes(2)
})

test('successful device auth force-refreshes the runtime model catalog', async () => {
  vi.useFakeTimers()
  const value = state()
  const codex = value.snapshot!.agents[0]!
  codex.provider = 'codex'
  codex.accountSummary = null
  value.bridge!.startAuth = vi.fn().mockResolvedValue({
    transactionId: `jauth_${'a'.repeat(32)}`,
    status: 'login_pending', userCode: '(FAKE)-CODE', verificationUrl: 'https://example.test/device', expiresAt: ''
  })
  value.bridge!.readAuth = vi.fn().mockResolvedValue({
    transactionId: `jauth_${'a'.repeat(32)}`,
    status: 'connected', userCode: null, verificationUrl: null, expiresAt: '', errorCode: null
  })
  renderExpanded(value)
  fireEvent.click(screen.getByRole('button', { name: 'Finish ChatGPT sign in' }))
  await act(async () => { await Promise.resolve() })
  await act(async () => { await vi.advanceTimersByTimeAsync(1500) })
  expect(value.loadModels).toHaveBeenCalledWith(agentId, true)
})

test('a disconnected Codex identity offers explicit account replacement on the same durable card', async () => {
  const value = state()
  const codex = value.snapshot!.agents[0]!
  codex.provider = 'codex'
  codex.lifecycle = 'disconnected'
  codex.accountSummary = { label: '(FAKE) preserved account' }
  codex.accountFingerprint = 'a'.repeat(64)
  value.bridge!.startAuth = vi.fn().mockResolvedValue({
    transactionId: `jauth_${'b'.repeat(32)}`,
    status: 'login_pending', userCode: '(FAKE)-CODE', verificationUrl: 'https://example.test/device', expiresAt: ''
  })
  renderExpanded(value)
  fireEvent.click(screen.getByRole('button', { name: 'Replace ChatGPT account' }))
  await waitFor(() => expect(value.bridge!.startAuth).toHaveBeenCalledWith(agentId, 'replace', 'a'.repeat(64)))
  expect(screen.getByText(/cannot verify the original account/i)).toBeTruthy()
})

test('an unhealthy connected Codex identity offers reconnect', async () => {
  const value = state()
  const codex = value.snapshot!.agents[0]!
  codex.provider = 'codex'
  codex.accountSummary = { label: '(FAKE) existing account' }
  codex.accountFingerprint = null
  codex.health = { ...codex.health, state: 'unavailable', label: 'Sign in required', providerAvailable: false }
  value.bridge!.startAuth = vi.fn().mockResolvedValue({
    transactionId: `jauth_${'c'.repeat(32)}`,
    status: 'login_pending', userCode: '(FAKE)-CODE', verificationUrl: 'https://example.test/device', expiresAt: ''
  })

  renderExpanded(value)
  fireEvent.click(screen.getByRole('button', { name: 'Replace ChatGPT account' }))

  await waitFor(() => expect(value.bridge!.startAuth).toHaveBeenCalledWith(agentId, 'replace', null))
})
