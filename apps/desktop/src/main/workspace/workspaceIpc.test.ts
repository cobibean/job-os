import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerWorkspaceIpc } from './workspaceIpc.js'

test('registers and validates workspace IPC before forwarding', () => {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as Pick<IpcMain, 'handle'>
  const client = { get: vi.fn(), save: vi.fn(), saveDocumentView: vi.fn() }
  registerWorkspaceIpc(ipc, () => client as never)
  expect([...handlers.keys()]).toEqual(['jobos:workspace:get', 'jobos:workspace:save', 'jobos:workspace:save-document-view'])
  const event = {} as IpcMainInvokeEvent
  expect(() => handlers.get('jobos:workspace:save')?.(event, { revision: 1.5 })).toThrow('Invalid workspace snapshot')
  expect(() => handlers.get('jobos:workspace:save-document-view')?.(event, 'bad', null, 1, 1)).toThrow('Invalid agent conversation')
  handlers.get('jobos:workspace:save-document-view')?.(event, 'conv_valid', null, 2, 1.5)
  expect(client.saveDocumentView).toHaveBeenCalledWith('conv_valid', null, 2, 1.5)
})
