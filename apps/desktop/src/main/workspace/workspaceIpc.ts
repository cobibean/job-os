import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { WorkspaceSnapshot } from '../../shared/contracts.js'
import type { createMainWorkspaceClient } from './workspace.js'

type WorkspaceClient = ReturnType<typeof createMainWorkspaceClient>

export function registerWorkspaceIpc(
  ipc: Pick<IpcMain, 'handle'>,
  trusted: (event: IpcMainInvokeEvent) => WorkspaceClient
): void {
  ipc.handle('jobos:workspace:get', event => trusted(event).get())
  ipc.handle('jobos:workspace:save', (event, snapshot: WorkspaceSnapshot) => {
    if (!snapshot || typeof snapshot !== 'object' || !Number.isInteger(snapshot.revision)) {
      throw new Error('Invalid workspace snapshot')
    }
    return trusted(event).save(snapshot)
  })
  ipc.handle('jobos:workspace:save-document-view', (event, conversationId: string, artifactId: string | null, page: number, zoom: number) => {
    if (typeof conversationId !== 'string' || !/^conv_[A-Za-z0-9_-]{1,128}$/.test(conversationId)) throw new Error('Invalid agent conversation')
    if (artifactId !== null && (typeof artifactId !== 'string' || !/^art_[A-Za-z0-9_-]{16,80}$/.test(artifactId))) throw new Error('Invalid artifact')
    if (!Number.isInteger(page) || page < 1 || !Number.isFinite(zoom) || zoom < 0.5 || zoom > 3) throw new Error('Invalid document view')
    return trusted(event).saveDocumentView(conversationId, artifactId, page, zoom)
  })
}
