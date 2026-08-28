import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { ConnectedAgentSummary } from '../../shared/contracts.js'
import type { createMainConnectedAgentsClient } from './connectedAgents.js'

type Client = ReturnType<typeof createMainConnectedAgentsClient>
const agentPattern = /^jagent_[a-f0-9]{32}$/
const transactionPattern = /^jauth_[a-f0-9]{32}$/

function connectedAgentId(value: unknown): string {
  if (typeof value !== 'string' || !agentPattern.test(value)) throw new Error('Invalid Connected Agent')
  return value
}

function transactionId(value: unknown): string {
  if (typeof value !== 'string' || !transactionPattern.test(value)) throw new Error('Invalid authentication transaction')
  return value
}

function revision(value: unknown): number {
  if (!Number.isInteger(value) || Number(value) < 1) throw new Error('Invalid Connected Agent revision')
  return Number(value)
}

function key(value: unknown): string {
  if (typeof value !== 'string' || value.length < 8 || value.length > 200 || /[\r\n]/.test(value)) throw new Error('Invalid idempotency key')
  return value
}

function text(value: unknown, maximum = 120): string {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum) throw new Error('Invalid Connected Agent value')
  return value.trim()
}

export function registerConnectedAgentsIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => Client
): void {
  ipc.handle('jobos:connected-agents:list', event => trusted(event).list())
  ipc.handle('jobos:connected-agents:models', (event, id) => trusted(event).models(connectedAgentId(id)))
  ipc.handle('jobos:connected-agents:test', (event, id) => trusted(event).test(connectedAgentId(id)))
  ipc.handle('jobos:connected-agents:create-codex', (event, displayName, avatarId, expected, idempotencyKey) => (
    trusted(event).createCodex(text(displayName), text(avatarId, 64), revision(expected), key(idempotencyKey))
  ))
  ipc.handle('jobos:connected-agents:update', (event, current, modelId, effort, expected, idempotencyKey) => {
    if (!current || typeof current !== 'object' || Array.isArray(current)) throw new Error('Invalid Connected Agent')
    const candidate = current as ConnectedAgentSummary
    const safeCurrent: ConnectedAgentSummary = {
      ...candidate,
      id: connectedAgentId(candidate.id),
      displayName: text(candidate.displayName),
      avatarId: text(candidate.avatarId, 64)
    }
    return trusted(event).update(
      safeCurrent,
      modelId === null ? null : text(modelId, 256),
      effort === null ? null : text(effort, 64),
      revision(expected),
      key(idempotencyKey)
    )
  })
  ipc.handle('jobos:connected-agents:set-default', (event, profileId, agentId, expected, idempotencyKey) => (
    trusted(event).setDefault(text(profileId, 64), agentId === null ? null : connectedAgentId(agentId), revision(expected), key(idempotencyKey))
  ))
  ipc.handle('jobos:connected-agents:impact', (event, id) => trusted(event).impact(connectedAgentId(id)))
  ipc.handle('jobos:connected-agents:disconnect', (event, id, expected, idempotencyKey) => (
    trusted(event).disconnect(connectedAgentId(id), revision(expected), key(idempotencyKey))
  ))
  ipc.handle('jobos:connected-agents:auth:start', (event, id, mode, fingerprint) => {
    if (!['connect', 'reconnect', 'replace'].includes(String(mode))) throw new Error('Invalid authentication mode')
    if (fingerprint !== null && !/^[a-f0-9]{64}$/.test(String(fingerprint))) throw new Error('Invalid account fingerprint')
    return trusted(event).startAuth(connectedAgentId(id), mode as 'connect' | 'reconnect' | 'replace', fingerprint as string | null)
  })
  ipc.handle('jobos:connected-agents:auth:read', (event, id) => trusted(event).readAuth(transactionId(id)))
  ipc.handle('jobos:connected-agents:auth:cancel', (event, id) => trusted(event).cancelAuth(transactionId(id)))
}
