import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerDocumentsIpc } from './documentsIpc.js'

test('registers artifact IPC and preserves opaque ID validation', () => {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as Pick<IpcMain, 'handle'>
  const client = { list: vi.fn(), refresh: vi.fn(), approve: vi.fn(), loadPdf: vi.fn(), loadOriginalDocx: vi.fn(), exportArtifact: vi.fn(), reveal: vi.fn(), open: vi.fn() }
  registerDocumentsIpc(ipc, () => client as never)
  expect([...handlers.keys()]).toEqual([
    'jobos:documents:list', 'jobos:documents:refresh', 'jobos:documents:approve',
    'jobos:documents:load-pdf', 'jobos:documents:load-original-docx',
    'jobos:documents:export', 'jobos:documents:reveal', 'jobos:documents:open'
  ])
  const event = {} as IpcMainInvokeEvent
  expect(() => handlers.get('jobos:documents:list')?.(event, '../job')).toThrow('Invalid job')
  expect(() => handlers.get('jobos:documents:open')?.(event, 'art_short')).toThrow('Invalid artifact')
  handlers.get('jobos:documents:approve')?.(event, 'job-1', `art_${'a'.repeat(16)}`)
  expect(client.approve).toHaveBeenCalledWith('job-1', `art_${'a'.repeat(16)}`)
})
