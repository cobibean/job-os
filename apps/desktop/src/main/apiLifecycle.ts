import { execFile } from 'node:child_process'

import type { ConnectivitySnapshot } from '../shared/contracts.js'
import { probeConnectivity } from './connectivity.js'
import type { DesktopRuntimeConfig } from './runtimeConfig.js'

interface LifecycleDependencies {
  probe: typeof probeConnectivity
  run: (file: string, arguments_: string[]) => Promise<void>
  sleep: (milliseconds: number) => Promise<void>
  now: () => number
  uid: number
  pollIntervalMs: number
  timeoutMs: number
}

export interface ApiLifecycle {
  ensureApiReady(runtime: DesktopRuntimeConfig, deviceToken: string): Promise<ConnectivitySnapshot>
}

function runCommand(file: string, arguments_: string[]): Promise<void> {
  return new Promise((resolve, reject) => {
    execFile(file, arguments_, { encoding: 'utf8', maxBuffer: 8192 }, error => {
      if (error) {
        reject(error)
        return
      }
      resolve()
    })
  })
}

function sleep(milliseconds: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

function failure(message: string): ConnectivitySnapshot {
  return {
    state: 'disconnected',
    checkedAt: new Date().toISOString(),
    message
  }
}

export function createApiLifecycle(
  overrides: Partial<LifecycleDependencies> = {}
): ApiLifecycle {
  const dependencies: LifecycleDependencies = {
    probe: probeConnectivity,
    run: runCommand,
    sleep,
    now: Date.now,
    uid: process.getuid?.() ?? 0,
    pollIntervalMs: 200,
    timeoutMs: 10_000,
    ...overrides
  }
  let inFlight: Promise<ConnectivitySnapshot> | null = null

  const ensure = async (
    runtime: DesktopRuntimeConfig,
    deviceToken: string
  ): Promise<ConnectivitySnapshot> => {
    const initial = await dependencies.probe({
      baseUrl: runtime.apiBaseUrl,
      deviceToken
    })
    if (initial.state !== 'disconnected' || runtime.mode === 'remote-client') return initial
    if (!runtime.launchdLabel) return failure('Local JobOS API service configuration is invalid')

    try {
      await dependencies.run('/bin/launchctl', [
        'kickstart',
        '-k',
        `gui/${dependencies.uid}/${runtime.launchdLabel}`
      ])
    } catch {
      return failure('Local JobOS API service could not be started')
    }

    const deadline = dependencies.now() + dependencies.timeoutMs
    do {
      await dependencies.sleep(dependencies.pollIntervalMs)
      const result = await dependencies.probe({
        baseUrl: runtime.apiBaseUrl,
        deviceToken
      })
      if (result.state !== 'disconnected') return result
    } while (dependencies.now() < deadline)

    return failure('Local JobOS API did not become ready')
  }

  return {
    ensureApiReady(runtime, deviceToken) {
      if (inFlight) return inFlight
      inFlight = ensure(runtime, deviceToken).finally(() => {
        inFlight = null
      })
      return inFlight
    }
  }
}
