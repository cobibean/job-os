import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { useConnectedAgents } from '../hooks/useConnectedAgents'
import { NewAgentChatDialog } from './NewAgentChatDialog'

const agentId = `jagent_${'a'.repeat(32)}`

function connectedAgents(): ReturnType<typeof useConnectedAgents> {
  const catalog = {
    live: true,
    models: [{ modelId: 'gpt-live', displayName: 'GPT Live', reasoningEfforts: ['low', 'medium'] }]
  }
  return {
    bridge: {} as never,
    snapshot: {
      registryRevision: 7,
      profileId: 'default',
      defaultConnectedAgentId: agentId,
      agents: [{
        id: agentId,
        provider: 'codex',
        displayName: 'Codex',
        avatarId: 'spark',
        defaultModelId: 'gpt-live',
        defaultReasoningEffort: 'medium',
        lifecycle: 'connected',
        accountSummary: null,
        accountFingerprint: null,
        health: { state: 'ready', label: 'Ready', providerAvailable: true, toolsAvailable: true, retryAfterSeconds: null },
        activeChats: 0,
        lockedChats: 0
      }]
    },
    models: { [agentId]: catalog },
    loading: false,
    error: null,
    refresh: vi.fn(),
    loadModels: vi.fn().mockResolvedValue(catalog)
  }
}

afterEach(cleanup)

test('creates a chat from only live ready choices and seals the exact selection receipt', async () => {
  const state = connectedAgents()
  const onCreate = vi.fn().mockResolvedValue(true)
  const onClose = vi.fn()
  render(<NewAgentChatDialog atMaximum={false} connectedAgents={state} onArchiveCurrent={vi.fn()} onClose={onClose} onCreate={onCreate} />)

  await screen.findByText('GPT Live')
  expect(state.loadModels).toHaveBeenCalledWith(agentId, true)
  expect(screen.getByText(/choice stays locked/i)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Start chat' }))

  await waitFor(() => expect(onCreate).toHaveBeenCalledOnce())
  expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({
    connectedAgentId: agentId,
    modelId: 'gpt-live',
    reasoningEffort: 'medium',
    expectedProfileRevision: 7,
    expectedAgentRegistryRevision: 7
  }))
  expect(onClose).toHaveBeenCalledOnce()
})

test('switching agents ignores a stale model response from the previous agent', async () => {
  const state = connectedAgents()
  const secondId = `jagent_${'c'.repeat(32)}`
  state.snapshot!.agents.push({
    ...state.snapshot!.agents[0]!, id: secondId, provider: 'hermes', displayName: 'Hermes',
    defaultModelId: 'hermes-live'
  })
  const first = Promise.withResolvers<{ live: boolean; models: Array<{ modelId: string; displayName: string; reasoningEfforts: string[] }> }>()
  const second = Promise.withResolvers<{ live: boolean; models: Array<{ modelId: string; displayName: string; reasoningEfforts: string[] }> }>()
  const catalogA = { live: true, models: [{ modelId: 'gpt-live', displayName: 'GPT Live', reasoningEfforts: ['medium'] }] }
  const catalogB = { live: true, models: [{ modelId: 'hermes-live', displayName: 'Hermes Live', reasoningEfforts: ['medium'] }] }
  state.models[secondId] = catalogB
  state.loadModels = vi.fn((id: string) => id === agentId ? first.promise : second.promise)
  const onCreate = vi.fn().mockResolvedValue(true)
  render(<NewAgentChatDialog atMaximum={false} connectedAgents={state} onArchiveCurrent={vi.fn()} onClose={vi.fn()} onCreate={onCreate} />)

  fireEvent.change(screen.getByLabelText('Agent'), { target: { value: secondId } })
  await act(async () => { first.resolve(catalogA); await first.promise })
  expect((screen.getByRole('button', { name: 'Start chat' }) as HTMLButtonElement).disabled).toBe(true)
  await act(async () => { second.resolve(catalogB); await second.promise })
  fireEvent.click(screen.getByRole('button', { name: 'Start chat' }))
  await waitFor(() => expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ connectedAgentId: secondId, modelId: 'hermes-live' })))
})

test('the five-chat state offers archive recovery instead of hiding New Chat', async () => {
  const onArchiveCurrent = vi.fn()
  render(<NewAgentChatDialog atMaximum connectedAgents={connectedAgents()} onArchiveCurrent={onArchiveCurrent} onClose={vi.fn()} onCreate={vi.fn()} />)

  expect(screen.getByText('Five chats are already open')).toBeTruthy()
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('dialog')))
  fireEvent.click(screen.getByRole('button', { name: 'Archive current chat' }))
  expect(onArchiveCurrent).toHaveBeenCalledOnce()
})

test('a rejected chat creation stays open and explains the failure', async () => {
  const onClose = vi.fn()
  render(<NewAgentChatDialog atMaximum={false} connectedAgents={connectedAgents()} onArchiveCurrent={vi.fn()} onClose={onClose} onCreate={vi.fn().mockRejectedValue(new Error('Provider is restarting'))} />)

  await screen.findByText('GPT Live')
  fireEvent.click(screen.getByRole('button', { name: 'Start chat' }))

  expect((await screen.findByRole('alert')).textContent).toContain('Provider is restarting')
  expect(onClose).not.toHaveBeenCalled()
  expect((screen.getByRole('button', { name: 'Start chat' }) as HTMLButtonElement).disabled).toBe(false)
})

test('Escape closes and Tab remains trapped in the dialog', () => {
  const onClose = vi.fn()
  render(<NewAgentChatDialog atMaximum connectedAgents={connectedAgents()} onArchiveCurrent={vi.fn()} onClose={onClose} onCreate={vi.fn()} />)
  const close = screen.getByRole('button', { name: 'Close New Chat' })
  const archive = screen.getByRole('button', { name: 'Archive current chat' })
  archive.focus()
  fireEvent.keyDown(archive, { key: 'Tab' })
  expect(document.activeElement).toBe(close)
  fireEvent.keyDown(close, { key: 'Escape' })
  expect(onClose).toHaveBeenCalledOnce()
})
