import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { useWorkspace } from './useWorkspace'
import { canonicalWorkspace } from '../workspaceLayout'

afterEach(() => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
})

test('changing the authoritative job clears a stale active artifact before persisting', async () => {
  const staleWorkspace = {
    ...canonicalWorkspace,
    revision: 7,
    selectedJobId: 'job-old',
    activeArtifactId: 'art_abcdefghijklmnop',
    activeArtifactPage: 4,
    activeArtifactZoom: 1.5
  }
  const save = vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 }))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      workspace: {
        get: vi.fn().mockResolvedValue(staleWorkspace),
        save
      }
    }
  })

  const { result, rerender } = renderHook(
    ({ selectedJobId }) => useWorkspace(selectedJobId, true),
    { initialProps: { selectedJobId: 'job-old' as string | null } }
  )
  await waitFor(() => expect(result.current.hydrated).toBe(true))

  rerender({ selectedJobId: 'job-new' })

  await waitFor(() => expect(save).toHaveBeenCalled())
  const persisted = save.mock.calls.at(-1)?.[0]
  expect(persisted).toMatchObject({
    selectedJobId: 'job-new',
    activeArtifactId: null,
    activeArtifactPage: 1,
    activeArtifactZoom: 1
  })
  expect(result.current.workspace).toMatchObject({
    selectedJobId: 'job-new',
    activeArtifactId: null
  })
})

test('job hydration does not clear restored document state before selection is ready', async () => {
  const restored = {
    ...canonicalWorkspace,
    revision: 3,
    selectedJobId: 'job-restored',
    activeArtifactId: 'art_abcdefghijklmnop'
  }
  const save = vi.fn()
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { workspace: { get: vi.fn().mockResolvedValue(restored), save } }
  })

  const { result, rerender } = renderHook(
    ({ ready }) => useWorkspace(null, ready),
    { initialProps: { ready: false } }
  )
  await waitFor(() => expect(result.current.hydrated).toBe(true))
  expect(result.current.workspace.activeArtifactId).toBe('art_abcdefghijklmnop')
  expect(save).not.toHaveBeenCalled()

  rerender({ ready: true })
  await act(async () => undefined)
  await waitFor(() => expect(save).toHaveBeenCalled())
  expect(save.mock.calls.at(-1)?.[0]).toMatchObject({ selectedJobId: null, activeArtifactId: null })
})
