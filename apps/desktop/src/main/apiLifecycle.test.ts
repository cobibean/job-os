// @vitest-environment node

import { expect, test } from 'vitest'

import type { ConnectivitySnapshot } from '../shared/contracts.js'
import { createApiLifecycle } from './apiLifecycle.js'
import type { DesktopRuntimeConfig } from './runtimeConfig.js'

const localRuntime: DesktopRuntimeConfig = {
  schemaVersion: 1,
  mode: 'local-service',
  apiBaseUrl: 'http://127.0.0.1:8766',
  deviceId: 'mini-device',
  launchdLabel: 'com.cobibean.jobos.api'
}
const remoteRuntime: DesktopRuntimeConfig = {
  schemaVersion: 1,
  mode: 'remote-client',
  apiBaseUrl: 'https://jobos.private.example',
  deviceId: 'macbook-device'
}
const disconnected: ConnectivitySnapshot = {
  state: 'disconnected',
  checkedAt: '2026-07-20T00:00:00Z',
  message: 'JobOS API unavailable'
}
const connected: ConnectivitySnapshot = {
  state: 'connected',
  apiVersion: '0.1.0',
  checkedAt: '2026-07-20T00:00:01Z',
  message: 'Private API authenticated'
}

test('reuses a healthy local API without invoking launchd', async () => {
  const commands: string[][] = []
  const lifecycle = createApiLifecycle({
    probe: async () => connected,
    run: async (_file, arguments_) => { commands.push(arguments_) },
    sleep: async () => undefined,
    now: () => 0,
    uid: 501
  })

  await expect(lifecycle.ensureApiReady(localRuntime, 'device-token')).resolves.toEqual(connected)
  expect(commands).toEqual([])
})

test('starts one launchd service and waits for authenticated readiness', async () => {
  const results = [disconnected, disconnected, connected]
  const commands: Array<{ file: string, arguments: string[] }> = []
  let now = 0
  const lifecycle = createApiLifecycle({
    probe: async () => results.shift() ?? connected,
    run: async (file, arguments_) => { commands.push({ file, arguments: arguments_ }) },
    sleep: async milliseconds => { now += milliseconds },
    now: () => now,
    uid: 501,
    pollIntervalMs: 100,
    timeoutMs: 1_000
  })

  await expect(lifecycle.ensureApiReady(localRuntime, 'device-token')).resolves.toEqual(connected)
  expect(commands).toEqual([{
    file: '/bin/launchctl',
    arguments: ['kickstart', '-k', 'gui/501/com.cobibean.jobos.api']
  }])
})

test('remote clients and authentication failures never invoke local lifecycle', async () => {
  const commands: string[][] = []
  const degraded: ConnectivitySnapshot = {
    state: 'degraded',
    checkedAt: '2026-07-20T00:00:00Z',
    message: 'Device authentication failed'
  }
  const remote = createApiLifecycle({
    probe: async () => disconnected,
    run: async (_file, arguments_) => { commands.push(arguments_) },
    sleep: async () => undefined,
    now: () => 0,
    uid: 501
  })
  const local = createApiLifecycle({
    probe: async () => degraded,
    run: async (_file, arguments_) => { commands.push(arguments_) },
    sleep: async () => undefined,
    now: () => 0,
    uid: 501
  })

  await expect(remote.ensureApiReady(remoteRuntime, 'device-token')).resolves.toEqual(disconnected)
  await expect(local.ensureApiReady(localRuntime, 'wrong-token')).resolves.toEqual(degraded)
  expect(commands).toEqual([])
})

test('returns a bounded failure when launchd never becomes ready', async () => {
  let now = 0
  const lifecycle = createApiLifecycle({
    probe: async () => disconnected,
    run: async () => undefined,
    sleep: async milliseconds => { now += milliseconds },
    now: () => now,
    uid: 501,
    pollIntervalMs: 100,
    timeoutMs: 250
  })

  await expect(lifecycle.ensureApiReady(localRuntime, 'device-token')).resolves.toMatchObject({
    state: 'disconnected',
    message: 'Local JobOS API did not become ready'
  })
  expect(now).toBe(300)
})

test('concurrent readiness requests share one launchd kickstart', async () => {
  let release: (() => void) | undefined
  const started = new Promise<void>(resolve => { release = resolve })
  let probes = 0
  let commands = 0
  const lifecycle = createApiLifecycle({
    probe: async () => {
      probes += 1
      return probes === 1 ? disconnected : connected
    },
    run: async () => {
      commands += 1
      await started
    },
    sleep: async () => undefined,
    now: () => 0,
    uid: 501
  })

  const first = lifecycle.ensureApiReady(localRuntime, 'device-token')
  const second = lifecycle.ensureApiReady(localRuntime, 'device-token')
  release?.()

  await expect(Promise.all([first, second])).resolves.toEqual([connected, connected])
  expect(commands).toBe(1)
})

test('launchctl errors are classified without leaking command details', async () => {
  const secret = 'must-not-leak'
  const lifecycle = createApiLifecycle({
    probe: async () => disconnected,
    run: async () => { throw new Error(`launchctl output ${secret}`) },
    sleep: async () => undefined,
    now: () => 0,
    uid: 501
  })

  const result = await lifecycle.ensureApiReady(localRuntime, 'device-token')
  expect(result).toMatchObject({
    state: 'disconnected',
    message: 'Local JobOS API service could not be started'
  })
  expect(JSON.stringify(result)).not.toContain(secret)
})
