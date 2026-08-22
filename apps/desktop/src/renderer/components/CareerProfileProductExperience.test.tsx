import { webcrypto } from 'node:crypto'

import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import type {
  CareerProfileBridge,
  CareerProfileCurrent,
  CareerProfileEvidence,
  CareerProfileItemSnapshot
} from '../../shared/contracts'
import { useCareerProfileProduct } from '../hooks/useCareerProfileProduct'
import { CareerProfileWorkspace } from './CareerProfileWorkspace'

const workArrangement = {
  profileRevision: 4,
  record: {
    actorPrincipal: 'primary-device',
    itemRevision: 2,
    profileRevision: 4,
    recordId: 'career_work_arrangement',
    updatedAt: '2026-08-21T15:00:00Z',
    value: { mode: 'remote' as const, strength: 'requirement' as const, note: '(FAKE) US remote' }
  }
}

const skill: CareerProfileItemSnapshot = {
  actorPrincipal: 'primary-device',
  area: 'my_career',
  createdAt: '2026-08-21T15:00:00Z',
  evidenceIds: [],
  itemId: 'cpi_fakeproductskill1234',
  itemRevision: 1,
  provenance: { method: 'user_entered', mutation_source: 'direct_user' },
  reviewStatus: 'accepted',
  updatedAt: '2026-08-21T15:00:00Z',
  value: { kind: 'skill', name: 'TypeScript', level: 'advanced', note: 'Shipping production interfaces.' }
}

const evidence: CareerProfileEvidence = {
  active: true,
  byteCount: 2048,
  capturedAt: null,
  evidenceId: 'cpe_fakeproductresume123',
  importedAt: '2026-08-21T15:00:00Z',
  mediaType: 'application/pdf',
  originalFilename: '(FAKE) Resume.pdf',
  provenance: { method: 'user_import', sourceKind: 'resume', sourceLabel: '(FAKE) Resume.pdf' },
  sha256: 'a'.repeat(64)
}

const complete: CareerProfileCurrent = {
  authorityEpoch: 2,
  items: [skill],
  profileRevision: 4,
  sourceEvidence: [evidence]
}

function savedProfile(overrides: Partial<CareerProfileCurrent>): CareerProfileCurrent {
  return { ...complete, ...overrides }
}

function bridge(overrides: Partial<CareerProfileBridge> = {}): CareerProfileBridge {
  return {
    availability: vi.fn().mockResolvedValue({ enabled: true }),
    validateCachedWorkArrangement: vi.fn().mockImplementation(async candidate => candidate),
    getWorkArrangement: vi.fn().mockResolvedValue(workArrangement),
    saveWorkArrangement: vi.fn().mockResolvedValue({ status: 'saved', current: workArrangement }),
    getWorkArrangementHistory: vi.fn().mockResolvedValue({ profileRevision: 4, revisions: [] }),
    restoreWorkArrangement: vi.fn().mockResolvedValue({ status: 'saved', current: workArrangement }),
    listConnectedAgents: vi.fn().mockResolvedValue([{
      active: true,
      agentId: 'job-hunter',
      connectedAt: '2026-08-21T15:00:00Z',
      disconnectedAt: null,
      displayName: 'Job Hunter',
      principal: 'agent:job-hunter',
      trustMode: 'review',
      updatedAt: '2026-08-21T15:00:00Z'
    }]),
    updateConnectedAgentTrustMode: vi.fn(),
    disconnectConnectedAgent: vi.fn(),
    listCareerProfileProposals: vi.fn().mockResolvedValue([]),
    decideCareerProfileProposal: vi.fn(),
    getCareerProfileChangeHistory: vi.fn().mockResolvedValue({ profileRevision: 4, revisions: [] }),
    undoCareerProfileChange: vi.fn(),
    getCareerProfile: vi.fn().mockResolvedValue(complete),
    createCareerProfileItem: vi.fn().mockResolvedValue({
      status: 'saved',
      current: savedProfile({ profileRevision: 5 })
    }),
    updateCareerProfileItem: vi.fn().mockResolvedValue({ status: 'saved', current: complete }),
    removeCareerProfileItem: vi.fn().mockResolvedValue({ status: 'saved', current: complete }),
    importCareerProfileEvidence: vi.fn().mockResolvedValue({
      status: 'saved',
      current: savedProfile({ profileRevision: 5 })
    }),
    removeCareerProfileEvidence: vi.fn().mockResolvedValue({ status: 'saved', current: complete }),
    getCareerProfileContext: vi.fn().mockResolvedValue({
      agentId: 'job-hunter',
      mode: 'none',
      selectedAreas: [],
      selectedItemIds: [],
      updatedAt: '2026-08-21T15:00:00Z'
    }),
    updateCareerProfileContext: vi.fn().mockResolvedValue({
      agentId: 'job-hunter',
      mode: 'selected',
      selectedAreas: ['my_career'],
      selectedItemIds: [],
      updatedAt: '2026-08-21T15:05:00Z'
    }),
    previewCareerProfileContext: vi.fn().mockResolvedValue({
      authorityEpoch: 2,
      contentHash: 'b'.repeat(64),
      createdAt: '2026-08-21T15:06:00Z',
      profile: { ...complete, sourceEvidence: [] },
      profileRevision: 4
    }),
    exportCareerProfile: vi.fn().mockResolvedValue({
      status: 'saved',
      byteCount: 1234,
      filename: 'JobOS-Career-Profile.zip',
      includedEvidenceIds: [],
      omittedEvidenceIds: [evidence.evidenceId],
      sha256: 'c'.repeat(64)
    }),
    chooseCareerProfileArchive: vi.fn().mockResolvedValue({
      archiveToken: 'cpa_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      byteCount: 1234,
      filename: '(FAKE) Career Profile.zip'
    }),
    restoreCareerProfile: vi.fn().mockResolvedValue({
      archiveSha256: 'd'.repeat(64),
      baselineCreated: true,
      profile: { ...complete, authorityEpoch: 3, profileRevision: 1 },
      restoredEvidenceIds: [evidence.evidenceId],
      unavailableEvidenceIds: []
    }),
    ...overrides
  }
}

beforeEach(() => {
  if (!globalThis.crypto.subtle) {
    Object.defineProperty(globalThis.crypto, 'subtle', {
      configurable: true,
      value: webcrypto.subtle
    })
  }
  const backing = new Map<string, string>()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      clear: () => { backing.clear() },
      getItem: (key: string) => backing.get(key) ?? null,
      key: (index: number) => Array.from(backing.keys())[index] ?? null,
      get length() { return backing.size },
      removeItem: (key: string) => { backing.delete(key) },
      setItem: (key: string, value: string) => { backing.set(key, String(value)) }
    }
  })
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.restoreAllMocks()
})

test('navigates all three real areas and explains item provenance without scoring the user', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })

  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  expect(await screen.findByRole('heading', { name: 'My Career' })).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /TypeScript/i }))
  const detail = await screen.findByRole('dialog', { name: 'TypeScript details' })
  expect(within(detail).getByText(/Added by you/i)).not.toBeNull()
  expect(within(detail).getByText(/No Evidence linked — that’s okay/i)).not.toBeNull()
  expect(screen.queryByText(/health score|readiness score|profile quality/i)).toBeNull()
  fireEvent.click(within(detail).getByRole('button', { name: 'Close details' }))

  fireEvent.click(screen.getByRole('button', { name: /My Evidence/i }))
  expect(await screen.findByRole('heading', { name: 'My Evidence' })).not.toBeNull()
  expect(screen.getByRole('button', { name: /Resume\.pdf/i })).not.toBeNull()
})

test('adds a typed career detail using accessible fields rather than JSON', async () => {
  const nextSkill = { ...skill, itemId: 'cpi_fakeproductskill5678', value: { kind: 'skill', name: 'React', level: 'expert' } }
  const createCareerProfileItem = vi.fn().mockResolvedValue({
    status: 'saved',
    current: savedProfile({ items: [skill, nextSkill], profileRevision: 5 })
  })
  const api = bridge({ createCareerProfileItem })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: 'Add career detail' }))

  const editor = screen.getByRole('dialog', { name: 'Add career detail' })
  fireEvent.change(within(editor).getByLabelText('Detail type'), { target: { value: 'skill' } })
  fireEvent.change(within(editor).getByLabelText('Skill name'), { target: { value: 'React' } })
  fireEvent.change(within(editor).getByLabelText('Level'), { target: { value: 'expert' } })
  fireEvent.click(within(editor).getByRole('button', { name: 'Save detail' }))

  await waitFor(() => expect(createCareerProfileItem).toHaveBeenCalledWith(expect.objectContaining({
    evidenceIds: [],
    expectedProfileRevision: 4,
    value: { kind: 'skill', name: 'React', level: 'expert' }
  })))
  expect(await screen.findByRole('button', { name: /React/i })).not.toBeNull()
  expect(screen.queryByText(/"kind"|JSON/i)).toBeNull()
})

test('imports Evidence with live recovery and retries the full original mutation identity after an ambiguous failure', async () => {
  const importedEvidence: CareerProfileEvidence = {
    ...evidence,
    evidenceId: 'cpe_fakeproductresume456',
    originalFilename: '(FAKE) New Resume.pdf',
    provenance: { ...evidence.provenance, sourceLabel: '(FAKE) New Resume.pdf' }
  }
  const supportingEvidence: CareerProfileEvidence = {
    ...evidence,
    evidenceId: 'cpe_fakeproductsupport456',
    originalFilename: '(FAKE) Supporting.txt',
    mediaType: 'text/plain',
    provenance: { ...evidence.provenance, sourceKind: 'supporting_document', sourceLabel: '(FAKE) Supporting.txt' }
  }
  let newResumeAttempts = 0
  const importCareerProfileEvidence = vi.fn().mockImplementation(async request => {
    if (request.originalFilename === '(FAKE) New Resume.pdf') {
      newResumeAttempts += 1
      if (newResumeAttempts === 1) throw new Error('response lost')
      return {
        status: 'saved',
        current: savedProfile({ profileRevision: 6, sourceEvidence: [evidence, supportingEvidence, importedEvidence] })
      }
    }
    return {
      status: 'saved',
      current: savedProfile({ profileRevision: 5, sourceEvidence: [evidence, supportingEvidence] })
    }
  })
  const api = bridge({ importCareerProfileEvidence })
  const view = render(<CareerProfileWorkspace active bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Evidence/i }))

  const file = new File([new Uint8Array([1, 2, 3])], '(FAKE) New Resume.pdf', { type: 'application/pdf' })
  Object.defineProperty(file, 'arrayBuffer', { value: vi.fn().mockResolvedValue(new Uint8Array([1, 2, 3]).buffer) })
  fireEvent.change(screen.getByLabelText('Choose Evidence files'), { target: { files: [file] } })

  const error = await screen.findByRole('alert', { name: /New Resume\.pdf import error/i })
  expect(error.textContent).toMatch(/could not be imported/i)
  const progress = screen.getByRole('region', { name: 'Evidence import progress' })
  expect(progress.getAttribute('aria-live')).toBe('polite')
  view.rerender(<CareerProfileWorkspace active={false} bridge={api} hasActiveTurn={false} />)
  view.rerender(<CareerProfileWorkspace active bridge={api} hasActiveTurn={false} />)
  expect(screen.getByText(/could not be imported/i)).not.toBeNull()

  const supportingFile = new File([new Uint8Array([4])], '(FAKE) Supporting.txt', { type: 'text/plain' })
  Object.defineProperty(supportingFile, 'arrayBuffer', { value: vi.fn().mockResolvedValue(new Uint8Array([4]).buffer) })
  fireEvent.change(screen.getByLabelText('Choose Evidence files'), { target: { files: [supportingFile] } })
  expect(await screen.findByText(/Imported .*Supporting\.txt/i)).not.toBeNull()

  const retry = screen.getByRole('button', { name: /Retry .*New Resume\.pdf/i })
  fireEvent.click(retry)
  await waitFor(() => expect(importCareerProfileEvidence).toHaveBeenCalledTimes(3))
  const originalRequest = importCareerProfileEvidence.mock.calls[0]?.[0]
  const retryRequest = importCareerProfileEvidence.mock.calls[2]?.[0]
  expect(retryRequest.expectedProfileRevision).toBe(originalRequest.expectedProfileRevision)
  expect(retryRequest.idempotencyKey).toBe(originalRequest.idempotencyKey)
  expect(await screen.findByText(/Imported .*New Resume\.pdf/i)).not.toBeNull()
})

test('makes agent context, export scope, and baseline restore explicit owner choices', async () => {
  const api = bridge()
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })

  fireEvent.click(screen.getByRole('button', { name: 'Agent access' }))
  const access = await screen.findByRole('dialog', { name: 'Agent Career Profile access' })
  const saveAccess = within(access).getByRole('button', { name: 'Save access' })
  await waitFor(() => expect(saveAccess.hasAttribute('disabled')).toBe(false))
  const savedScopePreview = within(access).getByRole('button', { name: 'Preview saved-scope context' })
  expect(savedScopePreview.hasAttribute('disabled')).toBe(false)
  fireEvent.click(within(access).getByLabelText(/Only selected details/i))
  expect(savedScopePreview.hasAttribute('disabled')).toBe(true)
  expect(within(access).getByText(/Save access before previewing/i)).not.toBeNull()
  fireEvent.click(within(access).getByLabelText('All of My Career'))
  fireEvent.click(saveAccess)
  await waitFor(() => expect(api.updateCareerProfileContext).toHaveBeenCalledWith('job-hunter', expect.objectContaining({
    expectedAuthorityEpoch: 2,
    expectedProfileRevision: 4,
    mode: 'selected',
    selectedAreas: ['my_career']
  })))
  await waitFor(() => expect(savedScopePreview.hasAttribute('disabled')).toBe(false))
  fireEvent.click(savedScopePreview)
  expect(await within(access).findByText(/Saved-scope preview created/i)).not.toBeNull()
  expect(within(access).getByText(/1 profile detail and 0 Evidence sources/i)).not.toBeNull()
  expect(within(access).getByText('TypeScript — My Career')).not.toBeNull()
  expect(within(access).getAllByRole('radio').every(option => option.getAttribute('name') === 'career-profile-context-mode')).toBe(true)
  fireEvent.click(within(access).getByRole('button', { name: 'Close access' }))

  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  const exportDialog = await screen.findByRole('dialog', { name: 'Export Career Profile' })
  const saveExport = within(exportDialog).getByRole('button', { name: 'Save export' })
  expect(saveExport.hasAttribute('disabled')).toBe(true)
  expect(within(exportDialog).getAllByRole('radio').every(option => !(option as HTMLInputElement).checked)).toBe(true)
  expect(within(exportDialog).getAllByRole('radio').every(option => option.getAttribute('name') === 'career-profile-export-evidence-mode')).toBe(true)
  fireEvent.click(within(exportDialog).getByLabelText(/Profile only/i))
  expect(saveExport.hasAttribute('disabled')).toBe(false)
  fireEvent.click(saveExport)
  await waitFor(() => expect(api.exportCareerProfile).toHaveBeenCalledWith({
    evidenceMode: 'profile_only', expectedProfileRevision: 4, selectedEvidenceIds: []
  }))
  fireEvent.click(within(exportDialog).getByRole('button', { name: 'Close export' }))

  fireEvent.click(screen.getByRole('button', { name: 'Restore baseline' }))
  const restore = await screen.findByRole('dialog', { name: 'Restore Career Profile baseline' })
  fireEvent.click(within(restore).getByRole('button', { name: 'Choose archive' }))
  expect(await within(restore).findByText('(FAKE) Career Profile.zip')).not.toBeNull()
  fireEvent.change(within(restore).getByLabelText('Type the restore confirmation'), {
    target: { value: 'RESTORE_CAREER_PROFILE_BASELINE' }
  })
  fireEvent.click(within(restore).getByRole('button', { name: 'Restore as new baseline' }))
  await waitFor(() => expect(api.restoreCareerProfile).toHaveBeenCalledWith(expect.objectContaining({
    archiveToken: 'cpa_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
    expectedProfileRevision: 4
  })))
  expect(await screen.findByText(/Baseline restored/i)).not.toBeNull()
})

test('blocks baseline restore while agent work is active', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Restore baseline' }))
  const restore = await screen.findByRole('dialog', { name: 'Restore Career Profile baseline' })
  expect(within(restore).getByText(/Finish or stop the active agent turn/i)).not.toBeNull()
  expect(within(restore).getByRole('button', { name: 'Choose archive' }).hasAttribute('disabled')).toBe(true)
})

test('preserves the latest item, proposed draft, and linked sources in a stale-edit conflict', async () => {
  const original = { ...skill, evidenceIds: [evidence.evidenceId] }
  const latest: CareerProfileItemSnapshot = {
    ...original,
    itemRevision: 2,
    updatedAt: '2026-08-21T15:10:00Z',
    value: { kind: 'skill', name: 'TypeScript', level: 'expert', note: 'Latest saved context.' }
  }
  const updateCareerProfileItem = vi.fn().mockResolvedValue({
    status: 'conflict',
    current: savedProfile({ items: [latest], profileRevision: 5 })
  })
  const api = bridge({
    getCareerProfile: vi.fn().mockResolvedValue(savedProfile({ items: [original] })),
    updateCareerProfileItem
  })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: /TypeScript details/i }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'TypeScript details' })).getByRole('button', { name: 'Edit detail' }))

  const editor = screen.getByRole('dialog', { name: 'Edit TypeScript' })
  fireEvent.change(within(editor).getByLabelText('Context'), { target: { value: 'My proposed context.' } })
  fireEvent.click(within(editor).getByRole('button', { name: 'Save detail' }))

  const conflict = await within(editor).findByRole('alert', { name: 'Resolve stale edit' })
  expect(conflict.getAttribute('aria-live')).toBe('assertive')
  expect(conflict.getAttribute('tabindex')).toBe('-1')
  expect(document.activeElement).toBe(conflict)
  expect(within(conflict).getByText('Latest saved context.')).not.toBeNull()
  expect(within(conflict).getByText('My proposed context.')).not.toBeNull()
  expect(within(conflict).getAllByText('(FAKE) Resume.pdf').length).toBeGreaterThan(0)
  expect(within(conflict).getByRole('button', { name: 'Keep current' })).not.toBeNull()
  expect(within(conflict).getByRole('button', { name: 'Reapply my change' })).not.toBeNull()
  expect(within(conflict).getByRole('button', { name: 'Preserve both' })).not.toBeNull()

  fireEvent.click(within(conflict).getByRole('button', { name: 'Keep current' }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Edit TypeScript' })).toBeNull())
  expect(screen.getByText('Latest saved context.')).not.toBeNull()
})

test('reapplies a stale draft against the latest revision and can preserve a parallel value', async () => {
  const latestRevisionFive: CareerProfileItemSnapshot = {
    ...skill,
    itemRevision: 2,
    value: { kind: 'skill', name: 'TypeScript', note: 'Latest revision five.' }
  }
  const latestRevisionSix: CareerProfileItemSnapshot = {
    ...latestRevisionFive,
    itemRevision: 3,
    value: { kind: 'skill', name: 'TypeScript', note: 'Latest revision six.' }
  }
  const updateCareerProfileItem = vi.fn()
    .mockResolvedValueOnce({ status: 'conflict', current: savedProfile({ items: [latestRevisionFive], profileRevision: 5 }) })
    .mockResolvedValueOnce({ status: 'conflict', current: savedProfile({ items: [latestRevisionSix], profileRevision: 6 }) })
  const createCareerProfileItem = vi.fn().mockResolvedValue({
    status: 'saved',
    current: savedProfile({ items: [latestRevisionSix, { ...skill, itemId: 'cpi_parallelproductski1', value: { kind: 'skill', name: 'TypeScript', note: 'Keep my draft.' } }], profileRevision: 7 })
  })
  const api = bridge({ updateCareerProfileItem, createCareerProfileItem })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: /TypeScript details/i }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'TypeScript details' })).getByRole('button', { name: 'Edit detail' }))
  const editor = screen.getByRole('dialog', { name: 'Edit TypeScript' })
  fireEvent.change(within(editor).getByLabelText('Context'), { target: { value: 'Keep my draft.' } })
  fireEvent.click(within(editor).getByRole('button', { name: 'Save detail' }))

  let conflict = await within(editor).findByRole('alert', { name: 'Resolve stale edit' })
  fireEvent.click(within(conflict).getByRole('button', { name: 'Reapply my change' }))
  await waitFor(() => expect(updateCareerProfileItem).toHaveBeenCalledTimes(2))
  expect(updateCareerProfileItem.mock.calls[1]?.[1]).toEqual(expect.objectContaining({
    expectedProfileRevision: 5,
    value: expect.objectContaining({ note: 'Keep my draft.' })
  }))

  conflict = await within(editor).findByRole('alert', { name: 'Resolve stale edit' })
  fireEvent.click(within(conflict).getByRole('button', { name: 'Preserve both' }))
  await waitFor(() => expect(createCareerProfileItem).toHaveBeenCalledWith(expect.objectContaining({
    expectedProfileRevision: 6,
    value: expect.objectContaining({ note: 'Keep my draft.' })
  })))
})

test('explains interpretation, example, and affected behavior for every search-preference kind', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Add preference' }))
  const editor = screen.getByRole('dialog', { name: 'Add preference' })
  const kinds = [
    ['target_roles', 'Target roles', /research, matching, and agent focus/i],
    ['compensation', 'Compensation', /matching and agent focus/i],
    ['location', 'Locations', /research, browsing, matching, and alerts/i],
    ['industries', 'Industries', /research, matching, and agent focus/i],
    ['priority', 'Priority', /ranking, matching, and agent focus/i],
    ['dealbreaker', 'Dealbreaker', /filtering, matching, and alerts/i]
  ] as const
  for (const [kind, label, effect] of kinds) {
    fireEvent.change(within(editor).getByLabelText('Detail type'), { target: { value: kind } })
    const guidance = within(editor).getByRole('region', { name: `${label} behavior` })
    expect(within(guidance).getByText('Interpretation')).not.toBeNull()
    expect(within(guidance).getByText('Example')).not.toBeNull()
    expect(within(guidance).getByText('Affects')).not.toBeNull()
    expect(guidance.textContent).toMatch(effect)
  }
})

test('parses list fields only at newlines so commas remain part of each value', async () => {
  const createCareerProfileItem = vi.fn().mockResolvedValue({ status: 'saved', current: savedProfile({ profileRevision: 5 }) })
  render(<CareerProfileWorkspace bridge={bridge({ createCareerProfileItem })} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Add preference' }))
  const editor = screen.getByRole('dialog', { name: 'Add preference' })
  fireEvent.change(within(editor).getByLabelText('Detail type'), { target: { value: 'location' } })
  fireEvent.change(within(editor).getByLabelText('Locations'), { target: { value: 'Austin, TX\nNew York, NY' } })
  fireEvent.click(within(editor).getByRole('button', { name: 'Save detail' }))
  await waitFor(() => expect(createCareerProfileItem).toHaveBeenCalledWith(expect.objectContaining({
    value: { kind: 'location', locations: ['Austin, TX', 'New York, NY'] }
  })))
})

test('requires a complete explicit Evidence export choice before enabling Save', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Export' }))
  const dialog = screen.getByRole('dialog', { name: 'Export Career Profile' })
  const save = within(dialog).getByRole('button', { name: 'Save export' })
  expect(save.hasAttribute('disabled')).toBe(true)
  fireEvent.click(within(dialog).getByLabelText('Selected Evidence'))
  expect(save.hasAttribute('disabled')).toBe(true)
  expect(within(dialog).getByRole('status').textContent).toMatch(/Select at least one Evidence source/i)
  fireEvent.click(within(dialog).getByLabelText('(FAKE) Resume.pdf'))
  expect(save.hasAttribute('disabled')).toBe(false)
})

test('makes selected and broader agent Evidence scope explicit without linked-source hitchhiking', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Agent access' }))
  const dialog = await screen.findByRole('dialog', { name: 'Agent Career Profile access' })
  expect(within(dialog).getByText(/Linked Evidence is not included unless you explicitly select My Evidence/i)).not.toBeNull()
  expect(within(dialog).getByText(/every active Evidence source/i)).not.toBeNull()
})

test('restores focus to the trigger after any product dialog closes', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  const trigger = screen.getByRole('button', { name: 'Export' })
  trigger.focus()
  fireEvent.click(trigger)
  const dialog = screen.getByRole('dialog', { name: 'Export Career Profile' })
  expect(dialog.contains(document.activeElement)).toBe(true)
  const backdrop = document.querySelector('.career-product-backdrop') as HTMLButtonElement
  expect(backdrop.getAttribute('aria-hidden')).toBe('true')
  expect(backdrop.tabIndex).toBe(-1)
  fireEvent.click(backdrop)
  await waitFor(() => expect(document.activeElement).toBe(trigger))
})

test('hard-disables Evidence picker change and drop behavior while offline', async () => {
  const importCareerProfileEvidence = vi.fn()
  render(<CareerProfileWorkspace bridge={bridge({ importCareerProfileEvidence })} hasActiveTurn={false} online={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Evidence/i }))
  const input = screen.getByLabelText('Choose Evidence files') as HTMLInputElement
  const dropzone = input.closest('label')!
  expect(input.disabled).toBe(true)
  expect(dropzone.getAttribute('aria-disabled')).toBe('true')
  const file = new File([new Uint8Array([1])], '(FAKE) Offline.pdf', { type: 'application/pdf' })
  Object.defineProperty(file, 'arrayBuffer', { value: vi.fn().mockResolvedValue(new Uint8Array([1]).buffer) })
  fireEvent.change(input, { target: { files: [file] } })
  fireEvent.drop(dropzone, { dataTransfer: { files: [file] } })
  expect(screen.queryByRole('region', { name: 'Evidence import progress' })).toBeNull()
  expect(importCareerProfileEvidence).not.toHaveBeenCalled()
})

test('distinguishes a confirmed Evidence conflict and starts a fresh latest-revision import only on request', async () => {
  const importCareerProfileEvidence = vi.fn()
    .mockResolvedValueOnce({ status: 'conflict', current: savedProfile({ profileRevision: 5 }) })
    .mockResolvedValueOnce({ status: 'saved', current: savedProfile({ profileRevision: 6 }) })
  render(<CareerProfileWorkspace bridge={bridge({ importCareerProfileEvidence })} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Evidence/i }))
  const file = new File([new Uint8Array([1])], '(FAKE) Conflict.pdf', { type: 'application/pdf' })
  Object.defineProperty(file, 'arrayBuffer', { value: vi.fn().mockResolvedValue(new Uint8Array([1]).buffer) })
  fireEvent.change(screen.getByLabelText('Choose Evidence files'), { target: { files: [file] } })
  const conflict = await screen.findByRole('alert', { name: /Conflict\.pdf import conflict/i })
  expect(conflict.textContent).toMatch(/profile changed first/i)
  fireEvent.click(screen.getByRole('button', { name: /Import .*Conflict\.pdf against latest profile/i }))
  await waitFor(() => expect(importCareerProfileEvidence).toHaveBeenCalledTimes(2))
  const first = importCareerProfileEvidence.mock.calls[0]?.[0]
  const second = importCareerProfileEvidence.mock.calls[1]?.[0]
  expect(first.expectedProfileRevision).toBe(4)
  expect(second.expectedProfileRevision).toBe(5)
  expect(second.idempotencyKey).not.toBe(first.idempotencyKey)
})

test('loads only an integrity-checked, schema-versioned complete-profile cache as read-only', async () => {
  const initial = renderHook(() => useCareerProfileProduct(bridge()))
  await waitFor(() => expect(initial.result.current.current?.profileRevision).toBe(4))
  await waitFor(() => expect(window.localStorage.length).toBe(1))
  const cacheKey = window.localStorage.key(0)!
  const validEnvelope = JSON.parse(window.localStorage.getItem(cacheKey)!) as Record<string, unknown>
  initial.unmount()

  const offlineApi = bridge({ getCareerProfile: vi.fn().mockRejectedValue(new Error('offline')) })
  const cached = renderHook(() => useCareerProfileProduct(offlineApi))
  await waitFor(() => expect(cached.result.current.current?.items[0]?.itemId).toBe(skill.itemId))
  expect(cached.result.current.readOnly).toBe(true)
  expect(cached.result.current.message).toMatch(/validated cached profile/i)
  cached.unmount()

  window.localStorage.setItem(cacheKey, JSON.stringify({ ...validEnvelope, schemaVersion: 999 }))
  const wrongSchema = renderHook(() => useCareerProfileProduct(offlineApi))
  await waitFor(() => expect(wrongSchema.result.current.status).toBe('error'))
  expect(wrongSchema.result.current.current).toBeNull()
  wrongSchema.unmount()

  const tampered = { ...validEnvelope, payload: String(validEnvelope.payload).replace('TypeScript', 'Tampered') }
  window.localStorage.setItem(cacheKey, JSON.stringify(tampered))
  const wrongIntegrity = renderHook(() => useCareerProfileProduct(offlineApi))
  await waitFor(() => expect(wrongIntegrity.result.current.status).toBe('error'))
  expect(wrongIntegrity.result.current.current).toBeNull()
})

test('preserves visible complete-profile content when a background refresh fails', async () => {
  const getCareerProfile = vi.fn()
    .mockResolvedValueOnce(complete)
    .mockRejectedValueOnce(new Error('refresh failed'))
  const api = bridge({ getCareerProfile })
  const product = renderHook(() => useCareerProfileProduct(api))
  await waitFor(() => expect(product.result.current.current?.profileRevision).toBe(4))
  await act(async () => { await product.result.current.load(false) })
  expect(product.result.current.current?.items[0]?.itemId).toBe(skill.itemId)
  expect(product.result.current.readOnly).toBe(true)
  expect(product.result.current.message).toMatch(/showing the last loaded profile/i)
})

test('refreshes the complete profile when the collaboration head revision advances', async () => {
  const revisionFive = savedProfile({ profileRevision: 5, items: [{ ...skill, itemRevision: 2, value: { kind: 'skill', name: 'React' } }] })
  const getCareerProfile = vi.fn()
    .mockResolvedValueOnce(complete)
    .mockResolvedValueOnce(revisionFive)
  const getCareerProfileChangeHistory = vi.fn()
    .mockResolvedValueOnce({ profileRevision: 4, revisions: [] })
    .mockResolvedValueOnce({ profileRevision: 5, revisions: [] })
  const api = bridge({ getCareerProfile, getCareerProfileChangeHistory })
  const product = renderHook(() => useCareerProfileProduct(api))
  await waitFor(() => expect(product.result.current.current?.profileRevision).toBe(4))
  window.dispatchEvent(new Event('focus'))
  await waitFor(() => expect(product.result.current.current?.profileRevision).toBe(5))
  expect(product.result.current.current?.items[0]?.value.name).toBe('React')
})

test('keeps the editor opening revision so a background refresh produces an explicit conflict', async () => {
  const revisionFiveItem: CareerProfileItemSnapshot = {
    ...skill,
    itemRevision: 2,
    updatedAt: '2026-08-21T15:10:00Z',
    value: { kind: 'skill', name: 'TypeScript', level: 'expert', note: 'Saved elsewhere.' }
  }
  const revisionFive = savedProfile({ items: [revisionFiveItem], profileRevision: 5 })
  const getCareerProfile = vi.fn()
    .mockResolvedValueOnce(complete)
    .mockResolvedValueOnce(revisionFive)
  let collaborationHeadRevision = 4
  const getCareerProfileChangeHistory = vi.fn().mockImplementation(async () => ({
    profileRevision: collaborationHeadRevision,
    revisions: []
  }))
  const updateCareerProfileItem = vi.fn().mockResolvedValue({ status: 'conflict', current: revisionFive })
  const api = bridge({ getCareerProfile, getCareerProfileChangeHistory, updateCareerProfileItem })

  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: /TypeScript details/i }))
  fireEvent.click(within(screen.getByRole('dialog', { name: 'TypeScript details' })).getByRole('button', { name: 'Edit detail' }))
  const editor = screen.getByRole('dialog', { name: 'Edit TypeScript' })
  fireEvent.change(within(editor).getByLabelText('Context'), { target: { value: 'My still-open draft.' } })

  await waitFor(() => expect(getCareerProfileChangeHistory).toHaveBeenCalled())
  await act(async () => { await Promise.resolve() })
  collaborationHeadRevision = 5
  window.dispatchEvent(new Event('focus'))
  await waitFor(() => expect(getCareerProfile).toHaveBeenCalledTimes(2))
  fireEvent.click(within(editor).getByRole('button', { name: 'Save detail' }))

  await waitFor(() => expect(updateCareerProfileItem).toHaveBeenCalledTimes(1))
  expect(updateCareerProfileItem.mock.calls[0]?.[1]).toEqual(expect.objectContaining({
    expectedProfileRevision: 4,
    value: expect.objectContaining({ note: 'My still-open draft.' })
  }))
  expect(await within(editor).findByRole('alert', { name: 'Resolve stale edit' })).not.toBeNull()
})

test('fails closed when a different agent scope cannot load', async () => {
  const secondAgent = {
    active: true,
    agentId: 'application-agent',
    connectedAt: '2026-08-21T15:00:00Z',
    disconnectedAt: null,
    displayName: 'Application Agent',
    principal: 'agent:application-agent',
    trustMode: 'review' as const,
    updatedAt: '2026-08-21T15:00:00Z'
  }
  const updateCareerProfileContext = vi.fn()
  const api = bridge({
    listConnectedAgents: vi.fn().mockResolvedValue([
      ...(await bridge().listConnectedAgents()),
      secondAgent
    ]),
    getCareerProfileContext: vi.fn().mockImplementation(async agentId => {
      if (agentId === secondAgent.agentId) throw new Error('scope unavailable')
      return {
        agentId: 'job-hunter',
        mode: 'selected',
        selectedAreas: ['my_career'],
        selectedItemIds: [],
        updatedAt: '2026-08-21T15:00:00Z'
      }
    }),
    updateCareerProfileContext
  })

  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Agent access' }))
  const access = await screen.findByRole('dialog', { name: 'Agent Career Profile access' })
  await waitFor(() => expect(within(access).getByRole('button', { name: 'Save access' }).hasAttribute('disabled')).toBe(false))
  fireEvent.change(within(access).getByLabelText('Connected agent'), { target: { value: secondAgent.agentId } })

  expect((await within(access).findByRole('alert')).textContent).toMatch(/could not load/i)
  expect(within(access).getByRole('button', { name: 'Save access' }).hasAttribute('disabled')).toBe(true)
  expect(within(access).getByRole('button', { name: 'Preview saved-scope context' }).hasAttribute('disabled')).toBe(true)
  expect(within(access).getAllByRole('radio').every(option => option.closest('fieldset')?.hasAttribute('disabled'))).toBe(true)
  expect(updateCareerProfileContext).not.toHaveBeenCalled()
})

test('retries an ambiguous agent-scope save with one identity and rotates it only after the draft changes', async () => {
  const updateCareerProfileContext = vi.fn().mockRejectedValue(new Error('response lost'))
  const api = bridge({ updateCareerProfileContext })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Agent access' }))
  const access = await screen.findByRole('dialog', { name: 'Agent Career Profile access' })
  const save = within(access).getByRole('button', { name: 'Save access' })
  await waitFor(() => expect(save.hasAttribute('disabled')).toBe(false))
  fireEvent.click(within(access).getByLabelText(/Only selected details/i))
  fireEvent.click(within(access).getByLabelText('All of My Career'))

  fireEvent.click(save)
  await waitFor(() => expect(updateCareerProfileContext).toHaveBeenCalledTimes(1))
  fireEvent.click(save)
  await waitFor(() => expect(updateCareerProfileContext).toHaveBeenCalledTimes(2))
  const firstKey = updateCareerProfileContext.mock.calls[0]?.[1].idempotencyKey
  const retryKey = updateCareerProfileContext.mock.calls[1]?.[1].idempotencyKey
  expect(retryKey).toBe(firstKey)

  fireEvent.click(within(access).getByLabelText(/Broader accepted profile/i))
  fireEvent.click(save)
  await waitFor(() => expect(updateCareerProfileContext).toHaveBeenCalledTimes(3))
  expect(updateCareerProfileContext.mock.calls[2]?.[1].idempotencyKey).not.toBe(firstKey)
})

test('shows truthful item state, saved preference guidance, and Evidence provenance', async () => {
  const proposedSkill: CareerProfileItemSnapshot = { ...skill, reviewStatus: 'proposed' }
  const location: CareerProfileItemSnapshot = {
    ...skill,
    area: 'what_im_looking_for',
    itemId: 'cpi_fakeproductlocation1',
    value: { kind: 'location', locations: ['Des Moines, IA'], relocation: 'no', strength: 'preference' }
  }
  const agentEvidence: CareerProfileEvidence = {
    ...evidence,
    provenance: { ...evidence.provenance, method: 'agent_import' }
  }
  render(<CareerProfileWorkspace bridge={bridge({
    getCareerProfile: vi.fn().mockResolvedValue(savedProfile({ items: [proposedSkill, location], sourceEvidence: [agentEvidence] }))
  })} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })

  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: /TypeScript details/i }))
  let detail = screen.getByRole('dialog', { name: 'TypeScript details' })
  expect(within(detail).getByText(/Proposed · Revision 1/i)).not.toBeNull()
  fireEvent.click(within(detail).getByRole('button', { name: 'Close details' }))

  fireEvent.click(screen.getByRole('button', { name: /What I’m Looking For/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Des Moines, IA details/i }))
  detail = screen.getByRole('dialog', { name: 'Des Moines, IA details' })
  const guidance = within(detail).getByRole('region', { name: 'Locations behavior' })
  expect(within(guidance).getByText('Interpretation')).not.toBeNull()
  expect(within(guidance).getByText('Example')).not.toBeNull()
  expect(within(guidance).getByText('Affects')).not.toBeNull()
  fireEvent.click(within(detail).getByRole('button', { name: 'Close details' }))

  fireEvent.click(screen.getByRole('button', { name: /My Evidence/i }))
  fireEvent.click(await screen.findByRole('button', { name: /Resume\.pdf details/i }))
  expect(within(screen.getByRole('dialog', { name: /Resume\.pdf details/i })).getByText('Imported by a connected agent')).not.toBeNull()
})

test('changing the chosen restore archive clears confirmation and starts a new mutation identity', async () => {
  const firstArchive = {
    archiveToken: 'cpa_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    byteCount: 1234,
    filename: '(FAKE) First Career Profile.zip'
  }
  const secondArchive = {
    archiveToken: 'cpa_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    byteCount: 2345,
    filename: '(FAKE) Second Career Profile.zip'
  }
  const chooseCareerProfileArchive = vi.fn()
    .mockResolvedValueOnce(firstArchive)
    .mockResolvedValueOnce(secondArchive)
  const restoreCareerProfile = vi.fn().mockRejectedValue(new Error('response lost'))
  const api = bridge({ chooseCareerProfileArchive, restoreCareerProfile })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: 'Restore baseline' }))
  const restore = await screen.findByRole('dialog', { name: 'Restore Career Profile baseline' })
  const choose = within(restore).getByRole('button', { name: 'Choose archive' })
  const confirmation = within(restore).getByLabelText('Type the restore confirmation') as HTMLInputElement

  fireEvent.click(choose)
  expect(await within(restore).findByText(firstArchive.filename)).not.toBeNull()
  fireEvent.change(confirmation, { target: { value: 'RESTORE_CAREER_PROFILE_BASELINE' } })
  fireEvent.click(within(restore).getByRole('button', { name: 'Restore as new baseline' }))
  await waitFor(() => expect(restoreCareerProfile).toHaveBeenCalledTimes(1))
  const firstKey = restoreCareerProfile.mock.calls[0]?.[0].idempotencyKey

  fireEvent.click(choose)
  expect(await within(restore).findByText(secondArchive.filename)).not.toBeNull()
  expect(confirmation.value).toBe('')
  expect(within(restore).getByRole('button', { name: 'Restore as new baseline' }).hasAttribute('disabled')).toBe(true)
  fireEvent.change(confirmation, { target: { value: 'RESTORE_CAREER_PROFILE_BASELINE' } })
  fireEvent.click(within(restore).getByRole('button', { name: 'Restore as new baseline' }))
  await waitFor(() => expect(restoreCareerProfile).toHaveBeenCalledTimes(2))
  expect(restoreCareerProfile.mock.calls[1]?.[0].idempotencyKey).not.toBe(firstKey)
})

test('reloads the authoritative complete profile after restore and retries an uncertain outcome with the same identity', async () => {
  const restoredSkill: CareerProfileItemSnapshot = {
    ...skill,
    itemRevision: 2,
    updatedAt: '2026-08-21T16:00:00Z',
    value: { kind: 'skill', name: 'React', level: 'expert' }
  }
  const authoritativeRestored = savedProfile({ authorityEpoch: 3, items: [restoredSkill], profileRevision: 1 })
  const getCareerProfile = vi.fn()
    .mockResolvedValueOnce(complete)
    .mockRejectedValueOnce(new Error('refresh lost'))
    .mockResolvedValue(authoritativeRestored)
  const restoreCareerProfile = vi.fn().mockResolvedValue({
    archiveSha256: 'd'.repeat(64),
    baselineCreated: true,
    profile: savedProfile({ authorityEpoch: 3, items: [], profileRevision: 1 }),
    restoredEvidenceIds: [],
    unavailableEvidenceIds: []
  })
  const api = bridge({ getCareerProfile, restoreCareerProfile })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  await waitFor(() => expect(window.localStorage.getItem('jobos.careerProfile.complete.v1')).not.toBeNull())

  fireEvent.click(screen.getByRole('button', { name: 'Restore baseline' }))
  const dialog = await screen.findByRole('dialog', { name: 'Restore Career Profile baseline' })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Choose archive' }))
  await within(dialog).findByText('(FAKE) Career Profile.zip')
  fireEvent.change(within(dialog).getByLabelText('Type the restore confirmation'), {
    target: { value: 'RESTORE_CAREER_PROFILE_BASELINE' }
  })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Restore as new baseline' }))

  const uncertain = await within(dialog).findByRole('alert')
  expect(uncertain.textContent).toMatch(/outcome is uncertain/i)
  expect(window.localStorage.getItem('jobos.careerProfile.complete.v1')).toBeNull()
  const firstKey = restoreCareerProfile.mock.calls[0]?.[0].idempotencyKey
  const retryRestore = screen.getByRole('button', { name: 'Retry restore' })
  expect(retryRestore.hasAttribute('disabled')).toBe(false)
  fireEvent.click(retryRestore)

  await waitFor(() => expect(restoreCareerProfile).toHaveBeenCalledTimes(2))
  expect(restoreCareerProfile.mock.calls[1]?.[0].idempotencyKey).toBe(firstKey)
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Restore Career Profile baseline' })).toBeNull())
  expect(getCareerProfile).toHaveBeenCalledTimes(3)
  expect(api.getWorkArrangement).toHaveBeenCalledTimes(3)
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  expect(await screen.findByRole('button', { name: /React details/i })).not.toBeNull()
  expect(screen.queryByRole('button', { name: /TypeScript details/i })).toBeNull()
})

test('invalidates persistent complete-profile data after Evidence erasure', async () => {
  const erased = savedProfile({ profileRevision: 5, sourceEvidence: [] })
  const api = bridge({
    removeCareerProfileEvidence: vi.fn().mockResolvedValue({ status: 'saved', current: erased })
  })
  const product = renderHook(() => useCareerProfileProduct(api))
  await waitFor(() => expect(product.result.current.status).toBe('ready'))
  await waitFor(() => expect(window.localStorage.length).toBe(1))

  await act(async () => { await product.result.current.removeEvidence(evidence.evidenceId) })
  expect(product.result.current.current?.sourceEvidence).toEqual([])
  expect(window.localStorage.length).toBe(0)
  product.unmount()

  const offline = renderHook(() => useCareerProfileProduct(bridge({
    getCareerProfile: vi.fn().mockRejectedValue(new Error('offline'))
  })))
  await waitFor(() => expect(offline.result.current.status).toBe('error'))
  expect(offline.result.current.current).toBeNull()
})

test('resolves an open detail by ID against the latest profile and opens editing at the matching revision', async () => {
  const latestSkill: CareerProfileItemSnapshot = {
    ...skill,
    itemRevision: 2,
    updatedAt: '2026-08-21T16:00:00Z',
    value: { kind: 'skill', name: 'React', level: 'expert', note: 'Latest saved detail.' }
  }
  const latest = savedProfile({ items: [latestSkill], profileRevision: 5 })
  let headRevision = 4
  const getCareerProfile = vi.fn().mockResolvedValueOnce(complete).mockResolvedValue(latest)
  const updateCareerProfileItem = vi.fn().mockResolvedValue({ status: 'saved', current: latest })
  const api = bridge({
    getCareerProfile,
    getCareerProfileChangeHistory: vi.fn().mockImplementation(async () => ({ profileRevision: headRevision, revisions: [] })),
    updateCareerProfileItem
  })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: /TypeScript details/i }))

  headRevision = 5
  window.dispatchEvent(new Event('focus'))
  const latestDialog = await screen.findByRole('dialog', { name: 'React details' })
  expect(within(latestDialog).getByText('Latest saved detail.')).not.toBeNull()
  fireEvent.click(within(latestDialog).getByRole('button', { name: 'Edit detail' }))
  const editor = screen.getByRole('dialog', { name: 'Edit React' })
  fireEvent.click(within(editor).getByRole('button', { name: 'Save detail' }))

  await waitFor(() => expect(updateCareerProfileItem).toHaveBeenCalledWith(
    skill.itemId,
    expect.objectContaining({ expectedProfileRevision: 5, value: expect.objectContaining({ name: 'React' }) })
  ))
})

test('shows a truthful missing state when an open detail disappears during refresh', async () => {
  let headRevision = 4
  const api = bridge({
    getCareerProfile: vi.fn().mockResolvedValueOnce(complete).mockResolvedValue(savedProfile({ items: [], profileRevision: 5 })),
    getCareerProfileChangeHistory: vi.fn().mockImplementation(async () => ({ profileRevision: headRevision, revisions: [] }))
  })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  fireEvent.click(await screen.findByRole('button', { name: /TypeScript details/i }))

  headRevision = 5
  window.dispatchEvent(new Event('focus'))
  const missing = await screen.findByRole('dialog', { name: 'Career detail no longer available' })
  expect(within(missing).getByRole('alert').textContent).toMatch(/no longer in the current Career Profile/i)
  expect(screen.queryByRole('dialog', { name: 'TypeScript details' })).toBeNull()
})

test('credits imported Evidence before the primary-device actor fallback', async () => {
  const importedSkill: CareerProfileItemSnapshot = {
    ...skill,
    provenance: { method: 'evidence_import', mutation_source: 'direct_user' }
  }
  render(<CareerProfileWorkspace bridge={bridge({
    getCareerProfile: vi.fn().mockResolvedValue(savedProfile({ items: [importedSkill] }))
  })} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })
  fireEvent.click(screen.getByRole('button', { name: /My Career/i }))
  expect(await screen.findByText('Added from imported Evidence')).not.toBeNull()
  expect(screen.queryByText('Added by you')).toBeNull()
})
