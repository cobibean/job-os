// @vitest-environment node

import { expect, test } from 'vitest'

import { loadDeviceCredential } from './credentialStore.js'

test('development credential override does not invoke Keychain', async () => {
  let calls = 0
  const credential = await loadDeviceCredential({
    deviceId: 'development-device',
    environment: { JOBOS_DEVICE_TOKEN: 'development-token' },
    run: async () => {
      calls += 1
      return { stdout: 'unexpected' }
    }
  })

  expect(credential).toBe('development-token')
  expect(calls).toBe(0)
})

test('reads the device credential from the fixed macOS Keychain service', async () => {
  const calls: Array<{ file: string, arguments: string[] }> = []
  const credential = await loadDeviceCredential({
    deviceId: 'mini-device',
    environment: {},
    helperPath: '/Applications/JobOS.app/Contents/Resources/jobos-keychain',
    run: async (file, arguments_) => {
      calls.push({ file, arguments: arguments_ })
      return { stdout: 'keychain-token\n' }
    }
  })

  expect(credential).toBe('keychain-token')
  expect(calls).toEqual([{
    file: '/Applications/JobOS.app/Contents/Resources/jobos-keychain',
    arguments: [
      'get',
      'com.cobibean.jobos.device-token',
      'mini-device'
    ]
  }])
})

test('missing or invalid Keychain values fail without exposing command output', async () => {
  const secret = 'credential-that-must-not-escape'
  await expect(loadDeviceCredential({
    deviceId: 'mini-device',
    environment: {},
    run: async () => { throw new Error(`security failed: ${secret}`) }
  })).rejects.toThrow('JobOS device credential is unavailable')

  try {
    await loadDeviceCredential({
      deviceId: 'mini-device',
      environment: {},
      run: async () => ({ stdout: `${'x'.repeat(4097)}\n` })
    })
  } catch (error) {
    expect(String(error)).not.toContain('x'.repeat(50))
    expect(String(error)).toContain('invalid')
  }
})
