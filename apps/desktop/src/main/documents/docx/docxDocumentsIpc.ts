import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { SaveDocxRequest } from '../../../shared/docxDocuments.js'
import type { DocumentKey } from '../../../shared/editableDocuments.js'
import type { DocxDocumentsService } from './docxDocuments.js'

function request(value: unknown): SaveDocxRequest {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid DOCX save')
  const typed = value as SaveDocxRequest
  if (!(typed.bytes instanceof ArrayBuffer) || typed.bytes.byteLength > 100 * 1024 * 1024) throw new Error('Invalid DOCX bytes')
  return typed
}

function bytes(value: unknown): ArrayBuffer {
  if (!(value instanceof ArrayBuffer) || value.byteLength > 100 * 1024 * 1024) throw new Error('Invalid DOCX bytes')
  return value
}

export function registerDocxDocumentsIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => DocxDocumentsService,
  artifacts?: {
    assertAvailable: () => void
    loadOriginalDocx: (artifactId: string) => Promise<{ filename: string, sha256: string, bytes: ArrayBuffer }>
  }
): void {
  ipc.handle('jobos:docx:list-bindings', (event, jobId: string) => trusted(event).listBindings(jobId))
  ipc.handle('jobos:docx:open-bound', (event, jobId: string, key: DocumentKey) => trusted(event).openBound(jobId, key))
  ipc.handle('jobos:docx:choose-file', (event, jobId: string, key: DocumentKey) => trusted(event).chooseFile(jobId, key))
  ipc.handle('jobos:docx:create-blank', (event, jobId: string, key: DocumentKey) => trusted(event).createBlank(jobId, key))
  ipc.handle('jobos:docx:reload', (event, bindingId: string) => trusted(event).reload(bindingId))
  ipc.handle('jobos:docx:save', (event, value: unknown) => trusted(event).save(request(value)))
  ipc.handle('jobos:docx:save-as', (event, bindingId: string, value: unknown) => trusted(event).saveAs(bindingId, bytes(value)))
  ipc.handle('jobos:docx:create-recovery', (event, bindingId: string, reason: 'baseline' | 'autosave' | 'manual' | 'conflict') => trusted(event).createRecovery(bindingId, reason))
  ipc.handle('jobos:docx:list-recoveries', (event, bindingId: string) => trusted(event).listRecoveries(bindingId))
  ipc.handle('jobos:docx:restore-recovery', (event, bindingId: string, recoveryId: string) => trusted(event).restoreRecovery(bindingId, recoveryId))
  ipc.handle('jobos:docx:unbind', (event, bindingId: string) => trusted(event).unbind(bindingId))
  if (artifacts) {
    ipc.handle('jobos:docx:open-artifact', async (event, owner: string, key: DocumentKey, id: string) => {
      const service = trusted(event)
      artifacts.assertAvailable()
      if (typeof id !== 'string' || !/^art_[A-Za-z0-9_-]{16,80}$/.test(id)) throw new Error('Invalid artifact')
      return service.openArtifact(owner, key, await artifacts.loadOriginalDocx(id))
    })
  }
}
