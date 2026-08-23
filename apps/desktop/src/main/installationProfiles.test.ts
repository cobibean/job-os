// @vitest-environment node

import { describe, expect, it, vi } from 'vitest'

import {
  assertProfileSwitchDownloadSafe,
  createInstallationProfilesClient,
  resolveProfileStorageIdentity
} from './installationProfiles.js'

const PROFILE_A = 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const PROFILE_B = 'jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

function response(value: unknown, status = 200, headers?: HeadersInit): Response {
  return Response.json(value, { status, headers })
}

describe('installation profiles main client', () => {
  it('blocks active downloads before an activation request can be made', () => {
    expect(() => assertProfileSwitchDownloadSafe({ state: 'starting' })).toThrow(
      'Wait for the browser download to finish'
    )
    expect(() => assertProfileSwitchDownloadSafe({ state: 'progressing' })).toThrow()
    expect(() => assertProfileSwitchDownloadSafe({ state: 'completed' })).not.toThrow()
    expect(() => assertProfileSwitchDownloadSafe(null)).not.toThrow()
  })

  it('uses explicit anchored identity instead of timestamps or profile ordering', () => {
    const timestamp = '2026-08-23T12:00:00Z'
    const profiles = {
      registry_revision: 2,
      active_profile_id: PROFILE_B,
      anchored_profile_id: PROFILE_B,
      profiles: [
        { profile_id: PROFILE_A, display_name: 'Managed', active: false, created_at: timestamp, updated_at: timestamp },
        { profile_id: PROFILE_B, display_name: 'Personal', active: true, created_at: timestamp, updated_at: timestamp }
      ]
    }
    expect(resolveProfileStorageIdentity(profiles, PROFILE_B)).toEqual({
      kind: 'anchored',
      profileId: PROFILE_B
    })
  })

  it('uses the server-confirmed created profile identity', async () => {
    const timestamp = '2026-08-23T12:00:00Z'
    const profiles = {
      registry_revision: 2,
      active_profile_id: PROFILE_A,
      anchored_profile_id: PROFILE_A,
      profiles: [
        { profile_id: PROFILE_A, display_name: 'Personal', active: true, created_at: timestamp, updated_at: timestamp },
        { profile_id: PROFILE_B, display_name: 'Fresh setup', active: false, created_at: timestamp, updated_at: timestamp }
      ]
    }
    const client = createInstallationProfilesClient({
      baseUrl: 'http://jobos.test',
      deviceToken: 'device-token',
      installationProfileId: PROFILE_A,
      fetch: async () => response(
        profiles,
        201,
        { 'X-JobOS-Created-Profile-Id': PROFILE_B }
      )
    })

    await expect(client.create('Fresh setup', 'create-fresh')).resolves.toEqual({
      profiles,
      createdProfileId: PROFILE_B
    })
  })

  it('sends the pinned profile header and exposes lifecycle operations', async () => {
    const requests: Request[] = []
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(input, init)
      requests.push(request)
      if (request.method === 'POST') {
        return response({
          switch_id: 'jpswitch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          from_profile_id: PROFILE_A,
          to_profile_id: PROFILE_B,
          status: 'accepted'
        }, 202)
      }
      return response({
        registry_revision: 1,
        active_profile_id: PROFILE_A,
        anchored_profile_id: PROFILE_A,
        profiles: []
      })
    })
    const client = createInstallationProfilesClient({
      baseUrl: 'http://jobos.test',
      deviceToken: 'device-token',
      installationProfileId: PROFILE_A,
      fetch: fetcher
    })

    await client.list()
    await client.activate(PROFILE_B, 1, 'activate-b')

    expect(requests).toHaveLength(2)
    for (const request of requests) {
      expect(request.headers.get('authorization')).toBe('Bearer device-token')
      expect(request.headers.get('x-jobos-profile-id')).toBe(PROFILE_A)
    }
  })

  it('rejects stale successful completion for the wrong target', async () => {
    const client = createInstallationProfilesClient({
      baseUrl: 'http://jobos.test',
      deviceToken: 'device-token',
      fetch: async () => response({
        switch_id: 'jpswitch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        target_profile_id: PROFILE_A,
        status: 'succeeded',
        active_profile_id: PROFILE_A,
        error_code: null
      })
    })

    await expect(client.waitForTarget(
      'jpswitch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      PROFILE_B,
      { timeoutMs: 1 }
    )).rejects.toMatchObject({
      code: 'profile_switch_identity_mismatch'
    })
  })

  it('bounds polling and maps rollback without raw helper details', async () => {
    const client = createInstallationProfilesClient({
      baseUrl: 'http://jobos.test',
      deviceToken: 'device-token',
      fetch: async () => response({
        switch_id: 'jpswitch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        target_profile_id: PROFILE_B,
        status: 'rolled_back',
        active_profile_id: PROFILE_A,
        error_code: 'target_startup_failed'
      })
    })

    await expect(client.waitForTarget(
      'jpswitch_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      PROFILE_B
    )).rejects.toMatchObject({ code: 'target_startup_failed' })
  })
})
