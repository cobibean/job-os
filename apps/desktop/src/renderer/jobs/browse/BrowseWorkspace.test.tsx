import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, test, vi } from 'vitest'

import type { BrowseMode } from '../../workspace/workspaceLayout'
import type { JobDetail, JobListItem, JobSortMode } from '../../../shared/contracts'
import { BrowseWorkspace } from './BrowseWorkspace'
import { filterAndSortBrowseJobs } from './useBrowseJobs'

afterEach(cleanup)

const jobs: JobListItem[] = [
  { jobId: 'one', company: 'Alpha', title: 'Builder', status: 'applied', statusGroup: 'Applied', canonicalUrl: 'https://example.com/one', discoveredAt: '2026-01-01', lastSeenAt: '2026-01-01' },
  { jobId: 'two', company: 'Beta', title: 'Designer', status: 'reviewed', statusGroup: 'Considering', canonicalUrl: 'https://example.com/two', discoveredAt: '2026-01-02', lastSeenAt: '2026-01-03' },
  { jobId: 'three', company: 'Gamma', title: 'Engineer', status: 'discovered', statusGroup: 'Inbox', canonicalUrl: 'https://example.com/three', discoveredAt: '2026-01-03', lastSeenAt: '2026-01-02' }
]

test('Browse filtering and all four orders produce one deterministic result array', () => {
  expect(filterAndSortBrowseJobs(jobs, '', '', 'manual').map(job => job.jobId)).toEqual(['one', 'two', 'three'])
  expect(filterAndSortBrowseJobs(jobs, '', '', 'recent').map(job => job.jobId)).toEqual(['two', 'three', 'one'])
  expect(filterAndSortBrowseJobs([...jobs].reverse(), '', '', 'alphabetical').map(job => job.jobId)).toEqual(['one', 'two', 'three'])
  expect(filterAndSortBrowseJobs(jobs, '', '', 'status').map(job => job.jobId)).toEqual(['three', 'two', 'one'])
  expect(filterAndSortBrowseJobs(jobs, 'design', 'Considering', 'manual').map(job => job.jobId)).toEqual(['two'])
})

function installJobsBridge(inspect: (jobId: string) => Promise<JobDetail> = vi.fn(async (jobId: string) => ({
  ...jobs.find(job => job.jobId === jobId)!, description: `Description ${jobId}`, location: `Location ${jobId}`
}))) {
  const select = vi.fn()
  const updateStatus = vi.fn(async (jobId: string, status: JobListItem['status']) => ({
    eventId: 1, job: { ...jobs.find(job => job.jobId === jobId)!, status }
  }))
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    jobs: { list: vi.fn().mockResolvedValue(jobs), inspect, select, updateStatus, subscribe: vi.fn(() => () => undefined) }
  } })
  return { inspect, select, updateStatus }
}

function Harness({ initialMode = 'list', initialFocus = null, onOpen = vi.fn(async () => true) }: {
  initialMode?: BrowseMode
  initialFocus?: string | null
  onOpen?: (jobId: string, url: string) => Promise<boolean>
}) {
  const [state, setState] = useState({
    mode: initialMode, focusJobId: initialFocus, query: '', statusGroup: '', sortMode: 'manual' as JobSortMode
  })
  return <BrowseWorkspace
    activeJobId="two"
    focusJobId={state.focusJobId}
    mode={state.mode}
    onOpenJob={onOpen}
    onUpdate={update => setState(current => ({ ...current, ...update }))}
    query={state.query}
    railWidth={292}
    sortMode={state.sortMode}
    statusGroup={state.statusGroup}
  />
}

test('list and swipe share local focus without selecting the authoritative job', async () => {
  const bridge = installJobsBridge()
  render(<Harness />)

  await screen.findByText('Description two')
  fireEvent.click(screen.getByRole('button', { name: 'Gamma Engineer' }))
  expect(await screen.findByText('Description three')).not.toBeNull()
  expect(bridge.select).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Swipe' }))
  expect(screen.getByRole('heading', { name: 'Gamma' })).not.toBeNull()
  expect(screen.getByText('3 of 3')).not.toBeNull()

  fireEvent.change(screen.getByRole('textbox', { name: 'Search saved jobs' }), { target: { value: 'Alpha' } })
  expect(await screen.findByRole('heading', { name: 'Alpha' })).not.toBeNull()
  expect(screen.getByText('1 of 1')).not.toBeNull()
  expect(bridge.select).not.toHaveBeenCalled()
})

test('stale detail responses are ignored', async () => {
  let resolveOne!: (value: JobDetail) => void
  const inspect = vi.fn((jobId: string): Promise<JobDetail> => jobId === 'one'
    ? new Promise<JobDetail>(resolve => { resolveOne = resolve })
    : Promise.resolve({ ...jobs.find(job => job.jobId === jobId)!, description: `Fresh ${jobId}`, location: null }))
  installJobsBridge(inspect)
  render(<Harness initialFocus="one" />)

  await waitFor(() => expect(inspect).toHaveBeenCalledWith('one'))
  fireEvent.click(screen.getByRole('button', { name: 'Beta Designer' }))
  expect(await screen.findByText('Fresh two')).not.toBeNull()
  resolveOne({ ...jobs[0]!, description: 'Stale one', location: null })
  await Promise.resolve()
  expect(screen.queryByText('Stale one')).toBeNull()
})

test('detail loading is keyed to focus and never flashes an uninspected empty description', async () => {
  let resolveTwo!: (value: JobDetail) => void
  const bridge = installJobsBridge(vi.fn((jobId: string) => jobId === 'two'
    ? new Promise<JobDetail>(resolve => { resolveTwo = resolve })
    : Promise.resolve({ ...jobs.find(job => job.jobId === jobId)!, description: '', location: null })))
  render(<Harness />)

  expect(await screen.findByText('Loading job detail…')).not.toBeNull()
  expect(screen.queryByText('No description available.')).toBeNull()
  await waitFor(() => expect(bridge.inspect).toHaveBeenCalledWith('two'))
  resolveTwo({ ...jobs[1]!, description: '', location: null })
  expect(await screen.findByText('No description available.')).not.toBeNull()
})

test('refreshes and direct mutations are latest-wins', async () => {
  const pendingLists: Array<(value: JobListItem[]) => void> = []
  let listener: (() => void) | undefined
  const list = vi.fn(() => new Promise<JobListItem[]>(resolve => pendingLists.push(resolve)))
  const updateStatus = vi.fn(async (_jobId: string, status: JobListItem['status']) => ({
    eventId: 2,
    job: { ...jobs[1]!, status, statusGroup: status === 'shortlisted' ? 'Considering' : jobs[1]!.statusGroup }
  }))
  Object.defineProperty(window, 'jobos', { configurable: true, value: { jobs: {
    list,
    inspect: vi.fn(async jobId => ({ ...jobs.find(job => job.jobId === jobId)!, description: `Description ${jobId}`, location: null })),
    select: vi.fn(),
    updateStatus,
    subscribe: vi.fn(callback => { listener = callback; return () => undefined })
  } } })
  render(<Harness />)
  await waitFor(() => expect(list).toHaveBeenCalledTimes(1))
  listener?.()
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  pendingLists[1]?.([jobs[1]!])
  expect(await screen.findByRole('button', { name: 'Beta Designer' })).not.toBeNull()
  pendingLists[0]?.([jobs[0]!])
  await Promise.resolve()
  expect(screen.queryByRole('button', { name: 'Alpha Builder' })).toBeNull()

  listener?.()
  await waitFor(() => expect(list).toHaveBeenCalledTimes(3))
  fireEvent.change(screen.getByRole('combobox', { name: 'Change Beta status' }), { target: { value: 'shortlisted' } })
  await waitFor(() => expect(updateStatus).toHaveBeenCalledWith('two', 'shortlisted'))
  await waitFor(() => expect((screen.getByRole('combobox', { name: 'Change Beta status' }) as HTMLSelectElement).value).toBe('shortlisted'))
  pendingLists[2]?.([jobs[1]!])
  await Promise.resolve()
  expect((screen.getByRole('combobox', { name: 'Change Beta status' }) as HTMLSelectElement).value).toBe('shortlisted')
})

test('swipe keyboard and pointer navigation respect endpoints and editable controls', async () => {
  installJobsBridge()
  render(<Harness initialMode="swipe" initialFocus="one" />)
  await screen.findByText('Description one')

  fireEvent.keyDown(window, { key: 'ArrowLeft' })
  expect(screen.getByText('1 of 3')).not.toBeNull()
  fireEvent.keyDown(window, { key: 'ArrowRight' })
  expect(await screen.findByText('2 of 3')).not.toBeNull()

  const search = screen.getByRole('textbox', { name: 'Search saved jobs' })
  fireEvent.keyDown(search, { key: 'ArrowRight' })
  expect(screen.getByText('2 of 3')).not.toBeNull()

  const stage = screen.getByRole('region', { name: 'Opportunity swipe browser' })
  const open = screen.getByRole('button', { name: 'Open job' })
  fireEvent.keyDown(open, { key: 'ArrowRight' })
  expect(screen.getByText('2 of 3')).not.toBeNull()
  fireEvent.pointerDown(open, { clientX: 300, pointerId: 2 })
  fireEvent.pointerUp(stage, { clientX: 210, pointerId: 2 })
  expect(screen.getByText('2 of 3')).not.toBeNull()
  fireEvent.pointerDown(stage, { clientX: 300, pointerId: 3 })
  fireEvent.pointerUp(open, { clientX: 210, pointerId: 3 })
  expect(screen.getByText('2 of 3')).not.toBeNull()
  fireEvent.pointerDown(stage, { clientX: 300, pointerId: 1 })
  fireEvent.pointerUp(stage, { clientX: 210, pointerId: 1 })
  expect(await screen.findByText('3 of 3')).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Next job' }) as HTMLButtonElement).disabled).toBe(true)
})

test('Open job prevents duplicates and reports failure without leaving Browse', async () => {
  installJobsBridge()
  let resolveOpen!: (success: boolean) => void
  const onOpen = vi.fn(() => new Promise<boolean>(resolve => { resolveOpen = resolve }))
  render(<Harness onOpen={onOpen} />)
  await screen.findByText('Description two')

  fireEvent.click(screen.getByRole('button', { name: 'Open job' }))
  const pending = screen.getByRole('button', { name: 'Opening…' }) as HTMLButtonElement
  expect(pending.disabled).toBe(true)
  fireEvent.click(pending)
  expect(onOpen).toHaveBeenCalledOnce()
  resolveOpen(false)
  expect((await screen.findByRole('alert')).textContent).toContain('Could not open this job')
  expect(screen.getByRole('heading', { name: 'Browse' })).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Open job' }) as HTMLButtonElement).disabled).toBe(false)
})

test('status controls expose and execute legal canonical transitions only', async () => {
  const bridge = installJobsBridge()
  render(<Harness initialFocus="one" />)
  await screen.findByText('Description one')
  const status = screen.getByRole('combobox', { name: 'Change Alpha status' }) as HTMLSelectElement
  const values = Array.from(status.options).map(option => option.value)
  expect(values).toEqual(['applied', 'interviewing', 'closed', 'archived'])
  expect(values).not.toContain('reviewed')
  fireEvent.change(status, { target: { value: 'interviewing' } })
  expect(bridge.updateStatus).toHaveBeenCalledWith('one', 'interviewing')
})

test('Inbox jobs can move to Considering and the rail exposes the canonical group', async () => {
  const bridge = installJobsBridge()
  render(<Harness initialFocus="three" />)
  await screen.findByText('Description three')

  expect(screen.getByRole('navigation', { name: 'Job groups' }).textContent).toContain('Considering')
  const status = screen.getByRole('combobox', { name: 'Change Gamma status' }) as HTMLSelectElement
  const considering = Array.from(status.options).find(option => option.textContent === 'Considering')
  expect(considering?.value).toBe('shortlisted')

  fireEvent.change(status, { target: { value: 'shortlisted' } })
  expect(bridge.updateStatus).toHaveBeenCalledWith('three', 'shortlisted')
})

test('rejected list and detail requests are visible alerts', async () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: { jobs: {
    list: vi.fn().mockRejectedValue(new Error('list unavailable')),
    inspect: vi.fn(),
    updateStatus: vi.fn(),
    subscribe: vi.fn(() => () => undefined)
  } } })
  const first = render(<Harness />)

  const listAlert = await screen.findByRole('alert')
  expect(listAlert.textContent).toContain('Jobs unavailable')

  first.unmount()
  installJobsBridge(vi.fn().mockRejectedValue(new Error('inspect unavailable')))
  render(<Harness />)

  const detailAlert = await screen.findByRole('alert')
  expect(detailAlert.textContent).toContain('Job detail unavailable')
})

test('rejected status updates are visible beside the status control and clear after success', async () => {
  installJobsBridge()
  const updateStatus = window.jobos.jobs.updateStatus as ReturnType<typeof vi.fn>
  updateStatus
    .mockRejectedValueOnce(new Error('update unavailable'))
    .mockImplementationOnce(async (jobId: string, status: JobListItem['status']) => ({
      eventId: 2, job: { ...jobs.find(job => job.jobId === jobId)!, status }
    }))
  render(<Harness initialFocus="one" />)
  await screen.findByText('Description one')
  const status = screen.getByRole('combobox', { name: 'Change Alpha status' })

  fireEvent.change(status, { target: { value: 'interviewing' } })
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('Status change failed')
  expect(alert.parentElement?.querySelector('select')).toBe(status)

  fireEvent.change(status, { target: { value: 'closed' } })
  await waitFor(() => expect(screen.queryByText('Status change failed')).toBeNull())
})
