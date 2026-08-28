import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import { safeExternalUrl } from '../../../shared/externalLinks.js'

export function registerShellIpc(
  ipc: Pick<IpcMain, 'handle'>,
  assertTrustedRenderer: (event: IpcMainInvokeEvent) => void,
  openExternal: (url: string) => Promise<unknown>
): void {
  ipc.handle('jobos:shell:open-external', async (event, rawUrl: unknown) => {
    assertTrustedRenderer(event)
    const url = safeExternalUrl(rawUrl)
    if (!url) throw new Error('Invalid external link')
    await openExternal(url)
  })
}
