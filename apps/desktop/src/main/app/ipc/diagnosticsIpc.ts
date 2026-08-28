import path from 'node:path'

import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { DesktopRuntimeState } from '../runtime/desktopRuntime.js'

export function registerDiagnosticsIpc(
  ipc: Pick<IpcMain, 'handle'>,
  assertTrustedRenderer: (event: IpcMainInvokeEvent) => void,
  dependencies: {
    configPath: string
    getRuntimeState: () => DesktopRuntimeState
    getAppVersion: () => string
    isProfileSwitching: () => boolean
    isRendererAvailable: () => boolean
    openPath: (path: string) => Promise<unknown>
  }
): void {
  ipc.handle('jobos:diagnostics:get', event => {
    assertTrustedRenderer(event)
    const state = dependencies.getRuntimeState()
    const runtime = state.runtime
    return {
      mode: runtime?.mode ?? 'not-configured',
      appVersion: dependencies.getAppVersion(),
      ...(state.connectivity.apiVersion ? { apiVersion: state.connectivity.apiVersion } : {}),
      ...(state.connectivity.installationProfileId && state.connectivity.installationProfileName ? {
        installationProfile: {
          id: state.connectivity.installationProfileId,
          name: state.connectivity.installationProfileName,
          switchStatus: dependencies.isProfileSwitching() ? 'switching' : 'idle'
        }
      } : {}),
      capabilities: {
        localService: !runtime || runtime.mode !== 'local-service' ? 'not-configured' : state.connectivity.state === 'connected' ? 'available' : 'unavailable',
        agent: !runtime || state.connectivity.agent === 'not-configured' ? 'not-configured'
          : state.connectivity.agent === 'offline' ? 'offline' : state.connectivity.agent === 'connecting' ? 'connecting' : 'available',
        desktop: state.connectivity.desktop === 'connected' ? 'available' : 'disconnected',
        renderer: dependencies.isRendererAvailable() ? 'available' : 'unavailable',
        artifactStorage: state.connectivity.artifactStorage ?? 'unavailable',
        artifactGateway: state.connectivity.artifactGateway ?? 'not-configured',
        transport: state.connectivity.transport ?? 'not-configured'
      }
    }
  })
  ipc.handle('jobos:diagnostics:open-data', async event => {
    assertTrustedRenderer(event)
    await dependencies.openPath(path.dirname(dependencies.configPath))
  })
  ipc.handle('jobos:diagnostics:open-logs', async event => {
    assertTrustedRenderer(event)
    const configuredLogs = dependencies.getRuntimeState().runtime?.paths?.logs ?? 'logs'
    await dependencies.openPath(path.isAbsolute(configuredLogs)
      ? configuredLogs
      : path.resolve(path.dirname(dependencies.configPath), configuredLogs))
  })
}
