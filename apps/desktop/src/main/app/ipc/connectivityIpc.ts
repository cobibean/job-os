import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { DesktopRuntimeState } from '../runtime/desktopRuntime.js'

export function registerConnectivityIpc(
  ipc: Pick<IpcMain, 'handle'>,
  assertTrustedRenderer: (event: IpcMainInvokeEvent) => void,
  dependencies: {
    getState: () => DesktopRuntimeState
    setState: (state: DesktopRuntimeState) => void
    ensureApiReady: (runtime: NonNullable<DesktopRuntimeState['runtime']>, deviceToken: string) => Promise<DesktopRuntimeState['connectivity']>
    captureEnabled: boolean
  }
): void {
  ipc.handle('jobos:connectivity:get', async event => {
    assertTrustedRenderer(event)
    const current = dependencies.getState()
    const { runtime, deviceToken } = current
    if (!runtime || !deviceToken) return current.connectivity
    const connectivity = await dependencies.ensureApiReady(runtime, deviceToken)
    dependencies.setState({ runtime, deviceToken, connectivity })
    if (dependencies.captureEnabled) console.info('[JobOS capture] connectivity', JSON.stringify(connectivity))
    return connectivity
  })
}
