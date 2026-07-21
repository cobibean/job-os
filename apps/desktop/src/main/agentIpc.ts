import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { createMainAgentClient } from './agent.js'

type AgentClient = ReturnType<typeof createMainAgentClient>

const turnPattern = /^[A-Za-z0-9_-]{1,128}$/

function idempotencyKey(value: unknown): string {
  if (typeof value !== 'string' || value.length < 8 || value.length > 200 || /[\r\n]/.test(value)) throw new Error('Invalid idempotency key')
  return value
}

function turnId(value: unknown): string {
  if (typeof value !== 'string' || !turnPattern.test(value)) throw new Error('Invalid agent turn')
  return value
}

export function registerAgentIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => AgentClient
): void {
  ipc.handle('jobos:agent:get', event => trusted(event).get())
  ipc.handle('jobos:agent:reset', event => trusted(event).reset())
  ipc.handle('jobos:agent:send', (event, text: unknown, key: unknown) => {
    if (typeof text !== 'string' || !text.trim() || text.length > 12_000) throw new Error('Invalid agent message')
    return trusted(event).send(text.trim(), idempotencyKey(key))
  })
  ipc.handle('jobos:agent:cancel', (event, id: unknown) => trusted(event).cancel(turnId(id)))
  ipc.handle('jobos:agent:retry', (event, id: unknown, key: unknown) => trusted(event).retry(turnId(id), idempotencyKey(key)))
}
