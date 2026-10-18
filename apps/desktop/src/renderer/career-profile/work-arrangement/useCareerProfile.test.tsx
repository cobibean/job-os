import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { CareerProfileBridge, WorkArrangementCurrent } from '../../../shared/contracts'
import { useCareerProfile } from './useCareerProfile'

afterEach(cleanup)

test('preserves dirty work-arrangement fields and their editing revision across an unrelated refresh', async () => {
  const current: WorkArrangementCurrent = {
    profileRevision: 1,
    record: {
      actorPrincipal: 'primary-device', itemRevision: 1, profileRevision: 1,
      recordId: 'career_work_arrangement', updatedAt: '2026-08-21T15:00:00Z',
      value: { mode: 'remote', strength: 'preference', note: '(FAKE) Saved note' }
    }
  }
  const getWorkArrangement = vi.fn().mockResolvedValue(current)
  const saveWorkArrangement = vi.fn().mockResolvedValue({ status: 'saved', current })
  const bridge = { getWorkArrangement, saveWorkArrangement } as unknown as CareerProfileBridge
  const { result } = renderHook(() => useCareerProfile(bridge))
  await waitFor(() => expect(result.current.current).toEqual(current))

  act(() => result.current.setDraft({ ...current.record!.value, note: '(FAKE) Unsaved note' }))
  getWorkArrangement.mockResolvedValue({ ...current, profileRevision: 2 })
  await act(async () => { await result.current.load(false) })

  expect(result.current.current?.profileRevision).toBe(2)
  expect(result.current.draft.note).toBe('(FAKE) Unsaved note')
  await act(async () => { await result.current.save() })
  expect(saveWorkArrangement).toHaveBeenCalledWith(expect.objectContaining({
    expectedProfileRevision: 1,
    value: { ...current.record!.value, note: '(FAKE) Unsaved note' }
  }))
})
