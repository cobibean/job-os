// @vitest-environment node

import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { validateAgentChatSelection } from './agentChatSelection.js'
import { registerConnectedAgentsIpc } from './connectedAgentsIpc.js'

const agentId = `jagent_${'c'.repeat(32)}`

test('chat selection validates immutable IDs, revisions, model, effort, and idempotency', () => {
  expect(validateAgentChatSelection({
    connectedAgentId: agentId,
    modelId: 'gpt-live',
    reasoningEffort: 'medium',
    expectedProfileRevision: 4,
    expectedAgentRegistryRevision: 4,
    idempotencyKey: 'desktop-new-chat-test'
  })).toEqual(expect.objectContaining({ connectedAgentId: agentId, modelId: 'gpt-live', initialSelectedJobId: null }))
  expect(() => validateAgentChatSelection({ connectedAgentId: '../agent' })).toThrow('Invalid Connected Agent')
})

test('Connected Agents IPC rejects malformed IDs and accepts provider auth transaction IDs', () => {
  const handlers = new Map<string, (...args: unknown[]) => unknown>()
  const ipc = { handle: vi.fn((channel: string, handler: (...args: unknown[]) => unknown) => handlers.set(channel, handler)) } as unknown as Pick<IpcMain, 'handle'>
  const client = {
    list: vi.fn(), models: vi.fn(), test: vi.fn(), createCodex: vi.fn(), update: vi.fn(), setDefault: vi.fn(),
    impact: vi.fn(), disconnect: vi.fn(), startAuth: vi.fn(), readAuth: vi.fn(), cancelAuth: vi.fn()
  }
  registerConnectedAgentsIpc(ipc, () => client as never)
  const event = {} as IpcMainInvokeEvent

  expect(() => handlers.get('jobos:connected-agents:models')?.(event, '../agent')).toThrow('Invalid Connected Agent')
  expect(() => handlers.get('jobos:connected-agents:auth:read')?.(event, 'auth_wrong')).toThrow('Invalid authentication transaction')
  handlers.get('jobos:connected-agents:auth:read')?.(event, `jauth_${'d'.repeat(32)}`)
  expect(client.readAuth).toHaveBeenCalledWith(`jauth_${'d'.repeat(32)}`)
})
