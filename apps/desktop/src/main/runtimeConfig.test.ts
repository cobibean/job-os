// @vitest-environment node

import { expect, test } from 'vitest'

import { loadDesktopRuntimeConfig, runtimeConfigPath } from './runtimeConfig.js'

test('uses the stable JobOS Application Support directory', () => {
  expect(runtimeConfigPath('/Users/cobi/Library/Application Support')).toBe(
    '/Users/cobi/Library/Application Support/JobOS/runtime.json'
  )
})

const localConfig = JSON.stringify({
  schemaVersion: 1,
  mode: 'local-service',
  apiBaseUrl: 'http://127.0.0.1:8766',
  deviceId: 'mini-device',
  launchdLabel: 'com.cobibean.jobos.api'
})

test('development environment overrides persisted runtime configuration', async () => {
  let reads = 0
  const config = await loadDesktopRuntimeConfig({
    configPath: '/unused/runtime.json',
    environment: {
      JOBOS_RUNTIME_MODE: 'remote-client',
      JOBOS_API_BASE_URL: 'https://jobos.private.example',
      JOBOS_DEVICE_ID: 'development-device'
    },
    readText: async () => {
      reads += 1
      return localConfig
    }
  })

  expect(config).toEqual({
    schemaVersion: 1,
    mode: 'remote-client',
    apiBaseUrl: 'https://jobos.private.example',
    deviceId: 'development-device'
  })
  expect(reads).toBe(0)
})

test('loads validated local-service and remote-client runtime modes', async () => {
  const local = await loadDesktopRuntimeConfig({
    configPath: '/runtime.json',
    environment: {},
    readText: async () => localConfig
  })
  const remote = await loadDesktopRuntimeConfig({
    configPath: '/runtime.json',
    environment: {},
    readText: async () => JSON.stringify({
      schemaVersion: 1,
      mode: 'remote-client',
      apiBaseUrl: 'https://jobos.private.example',
      deviceId: 'macbook-device'
    })
  })

  expect(local).toEqual({
    schemaVersion: 1,
    mode: 'local-service',
    apiBaseUrl: 'http://127.0.0.1:8766',
    deviceId: 'mini-device',
    launchdLabel: 'com.cobibean.jobos.api'
  })
  expect(remote.mode).toBe('remote-client')
})

test.each([
  [{ ...JSON.parse(localConfig), extra: 'not allowed' }, 'unknown'],
  [{ ...JSON.parse(localConfig), apiBaseUrl: 'http://192.168.1.10:8766' }, 'loopback'],
  [{ ...JSON.parse(localConfig), apiBaseUrl: 'http://user:password@127.0.0.1:8766' }, 'credentials'],
  [{ ...JSON.parse(localConfig), apiBaseUrl: 'http://127.0.0.1:8766?token=secret' }, 'query'],
  [{ ...JSON.parse(localConfig), mode: 'remote-client', apiBaseUrl: 'http://jobos.private.example' }, 'HTTPS'],
  [{ ...JSON.parse(localConfig), mode: 'remote-client', apiBaseUrl: 'https://127.0.0.1:8766' }, 'Remote'],
  [{ ...JSON.parse(localConfig), deviceId: 'x'.repeat(129) }, 'device']
])('rejects unsafe or malformed persisted runtime configuration %#', async (value, message) => {
  await expect(loadDesktopRuntimeConfig({
    configPath: '/runtime.json',
    environment: {},
    readText: async () => JSON.stringify(value)
  })).rejects.toThrow(message)
})

test('missing persisted configuration produces an actionable error', async () => {
  const missing = Object.assign(new Error('missing'), { code: 'ENOENT' })
  await expect(loadDesktopRuntimeConfig({
    configPath: '/runtime.json',
    environment: {},
    readText: async () => { throw missing }
  })).rejects.toThrow('JobOS runtime configuration is required')
})
