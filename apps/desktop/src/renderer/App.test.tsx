import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { App } from './App'

afterEach(cleanup)


test('the shell reports authenticated Mini connectivity without exposing credentials', async () => {
  const getConnectivity = vi.fn().mockResolvedValue({
    state: 'connected',
    apiVersion: '0.1.0',
    checkedAt: '2026-07-20T00:00:00.000Z',
    message: 'Private API authenticated'
  })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: getConnectivity }
    }
  })

  render(<App />)

  expect(screen.getByText('Connecting to Mac Mini…')).not.toBeNull()
  expect(await screen.findByText('Mac Mini connected')).not.toBeNull()
  expect(screen.getByText('API 0.1.0')).not.toBeNull()
  expect(getConnectivity).toHaveBeenCalledOnce()
  expect(JSON.stringify(window.jobos)).not.toContain('test-device-token')
})

test('reset preserves the selected layout preset', async () => {
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: {
        get: vi.fn().mockResolvedValue({
          state: 'connected',
          apiVersion: '0.1.0',
          checkedAt: '2026-07-20T00:00:00.000Z',
          message: 'Private API authenticated'
        })
      }
    }
  })

  render(<App />)
  const research = screen.getByRole('button', { name: 'Research' })
  fireEvent.click(research)
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))

  expect(research.getAttribute('aria-pressed')).toBe('true')
})

test('later-phase controls are visibly disabled while layout controls remain interactive', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)

  for (const name of [
    'Open a new surface',
    'Agent context settings',
    'Send message',
    'Open settings'
  ]) {
    expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
  }
  expect((screen.getByRole('tab', { name: 'Browser' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Research' }) as HTMLButtonElement).disabled).toBe(false)
  expect((screen.getByRole('button', { name: 'Reset layout' }) as HTMLButtonElement).disabled).toBe(false)
})

test('auth degradation is distinct from network unavailability', async () => {
  const get = vi.fn()
    .mockResolvedValueOnce({
      state: 'degraded',
      checkedAt: '2026-07-20T00:00:00.000Z',
      message: 'Device authentication failed'
    })
    .mockResolvedValueOnce({
      state: 'disconnected',
      checkedAt: '2026-07-20T00:00:01.000Z',
      message: 'Mac Mini unavailable'
    })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectivity: { get } }
  })

  render(<App />)
  expect(await screen.findByText('Mac Mini authentication failed')).not.toBeNull()
  fireEvent.focus(window)
  expect(await screen.findByText('Mac Mini unavailable')).not.toBeNull()
})

test('real jobs render compactly and user selection and status use the shared bridge', async () => {
  const select = vi.fn().mockResolvedValue({ eventId: 1 })
  const updateStatus = vi.fn().mockResolvedValue({
    eventId: 2,
    job: {
      jobId: 'job-1',
      company: 'Example Co',
      title: 'Product Builder',
      status: 'reviewed',
      statusGroup: 'Inbox',
      canonicalUrl: 'https://example.com/jobs/1',
      discoveredAt: '2026-07-20T00:00:00Z',
      lastSeenAt: '2026-07-20T01:00:00Z'
    }
  })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      jobs: {
        getState: vi.fn().mockResolvedValue({
          jobs: [{
            jobId: 'job-1',
            company: 'Example Co',
            title: 'Product Builder',
            status: 'discovered',
            statusGroup: 'Inbox',
            canonicalUrl: 'https://example.com/jobs/1',
            discoveredAt: '2026-07-20T00:00:00Z',
            lastSeenAt: '2026-07-20T01:00:00Z'
          }],
          selectedJobId: null,
          sortMode: 'manual',
          manualOrder: ['job-1']
        }),
        list: vi.fn(),
        select,
        reorder: vi.fn(),
        setSort: vi.fn(),
        updateStatus,
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })

  render(<App />)
  const job = await screen.findByRole('button', { name: 'Select Example Co Product Builder' })
  fireEvent.click(job)
  fireEvent.change(screen.getByRole('combobox', { name: 'Change Example Co status' }), {
    target: { value: 'reviewed' }
  })

  expect(select).toHaveBeenCalledWith('job-1')
  expect(updateStatus).toHaveBeenCalledWith('job-1', 'reviewed')
  expect(await screen.findByText('Status changed to reviewed')).not.toBeNull()
})

test('an MCP status event refreshes the navigator without a manual action', async () => {
  let listener: ((event: { eventId: number; eventType: string; origin: 'mcp' }) => void) | undefined
  const original = {
    jobId: 'job-1', company: 'Example Co', title: 'Product Builder', status: 'discovered' as const,
    statusGroup: 'Inbox', canonicalUrl: 'https://example.com/jobs/1',
    discoveredAt: '2026-07-20T00:00:00Z', lastSeenAt: '2026-07-20T01:00:00Z'
  }
  const list = vi.fn().mockResolvedValue([{ ...original, status: 'reviewed' }])
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      jobs: {
        getState: vi.fn().mockResolvedValue({
          jobs: [original], selectedJobId: null, sortMode: 'manual', manualOrder: ['job-1']
        }),
        list,
        select: vi.fn(), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockImplementation(callback => {
          listener = callback
          return () => undefined
        })
      }
    }
  })

  render(<App />)
  expect(await screen.findByDisplayValue('discovered')).not.toBeNull()
  await act(async () => {
    listener?.({ eventId: 8, eventType: 'job_status_changed', origin: 'mcp' })
  })

  expect(await screen.findByDisplayValue('reviewed')).not.toBeNull()
  expect(screen.getByText('Agent changes synced')).not.toBeNull()
  expect(list).toHaveBeenCalled()
})

test('filtering the list never clears the active job context', async () => {
  const apollo = { jobId: 'apollo', company: 'Apollo.io', title: 'Account Executive', status: 'scored' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/apollo', discoveredAt: '', lastSeenAt: '' }
  const northstar = { jobId: 'northstar', company: 'Northstar', title: 'Product Manager', status: 'reviewed' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/northstar', discoveredAt: '', lastSeenAt: '' }
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      jobs: {
        getState: vi.fn().mockResolvedValue({ jobs: [apollo, northstar], selectedJobId: null, sortMode: 'manual', manualOrder: ['apollo', 'northstar'] }),
        list: vi.fn().mockResolvedValue([apollo]), select: vi.fn().mockResolvedValue({ eventId: 1 }),
        reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })

  render(<App />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Northstar Product Manager' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Filter jobs' }), { target: { value: 'Apollo' } })

  await waitFor(() => expect(screen.queryByRole('button', { name: 'Select Northstar Product Manager' })).toBeNull())
  expect(screen.getByText('Northstar · Product Manager')).not.toBeNull()
})
