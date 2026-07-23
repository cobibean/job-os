import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { useJobs } from './useJobs'

const discoveredJob = {
  jobId: 'job-1',
  company: 'Example Co',
  title: 'Product Builder',
  status: 'discovered' as const,
  statusGroup: 'Inbox',
  canonicalUrl: 'https://example.com/jobs/1',
  discoveredAt: '2026-07-20T00:00:00Z',
  lastSeenAt: '2026-07-20T01:00:00Z'
}

afterEach(cleanup)

test('a rejected status transition preserves the API explanation for the user', async () => {
  const updateStatus = vi.fn().mockRejectedValue(
    new Error("Error invoking remote method 'jobos:jobs:update-status': Error: Invalid lead state transition: discovered -> applied")
  )
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      jobs: {
        getState: vi.fn().mockResolvedValue({
          jobs: [discoveredJob],
          selectedJobId: null,
          sortMode: 'manual',
          manualOrder: ['job-1']
        }),
        list: vi.fn().mockResolvedValue([discoveredJob]),
        select: vi.fn(),
        reorder: vi.fn(),
        setSort: vi.fn(),
        updateStatus,
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })

  const { result } = renderHook(() => useJobs())
  await waitFor(() => expect(result.current.loading).toBe(false))

  await act(async () => {
    await result.current.changeStatus('job-1', 'applied')
  })

  expect(updateStatus).toHaveBeenCalledWith('job-1', 'applied')
  expect(result.current.error).toBe('Invalid lead state transition: discovered -> applied')
})

test('an unknown transition-shaped IPC error stays generic', async () => {
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      jobs: {
        getState: vi.fn().mockResolvedValue({
          jobs: [discoveredJob],
          selectedJobId: null,
          sortMode: 'manual',
          manualOrder: ['job-1']
        }),
        list: vi.fn().mockResolvedValue([discoveredJob]),
        select: vi.fn(),
        reorder: vi.fn(),
        setSort: vi.fn(),
        updateStatus: vi.fn().mockRejectedValue(
          new Error("Error invoking remote method 'jobos:jobs:update-status': Error: Invalid lead state transition: internal_state -> leaked_state")
        ),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })

  const { result } = renderHook(() => useJobs())
  await waitFor(() => expect(result.current.loading).toBe(false))

  await act(async () => {
    await result.current.changeStatus('job-1', 'applied')
  })

  expect(result.current.error).toBe('Status change failed')
})
