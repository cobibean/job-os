import path from 'node:path'

import type { IpcMain, IpcMainInvokeEvent } from 'electron'

import type { SetupSnapshot } from '../../../shared/contracts.js'

export function registerSetupIpc(
  ipc: Pick<IpcMain, 'handle'>,
  assertTrustedRenderer: (event: IpcMainInvokeEvent) => void,
  dependencies: {
    configPath: string
    configured: boolean
    runtimeCredentialAvailable: boolean
    retryConnection: () => Promise<boolean>
    runInitializer: (arguments_: string[]) => Promise<void>
    restart: () => void
  }
): void {
  let setupState: SetupSnapshot = dependencies.configured
    ? { state: 'ready', message: 'JobOS is configured' }
    : dependencies.runtimeCredentialAvailable
      ? { state: 'error', recovery: true, message: 'JobOS could not connect to the existing profile. Retry the connection or restart JobOS.' }
      : { state: 'required', message: 'JobOS setup or credential repair is required' }

  ipc.handle('jobos:setup:get', event => {
    assertTrustedRenderer(event)
    return setupState
  })
  ipc.handle('jobos:setup:initialize', async (event, resetDemo: unknown, confirmed: unknown) => {
    assertTrustedRenderer(event)
    if (dependencies.runtimeCredentialAvailable && (!dependencies.configured || !resetDemo)) return setupState
    if (resetDemo && confirmed !== true) return { state: 'error', message: 'Demo reset requires confirmation' }
    setupState = { state: 'working', message: resetDemo ? 'Resetting demo…' : 'Creating local profile…' }
    try {
      await dependencies.runInitializer([
        '--data-dir', path.dirname(dependencies.configPath),
        '--config-path', dependencies.configPath,
        ...(resetDemo ? ['--reset-demo', '--confirm-reset-demo'] : [])
      ])
      setupState = { state: 'succeeded', message: 'Setup complete. Restart JobOS to continue.' }
    } catch {
      setupState = { state: 'error', message: 'Setup could not finish. Check that the local JobOS tools are installed, then retry.' }
    }
    return setupState
  })
  let reconnecting = false
  ipc.handle('jobos:setup:retry-connection', async event => {
    assertTrustedRenderer(event)
    if (!dependencies.runtimeCredentialAvailable || dependencies.configured || reconnecting) return setupState
    reconnecting = true
    setupState = { state: 'working', recovery: true, message: 'Connecting to the existing JobOS profile…' }
    try {
      if (!await dependencies.retryConnection()) throw new Error('Profile unavailable')
      setupState = { state: 'succeeded', recovery: true, message: 'Connection restored. Restarting JobOS…' }
      dependencies.restart()
    } catch {
      setupState = { state: 'error', recovery: true, message: 'The existing JobOS profile is still unavailable. Retry the connection or restart JobOS.' }
    } finally { reconnecting = false }
    return setupState
  })
  ipc.handle('jobos:setup:restart', event => {
    assertTrustedRenderer(event)
    if (!dependencies.runtimeCredentialAvailable && setupState.state !== 'succeeded' && setupState.state !== 'ready') throw new Error('JobOS restart is unavailable until setup succeeds')
    dependencies.restart()
  })
}
