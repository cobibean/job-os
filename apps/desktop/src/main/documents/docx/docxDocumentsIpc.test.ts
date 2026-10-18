import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerDocxDocumentsIpc } from './docxDocumentsIpc.js'

test('keeps artifact opening in the DOCX registrar with availability and ID checks', async () => {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler) } as unknown as Pick<IpcMain, 'handle'>
  const service = { openArtifact: vi.fn(async () => ({ bindingId: 'binding' })) }
  const assertAvailable = vi.fn()
  const loadOriginalDocx = vi.fn(async () => ({ filename: 'resume.docx', sha256: 'a'.repeat(64), bytes: new ArrayBuffer(1) }))
  registerDocxDocumentsIpc(ipc, () => service as never, { assertAvailable, loadOriginalDocx })
  expect(handlers.has('jobos:docx:open-artifact')).toBe(true)
  const event = {} as IpcMainInvokeEvent
  await expect(handlers.get('jobos:docx:open-artifact')?.(event, 'job', 'resume', 'bad')).rejects.toThrow('Invalid artifact')
  expect(assertAvailable).toHaveBeenCalledOnce()
  expect(loadOriginalDocx).not.toHaveBeenCalled()
  await handlers.get('jobos:docx:open-artifact')?.(event, 'job', 'resume', `art_${'a'.repeat(16)}`)
  expect(assertAvailable).toHaveBeenCalledBefore(loadOriginalDocx)
  expect(service.openArtifact).toHaveBeenCalledWith('job', 'resume', expect.objectContaining({ filename: 'resume.docx' }))
})


test.each(['export', 'open', 'reveal'] as const)('requires a trusted renderer and preserves reviewed identity for %s', async action => {
  const handlers = new Map<string, (...arguments_: unknown[]) => unknown>()
  const ipc = { handle: (channel: string, handler: (...arguments_: unknown[]) => unknown) => handlers.set(channel, handler) } as unknown as Pick<IpcMain, 'handle'>
  const method = vi.fn(async () => 'Done')
  const trusted = vi.fn(() => ({ [action]: method }) as never)
  registerDocxDocumentsIpc(ipc, trusted)
  const event = {} as IpcMainInvokeEvent
  await handlers.get(`jobos:docx:${action}`)?.(event, 'docx_000000000000000000000000', 'a'.repeat(64))
  expect(trusted).toHaveBeenCalledWith(event)
  expect(method).toHaveBeenCalledWith('docx_000000000000000000000000', 'a'.repeat(64))
  trusted.mockImplementationOnce(() => { throw new Error('Untrusted renderer') })
  expect(() => handlers.get(`jobos:docx:${action}`)?.(event, 'docx_000000000000000000000000', 'a'.repeat(64))).toThrow('Untrusted renderer')
  expect(method).toHaveBeenCalledOnce()
})
