import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentSessionJobContext } from '../../shared/contracts'
import { canonicalWorkspace } from '../workspaceLayout'
import { useWorkspace } from './useWorkspace'

afterEach(() => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
})

const context = (
  selectedJobId: string,
  activeArtifactId: string | null = null,
  activeArtifactPage = 1,
  activeArtifactZoom = 1
): AgentSessionJobContext => ({ selectedJobId, activeArtifactId, activeArtifactPage, activeArtifactZoom })

test('switching sessions projects each conversation job and artifact without persisting them globally', async () => {
  const save = vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 }))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      workspace: {
        get: vi.fn().mockResolvedValue({ ...canonicalWorkspace, revision: 3 }),
        save,
        saveDocumentView: vi.fn()
      }
    }
  })

  const { result, rerender } = renderHook(
    ({ conversationId, jobContext }) => useWorkspace(conversationId, jobContext),
    {
      initialProps: {
        conversationId: 'conv-a' as string | null,
        jobContext: context('job-a', 'art-a', 4, 1.5) as AgentSessionJobContext | null
      }
    }
  )
  await waitFor(() => expect(result.current.hydrated).toBe(true))
  expect(result.current.workspace).toMatchObject({
    selectedJobId: 'job-a', activeArtifactId: 'art-a', activeArtifactPage: 4, activeArtifactZoom: 1.5
  })

  rerender({ conversationId: 'conv-b', jobContext: context('job-b', 'art-b', 2, 1.25) })
  expect(result.current.workspace).toMatchObject({
    selectedJobId: 'job-b', activeArtifactId: 'art-b', activeArtifactPage: 2, activeArtifactZoom: 1.25
  })
  expect(save).not.toHaveBeenCalled()
})

test('a stale document-view response from session A cannot overwrite active session B', async () => {
  let resolveA!: (value: AgentSessionJobContext) => void
  const saveDocumentView = vi.fn().mockImplementation(() => new Promise<AgentSessionJobContext>(resolve => { resolveA = resolve }))
  const onJobContextChange = vi.fn()
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      workspace: {
        get: vi.fn().mockResolvedValue({ ...canonicalWorkspace, revision: 1 }),
        save: vi.fn(),
        saveDocumentView
      }
    }
  })

  const { result, rerender } = renderHook(
    ({ conversationId, jobContext }) => useWorkspace(conversationId, jobContext, onJobContextChange),
    {
      initialProps: {
        conversationId: 'conv-a' as string | null,
        jobContext: context('job-a') as AgentSessionJobContext | null
      }
    }
  )
  await waitFor(() => expect(result.current.hydrated).toBe(true))
  let pending!: Promise<void>
  act(() => { pending = result.current.updateDocumentState('art-a', 3, 1.4) })
  await waitFor(() => expect(saveDocumentView).toHaveBeenCalledWith('conv-a', 'art-a', 3, 1.4))

  rerender({ conversationId: 'conv-b', jobContext: context('job-b', 'art-b', 2, 1.2) })
  await act(async () => {
    resolveA(context('job-a', 'art-a', 3, 1.4))
    await pending
  })

  expect(result.current.workspace).toMatchObject({ selectedJobId: 'job-b', activeArtifactId: 'art-b' })
  expect(onJobContextChange).not.toHaveBeenCalled()
})