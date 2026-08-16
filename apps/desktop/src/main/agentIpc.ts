import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { createMainAgentClient } from './agent.js'

type AgentClient = ReturnType<typeof createMainAgentClient>

const turnPattern = /^turn_[A-Za-z0-9_-]{1,128}$/
const conversationPattern = /^conv_[A-Za-z0-9_-]{1,128}$/

function idempotencyKey(value: unknown): string {
  if (typeof value !== 'string' || value.length < 8 || value.length > 200 || /[\r\n]/.test(value)) throw new Error('Invalid idempotency key')
  return value
}

function turnId(value: unknown): string {
  if (typeof value !== 'string' || !turnPattern.test(value)) throw new Error('Invalid agent turn')
  return value
}

function conversationId(value: unknown): string {
  if (typeof value !== 'string' || !conversationPattern.test(value)) throw new Error('Invalid agent conversation')
  return value
}

export function registerAgentIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => AgentClient
): void {
  ipc.handle('jobos:agent:list', event => trusted(event).list())
  ipc.handle('jobos:agent:create', event => trusted(event).create())
  ipc.handle('jobos:agent:get', (event, conversation: unknown) => trusted(event).get(conversationId(conversation)))
  ipc.handle('jobos:agent:archive', (event, conversation: unknown) => trusted(event).archive(conversationId(conversation)))
  ipc.handle('jobos:agent:send', (event, conversation: unknown, text: unknown, key: unknown) => {
    if (typeof text !== 'string' || !text.trim() || text.length > 12_000) throw new Error('Invalid agent message')
    return trusted(event).send(conversationId(conversation), text.trim(), idempotencyKey(key))
  })
  ipc.handle('jobos:agent:cancel', (event, conversation: unknown, id: unknown) => trusted(event).cancel(conversationId(conversation), turnId(id)))
  ipc.handle('jobos:agent:retry', (event, conversation: unknown, id: unknown, key: unknown) => trusted(event).retry(conversationId(conversation), turnId(id), idempotencyKey(key)))
}
