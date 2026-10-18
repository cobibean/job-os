import { useState } from 'react'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentSessionJobContext, JobEvent } from '../../shared/contracts'
import { useJobs } from './useJobs'

const emptyJobContext: AgentSessionJobContext = {
  selectedJobId: null,
  activeArtifactId: null,
  activeArtifactPage: 1,
  activeArtifactZoom: 1
}

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

function useConversationJobs() {
  const [context, setContext] = useState<AgentSessionJobContext>(emptyJobContext)
  return useJobs('conv-1', context, (_conversationId, nextContext) => setContext(nextContext))
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

  const { result } = renderHook(() => useJobs('conv-1', emptyJobContext))
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

  const { result } = renderHook(() => useJobs('conv-1', emptyJobContext))
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
    return { ...emptyJobContext, selectedJobId: 'job-1' }
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
  const { result } = renderHook(() => useConversationJobs())
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
        select: vi.fn().mockResolvedValue({ ...emptyJobContext, selectedJobId: 'job-1' }),
        reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })
  const { result } = renderHook(() => useConversationJobs())
  await waitFor(() => expect(result.current.loading).toBe(false))

  await act(async () => { await result.current.selectJob('job-1') })

  expect(inspect).toHaveBeenCalledWith('job-1')
  expect(result.current.selectedJobDetail?.description).toBe('Complete responsibilities and qualifications.')
})

test('a newer session job context wins over an older startup snapshot', async () => {
  const secondJob = { ...discoveredJob, jobId: 'job-2', title: 'Second role' }
  let resolveSnapshot: ((value: {
    jobs: typeof discoveredJob[]
    selectedJobId: string | null
    sortMode: 'manual'
    manualOrder: string[]
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
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })
  const { result, rerender } = renderHook(
    ({ context }) => useJobs('conv-1', context),
    { initialProps: { context: emptyJobContext } }
  )

  await act(async () => {
    rerender({ context: { ...emptyJobContext, selectedJobId: 'job-2' } })
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

function installRegressionBridge() {
  const secondJob = { ...discoveredJob, jobId: 'job-2', company: 'Other Co', title: 'Designer' }
  const all = [discoveredJob, secondJob]
  const bridge = {
    getState: vi.fn().mockResolvedValue({ jobs: all, selectedJobId: null, sortMode: 'manual', manualOrder: all.map(job => job.jobId) }),
    list: vi.fn().mockImplementation(async (_sort, query) => query ? all.filter(job => job.company.includes(query)) : all),
    inspect: vi.fn(async (jobId: string) => ({ ...all.find(job => job.jobId === jobId)!, description: `${jobId} detail`, location: 'Remote' })),
    select: vi.fn().mockResolvedValue({ ...emptyJobContext, selectedJobId: discoveredJob.jobId }),
    reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), subscribe: vi.fn((_callback: (event: JobEvent) => void) => () => undefined)
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: { jobs: bridge } })
  return { bridge, all }
}

test('selects and inspects local jobs without creating or selecting an agent conversation', async () => {
  const { bridge } = installRegressionBridge()
  const { result } = renderHook(() => useJobs(null, null))
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(async () => { expect(await result.current.selectJob('job-1')).toBe(true) })
  expect(result.current.selectedJobId).toBe('job-1')
  expect(result.current.selectedJobDetail?.description).toBe('job-1 detail')
  expect(bridge.select).not.toHaveBeenCalled()
})

test('document zoom and chat context updates preserve the filtered navigator and canonical jobs', async () => {
  const { bridge, all } = installRegressionBridge()
  const { result, rerender } = renderHook(({ context }) => useJobs('conv-1', context), { initialProps: { context: emptyJobContext } })
  await waitFor(() => expect(result.current.ready).toBe(true))
  act(() => result.current.setQuery('Example'))
  await waitFor(() => expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-1']))
  rerender({ context: { ...emptyJobContext, activeArtifactPage: 3, activeArtifactZoom: 1.5 } })
  await act(async () => { await Promise.resolve() })
  expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-1'])
  expect(result.current.allJobs).toEqual(all)
  expect(bridge.getState).toHaveBeenCalledTimes(1)
})

test('an older search cannot overwrite a newer search result or its error state', async () => {
  const { bridge, all } = installRegressionBridge()
  let resolveOlder!: (jobs: typeof all) => void
  bridge.list.mockImplementation(async (_sort, query) => query === 'Example'
    ? new Promise<typeof all>(resolve => { resolveOlder = resolve })
    : query === 'Other' ? [all[1]!] : all)
  const { result } = renderHook(() => useJobs(null, null))
  await waitFor(() => expect(result.current.ready).toBe(true))
  act(() => result.current.setQuery('Example'))
  await waitFor(() => expect(resolveOlder).toBeDefined())
  act(() => result.current.setQuery('Other'))
  await waitFor(() => expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-2']))
  await act(async () => { resolveOlder([all[0]!]) })
  expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-2'])
  expect(result.current.error).toBeNull()
})

test('retry recovers an initial snapshot failure and enables filtering', async () => {
  const { bridge } = installRegressionBridge()
  bridge.getState.mockRejectedValueOnce(new Error('local API starting'))
  const { result } = renderHook(() => useJobs(null, null))
  await waitFor(() => expect(result.current.error).toBe('Jobs unavailable'))
  await act(async () => { await result.current.retry() })
  await waitFor(() => expect(result.current.ready).toBe(true))
  act(() => result.current.setQuery('Other'))
  await waitFor(() => expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-2']))
})

test.each(['status', 'sort', 'event'] as const)('obsolete list results cannot replace the latest %s controls', async kind => {
  const { bridge, all } = installRegressionBridge()
  let listener!: (event: JobEvent) => void
  bridge.subscribe.mockImplementation(callback => { listener = callback; return () => undefined })
  const pending: Array<{ sort: string; group?: string; resolve: (jobs: typeof all) => void }> = []
  bridge.list.mockImplementation((sort, _query, group) => new Promise<typeof all>(resolve => pending.push({ sort, group, resolve })))
  const { result } = renderHook(() => useJobs(null, null))
  await waitFor(() => expect(pending.length).toBe(1))
  if (kind === 'status') act(() => result.current.setStatusGroup('Inbox'))
  else if (kind === 'sort') await act(async () => { await result.current.changeSort('recent') })
  else act(() => listener({ eventId: 2, eventType: 'job_description_updated', origin: 'mcp', jobId: 'job-1' }))
  await waitFor(() => expect(pending.length).toBe(kind === 'event' ? 2 : 3))
  await act(async () => {
    for (const request of pending.slice(1)) request.resolve([all[1]!])
  })
  expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-2'])
  await act(async () => { pending[0]!.resolve([all[0]!]) })
  expect(result.current.jobs.map(job => job.jobId)).toEqual(['job-2'])
})

test('an obsolete search failure is ignored while the next search debounce is pending', async () => {
  const { bridge } = installRegressionBridge()
  let rejectOlder!: (error: Error) => void
  bridge.list.mockImplementation(async (_sort, query) => query === 'Example'
    ? new Promise((_resolve, reject) => { rejectOlder = reject }) : [discoveredJob])
  const { result } = renderHook(() => useJobs(null, null))
  await waitFor(() => expect(result.current.ready).toBe(true))
  act(() => result.current.setQuery('Example'))
  await waitFor(() => expect(rejectOlder).toBeDefined())
  act(() => result.current.setQuery('Other'))
  await act(async () => { rejectOlder(new Error('old search unavailable')) })
  expect(result.current.error).toBeNull()
})

test('recovered local service connectivity retries failed startup without a job event', async () => {
  const { bridge } = installRegressionBridge()
  bridge.getState.mockRejectedValueOnce(new Error('local API starting'))
  const { result, rerender } = renderHook(({ connectivity }) => useJobs(null, null, undefined, undefined, connectivity), {
    initialProps: { connectivity: 'disconnected' }
  })
  await waitFor(() => expect(result.current.error).toBe('Jobs unavailable'))
  rerender({ connectivity: 'connected' })
  await waitFor(() => expect(result.current.ready).toBe(true))
  expect(result.current.jobs).toHaveLength(2)
  expect(result.current.error).toBeNull()
})
