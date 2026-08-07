// @vitest-environment node

import type { IpcMain, IpcMainInvokeEvent } from 'electron'
import { describe, expect, it, vi } from 'vitest'

import type { MainEditableDocumentsClient } from './editableDocuments.js'
import { registerEditableDocumentsIpc } from './editableDocumentsIpc.js'

const documentId = 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX'

type Handler = (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown

function register() {
  const handlers = new Map<string, Handler>()
  const ipc = {
    handle: vi.fn((channel: string, handler: Handler) => handlers.set(channel, handler))
  } as unknown as Pick<IpcMain, 'handle'>
  const client = {
    list: vi.fn(async () => []),
    getForJob: vi.fn(),
    get: vi.fn(),
    createBlank: vi.fn(async () => ({ documentId })),
    save: vi.fn(),
    listSnapshots: vi.fn(),
    createSnapshot: vi.fn(),
    restoreSnapshot: vi.fn(),
    applyOperations: vi.fn(),
    importRegisteredArtifact: vi.fn(),
    importExternalDocx: vi.fn(),
    preview: vi.fn(),
    exportGenerated: vi.fn(),
    publish: vi.fn()
  } as unknown as MainEditableDocumentsClient
  const trusted = vi.fn(() => client)
  registerEditableDocumentsIpc(ipc, trusted)
  return { client, handlers, trusted }
}

describe('editable document IPC bridge', () => {
  it('registers every Phase 1 and later-phase document channel', () => {
    const { handlers } = register()
    expect([...handlers.keys()].sort()).toEqual([
      'jobos:editable-documents:apply-operations',
      'jobos:editable-documents:create-blank',
      'jobos:editable-documents:create-snapshot',
      'jobos:editable-documents:export',
      'jobos:editable-documents:get',
      'jobos:editable-documents:get-for-job',
      'jobos:editable-documents:import-file',
      'jobos:editable-documents:import-registered',
      'jobos:editable-documents:list',
      'jobos:editable-documents:list-snapshots',
      'jobos:editable-documents:preview',
      'jobos:editable-documents:publish',
      'jobos:editable-documents:restore-snapshot',
      'jobos:editable-documents:save'
    ])
  })

  it('validates identifiers before calling the trusted client', async () => {
    const { client, handlers } = register()
    const event = {} as IpcMainInvokeEvent
    const create = handlers.get('jobos:editable-documents:create-blank')
    if (!create) throw new Error('Create IPC handler was not registered')

    await expect(create(event, 'job-7', 'references', 'create-1')).resolves.toEqual({ documentId })
    expect(client.createBlank).toHaveBeenCalledWith('job-7', 'references', 'create-1')
    expect(() => create(event, '../job', 'resume', 'create-2')).toThrow('Invalid job')
    expect(() => create(event, 'job-7', 'portfolio', 'create-3')).toThrow('Invalid document type')
    expect(client.createBlank).toHaveBeenCalledTimes(1)
  })

  it('rejects malformed request objects and export formats at the IPC boundary', () => {
    const { client, handlers } = register()
    const event = {} as IpcMainInvokeEvent
    const save = handlers.get('jobos:editable-documents:save')
    const exportDocument = handlers.get('jobos:editable-documents:export')
    if (!save || !exportDocument) throw new Error('Document IPC handlers were not registered')

    expect(() => save(event, documentId, [])).toThrow('Invalid editable document save')
    expect(() => exportDocument(event, documentId, 'html')).toThrow('Invalid document export format')
    expect(client.save).not.toHaveBeenCalled()
    expect(client.exportGenerated).not.toHaveBeenCalled()
  })
})
