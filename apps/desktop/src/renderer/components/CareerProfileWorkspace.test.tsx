import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { CareerProfileBridge } from '../../shared/contracts'
import { CareerProfileWorkspace } from './CareerProfileWorkspace'

const localCache = new Map<string, string>()
Object.defineProperty(window, 'localStorage', {
  configurable: true,
  value: {
    clear: () => localCache.clear(),
    getItem: (key: string) => localCache.get(key) ?? null,
    removeItem: (key: string) => localCache.delete(key),
    setItem: (key: string, value: string) => { localCache.set(key, value) }
  }
})

const current = {
  profileRevision: 2,
  record: {
    actorPrincipal: 'primary-device', itemRevision: 2, profileRevision: 2,
    recordId: 'career_work_arrangement', updatedAt: '2026-08-21T15:00:00Z',
    value: { mode: 'remote' as const, strength: 'requirement' as const, note: '(FAKE) Remote in the US' }
  }
}

const completeCurrent = {
  authorityEpoch: 1,
  items: [],
  profileRevision: 2,
  sourceEvidence: []
}

function bridge(overrides: Partial<CareerProfileBridge> = {}): CareerProfileBridge {
  return {
    availability: vi.fn().mockResolvedValue({ enabled: true }),
    validateCachedWorkArrangement: vi.fn().mockImplementation(async candidate => candidate),
    getWorkArrangement: vi.fn().mockResolvedValue(current),
    saveWorkArrangement: vi.fn().mockResolvedValue({ status: 'saved', current: { ...current, profileRevision: 3 } }),
    getWorkArrangementHistory: vi.fn().mockResolvedValue({
      profileRevision: 2,
      revisions: [{
        actorPrincipal: 'primary-device', baseProfileRevision: 1, changedFields: ['mode'],
        createdAt: '2026-08-21T15:00:00Z', itemRevision: 2, operation: 'set',
        profileRevision: 2, recordId: 'career_work_arrangement', revisionId: 'rev_2',
        restoredFromProfileRevision: null, value: current.record.value
      }, {
        actorPrincipal: 'primary-device', baseProfileRevision: 0, changedFields: ['mode', 'strength', 'note'],
        createdAt: '2026-08-20T15:00:00Z', itemRevision: 1, operation: 'set',
        profileRevision: 1, recordId: 'career_work_arrangement', revisionId: 'rev_1',
        restoredFromProfileRevision: null,
        value: { mode: 'hybrid', strength: 'strong_preference', note: '(FAKE) One office day is okay' }
      }]
    }),
    restoreWorkArrangement: vi.fn().mockResolvedValue({ status: 'saved', current }),
    listConnectedAgents: vi.fn().mockResolvedValue([]),
    updateConnectedAgentTrustMode: vi.fn(),
    disconnectConnectedAgent: vi.fn(),
    listCareerProfileProposals: vi.fn().mockResolvedValue([]),
    decideCareerProfileProposal: vi.fn(),
    getCareerProfileChangeHistory: vi.fn().mockResolvedValue({ profileRevision: 2, revisions: [] }),
    undoCareerProfileChange: vi.fn(),
    getCareerProfile: vi.fn().mockResolvedValue(completeCurrent),
    createCareerProfileItem: vi.fn().mockResolvedValue({ status: 'saved', current: completeCurrent }),
    updateCareerProfileItem: vi.fn().mockResolvedValue({ status: 'saved', current: completeCurrent }),
    removeCareerProfileItem: vi.fn().mockResolvedValue({ status: 'saved', current: completeCurrent }),
    importCareerProfileEvidence: vi.fn().mockResolvedValue({ status: 'saved', current: completeCurrent }),
    removeCareerProfileEvidence: vi.fn().mockResolvedValue({ status: 'saved', current: completeCurrent }),
    getCareerProfileContext: vi.fn().mockResolvedValue({
      agentId: 'job-hunter', mode: 'none', selectedAreas: [], selectedItemIds: [], updatedAt: '2026-08-21T15:00:00Z'
    }),
    updateCareerProfileContext: vi.fn(),
    previewCareerProfileContext: vi.fn(),
    exportCareerProfile: vi.fn().mockResolvedValue({ status: 'cancelled' }),
    chooseCareerProfileArchive: vi.fn().mockResolvedValue(null),
    restoreCareerProfile: vi.fn(),
    ...overrides
  }
}

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.restoreAllMocks()
})

test('explains the saved preference in normal language without exposing plumbing', async () => {
  render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)

  expect(await screen.findByRole('heading', { name: 'Work arrangement' })).not.toBeNull()
  expect(screen.getByText(/Only show roles that support remote work/i)).not.toBeNull()
  expect(screen.getByText(/A fully onsite role would be filtered out/i)).not.toBeNull()
  expect(screen.queryByText(/search_preferences|namespace|JSON|weight/i)).toBeNull()
})

test('validates locally, saves accessible fields, and announces next-turn timing', async () => {
  const api = bridge()
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')

  fireEvent.change(screen.getByLabelText('Work arrangement'), { target: { value: 'hybrid' } })
  fireEvent.change(screen.getByLabelText('How important is this?'), { target: { value: 'strong_preference' } })
  fireEvent.change(screen.getByLabelText('Additional context'), { target: { value: '🙂'.repeat(1001) } })
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  expect(screen.getByRole('alert').textContent).toContain('Keep the note to 1000 characters or fewer.')
  expect(api.saveWorkArrangement).not.toHaveBeenCalled()

  fireEvent.change(screen.getByLabelText('Additional context'), { target: { value: '🙂'.repeat(1000) } })
  expect(screen.getByText('1000/1000')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  await waitFor(() => expect(api.saveWorkArrangement).toHaveBeenCalledWith(expect.objectContaining({
    expectedProfileRevision: 2,
    value: { mode: 'hybrid', strength: 'strong_preference', note: '🙂'.repeat(1000) }
  })))
  expect(await screen.findByText('Saved — applies to the next turn.')).not.toBeNull()
})

test('preserves the proposed edit and shows the latest value after a stale conflict', async () => {
  const latest = {
    ...current,
    profileRevision: 3,
    record: { ...current.record, profileRevision: 3, value: { mode: 'onsite' as const, strength: 'preference' as const, note: null } }
  }
  const api = bridge({ saveWorkArrangement: vi.fn().mockResolvedValue({ status: 'conflict', current: latest }) })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')

  fireEvent.change(screen.getByLabelText('Work arrangement'), { target: { value: 'hybrid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))

  expect((await screen.findByRole('alert')).textContent).toContain('This preference changed somewhere else')
  const conflictCard = screen.getByRole('heading', { name: 'Review before saving' }).closest('section')
  expect(conflictCard?.textContent).toContain('Current saved valueOnsite')
  expect(conflictCard?.textContent).toContain('Your proposed valueHybrid')
  fireEvent.click(screen.getByRole('button', { name: 'Reapply my change' }))
  await waitFor(() => expect(api.saveWorkArrangement).toHaveBeenLastCalledWith(expect.objectContaining({ expectedProfileRevision: 3 })))
})

test('accepts the latest saved value when resolving a stale conflict', async () => {
  const latest = {
    ...current,
    profileRevision: 3,
    record: { ...current.record, profileRevision: 3, value: { mode: 'onsite' as const, strength: 'dealbreaker' as const, note: '(FAKE) Local only' } }
  }
  const api = bridge({ saveWorkArrangement: vi.fn().mockResolvedValue({ status: 'conflict', current: latest }) })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')

  fireEvent.change(screen.getByLabelText('Work arrangement'), { target: { value: 'hybrid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Keep current' }))

  expect((screen.getByLabelText('Work arrangement') as HTMLSelectElement).value).toBe('onsite')
  expect((screen.getByLabelText('How important is this?') as HTMLSelectElement).value).toBe('dealbreaker')
  expect(screen.getByDisplayValue('(FAKE) Local only')).not.toBeNull()
  expect(screen.queryByRole('heading', { name: 'Review before saving' })).toBeNull()
  expect(screen.getByText('Kept the latest saved preference.')).not.toBeNull()
})

test('reloads history after accepting the latest value from a stale conflict', async () => {
  const latest = {
    ...current,
    profileRevision: 3,
    record: { ...current.record, profileRevision: 3, value: { mode: 'onsite' as const, strength: 'preference' as const, note: null } }
  }
  const getWorkArrangementHistory = vi.fn()
    .mockResolvedValueOnce({ profileRevision: 2, revisions: [] })
    .mockResolvedValueOnce({ profileRevision: 3, revisions: [] })
  const api = bridge({
    getWorkArrangementHistory,
    saveWorkArrangement: vi.fn().mockResolvedValue({ status: 'conflict', current: latest })
  })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')

  fireEvent.click(screen.getByRole('button', { name: 'View history' }))
  await waitFor(() => expect(getWorkArrangementHistory).toHaveBeenCalledTimes(1))
  fireEvent.click(screen.getByRole('button', { name: 'Close history' }))

  fireEvent.change(screen.getByLabelText('Work arrangement'), { target: { value: 'hybrid' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Keep current' }))

  fireEvent.click(screen.getByRole('button', { name: 'View history' }))
  await waitFor(() => expect(getWorkArrangementHistory).toHaveBeenCalledTimes(2))
})

test('shows history, performs Undo as a new revision, and closes the drawer', async () => {
  const api = bridge()
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })

  const historyButton = screen.getByRole('button', { name: 'View history' })
  fireEvent.click(historyButton)
  const dialog = await screen.findByRole('dialog', { name: 'Work arrangement history' })
  const closeButton = screen.getByRole('button', { name: 'Close history' })
  const undoButton = screen.getByRole('button', { name: 'Undo to before revision 2' })
  expect(document.activeElement).toBe(closeButton)
  const workspaceRoot = document.querySelector('.career-profile-workspace')?.parentElement
  expect(workspaceRoot).not.toBeNull()
  expect((workspaceRoot as HTMLElement).inert).toBe(true)
  fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(undoButton)
  fireEvent.keyDown(dialog, { key: 'Tab' })
  expect(document.activeElement).toBe(closeButton)
  expect(within(dialog).getByText('Revision 2')).not.toBeNull()
  fireEvent.click(undoButton)

  await waitFor(() => expect(api.restoreWorkArrangement).toHaveBeenCalledWith(expect.objectContaining({
    expectedProfileRevision: 2,
    targetProfileRevision: 1
  })))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Work arrangement history' })).toBeNull())
  await waitFor(() => expect(document.activeElement).toBe(historyButton))

  fireEvent.click(historyButton)
  fireEvent.keyDown(await screen.findByRole('dialog', { name: 'Work arrangement history' }), { key: 'Escape' })
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Work arrangement history' })).toBeNull())
  expect(dialog.getAttribute('aria-modal')).toBe('true')
})

test('reuses mutation identities after ambiguous save and Undo failures', async () => {
  const saveWorkArrangement = vi.fn()
    .mockRejectedValueOnce(new Error('response lost'))
    .mockResolvedValueOnce({ status: 'saved', current: { ...current, profileRevision: 3 } })
  const restoreWorkArrangement = vi.fn()
    .mockRejectedValueOnce(new Error('response lost'))
    .mockResolvedValueOnce({ status: 'saved', current: { ...current, profileRevision: 3 } })
  const api = bridge({ restoreWorkArrangement, saveWorkArrangement })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')

  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  await screen.findByText(/could not save this preference/i)
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  await waitFor(() => expect(saveWorkArrangement).toHaveBeenCalledTimes(2))
  expect(saveWorkArrangement.mock.calls[1]?.[0].idempotencyKey).toBe(saveWorkArrangement.mock.calls[0]?.[0].idempotencyKey)

  fireEvent.click(screen.getByRole('button', { name: 'View history' }))
  const historyDialog = await screen.findByRole('dialog', { name: 'Work arrangement history' })
  expect(within(historyDialog).getByText('Revision 2')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Undo to before revision 2' }))
  const restoreErrorDialog = await screen.findByRole('dialog', { name: 'Work arrangement history' })
  expect(within(restoreErrorDialog).getByRole('alert').textContent).toContain('could not restore that version')
  fireEvent.click(within(restoreErrorDialog).getByRole('button', { name: 'Try again' }))
  fireEvent.click(screen.getByRole('button', { name: 'Undo to before revision 2' }))
  await waitFor(() => expect(restoreWorkArrangement).toHaveBeenCalledTimes(2))
  expect(restoreWorkArrangement.mock.calls[1]?.[0].idempotencyKey).toBe(restoreWorkArrangement.mock.calls[0]?.[0].idempotencyKey)
})

test('keeps offline data readable while blocking save and Undo', async () => {
  const api = bridge()
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} online={false} />)
  expect(await screen.findByDisplayValue('(FAKE) Remote in the US')).not.toBeNull()
  expect(screen.getByText(/Offline — your saved preference is still readable/i)).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Save preference' }).hasAttribute('disabled')).toBe(true)

  fireEvent.click(screen.getByRole('button', { name: 'View history' }))
  expect((await screen.findByRole('button', { name: 'Undo to before revision 2' })).hasAttribute('disabled')).toBe(true)
  expect(api.saveWorkArrangement).not.toHaveBeenCalled()
  expect(api.restoreWorkArrangement).not.toHaveBeenCalled()
})

test('reopens the last known profile for read-only use after a cold offline failure', async () => {
  const first = render(<CareerProfileWorkspace bridge={bridge()} hasActiveTurn={false} />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')
  first.unmount()

  const offlineBridge = bridge({ getWorkArrangement: vi.fn().mockRejectedValue(new Error('offline')) })
  render(<CareerProfileWorkspace bridge={offlineBridge} hasActiveTurn={false} online={false} />)
  expect(await screen.findByDisplayValue('(FAKE) Remote in the US')).not.toBeNull()
  expect(screen.getByText(/showing your last saved Career Profile/i)).not.toBeNull()
  expect(screen.getByRole('button', { name: 'Save preference' }).hasAttribute('disabled')).toBe(true)
})

test('preserves flexible strength combinations and freezes fields during save', async () => {
  let finishSave: ((value: unknown) => void) | undefined
  const saveWorkArrangement = vi.fn().mockReturnValue(new Promise(resolve => { finishSave = resolve }))
  const api = bridge({ saveWorkArrangement })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByDisplayValue('(FAKE) Remote in the US')

  fireEvent.change(screen.getByLabelText('How important is this?'), { target: { value: 'dealbreaker' } })
  fireEvent.change(screen.getByLabelText('Work arrangement'), { target: { value: 'flexible' } })
  expect((screen.getByLabelText('How important is this?') as HTMLSelectElement).value).toBe('dealbreaker')
  expect(screen.getByLabelText('How important is this?').hasAttribute('disabled')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Save preference' }))
  await waitFor(() => expect(saveWorkArrangement).toHaveBeenCalledTimes(1))
  expect(saveWorkArrangement).toHaveBeenCalledWith(expect.objectContaining({
    value: expect.objectContaining({ mode: 'flexible', strength: 'dealbreaker' })
  }))
  expect(screen.getByLabelText('Work arrangement').hasAttribute('disabled')).toBe(true)
  expect(screen.getByLabelText('Additional context').hasAttribute('disabled')).toBe(true)

  finishSave?.({ status: 'saved', current: { ...current, profileRevision: 3 } })
  await screen.findByText('Saved.')
})

test('reapplies a stale Undo as the selected restore, not as a normal save', async () => {
  const latest = {
    ...current,
    profileRevision: 3,
    record: { ...current.record, profileRevision: 3, value: { mode: 'onsite' as const, strength: 'dealbreaker' as const, note: '(FAKE) Local only' } }
  }
  const restoreWorkArrangement = vi.fn()
    .mockResolvedValueOnce({ status: 'conflict', current: latest })
    .mockResolvedValueOnce({ status: 'saved', current: { ...current, profileRevision: 4 } })
  const api = bridge({ restoreWorkArrangement })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })

  fireEvent.click(screen.getByRole('button', { name: 'View history' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Undo to before revision 2' }))
  const conflict = await screen.findByRole('heading', { name: 'Review before saving' })
  expect(conflict.closest('section')?.textContent).toContain('Onsite · Dealbreaker — (FAKE) Local only')
  expect(conflict.closest('section')?.textContent).toContain('Hybrid · Strong preference — (FAKE) One office day is okay')

  fireEvent.click(screen.getByRole('button', { name: 'Reapply my change' }))
  await waitFor(() => expect(restoreWorkArrangement).toHaveBeenLastCalledWith(expect.objectContaining({
    expectedProfileRevision: 3,
    targetProfileRevision: 1
  })))
  expect(api.saveWorkArrangement).not.toHaveBeenCalled()
})

test('shows history load errors inside the drawer and retries in place', async () => {
  const getWorkArrangementHistory = vi.fn()
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce(await bridge().getWorkArrangementHistory())
  render(<CareerProfileWorkspace bridge={bridge({ getWorkArrangementHistory })} hasActiveTurn={false} />)
  await screen.findByRole('heading', { name: 'Work arrangement' })

  fireEvent.click(screen.getByRole('button', { name: 'View history' }))
  expect(await screen.findByText('History could not load. Try again.')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  await waitFor(() => expect(within(screen.getByRole('dialog', { name: 'Work arrangement history' })).getByText('Revision 2')).not.toBeNull())
})

test('keeps empty and failure states actionable', async () => {
  const retry = vi.fn()
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ profileRevision: 0, record: null })
  render(<CareerProfileWorkspace bridge={bridge({ getWorkArrangement: retry })} hasActiveTurn={false} />)

  expect((await screen.findByRole('alert')).textContent).toContain('Career Profile is unavailable right now')
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
  expect(await screen.findByText('Tell JobOS where you want to work')).not.toBeNull()
})

test('shows an exact zero-Evidence proposal and binds approval to its revision and payload', async () => {
  const proposal = {
    after: {
      actorPrincipal: 'agent:job-hunter',
      area: 'my_career' as const,
      createdAt: '2026-08-21T15:00:00Z',
      evidenceIds: [],
      itemId: 'cpi_fakeproposalitem1234',
      itemRevision: 2,
      provenance: { method: 'agent_edit', mutation_source: 'agent_inference' },
      reviewStatus: 'accepted' as const,
      updatedAt: '2026-08-21T15:00:00Z',
      value: { kind: 'skill', name: 'TypeScript', level: 'advanced' }
    },
    agentDisplayName: 'Job Hunter',
    agentId: 'job-hunter',
    baseProfileRevision: 2,
    before: {
      actorPrincipal: 'primary-device',
      area: 'my_career' as const,
      createdAt: '2026-08-20T15:00:00Z',
      evidenceIds: [],
      itemId: 'cpi_fakeproposalitem1234',
      itemRevision: 1,
      provenance: { method: 'user_entered', mutation_source: 'direct_user_edit' },
      reviewStatus: 'accepted' as const,
      updatedAt: '2026-08-20T15:00:00Z',
      value: { kind: 'skill', name: 'TypeScript', level: 'intermediate' }
    },
    createdAt: '2026-08-21T15:00:00Z',
    evidenceIds: [],
    operation: 'item.update' as const,
    proposalId: 'cpp_fakeproposal123456',
    proposalSha256: 'a'.repeat(64),
    reason: 'Your recent project shows deeper TypeScript work.',
    reviewReason: 'This agent is set to Review every change.',
    status: 'pending' as const,
    targetId: 'cpi_fakeproposalitem1234'
  }
  const decideCareerProfileProposal = vi.fn().mockResolvedValue({
    profileRevision: 3,
    proposal: { ...proposal, status: 'accepted' }
  })
  const api = bridge({
    listCareerProfileProposals: vi.fn().mockResolvedValue([proposal]),
    decideCareerProfileProposal
  })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)

  expect(await screen.findByRole('heading', { name: /Review Job Hunter’s change/i })).not.toBeNull()
  expect(screen.getByText(/No Evidence attached — that’s okay/i)).not.toBeNull()
  expect(screen.getByText(/intermediate/)).not.toBeNull()
  expect(screen.getByText(/advanced/)).not.toBeNull()
  expect(screen.getAllByText('Level')).toHaveLength(2)
  expect(screen.queryByText(/"level":/)).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Accept exact change' }))
  await waitFor(() => expect(decideCareerProfileProposal).toHaveBeenCalledWith(
    'cpp_fakeproposal123456',
    expect.objectContaining({
      decision: 'accept',
      expectedProfileRevision: 2,
      proposalSha256: 'a'.repeat(64)
    })
  ))
})

test('surfaces a direct agent edit as a lightweight confirmation with prominent Undo', async () => {
  const undoCareerProfileChange = vi.fn().mockResolvedValue({ profileRevision: 4 })
  const api = bridge({
    getCareerProfileChangeHistory: vi.fn().mockResolvedValue({
      profileRevision: 3,
      revisions: [{
        actorKind: 'autonomous_agent',
        actorPrincipal: 'agent:job-hunter',
        affectedFields: ['value'],
        after: { value: { kind: 'skill', name: 'TypeScript' } },
        baseProfileRevision: 2,
        before: null,
        createdAt: '2026-08-21T15:00:00Z',
        evidenceId: null,
        itemId: 'cpi_fakedirectitem12345',
        operation: 'item.upsert',
        profileRevision: 3,
        proposalId: null,
        reason: 'Added the skill you asked me to capture.',
        revisionId: 'cpv_fakedirectrevision1',
        undoOfRevisionId: null,
        undoable: true
      }]
    }),
    undoCareerProfileChange
  })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)

  expect(await screen.findByText(/Job Hunter updated your Career Profile/i)).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Undo agent change' }))
  await waitFor(() => expect(undoCareerProfileChange).toHaveBeenCalledWith(
    'cpv_fakedirectrevision1',
    expect.objectContaining({ expectedProfileRevision: 3 })
  ))
})

test('refreshes agent proposals and history when the app regains focus', async () => {
  const initialHistory = { profileRevision: 2, revisions: [] }
  const getCareerProfileChangeHistory = vi.fn()
    .mockResolvedValueOnce(initialHistory)
    .mockResolvedValueOnce(initialHistory)
    .mockResolvedValue({
      profileRevision: 3,
      revisions: [{
        actorKind: 'autonomous_agent',
        actorPrincipal: 'agent:job-hunter',
        affectedFields: ['value'],
        after: { value: { kind: 'skill', name: 'TypeScript' } },
        baseProfileRevision: 2,
        before: null,
        createdAt: '2026-08-21T15:00:00Z',
        evidenceId: null,
        itemId: 'cpi_fakefocusitem123456',
        operation: 'item.upsert',
        profileRevision: 3,
        proposalId: null,
        reason: 'Captured a skill while JobOS was in the background.',
        revisionId: 'cpv_fakefocusrevision1',
        undoOfRevisionId: null,
        undoable: true
      }]
    })
  const api = bridge({ getCareerProfileChangeHistory })
  render(<CareerProfileWorkspace bridge={api} hasActiveTurn={false} />)

  await waitFor(() => expect(getCareerProfileChangeHistory).toHaveBeenCalledTimes(2))
  const initialCallCount = getCareerProfileChangeHistory.mock.calls.length
  window.dispatchEvent(new Event('focus'))

  await waitFor(() => expect(getCareerProfileChangeHistory.mock.calls.length).toBeGreaterThan(initialCallCount))
  expect(await screen.findByText(/Job Hunter updated your Career Profile/i)).not.toBeNull()
})
