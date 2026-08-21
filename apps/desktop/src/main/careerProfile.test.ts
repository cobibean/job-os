import { afterEach, describe, expect, it, vi } from 'vitest'

import { createMainCareerProfileClient } from './careerProfile.js'

const originalFetch = globalThis.fetch

const current = {
  profile_revision: 2,
  record: {
    actor_principal: 'primary-device',
    item_revision: 2,
    namespace: 'search_preferences.work_arrangement',
    profile_revision: 2,
    record_id: 'career_work_arrangement',
    updated_at: '2026-08-21T15:00:00Z',
    value: { mode: 'remote', strength: 'requirement', note: '(FAKE) Iowa-based role' }
  }
}

afterEach(() => {
  globalThis.fetch = originalFetch
  vi.restoreAllMocks()
})

function client() {
  return createMainCareerProfileClient({
    baseUrl: 'http://127.0.0.1:8766',
    deviceToken: 'test-device-token'
  })
}

describe('Career Profile desktop client', () => {
  it('reports the dormant feature without leaking an API error', async () => {
    globalThis.fetch = vi.fn(async () => new Response(
      JSON.stringify({ detail: 'Career Profile is not enabled' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } }
    )) as typeof fetch

    await expect(client().availability()).resolves.toEqual({ enabled: false })
  })

  it('maps the typed work-arrangement record and authenticates the request', async () => {
    globalThis.fetch = vi.fn(async (input, init) => {
      expect(String(input)).toBe('http://127.0.0.1:8766/v1/career-profile/work-arrangement')
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-device-token')
      return new Response(JSON.stringify(current), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as typeof fetch

    await expect(client().getWorkArrangement()).resolves.toMatchObject({
      profileRevision: 2,
      record: { value: { mode: 'remote', strength: 'requirement' } }
    })
  })

  it('binds offline cache data to this API and device credential', async () => {
    globalThis.fetch = vi.fn(async () => new Response(
      JSON.stringify(current),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )) as typeof fetch

    const primary = client()
    const protectedCurrent = await primary.getWorkArrangement()
    expect(protectedCurrent.cacheProof).toMatch(/^[a-f0-9]{64}$/)
    expect(primary.validateCachedWorkArrangement(protectedCurrent)).toEqual(protectedCurrent)
    expect(primary.validateCachedWorkArrangement({ profileRevision: 0, record: null })).toBeNull()
    expect(primary.validateCachedWorkArrangement({ ...protectedCurrent, profileRevision: 999 })).toBeNull()

    const otherRuntime = createMainCareerProfileClient({
      baseUrl: 'http://127.0.0.1:9999',
      deviceToken: 'different-test-device-token'
    })
    expect(otherRuntime.validateCachedWorkArrangement(protectedCurrent)).toBeNull()
  })

  it('returns the latest value on a stale save instead of overwriting it', async () => {
    globalThis.fetch = vi.fn(async (_input, init) => {
      if (init?.method === 'PUT') {
        return new Response(JSON.stringify({ detail: 'Career Profile revision conflict; current revision is 2' }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' }
        })
      }
      return new Response(JSON.stringify(current), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as typeof fetch

    await expect(client().saveWorkArrangement({
      expectedProfileRevision: 1,
      idempotencyKey: 'desktop-save-1',
      value: { mode: 'hybrid', strength: 'preference', note: null }
    })).resolves.toEqual(expect.objectContaining({ status: 'conflict', current: expect.objectContaining({ profileRevision: 2 }) }))
  })

  it('loads history and restores through the authenticated API', async () => {
    const fetchMock = vi.fn(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/history')) {
        return new Response(JSON.stringify({
          profile_revision: 2,
          revisions: [{
            actor_principal: 'primary-device', base_profile_revision: 1, changed_fields: ['mode'],
            created_at: '2026-08-21T15:00:00Z', item_revision: 2, operation: 'set',
            profile_revision: 2, record_id: 'career_work_arrangement', revision_id: 'rev_2',
            restored_from_profile_revision: null,
            value: { mode: 'remote', strength: 'requirement', note: null }
          }]
        }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      }
      expect(init?.method).toBe('POST')
      return new Response(JSON.stringify(current), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    globalThis.fetch = fetchMock as typeof fetch

    await expect(client().getWorkArrangementHistory()).resolves.toMatchObject({ profileRevision: 2 })
    await expect(client().restoreWorkArrangement({
      expectedProfileRevision: 2,
      idempotencyKey: 'desktop-undo-1',
      targetProfileRevision: 1
    })).resolves.toMatchObject({ status: 'saved' })
  })

  it('rejects contradictory flexible strength before sending it', async () => {
    const fetchMock = vi.fn()
    globalThis.fetch = fetchMock as typeof fetch

    await expect(client().saveWorkArrangement({
      expectedProfileRevision: 2,
      idempotencyKey: 'desktop-save-flexible',
      value: { mode: 'flexible', strength: 'dealbreaker', note: null }
    })).rejects.toThrow('Flexible work arrangement must use preference strength')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('rejects a restore target that the API cannot represent', async () => {
    const fetchMock = vi.fn()
    globalThis.fetch = fetchMock as typeof fetch

    await expect(client().restoreWorkArrangement({
      expectedProfileRevision: 2,
      idempotencyKey: 'desktop-undo-empty',
      targetProfileRevision: 0
    })).rejects.toThrow('Invalid Career Profile revision')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
