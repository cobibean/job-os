import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { ConnectedAgentsSnapshot } from '../../../shared/contracts'
import { useConnectedAgents } from './useConnectedAgents'

const agentId = `jagent_${'a'.repeat(32)}`

const snapshot: ConnectedAgentsSnapshot = {
  registryRevision: 3,
  profileId: `jprof_${'b'.repeat(32)}`,
  defaultConnectedAgentId: agentId,
  agents: [{
    id: agentId,
    provider: 'codex',
    displayName: 'Codex',
    avatarId: 'spark',
    defaultModelId: '(FAKE)-live',
    defaultReasoningEffort: 'medium',
    lifecycle: 'connected',
    accountSummary: null,
    accountFingerprint: null,
    health: {
      state: 'ready',
      label: 'Ready',
      providerAvailable: true,
      toolsAvailable: true,
      retryAfterSeconds: null
    },
    activeChats: 0,
    lockedChats: 0
  }]
}

afterEach(cleanup)

test('refresh returns the loaded roster so startup actions can await readiness', async () => {
  const list = vi.fn().mockResolvedValue(snapshot)
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectedAgents: { list, models: vi.fn() } }
  })
  const { result } = renderHook(() => useConnectedAgents())

  await waitFor(() => expect(result.current.snapshot).toEqual(snapshot))
  let refreshed: ConnectedAgentsSnapshot | null | undefined
  await act(async () => { refreshed = await result.current.refresh() })

  expect(refreshed).toEqual(snapshot)
  expect(list).toHaveBeenCalledTimes(2)
})

test('does not pin a failed model probe in the cache', async () => {
  const unavailable = { live: false, models: [] }
  const available = {
    live: true,
    models: [{ modelId: '(FAKE)-live', displayName: '(FAKE) Live', reasoningEfforts: ['medium'] }]
  }
  const models = vi.fn().mockResolvedValueOnce(unavailable).mockResolvedValueOnce(available)
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectedAgents: { list: vi.fn().mockResolvedValue(snapshot), models } }
  })
  const { result } = renderHook(() => useConnectedAgents())
  await waitFor(() => expect(result.current.snapshot).toEqual(snapshot))

  await act(async () => { expect(await result.current.loadModels(agentId)).toEqual(unavailable) })
  await act(async () => { expect(await result.current.loadModels(agentId)).toEqual(available) })

  expect(models).toHaveBeenCalledTimes(2)
})
