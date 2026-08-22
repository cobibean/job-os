import { createHash } from 'node:crypto'
import { constants } from 'node:fs'
import { appendFile, mkdtemp, open, readFile, readdir, rm, stat, symlink, truncate, writeFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
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

const emptyCompleteProfile = {
  authority_epoch: 4,
  items: [],
  profile_revision: 7,
  source_evidence: []
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

  it('sends exact connected-agent context choices and maps the immutable preview', async () => {
    const scope = {
      agent_id: 'job-hunter',
      mode: 'selected',
      selected_areas: ['my_career'],
      selected_item_ids: ['cpi_abcdefghijklmnop'],
      updated_at: '2026-08-21T16:00:00Z'
    }
    const fetchMock = vi.fn(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/context') && init?.method === 'PUT') {
        expect(JSON.parse(String(init.body))).toEqual({
          expected_authority_epoch: 4,
          expected_profile_revision: 7,
          idempotency_key: 'context-choice-1',
          mode: 'selected',
          selected_areas: ['my_career'],
          selected_item_ids: ['cpi_abcdefghijklmnop']
        })
        return new Response(JSON.stringify(scope), { status: 200 })
      }
      if (url.endsWith('/context/preview')) {
        expect(init?.method).toBe('POST')
        return new Response(JSON.stringify({
          agent_id: 'job-hunter',
          authority_epoch: 4,
          content_hash: 'a'.repeat(64),
          created_at: '2026-08-21T16:00:00Z',
          profile_revision: 7,
          projection: emptyCompleteProfile,
          scope
        }), { status: 200 })
      }
      return new Response(JSON.stringify(scope), { status: 200 })
    })
    globalThis.fetch = fetchMock as typeof fetch

    await expect(client().getCareerProfileContext('job-hunter')).resolves.toMatchObject({
      mode: 'selected',
      selectedAreas: ['my_career']
    })
    await expect(client().updateCareerProfileContext('job-hunter', {
      expectedAuthorityEpoch: 4,
      expectedProfileRevision: 7,
      idempotencyKey: 'context-choice-1',
      mode: 'selected',
      selectedAreas: ['my_career'],
      selectedItemIds: ['cpi_abcdefghijklmnop']
    })).resolves.toMatchObject({ selectedItemIds: ['cpi_abcdefghijklmnop'] })
    await expect(client().previewCareerProfileContext('job-hunter')).resolves.toMatchObject({
      contentHash: 'a'.repeat(64),
      profile: { authorityEpoch: 4, profileRevision: 7 }
    })
  })

  it('atomically replaces an export through a private no-follow sibling and syncs its directory', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-export-'))
    try {
      const target = path.join(directory, 'verified-profile.zip')
      const bytes = Buffer.from('(FAKE) bounded Career Profile ZIP')
      const digest = createHash('sha256').update(bytes).digest('hex')
      await writeFile(target, '(FAKE) previous export')
      const chooseExportPath = vi.fn(async () => target)
      let directorySyncs = 0
      const openArchiveFile = vi.fn(async (filePath: string, flags: number, mode?: number) => {
        const handle = await open(filePath, flags, mode)
        if (filePath === directory) {
          const syncDirectory = handle.sync.bind(handle)
          Object.defineProperty(handle, 'sync', {
            configurable: true,
            value: async () => {
              directorySyncs += 1
              await syncDirectory()
            }
          })
        }
        return handle
      })
      const fetchMock = vi.fn(async (_input, init) => {
        expect(JSON.parse(String(init?.body))).toEqual({
          evidence_mode: 'selected',
          expected_profile_revision: 7,
          selected_evidence_ids: ['cpe_abcdefghijklmnop']
        })
        return new Response(JSON.stringify({
          byte_count: bytes.length,
          content_base64: bytes.toString('base64'),
          filename: 'career-profile.zip',
          included_evidence_ids: ['cpe_abcdefghijklmnop'],
          omitted_evidence_ids: [],
          sha256: digest
        }), { status: 200 })
      })
      globalThis.fetch = fetchMock as typeof fetch
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => null,
        chooseExportPath
      }, {
        archiveFileSystem: { open: openArchiveFile }
      })

      await expect(nativeClient.exportCareerProfile({
        evidenceMode: 'selected',
        expectedProfileRevision: 7,
        selectedEvidenceIds: ['cpe_abcdefghijklmnop']
      })).resolves.toEqual({
        status: 'saved',
        byteCount: bytes.length,
        filename: 'verified-profile.zip',
        includedEvidenceIds: ['cpe_abcdefghijklmnop'],
        omittedEvidenceIds: [],
        sha256: digest
      })
      expect(await readFile(target)).toEqual(bytes)
      expect(chooseExportPath).toHaveBeenCalledWith('career-profile.zip')
      const temporaryOpen = openArchiveFile.mock.calls.find(call => call[2] === 0o600)
      expect(temporaryOpen).toBeDefined()
      const [temporaryPath, flags, mode] = temporaryOpen!
      expect(path.dirname(temporaryPath)).toBe(directory)
      expect(temporaryPath).not.toBe(target)
      expect(flags & constants.O_CREAT).toBe(constants.O_CREAT)
      expect(flags & constants.O_EXCL).toBe(constants.O_EXCL)
      if (typeof constants.O_NOFOLLOW === 'number') {
        expect(flags & constants.O_NOFOLLOW).toBe(constants.O_NOFOLLOW)
      }
      expect(mode).toBe(0o600)
      if (process.platform !== 'win32') expect((await stat(target)).mode & 0o777).toBe(0o600)
      expect((await readdir(directory)).sort()).toEqual(['verified-profile.zip'])
      if (process.platform !== 'win32') expect(directorySyncs).toBe(1)
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('cleans an interrupted temporary export without touching the existing target', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-interrupted-export-'))
    try {
      const target = path.join(directory, 'career-profile.zip')
      const previousBytes = Buffer.from('(FAKE) previous complete export')
      const bytes = Buffer.from('(FAKE) replacement Career Profile ZIP')
      await writeFile(target, previousBytes)
      globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
        byte_count: bytes.length,
        content_base64: bytes.toString('base64'),
        filename: 'career-profile.zip',
        included_evidence_ids: [],
        omitted_evidence_ids: [],
        sha256: createHash('sha256').update(bytes).digest('hex')
      }), { status: 200 })) as typeof fetch
      const openArchiveFile = async (filePath: string, flags: number, mode?: number) => {
        const handle = await open(filePath, flags, mode)
        if (mode === 0o600) {
          const writeTemporary = handle.writeFile.bind(handle)
          Object.defineProperty(handle, 'writeFile', {
            configurable: true,
            value: async (data: string | Uint8Array) => {
              const partial = Buffer.from(data).subarray(0, 5)
              await writeTemporary(partial)
              throw new Error('simulated interrupted write')
            }
          })
        }
        return handle
      }
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => null,
        chooseExportPath: async () => target
      }, {
        archiveFileSystem: { open: openArchiveFile }
      })

      await expect(nativeClient.exportCareerProfile({
        evidenceMode: 'profile_only',
        expectedProfileRevision: 7,
        selectedEvidenceIds: []
      })).rejects.toThrow('simulated interrupted write')
      expect(await readFile(target)).toEqual(previousBytes)
      expect(await readdir(directory)).toEqual(['career-profile.zip'])
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('verifies the temporary export bytes before replacing an existing target', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-corrupt-export-'))
    try {
      const target = path.join(directory, 'career-profile.zip')
      const previousBytes = Buffer.from('(FAKE) previous complete export')
      const bytes = Buffer.from('(FAKE) replacement Career Profile ZIP')
      await writeFile(target, previousBytes)
      globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
        byte_count: bytes.length,
        content_base64: bytes.toString('base64'),
        filename: 'career-profile.zip',
        included_evidence_ids: [],
        omitted_evidence_ids: [],
        sha256: createHash('sha256').update(bytes).digest('hex')
      }), { status: 200 })) as typeof fetch
      const openArchiveFile = async (filePath: string, flags: number, mode?: number) => {
        const handle = await open(filePath, flags, mode)
        if (mode === 0o600) {
          const writeTemporary = handle.writeFile.bind(handle)
          Object.defineProperty(handle, 'writeFile', {
            configurable: true,
            value: async (data: string | Uint8Array) => {
              const corrupted = Buffer.from(data)
              corrupted[0] = (corrupted[0] ?? 0) ^ 0xff
              await writeTemporary(corrupted)
            }
          })
        }
        return handle
      }
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => null,
        chooseExportPath: async () => target
      }, {
        archiveFileSystem: { open: openArchiveFile }
      })

      await expect(nativeClient.exportCareerProfile({
        evidenceMode: 'profile_only',
        expectedProfileRevision: 7,
        selectedEvidenceIds: []
      })).rejects.toThrow('integrity')
      expect(await readFile(target)).toEqual(previousBytes)
      expect(await readdir(directory)).toEqual(['career-profile.zip'])
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('rejects malformed export integrity metadata before asking where to save', async () => {
    const chooseExportPath = vi.fn(async () => '/unused')
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
      byte_count: 4,
      content_base64: Buffer.from('fake').toString('base64'),
      filename: 'career-profile.zip',
      included_evidence_ids: [],
      omitted_evidence_ids: [],
      sha256: 'short'
    }), { status: 200 })) as typeof fetch
    const nativeClient = createMainCareerProfileClient({
      baseUrl: 'http://127.0.0.1:8766',
      deviceToken: 'test-device-token'
    }, {
      chooseArchivePath: async () => null,
      chooseExportPath
    })

    await expect(nativeClient.exportCareerProfile({
      evidenceMode: 'profile_only',
      expectedProfileRevision: 7,
      selectedEvidenceIds: []
    })).rejects.toThrow('integrity')
    expect(chooseExportPath).not.toHaveBeenCalled()
  })

  it('fstats and reads an archive through one no-follow descriptor', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-descriptor-'))
    try {
      const archivePath = path.join(directory, 'career-profile.zip')
      const bytes = Buffer.from('(FAKE) same descriptor restore archive')
      await writeFile(archivePath, bytes)
      let descriptorReads = 0
      let descriptorStats = 0
      const openArchiveFile = vi.fn(async (filePath: string, flags: number, mode?: number) => {
        const handle = await open(filePath, flags, mode)
        if (filePath === archivePath) {
          const statDescriptor = handle.stat.bind(handle)
          const readDescriptor = handle.read.bind(handle)
          Object.defineProperty(handle, 'stat', {
            configurable: true,
            value: (options: { bigint: true }) => {
              descriptorStats += 1
              return statDescriptor(options)
            }
          })
          Object.defineProperty(handle, 'read', {
            configurable: true,
            value: (buffer: Buffer, offset: number, length: number, position: number) => {
              descriptorReads += 1
              return readDescriptor(buffer, offset, length, position)
            }
          })
        }
        return handle
      })
      const fetchMock = vi.fn(async (_input, init) => {
        expect(JSON.parse(String(init?.body)).archive_base64).toBe(bytes.toString('base64'))
        return new Response(JSON.stringify({
          archive_sha256: createHash('sha256').update(bytes).digest('hex'),
          baseline_created: true,
          profile: { ...emptyCompleteProfile, profile_revision: 1 },
          restored_evidence_ids: [],
          unavailable_evidence_ids: []
        }), { status: 200 })
      })
      globalThis.fetch = fetchMock as typeof fetch
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => archivePath,
        chooseExportPath: async () => null
      }, {
        archiveFileSystem: { open: openArchiveFile }
      })

      const selection = await nativeClient.chooseCareerProfileArchive()
      expect(selection).toMatchObject({ byteCount: bytes.length, filename: 'career-profile.zip' })
      const archiveOpens = openArchiveFile.mock.calls.filter(call => call[0] === archivePath)
      expect(archiveOpens).toHaveLength(1)
      if (typeof constants.O_NOFOLLOW === 'number') {
        expect(archiveOpens[0]![1] & constants.O_NOFOLLOW).toBe(constants.O_NOFOLLOW)
      }
      expect(descriptorStats).toBeGreaterThanOrEqual(2)
      expect(descriptorReads).toBeGreaterThan(0)
      await expect(nativeClient.restoreCareerProfile({
        archiveToken: selection!.archiveToken,
        confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
        expectedProfileRevision: 7,
        idempotencyKey: 'same-descriptor-1'
      })).resolves.toMatchObject({ baselineCreated: true })
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('rejects an archive that changes while its descriptor is being read', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-changing-'))
    try {
      const archivePath = path.join(directory, 'career-profile.zip')
      const bytes = Buffer.from('(FAKE) changing restore archive')
      await writeFile(archivePath, bytes)
      let changed = false
      const openArchiveFile = async (filePath: string, flags: number, mode?: number) => {
        const handle = await open(filePath, flags, mode)
        if (filePath === archivePath) {
          const readDescriptor = handle.read.bind(handle)
          Object.defineProperty(handle, 'read', {
            configurable: true,
            value: async (buffer: Buffer, offset: number, length: number, position: number) => {
              const result = await readDescriptor(buffer, offset, length, position)
              if (!changed && result.bytesRead > 0) {
                changed = true
                await appendFile(archivePath, '!')
              }
              return result
            }
          })
        }
        return handle
      }
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => archivePath,
        chooseExportPath: async () => null
      }, {
        archiveFileSystem: { open: openArchiveFile }
      })

      await expect(nativeClient.chooseCareerProfileArchive()).rejects.toThrow('changed while it was being read')
      expect(changed).toBe(true)
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('rejects non-regular and oversized restore selections from descriptor metadata', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-invalid-'))
    try {
      const oversizedPath = path.join(directory, 'oversized.zip')
      await writeFile(oversizedPath, '')
      await truncate(oversizedPath, (100 * 1024 * 1024) + 1)
      for (const selectedPath of [directory, oversizedPath]) {
        const nativeClient = createMainCareerProfileClient({
          baseUrl: 'http://127.0.0.1:8766',
          deviceToken: 'test-device-token'
        }, {
          chooseArchivePath: async () => selectedPath,
          chooseExportPath: async () => null
        })
        await expect(nativeClient.chooseCareerProfileArchive()).rejects.toThrow('regular')
      }
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('keeps restore archive bytes in main, rejects symlinks, and consumes a successful token', async () => {
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-restore-'))
    try {
      const archivePath = path.join(directory, 'career-profile.zip')
      const symlinkPath = path.join(directory, 'linked-profile.zip')
      const bytes = Buffer.from('(FAKE) restore archive')
      await writeFile(archivePath, bytes)
      await symlink(archivePath, symlinkPath)

      const symlinkClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => symlinkPath,
        chooseExportPath: async () => null
      })
      await expect(symlinkClient.chooseCareerProfileArchive()).rejects.toThrow('regular')

      const fetchMock = vi.fn(async (_input, init) => {
        const body = JSON.parse(String(init?.body))
        expect(body).toEqual({
          archive_base64: bytes.toString('base64'),
          confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
          expected_profile_revision: 7,
          idempotency_key: 'restore-archive-1'
        })
        expect(JSON.stringify(body)).not.toContain(archivePath)
        return new Response(JSON.stringify({
          archive_sha256: createHash('sha256').update(bytes).digest('hex'),
          baseline_created: true,
          profile: { ...emptyCompleteProfile, profile_revision: 1 },
          restored_evidence_ids: [],
          unavailable_evidence_ids: []
        }), { status: 200 })
      })
      globalThis.fetch = fetchMock as typeof fetch
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766',
        deviceToken: 'test-device-token'
      }, {
        chooseArchivePath: async () => archivePath,
        chooseExportPath: async () => null
      })
      const selection = await nativeClient.chooseCareerProfileArchive()
      expect(selection).toMatchObject({ byteCount: bytes.length, filename: 'career-profile.zip' })
      expect(selection?.archiveToken).toMatch(/^cpa_[a-f0-9]{32}$/)
      const request = {
        archiveToken: selection!.archiveToken,
        confirmation: 'RESTORE_CAREER_PROFILE_BASELINE' as const,
        expectedProfileRevision: 7,
        idempotencyKey: 'restore-archive-1'
      }
      await expect(nativeClient.restoreCareerProfile(request)).resolves.toMatchObject({
        baselineCreated: true,
        profile: { profileRevision: 1 }
      })
      await expect(nativeClient.restoreCareerProfile(request)).rejects.toThrow('expired')
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally {
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('expires pending restore bytes on schedule without another archive operation', async () => {
    vi.useFakeTimers()
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-expiry-'))
    try {
      const archivePath = path.join(directory, 'career-profile.zip')
      await writeFile(archivePath, '(FAKE) expiring restore archive')
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766', deviceToken: 'test-device-token'
      }, { chooseArchivePath: async () => archivePath, chooseExportPath: async () => null })
      const selection = await nativeClient.chooseCareerProfileArchive()
      await vi.advanceTimersByTimeAsync(30 * 60 * 1000)
      await expect(nativeClient.restoreCareerProfile({
        archiveToken: selection!.archiveToken,
        confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
        expectedProfileRevision: 7,
        idempotencyKey: 'expired-archive-1'
      })).rejects.toThrow('expired')
    } finally {
      vi.useRealTimers()
      await rm(directory, { force: true, recursive: true })
    }
  })

  it('cancels the scheduled expiry when restore consumes the archive', async () => {
    vi.useFakeTimers()
    const directory = await mkdtemp(path.join(os.tmpdir(), 'jobos-career-profile-consumed-timer-'))
    try {
      const archivePath = path.join(directory, 'career-profile.zip')
      const bytes = Buffer.from('(FAKE) consumed restore archive')
      await writeFile(archivePath, bytes)
      globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({
        archive_sha256: createHash('sha256').update(bytes).digest('hex'),
        baseline_created: true,
        profile: { ...emptyCompleteProfile, profile_revision: 1 },
        restored_evidence_ids: [],
        unavailable_evidence_ids: []
      }), { status: 200 })) as typeof fetch
      const nativeClient = createMainCareerProfileClient({
        baseUrl: 'http://127.0.0.1:8766', deviceToken: 'test-device-token'
      }, { chooseArchivePath: async () => archivePath, chooseExportPath: async () => null })
      const selection = await nativeClient.chooseCareerProfileArchive()
      expect(vi.getTimerCount()).toBe(1)
      await nativeClient.restoreCareerProfile({
        archiveToken: selection!.archiveToken,
        confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
        expectedProfileRevision: 7,
        idempotencyKey: 'consumed-archive-1'
      })
      expect(vi.getTimerCount()).toBe(0)
    } finally {
      vi.useRealTimers()
      await rm(directory, { force: true, recursive: true })
    }
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
