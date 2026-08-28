import { expect, test, vi } from 'vitest'

import { createProfileSwitchCoordinator } from './profileSwitchCoordinator.js'

function runtimeState() {
  return {
    runtime: { apiBaseUrl: 'http://127.0.0.1:8766', deviceId: 'device', mode: 'local-service' as const },
    deviceToken: 'token',
    connectivity: { state: 'connected' as const, checkedAt: '', installationProfileId: 'profile-old' }
  }
}

test('disables downloads before both preflights and relaunches only after exact identity confirmation', async () => {
  const order: string[] = []
  const browser = {
    setDownloadsAllowed: vi.fn((allowed: boolean) => order.push(`downloads:${allowed}`)),
    getBounds: vi.fn(() => ({ x: 1, y: 2, width: 3, height: 4, visible: true })),
    getState: vi.fn(() => { order.push('download-check'); return { download: null } }),
    setBounds: vi.fn(() => order.push('hide'))
  }
  const client = {
    activate: vi.fn(async () => { order.push('activate'); return { switch_id: 'switch-1', to_profile_id: 'profile-new' } })
  }
  const coordinator = createProfileSwitchCoordinator({
    getBrowserManager: () => browser as never,
    requestWorkspaceSafety: async () => { order.push('safety'); return true },
    getClient: () => client as never,
    getRuntimeState: runtimeState,
    apiLifecycle: { ensureApiReady: vi.fn() },
    sourceApi: { stop: vi.fn(async () => { order.push('stop-source') }), rollbackProfileSwitch: vi.fn() },
    probe: vi.fn(async () => ({ state: 'connected', checkedAt: '', installationProfileId: 'profile-new' })),
    relaunchAndQuit: () => order.push('relaunch')
  })
  await coordinator.complete({ profileId: 'profile-new', expectedRegistryRevision: 2, activationIdempotencyKey: 'activate-key' })
  expect(order).toEqual([
    'downloads:false', 'download-check', 'safety', 'download-check', 'hide',
    'activate', 'stop-source', 'relaunch', 'downloads:true'
  ])
})

test('rolls source runtime back in order and restores browser state on confirmation failure', async () => {
  const order: string[] = []
  const browser = {
    setDownloadsAllowed: vi.fn((allowed: boolean) => order.push(`downloads:${allowed}`)),
    getBounds: vi.fn(() => ({ x: 1, y: 2, width: 3, height: 4, visible: true })),
    getState: vi.fn(() => ({ download: null })),
    setBounds: vi.fn(bounds => order.push(bounds.visible === false ? 'hide' : 'restore-bounds'))
  }
  let nowCall = 0
  const coordinator = createProfileSwitchCoordinator({
    getBrowserManager: () => browser as never,
    requestWorkspaceSafety: async () => true,
    getClient: () => ({ activate: async () => ({ switch_id: 'switch-1', to_profile_id: 'profile-new' }) }) as never,
    getRuntimeState: runtimeState,
    apiLifecycle: { ensureApiReady: vi.fn(async () => { order.push('ensure-api'); return { state: 'disconnected', checkedAt: '' } }) },
    sourceApi: {
      stop: vi.fn(async () => { order.push('stop') }),
      rollbackProfileSwitch: vi.fn(async () => { order.push('rollback-registry') })
    },
    probe: vi.fn(async () => ({ state: 'disconnected', checkedAt: '' })),
    now: () => nowCall++ === 0 ? 0 : 20_001,
    sleep: vi.fn(async () => undefined),
    relaunchAndQuit: vi.fn()
  })
  await expect(coordinator.complete({ profileId: 'profile-new', expectedRegistryRevision: 2, activationIdempotencyKey: 'activate-key' }))
    .rejects.toThrow('JobOS stayed in the previous profile; no workspace data was changed.')
  expect(order).toEqual([
    'downloads:false', 'hide', 'stop', 'ensure-api', 'stop', 'rollback-registry',
    'ensure-api', 'restore-bounds', 'downloads:true'
  ])
})
