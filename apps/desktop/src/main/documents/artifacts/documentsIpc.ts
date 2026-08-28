import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { createMainDocumentsClient } from './documents.js'

type DocumentsClient = ReturnType<typeof createMainDocumentsClient>

export function registerDocumentsIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => DocumentsClient
): void {
  const artifactId = (value: unknown) => {
    if (typeof value !== 'string' || !/^art_[A-Za-z0-9_-]{16,80}$/.test(value)) throw new Error('Invalid artifact')
    return value
  }
  const jobId = (value: unknown) => {
    if (typeof value !== 'string' || !value || value.length > 512 || /[\\/]/.test(value)) throw new Error('Invalid job')
    return value
  }
  ipc.handle('jobos:documents:list', (event, id: string) => trusted(event).list(jobId(id)))
  ipc.handle('jobos:documents:refresh', (event, id: string) => trusted(event).refresh(jobId(id)))
  ipc.handle('jobos:documents:approve', (event, owner: string, id: string) => trusted(event).approve(jobId(owner), artifactId(id)))
  ipc.handle('jobos:documents:load-pdf', (event, id: string) => trusted(event).loadPdf(artifactId(id)))
  ipc.handle('jobos:documents:load-original-docx', (event, id: string) => trusted(event).loadOriginalDocx(artifactId(id)))
  ipc.handle('jobos:documents:export', (event, id: string) => trusted(event).exportArtifact(artifactId(id)))
  ipc.handle('jobos:documents:reveal', (event, id: string) => trusted(event).reveal(artifactId(id)))
  ipc.handle('jobos:documents:open', (event, id: string) => trusted(event).open(artifactId(id)))
}
