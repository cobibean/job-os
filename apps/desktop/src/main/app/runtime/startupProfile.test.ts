// @vitest-environment node
import { expect, test, vi } from 'vitest'

import type { DesktopRuntimeState } from './desktopRuntime.js'
import { resolveStartupProfile } from './startupProfile.js'

const profileId = 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const otherId = 'jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
const profiles = {
  active_profile_id: profileId, anchored_profile_id: profileId, registry_revision: 1,
  profiles: [{ profile_id: profileId, display_name: 'Synthetic', active: true, created_at: '', updated_at: '' }]
}
function state(mode: 'local-service' | 'remote-client'): DesktopRuntimeState {
  return {
    runtime: { schemaVersion: 1, mode, apiBaseUrl: 'http://127.0.0.1:18766', deviceId: 'synthetic' },
    deviceToken: 'synthetic-token',
    connectivity: { state: 'connected', checkedAt: '', message: 'Synthetic', installationProfileId: profileId }
  }
}

test.each(['local-service', 'remote-client'] as const)('resolves %s profile identity using the retained configuration and credential', async mode => {
  const createClient = vi.fn(() => ({ list: vi.fn().mockResolvedValue(profiles) }))
  await expect(resolveStartupProfile(state(mode), createClient)).resolves.toEqual({ kind: 'anchored', profileId })
  expect(createClient).toHaveBeenCalledWith({ baseUrl: 'http://127.0.0.1:18766', deviceToken: 'synthetic-token', installationProfileId: profileId })
})

test('list failure and a changed active identity retain recovery instead of opening storage', async () => {
  const createClient = () => ({ list: vi.fn().mockRejectedValue(new Error('Synthetic outage')) })
  await expect(resolveStartupProfile(state('local-service'), createClient)).resolves.toEqual({ kind: 'recovery' })
  await expect(resolveStartupProfile(state('local-service'), () => ({ list: vi.fn().mockResolvedValue({ ...profiles, active_profile_id: otherId }) }))).resolves.toEqual({ kind: 'recovery' })
})

test('an outage can recover with the same configuration only after exact profile verification', async () => {
  const current = state('local-service')
  current.connectivity = { state: 'disconnected', checkedAt: '', message: 'Synthetic outage' }
  const createClient = vi.fn(() => ({ list: vi.fn().mockResolvedValue(profiles) }))
  await expect(resolveStartupProfile(current, createClient)).resolves.toEqual({ kind: 'recovery' })
  expect(createClient).not.toHaveBeenCalled()
  current.connectivity = state('local-service').connectivity
  await expect(resolveStartupProfile(current, createClient)).resolves.toEqual({ kind: 'anchored', profileId })
})
