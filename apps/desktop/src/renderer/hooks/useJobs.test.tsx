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

test('a selection event emitted by the same successful user action does not invalidate it', async () => {
  let listener: ((event: { eventId: number; eventType: string; origin: 'user'; jobId: string }) => void) | undefined
  const select = vi.fn().mockImplementation(async () => {
    listener?.({ eventId: 3, eventType: 'job_selected', origin: 'user', jobId: 'job-1' })
    return { eventId: 3 }
  })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      jobs: {
        getState: vi.fn().mockResolvedValue({
          jobs: [discoveredJob], selectedJobId: 'job-1', sortMode: 'manual', manualOrder: ['job-1']
        }),
        list: vi.fn().mockResolvedValue([discoveredJob]),
        select,
        reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockImplementation(callback => {
          listener = callback
          return () => undefined
        })
      }
    }
  })
  const { result } = renderHook(() => useJobs())
  await waitFor(() => expect(result.current.loading).toBe(false))

  let selected = false
  await act(async () => { selected = await result.current.selectJob('job-1') })

  expect(selected).toBe(true)
  expect(result.current.selectedJobId).toBe('job-1')
})

test('selecting a job fetches its full detail separately from the lightweight list', async () => {
  const inspect = vi.fn().mockResolvedValue({
    ...discoveredJob,
    description: 'Complete responsibilities and qualifications.',
    location: 'Remote'
  })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      jobs: {
        getState: vi.fn().mockResolvedValue({
          jobs: [discoveredJob], selectedJobId: null, sortMode: 'manual', manualOrder: ['job-1']
        }),
        list: vi.fn().mockResolvedValue([discoveredJob]),
        inspect,
        select: vi.fn().mockResolvedValue({ eventId: 2 }),
        reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })
  const { result } = renderHook(() => useJobs())
  await waitFor(() => expect(result.current.loading).toBe(false))

  await act(async () => { await result.current.selectJob('job-1') })

  expect(inspect).toHaveBeenCalledWith('job-1')
  expect(result.current.selectedJobDetail?.description).toBe('Complete responsibilities and qualifications.')
})

test('a live selection event wins over an older startup snapshot', async () => {
  const secondJob = { ...discoveredJob, jobId: 'job-2', title: 'Second role' }
  let resolveSnapshot: ((value: {
    jobs: typeof discoveredJob[]
    selectedJobId: string | null
    sortMode: 'manual'
    manualOrder: string[]
  }) => void) | undefined
  let listener: ((event: {
    eventId: number
    eventType: string
    origin: 'mcp'
    jobId: string
  }) => void) | undefined
  const getState = vi.fn().mockReturnValue(new Promise((resolve) => { resolveSnapshot = resolve }))
  const inspect = vi.fn().mockImplementation(async (jobId: string) => ({
    ...(jobId === 'job-2' ? secondJob : discoveredJob),
    description: `${jobId} description`,
    location: 'Remote'
  }))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      jobs: {
        getState,
        list: vi.fn().mockResolvedValue([discoveredJob, secondJob]),
        inspect,
        select: vi.fn(), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockImplementation(callback => {
          listener = callback
          return () => undefined
        })
      }
    }
  })
  const { result } = renderHook(() => useJobs())

  await act(async () => {
    listener?.({ eventId: 8, eventType: 'job_selected', origin: 'mcp', jobId: 'job-2' })
    await Promise.resolve()
  })
  await waitFor(() => expect(result.current.selectedJobId).toBe('job-2'))

  await act(async () => {
    resolveSnapshot?.({
      jobs: [discoveredJob, secondJob],
      selectedJobId: 'job-1',
      sortMode: 'manual',
      manualOrder: ['job-1', 'job-2']
    })
    await Promise.resolve()
  })

  expect(result.current.selectedJobId).toBe('job-2')
  expect(result.current.selectedJobDetail?.jobId).toBe('job-2')
})
