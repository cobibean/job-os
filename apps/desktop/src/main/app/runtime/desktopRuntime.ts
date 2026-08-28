import type { ConnectivitySnapshot } from '../../../shared/contracts.js'
import type { ApiLifecycle } from './apiLifecycle.js'
import { loadDeviceCredential } from './credentialStore.js'
import { loadDesktopRuntimeConfig } from './runtimeConfig.js'
import type { DesktopRuntimeConfig } from './runtimeConfig.js'

export interface DesktopRuntimeState {
  runtime: DesktopRuntimeConfig | null
  deviceToken: string | null
  connectivity: ConnectivitySnapshot
}

interface InitializeOptions {
  configPath: string
  environment: Record<string, string | undefined>
  loadConfig?: typeof loadDesktopRuntimeConfig
  loadCredential?: typeof loadDeviceCredential
  ensureApiReady: ApiLifecycle['ensureApiReady']
}

function disconnected(message: string): ConnectivitySnapshot {
  return {
    state: 'disconnected',
    checkedAt: new Date().toISOString(),
    message
  }
}

export async function initializeDesktopRuntime(
  options: InitializeOptions
): Promise<DesktopRuntimeState> {
  let runtime: DesktopRuntimeConfig
  try {
    runtime = await (options.loadConfig ?? loadDesktopRuntimeConfig)({
      configPath: options.configPath,
      environment: options.environment
    })
  } catch {
    return {
      runtime: null,
      deviceToken: null,
      connectivity: disconnected('JobOS setup is required')
    }
  }

  let deviceToken: string
  try {
    deviceToken = await (options.loadCredential ?? loadDeviceCredential)({
      deviceId: runtime.deviceId,
      environment: options.environment,
      credentialStore: runtime.credentialStore,
      configPath: options.configPath
    })
  } catch {
    return {
      runtime,
      deviceToken: null,
      connectivity: disconnected('JobOS device credential is unavailable')
    }
  }

  try {
    const connectivity = await options.ensureApiReady(runtime, deviceToken)
    return { runtime, deviceToken, connectivity }
  } catch {
    return {
      runtime,
      deviceToken,
      connectivity: disconnected('JobOS host unavailable')
    }
  }
}
