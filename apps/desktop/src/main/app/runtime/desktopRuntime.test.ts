// @vitest-environment node

import { expect, test } from 'vitest'

import type { ConnectivitySnapshot } from '../../../shared/contracts.js'
import { initializeDesktopRuntime } from './desktopRuntime.js'
import type { DesktopRuntimeConfig } from './runtimeConfig.js'

const runtime: DesktopRuntimeConfig = {
  schemaVersion: 1,
  mode: 'local-service',
  apiBaseUrl: 'http://127.0.0.1:8766',
  deviceId: 'mini-device',
  launchdLabel: 'com.cobibean.jobos.api'
}
const connected: ConnectivitySnapshot = {
  state: 'connected',
  apiVersion: '0.1.0',
  checkedAt: '2026-07-20T00:00:00Z',
  message: 'Private API authenticated'
}

test('resolves config and credential before ensuring API readiness', async () => {
  const order: string[] = []
  const state = await initializeDesktopRuntime({
    configPath: '/runtime.json',
    environment: {},
    loadConfig: async () => {
      order.push('config')
      return runtime
    },
    loadCredential: async options => {
      order.push(`credential:${options.deviceId}`)
      return 'device-token'
    },
    ensureApiReady: async (resolved, token) => {
      order.push(`ready:${resolved.mode}:${token}`)
      return connected
    }
  })

  expect(order).toEqual([
    'config',
    'credential:mini-device',
    'ready:local-service:device-token'
  ])
  expect(state).toEqual({ runtime, deviceToken: 'device-token', connectivity: connected })
})

test('configuration and credential failures become safe startup states', async () => {
  const configuration = await initializeDesktopRuntime({
    configPath: '/runtime.json',
    environment: {},
    loadConfig: async () => { throw new Error('JobOS runtime configuration is required') },
    loadCredential: async () => 'unused',
    ensureApiReady: async () => connected
  })
  const credential = await initializeDesktopRuntime({
    configPath: '/runtime.json',
    environment: {},
    loadConfig: async () => runtime,
    loadCredential: async () => { throw new Error('secret credential backend details') },
    ensureApiReady: async () => connected
  })

  expect(configuration).toMatchObject({
    runtime: null,
    deviceToken: null,
    connectivity: { state: 'disconnected', message: 'JobOS setup is required' }
  })
  expect(credential).toMatchObject({
    runtime,
    deviceToken: null,
    connectivity: { state: 'disconnected', message: 'JobOS device credential is unavailable' }
  })
  expect(JSON.stringify(credential)).not.toContain('backend details')
})
