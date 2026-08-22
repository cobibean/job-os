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

  it('preserves every flexible strength and the 1000-character context boundary', async () => {
    const fetchMock = vi.fn(async (_input, init) => {
      const body = JSON.parse(String(init?.body))
      expect(body.value).toEqual({
        mode: 'flexible',
        strength: 'dealbreaker',
        note: '🙂'.repeat(1000)
      })
      return new Response(JSON.stringify({
        ...current,
        record: { ...current.record, value: body.value }
      }), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    globalThis.fetch = fetchMock as typeof fetch

    await expect(client().saveWorkArrangement({
      expectedProfileRevision: 2,
      idempotencyKey: 'desktop-save-flexible',
      value: { mode: 'flexible', strength: 'dealbreaker', note: '🙂'.repeat(1000) }
    })).resolves.toMatchObject({
      status: 'saved',
      current: { record: { value: { mode: 'flexible', strength: 'dealbreaker' } } }
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await expect(client().saveWorkArrangement({
      expectedProfileRevision: 3,
      idempotencyKey: 'desktop-save-oversized',
      value: { mode: 'flexible', strength: 'requirement', note: '🙂'.repeat(1001) }
    })).rejects.toThrow('Work arrangement note is too long')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('maps connected-agent review, exact decisions, history, and Undo through authenticated routes', async () => {
    const agent = {
      active: true,
      agent_id: 'job-hunter',
      connected_at: '2026-08-21T15:00:00Z',
      disconnected_at: null,
      display_name: 'Job Hunter',
      principal: 'agent:job-hunter',
      trust_mode: 'review',
      updated_at: '2026-08-21T15:00:00Z'
    }
    const item = {
      actor_principal: 'agent:job-hunter',
      area: 'my_career',
      created_at: '2026-08-21T15:00:00Z',
      evidence_ids: [],
      item_id: 'cpi_fakeproposalitem1234',
      item_revision: 1,
      provenance: { method: 'agent_edit', mutation_source: 'agent_inference' },
      review_status: 'accepted',
      updated_at: '2026-08-21T15:00:00Z',
      value: { kind: 'skill', name: 'TypeScript' }
    }
    const proposal = {
      after: item,
      agent_display_name: 'Job Hunter',
      agent_id: 'job-hunter',
      base_profile_revision: 2,
      before: null,
      created_at: '2026-08-21T15:00:00Z',
      evidence_ids: [],
      operation: 'item.create',
      proposal_id: 'cpp_fakeproposal123456',
      proposal_sha256: 'a'.repeat(64),
      reason: 'Add the skill you asked me to capture.',
      review_reason: 'This agent is set to Review every change.',
      status: 'pending',
      target_id: item.item_id
    }
    const revision = {
      actor_kind: 'autonomous_agent',
      actor_principal: 'agent:job-hunter',
      affected_fields: ['value'],
      after: item,
      base_profile_revision: 2,
      before: null,
      created_at: '2026-08-21T15:00:00Z',
      evidence_id: null,
      item_id: item.item_id,
      operation: 'item.upsert',
      profile_revision: 3,
      proposal_id: null,
      reason: proposal.reason,
      revision_id: 'cpv_fakedirectrevision1',
      undo_of_revision_id: null,
      undoable: true
    }

    const fetchMock = vi.fn(async (input, init) => {
      const url = String(input)
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-device-token')
      if (url.endsWith('/agents') && !init?.method) {
        return new Response(JSON.stringify({ agents: [agent] }), { status: 200 })
      }
      if (url.endsWith('/agents/job-hunter') && init?.method === 'PATCH') {
        expect(JSON.parse(String(init.body))).toEqual({ trust_mode: 'direct' })
        return new Response(JSON.stringify({ ...agent, trust_mode: 'direct' }), { status: 200 })
      }
      if (url.endsWith('/agents/job-hunter') && init?.method === 'DELETE') {
        return new Response(JSON.stringify({ ...agent, active: false }), { status: 200 })
      }
      if (url.endsWith('/proposals') && !init?.method) {
        return new Response(JSON.stringify({ proposals: [proposal] }), { status: 200 })
      }
      if (url.endsWith('/proposals/cpp_fakeproposal123456/decision')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          decision: 'accept',
          expected_profile_revision: 2,
          idempotency_key: 'proposal-decision-1',
          proposal_sha256: 'a'.repeat(64)
        })
        return new Response(JSON.stringify({
          profile: { authority_epoch: 1, items: [item], profile_revision: 3, source_evidence: [] },
          proposal: { ...proposal, status: 'accepted' }
        }), { status: 200 })
      }
      if (url.endsWith('/history') && !init?.method) {
        return new Response(JSON.stringify({ profile_revision: 3, revisions: [revision] }), { status: 200 })
      }
      if (url.endsWith('/history/cpv_fakedirectrevision1/undo')) {
        expect(JSON.parse(String(init?.body))).toEqual({
          expected_profile_revision: 3,
          idempotency_key: 'direct-undo-1'
        })
        return new Response(JSON.stringify({ authority_epoch: 1, items: [], profile_revision: 4, source_evidence: [] }), { status: 200 })
      }
      return new Response(null, { status: 404 })
    })
    globalThis.fetch = fetchMock as typeof fetch

    await expect(client().listConnectedAgents()).resolves.toMatchObject([{ agentId: 'job-hunter', trustMode: 'review' }])
    await expect(client().updateConnectedAgentTrustMode('job-hunter', 'direct')).resolves.toMatchObject({ trustMode: 'direct' })
    await expect(client().disconnectConnectedAgent('job-hunter')).resolves.toMatchObject({ active: false })
    await expect(client().listCareerProfileProposals()).resolves.toMatchObject([{
      agentDisplayName: 'Job Hunter', evidenceIds: [], proposalId: 'cpp_fakeproposal123456'
    }])
    await expect(client().decideCareerProfileProposal('cpp_fakeproposal123456', {
      decision: 'accept',
      expectedProfileRevision: 2,
      idempotencyKey: 'proposal-decision-1',
      proposalSha256: 'a'.repeat(64)
    })).resolves.toMatchObject({ profileRevision: 3, proposal: { status: 'accepted' } })
    await expect(client().getCareerProfileChangeHistory()).resolves.toMatchObject({
      profileRevision: 3,
      revisions: [{ actorKind: 'autonomous_agent', revisionId: 'cpv_fakedirectrevision1', undoable: true }]
    })
    await expect(client().undoCareerProfileChange('cpv_fakedirectrevision1', {
      expectedProfileRevision: 3,
      idempotencyKey: 'direct-undo-1'
    })).resolves.toEqual({ profileRevision: 4 })
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
