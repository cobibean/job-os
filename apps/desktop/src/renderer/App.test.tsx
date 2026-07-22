import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentStreamUpdate, JobListItem } from '../shared/contracts'
import { App } from './App'
import { isExpectedSaveNavigation } from './components/CenterWorkspace'

afterEach(cleanup)


test('browser save navigation only accepts the original listing or its slug-matched detail page', () => {
  expect(isExpectedSaveNavigation(
    'https://wellfound.com/jobs/starred?job_listing_slug=applied-ai-builder',
    'https://wellfound.com/jobs/123-applied-ai-builder'
  )).toBe(true)
  expect(isExpectedSaveNavigation(
    'https://wellfound.com/jobs/starred?job_listing_slug=applied-ai-builder',
    'https://wellfound.com/jobs/starred?job_listing_slug=unrelated-role'
  )).toBe(false)
})


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

test('later-phase controls stay disabled while the browser surface is recoverable', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)

  for (const name of [
    'Start new agent session',
    'Send message'
  ]) {
    expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
  }
  expect((screen.getByRole('button', { name: 'Open settings' }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Research' }))
  expect((screen.getByRole('button', { name: 'Open a new tab' }) as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText('Browser available in the desktop app')).not.toBeNull()
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
        list: vi.fn().mockResolvedValue([{
          jobId: 'job-1', company: 'Example Co', title: 'Product Builder', status: 'discovered',
          statusGroup: 'Inbox', canonicalUrl: 'https://example.com/jobs/1',
          discoveredAt: '2026-07-20T00:00:00Z', lastSeenAt: '2026-07-20T01:00:00Z'
        }]),
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
  await waitFor(() => expect(select).toHaveBeenCalledWith('job-1'))
  await screen.findByText('Example Co · Product Builder')
  fireEvent.change(screen.getByRole('combobox', { name: 'Change Example Co status' }), {
    target: { value: 'reviewed' }
  })

  expect(updateStatus).toHaveBeenCalledWith('job-1', 'reviewed')
  expect(await screen.findByText('Status changed to reviewed')).not.toBeNull()
})

test('saving dispatches job-hunter and reconciles a failure emitted before send resolves', async () => {
  const listing = {
    companyName: 'Northstar Labs',
    title: 'Applied AI Product Builder',
    canonicalUrl: 'https://jobs.example.com/northstar',
    locationText: 'United States · Remote',
    descriptionText: 'Build useful agent workflows with operators.',
    applicationUrl: 'https://jobs.example.com/northstar/apply'
  }

  const browserTab = {
    tabId: 'job-tab', url: listing.canonicalUrl, title: listing.title, faviconUrl: null,
    associatedJobId: null as string | null, loading: false, canGoBack: false, canGoForward: false,
    error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [browserTab], activeTabId: browserTab.tabId, download: null, notice: null }
  const associate = vi.fn()
  let saveOutcome: 'idle' | 'failed' | 'completed' | 'running' | 'interrupted' = 'idle'
  let currentTurnId = ''
  let sendCount = 0
  let browserListener: (state: typeof browserState) => void = () => undefined
  const agentListeners: Array<(update: AgentStreamUpdate) => void> = []
  const cancel = vi.fn().mockResolvedValue(undefined)
  let successfulJob: JobListItem | null = null
  const saveFromBrowser = vi.fn().mockImplementation(async () => ({
    eventId: 9,
    created: true,
    associated: true,
    job: successfulJob
  }))
  const send = vi.fn().mockImplementation(async () => {
    sendCount += 1
    currentTurnId = `turn-save-job-${sendCount}`
    if (saveOutcome === 'idle') saveOutcome = 'failed'
    if (successfulJob) browserListener({
      ...browserState,
      tabs: [{ ...browserTab, associatedJobId: successfulJob.jobId }]
    })
    return { turnId: currentTurnId, status: saveOutcome }
  })
  const workspace = {
    revision: 1,
    selectedPreset: 'research' as const,
    layouts: {
      research: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
      review: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 280, center: 700, agent: 380 }, collapsed: [] },
      'agent-focus': { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
    },
    selectedJobId: null,
    activeCenterSurface: 'browser' as const,
    repairedPresets: [],
    browserTabs: [{ tabId: browserTab.tabId, url: browserTab.url, title: browserTab.title, faviconUrl: null, associatedJobId: null }],
    activeBrowserTabId: browserTab.tabId,
    repairedBrowser: false
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    agent: {
      get: vi.fn().mockImplementation(async () => ({
        conversationId: 'conv-1',
        entries: ['failed', 'completed', 'interrupted'].includes(saveOutcome) ? [{
          eventId: 1,
          turnId: currentTurnId,
          type: saveOutcome === 'failed' ? 'error' : saveOutcome === 'interrupted' ? 'status' : 'assistant_message',
          state: saveOutcome === 'failed' ? 'failed' : saveOutcome === 'interrupted' ? 'interrupted' : 'completed',
          summary: saveOutcome === 'failed' ? 'Agent connection unavailable'
            : successfulJob ? `JOBOS_SAVE_RESULT:${JSON.stringify({
                jobId: successfulJob.jobId,
                created: true
              })}` : 'Could not identify a job listing',
          detail: {}, occurredAt: ''
        }] : [],
        activeTurn: null, connection: 'online', latestEventId: ['failed', 'completed', 'interrupted'].includes(saveOutcome) ? 1 : 0
      })),
      send, cancel, retry: vi.fn(), reset: vi.fn(),
      subscribe: vi.fn(listener => { agentListeners.push(listener); return () => undefined })
    },
    jobs: {
      getState: vi.fn().mockImplementation(async () => ({
        jobs: successfulJob ? [successfulJob] : [], selectedJobId: null,
        sortMode: 'manual', manualOrder: []
      })),
      list: vi.fn().mockImplementation(async () => successfulJob ? [successfulJob] : []), select: vi.fn(), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), saveFromBrowser,
      subscribe: vi.fn(() => () => undefined)
    },
    workspace: { get: vi.fn().mockResolvedValue(workspace), save: vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 })) },
    browser: {
      getState: vi.fn().mockImplementation(async () => successfulJob ? {
        ...browserState,
        tabs: [{ ...browserTab, associatedJobId: successfulJob.jobId }]
      } : browserState),
      restore: vi.fn().mockResolvedValue(browserState),
      associate, create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
      reload: vi.fn(), stop: vi.fn(), copyBlockedUrl: vi.fn(), setBounds: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(listener => { browserListener = listener; return () => undefined })
    }
  } })

  render(<App />)
  await screen.findByRole('tab', { name: `Select ${listing.title}` })
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))

  await waitFor(() => expect(send).toHaveBeenCalledOnce())
  expect(send.mock.calls[0]?.[0]).toContain('job-tab')
  expect(send.mock.calls[0]?.[0]).toContain('mcp__jobos__browser_click')
  expect(send.mock.calls[0]?.[0]).toContain('link whose href or name matches the job slug')
  expect(send.mock.calls[0]?.[0]).toContain('JOBOS_SAVE_RESULT:')
  expect(send.mock.calls[0]?.[0]).toContain('mcp__jobos__job_create_from_browser')
  expect(send.mock.calls[0]?.[0]).toContain('mcp__jobos__browser_tab_associate')
  expect(send.mock.calls[0]?.[1]).toMatch(/^browser-save-/)
  expect(associate).not.toHaveBeenCalled()
  await screen.findByRole('alert')
  expect(screen.getByText('Agent connection unavailable')).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement).disabled).toBe(false)

  saveOutcome = 'completed'
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(2))
  expect(await screen.findByText('Job hunter finished without returning usable job details. You can retry.')).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement).disabled).toBe(false)

  saveOutcome = 'running'
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(3))
  act(() => browserListener({
    ...browserState,
    tabs: [{ ...browserTab, url: 'https://jobs.example.com/jobs/northstar' }]
  }))
  expect(cancel).not.toHaveBeenCalledWith('turn-save-job-3')
  browserListener({
    ...browserState,
    tabs: [{ ...browserTab, url: 'https://wellfound.com/jobs/another-listing' }]
  })
  await waitFor(() => expect(cancel).toHaveBeenCalledWith('turn-save-job-3'))
  expect(screen.getByText('The browser listing changed before saving finished. Retry on the intended listing.')).not.toBeNull()

  browserListener(browserState)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save this job to JobOS' }).textContent).toContain('Save job'))
  saveOutcome = 'running'
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(4))
  saveOutcome = 'interrupted'
  act(() => {
    agentListeners.forEach(listener => listener({ kind: 'event', event: {
      eventId: 2, turnId: currentTurnId, type: 'status', state: 'interrupted',
      summary: 'Turn stopped', detail: {}, occurredAt: ''
    } }))
  })
  expect(await screen.findByText('Job hunter finished without returning usable job details. You can retry.')).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement).disabled).toBe(false)

  successfulJob = {
    jobId: 'job-saved-by-turn',
    company: 'Northstar Labs',
    title: listing.title,
    status: 'discovered',
    statusGroup: 'Inbox',
    canonicalUrl: listing.canonicalUrl,
    discoveredAt: '2026-07-22T00:00:00Z',
    lastSeenAt: '2026-07-22T00:00:00Z'
  }
  saveOutcome = 'completed'
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(5))
  expect(saveFromBrowser).not.toHaveBeenCalled()
  expect(await screen.findByText(`Saved to JobOS: Northstar Labs · ${listing.title}`)).not.toBeNull()
  expect(await screen.findByRole('button', {
    name: `Select Northstar Labs ${listing.title}`
  })).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement).disabled).toBe(true)
})


test('changing jobs never selects, closes, navigates, or reassociates an unrelated browser tab', async () => {
  const jobs = [
    { jobId: 'job-1', company: 'Example Co', title: 'Product Builder', status: 'reviewed' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/jobs/1', discoveredAt: '', lastSeenAt: '' },
    { jobId: 'job-2', company: 'Northstar', title: 'Staff PM', status: 'shortlisted' as const, statusGroup: 'Considering', canonicalUrl: 'https://example.com/jobs/2', discoveredAt: '', lastSeenAt: '' }
  ]
  const browserTab = {
    tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', faviconUrl: null,
    associatedJobId: null, loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false
  }
  const browserActions = {
    select: vi.fn(), close: vi.fn(), navigate: vi.fn(), associate: vi.fn()
  }
  const workspace = {
    revision: 1,
    selectedPreset: 'research' as const,
    layouts: {
      research: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
      review: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 280, center: 700, agent: 380 }, collapsed: [] },
      'agent-focus': { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
    },
    selectedJobId: null,
    activeCenterSurface: 'browser' as const,
    repairedPresets: [],
    browserTabs: [{ tabId: 'gmail', url: browserTab.url, title: browserTab.title, faviconUrl: null, associatedJobId: null }],
    activeBrowserTabId: 'gmail',
    repairedBrowser: false
  }
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      jobs: {
        getState: vi.fn().mockResolvedValue({ jobs, selectedJobId: null, sortMode: 'manual', manualOrder: ['job-1', 'job-2'] }),
        list: vi.fn().mockResolvedValue(jobs), select: vi.fn().mockResolvedValue({ eventId: 1 }), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      },
      workspace: { get: vi.fn().mockResolvedValue(workspace), save: vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 })) },
      browser: {
        getState: vi.fn(),
        restore: vi.fn().mockResolvedValue({ tabs: [browserTab], activeTabId: 'gmail', download: null, notice: null }),
        create: vi.fn(), reorder: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), setBounds: vi.fn().mockResolvedValue(undefined),
        subscribe: vi.fn().mockReturnValue(() => undefined),
        ...browserActions
      }
    }
  })

  render(<App />)
  expect(await screen.findByRole('tab', { name: 'Select Gmail' })).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Select Northstar Staff PM' }))

  await waitFor(() => expect(screen.getByText('Northstar · Staff PM')).not.toBeNull())
  expect(screen.getByRole('tab', { name: 'Select Gmail' })).not.toBeNull()
  expect(browserActions.select).not.toHaveBeenCalled()
  expect(browserActions.close).not.toHaveBeenCalled()
  expect(browserActions.navigate).not.toHaveBeenCalled()
  expect(browserActions.associate).not.toHaveBeenCalled()
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

test('changing selected jobs preserves one mounted durable agent conversation and draft', async () => {
  const jobs = [
    { jobId: 'job-1', company: 'Northstar', title: 'Staff PM', status: 'reviewed' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/1', discoveredAt: '', lastSeenAt: '' },
    { jobId: 'job-2', company: 'Daybreak', title: 'Platform PM', status: 'shortlisted' as const, statusGroup: 'Considering', canonicalUrl: 'https://example.com/2', discoveredAt: '', lastSeenAt: '' }
  ]
  const conversationGet = vi.fn().mockResolvedValue({
    conversationId: 'conv-current', activeTurn: null, connection: 'online', latestEventId: 1,
    entries: [{ eventId: 1, turnId: 'turn-1', type: 'assistant_message', state: 'completed', summary: 'Persistent response', detail: { type: 'message.complete' }, occurredAt: '2026-07-20T10:00:00Z' }]
  })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', apiVersion: '0.1.0', checkedAt: '', message: 'Private API authenticated' }) },
    jobs: {
      getState: vi.fn().mockResolvedValue({ jobs, selectedJobId: 'job-1', sortMode: 'manual', manualOrder: ['job-1', 'job-2'] }),
      list: vi.fn().mockResolvedValue(jobs), select: vi.fn().mockResolvedValue({ eventId: 2 }), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), subscribe: vi.fn(() => () => undefined)
    },
    agent: { get: conversationGet, send: vi.fn(), cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn(() => () => undefined) }
  } })

  render(<App />)
  expect(await screen.findByText('Persistent response')).not.toBeNull()
  const composer = screen.getByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Keep this draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Select Daybreak Platform PM' }))

  await waitFor(() => expect(screen.getByText('Daybreak · Platform PM')).not.toBeNull())
  expect(screen.getByText('Persistent response')).not.toBeNull()
  expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Keep this draft')
  expect(conversationGet).toHaveBeenCalledOnce()
})

test('primary panels resize, collapse, reopen, and reorder with keyboard alternatives', async () => {
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      workspace: {
        get: vi.fn().mockResolvedValue({
          revision: 0,
          selectedPreset: 'review',
          layouts: {
            research: { order: ['jobs', 'center', 'agent'], widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
            review: { order: ['jobs', 'center', 'agent'], widths: { jobs: 280, center: 700, agent: 380 }, collapsed: [] },
            'agent-focus': { order: ['jobs', 'center', 'agent'], widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
          },
          selectedJobId: null,
          activeCenterSurface: 'document',
          repairedPresets: []
        }),
        save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
      }
    }
  })

  render(<App />)
  const separator = await screen.findByRole('separator', { name: 'Resize Job navigation and Center workspace' })
  fireEvent.keyDown(separator, { key: 'ArrowRight' })
  expect(screen.getByText(/Job navigation 300 pixels/)).not.toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Collapse Job navigation' }))
  expect(screen.getByRole('button', { name: 'Reopen Job navigation' })).not.toBeNull()
  expect(screen.queryByRole('complementary', { name: 'Job navigation' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Reopen Job navigation' }))
  expect(screen.getByRole('navigation', { name: 'Workspace layouts' })).not.toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Move Agent chat left' }))
  expect(panelDomOrder()).toEqual(['jobs', 'agent', 'center'])
})

test('an early layout action is rebased onto delayed startup restoration and persisted', async () => {
  let resolveWorkspace!: (workspace: ReturnType<typeof restoredWorkspace>) => void
  const get = vi.fn().mockReturnValue(new Promise(resolve => { resolveWorkspace = resolve }))
  const save = vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      workspace: { get, save }
    }
  })

  render(<App />)
  fireEvent.click(screen.getByRole('button', { name: 'Research' }))
  expect(screen.getByRole('button', { name: 'Research' }).getAttribute('aria-pressed')).toBe('true')

  await act(async () => resolveWorkspace(restoredWorkspace(7)))

  expect(screen.getByRole('button', { name: 'Research' }).getAttribute('aria-pressed')).toBe('true')
  await waitFor(() => expect(save).toHaveBeenCalled())
  expect(save.mock.calls.at(-1)?.[0]).toMatchObject({ revision: 7, selectedPreset: 'research' })
})

test('a first action after failed startup hydration replays over remote state after a save conflict', async () => {
  let rejectInitialGet!: (error: Error) => void
  const remote = remoteWorkspace(11)
  const get = vi.fn()
    .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectInitialGet = reject }))
    .mockResolvedValueOnce(remote)
  const save = vi.fn()
    .mockRejectedValueOnce(new Error('revision conflict'))
    .mockImplementationOnce(snapshot => Promise.resolve({ ...snapshot, revision: 12 }))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      workspace: { get, save }
    }
  })

  render(<App />)
  await act(async () => rejectInitialGet(new Error('startup unavailable')))
  await waitFor(() => expect(screen.getByText('Using safe default layout')).not.toBeNull())

  fireEvent.click(screen.getByRole('button', { name: 'Move Agent chat left' }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2))

  const recoveredSave = save.mock.calls[1]?.[0]
  if (!recoveredSave) throw new Error('Recovered workspace save was not captured')
  expect(get).toHaveBeenCalledTimes(2)
  expect(recoveredSave).toMatchObject({
    revision: 11,
    selectedPreset: 'agent-focus',
    activeCenterSurface: 'document',
    selectedJobId: 'remote-job'
  })
  expect(recoveredSave.layouts.research).toEqual(remote.layouts.research)
  expect(recoveredSave.layouts.review).toEqual(remote.layouts.review)
  expect(recoveredSave.layouts['agent-focus']).toEqual({
    ...remote.layouts['agent-focus'],
    order: ['center', 'agent', 'jobs']
  })
  expect(panelDomOrder()).toEqual(['center', 'agent', 'jobs'])
})

test('a synthesized default tab is not replayed over authoritative browser state after startup recovery', async () => {
  let rejectInitialGet!: (error: Error) => void
  const authoritativeTabs = [
    { tabId: 'gmail', url: 'https://mail.google.com/mail/u/0/', title: 'Gmail', faviconUrl: null, associatedJobId: null },
    { tabId: 'listing', url: 'https://jobs.example.com/roles/7', title: 'Listing', faviconUrl: null, associatedJobId: 'job-7' }
  ]
  const remote = {
    ...remoteWorkspace(11),
    browserTabs: authoritativeTabs,
    activeBrowserTabId: 'listing',
    repairedBrowser: false
  }
  const defaultTab = {
    tabId: 'default-google', url: 'https://www.google.com/', title: 'Google', faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const authoritativeState = {
    tabs: authoritativeTabs.map(tab => ({ ...tab, loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null })),
    activeTabId: 'listing', download: null, notice: null
  }
  const get = vi.fn()
    .mockReturnValueOnce(new Promise((_resolve, reject) => { rejectInitialGet = reject }))
    .mockResolvedValueOnce(remote)
  const save = vi.fn()
    .mockRejectedValueOnce(new Error('revision conflict'))
    .mockImplementationOnce(snapshot => Promise.resolve({ ...snapshot, revision: 12 }))
  const restore = vi.fn()
    .mockResolvedValueOnce({ tabs: [defaultTab], activeTabId: defaultTab.tabId, download: null, notice: null })
    .mockResolvedValueOnce(authoritativeState)
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      workspace: { get, save },
      browser: {
        restore, getState: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined), setBounds: vi.fn().mockResolvedValue(undefined),
        create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
        reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
      }
    }
  })

  render(<App />)
  await act(async () => rejectInitialGet(new Error('startup unavailable')))
  await waitFor(() => expect(restore).toHaveBeenCalledWith({ tabs: [], activeTabId: null }))
  expect(save).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Move Agent chat left' }))
  await waitFor(() => expect(save).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(restore).toHaveBeenLastCalledWith({ tabs: authoritativeTabs, activeTabId: 'listing' }))

  expect(save.mock.calls[1]?.[0]).toMatchObject({
    revision: 11,
    browserTabs: authoritativeTabs,
    activeBrowserTabId: 'listing'
  })
})

test('browser tabs expose valid keyboard tab semantics, adjacent actions, and focus tooltips', async () => {
  const metadata = [
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', faviconUrl: null, associatedJobId: null },
    { tabId: 'listing', url: 'https://jobs.example.com/7', title: 'Listing', faviconUrl: null, associatedJobId: null }
  ]
  const browserState = (activeTabId: string) => ({
    tabs: metadata.map(tab => ({ ...tab, loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null })),
    activeTabId, download: null, notice: null
  })
  const select = vi.fn().mockImplementation(tabId => Promise.resolve(browserState(tabId)))
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      workspace: {
        get: vi.fn().mockResolvedValue({ ...restoredWorkspace(2), selectedPreset: 'research', activeCenterSurface: 'browser', browserTabs: metadata, activeBrowserTabId: 'gmail', repairedBrowser: true, browserRepairReasons: ['dropped_tabs', 'reselected_active_tab'] }),
        save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
      },
      browser: {
        restore: vi.fn().mockResolvedValue(browserState('gmail')), select,
        getState: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined), setBounds: vi.fn().mockResolvedValue(undefined),
        create: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
        reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
      }
    }
  })

  render(<App />)
  const gmail = await screen.findByRole('tab', { name: 'Select Gmail' })
  const listing = screen.getByRole('tab', { name: 'Select Listing' })
  expect(gmail.querySelector('button')).toBeNull()
  expect(gmail.getAttribute('aria-selected')).toBe('true')
  expect(gmail.getAttribute('tabindex')).toBe('0')
  expect(listing.getAttribute('tabindex')).toBe('-1')
  expect(screen.getAllByText(/invalid saved tabs were skipped; a recoverable active tab was selected/)).toHaveLength(2)
  fireEvent.keyDown(gmail, { key: 'ArrowRight' })
  await waitFor(() => expect(select).toHaveBeenCalledWith('listing'))
  expect(document.activeElement).toBe(listing)
  await screen.findByRole('button', { name: 'Close Listing' })

  for (const name of ['Select Gmail', 'Select Listing', 'Move Listing left', 'Move Listing right', 'Close Listing', 'Open a new tab', 'Back', 'Forward', 'Reload']) {
    const control = screen.getByRole(name.startsWith('Select') ? 'tab' : 'button', { name })
    expect(control.getAttribute('data-tooltip')).toBe(name)
    expect(control.getAttribute('aria-label')).toBe(name)
  }

  fireEvent.mouseEnter(listing)
  expect(screen.getByRole('tooltip').textContent).toBe('Select Listing')
  expect(screen.getByRole('tooltip').parentElement).toBe(document.body)
  fireEvent.mouseLeave(listing)
  expect(screen.queryByRole('tooltip')).toBeNull()

  const reorder = screen.getByRole('button', { name: 'Move Listing left' })
  fireEvent.focus(reorder)
  expect(screen.getByRole('tooltip').textContent).toBe('Move Listing left')
  fireEvent.blur(reorder)

  const close = screen.getByRole('button', { name: 'Close Listing' })
  fireEvent.mouseEnter(close)
  expect(screen.getByRole('tooltip').textContent).toBe('Close Listing')
  fireEvent.mouseLeave(close)

  const add = screen.getByRole('button', { name: 'Open a new tab' })
  fireEvent.focus(add)
  expect(screen.getByRole('tooltip').textContent).toBe('Open a new tab')
})

test.each([
  [['protected_title'], 'Credential-like title metadata was protected. No browser tabs were lost.'],
  [['dropped_tabs'], 'Browser metadata was repaired: invalid saved tabs were skipped.'],
  [['reselected_active_tab'], 'Browser metadata was repaired: a recoverable active tab was selected.'],
  [['protected_title', 'dropped_tabs', 'reselected_active_tab'], 'Browser metadata was repaired: credential-like title metadata was protected; invalid saved tabs were skipped; a recoverable active tab was selected.']
] as const)('announces %j browser repairs accurately in the live region', async (reasons, expected) => {
  const metadata = [{ tabId: 'safe', url: 'https://example.com/', title: 'Safe', faviconUrl: null, associatedJobId: null }]
  const browserState = {
    tabs: metadata.map(tab => ({ ...tab, loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null })),
    activeTabId: 'safe', download: null, notice: null
  }
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      workspace: {
        get: vi.fn().mockResolvedValue({
          ...restoredWorkspace(2), selectedPreset: 'research', activeCenterSurface: 'browser',
          browserTabs: metadata, activeBrowserTabId: 'safe', repairedBrowser: true,
          browserRepairReasons: [...reasons]
        }),
        save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
      },
      browser: {
        restore: vi.fn().mockResolvedValue(browserState), getState: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined),
        setBounds: vi.fn().mockResolvedValue(undefined), create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(),
        navigate: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
      }
    }
  })

  render(<App />)

  await waitFor(() => {
    const messages = screen.getAllByText(expected)
    expect(messages.some(message => message.getAttribute('role') === 'status')).toBe(true)
  })
})

test('layout changes preserve mounted content surface identities', async () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)
  const center = screen.getByRole('main')
  const agent = screen.getByRole('complementary', { name: 'Agent chat' })

  fireEvent.click(screen.getByRole('button', { name: 'Agent Focus' }))

  expect(screen.getByRole('main')).toBe(center)
  expect(screen.getByRole('complementary', { name: 'Agent chat' })).toBe(agent)
})

test('reordering aligns DOM, focus, and reading order without remounting surfaces', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)
  const jobs = screen.getByTestId('panel-jobs')
  const center = screen.getByTestId('panel-center')
  const agent = screen.getByTestId('panel-agent')

  fireEvent.click(screen.getByRole('button', { name: 'Move Agent chat left' }))

  expect(panelDomOrder()).toEqual(['jobs', 'agent', 'center'])
  expect(screen.getByTestId('panel-jobs')).toBe(jobs)
  expect(screen.getByTestId('panel-center')).toBe(center)
  expect(screen.getByTestId('panel-agent')).toBe(agent)
  const collapseControls = screen.getAllByRole('button', { name: /^Collapse / })
  expect(collapseControls.map(control => control.getAttribute('aria-label'))).toEqual([
    'Collapse Job navigation',
    'Collapse Agent chat',
    'Collapse Center workspace'
  ])
})

test('collapse and reopen transfer focus and expose the controlled panel state', async () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)
  const collapse = screen.getByRole('button', { name: 'Collapse Center workspace' })
  collapse.focus()

  fireEvent.click(collapse)
  const reopen = screen.getByRole('button', { name: 'Reopen Center workspace' })
  await waitFor(() => expect(document.activeElement).toBe(reopen))
  expect(reopen.getAttribute('aria-controls')).toBe('workbench-panel-center')
  expect(reopen.getAttribute('aria-expanded')).toBe('false')
  expect(screen.getByTestId('panel-center').id).toBe('workbench-panel-center')
  expect(screen.getByTestId('panel-center').hidden).toBe(true)

  fireEvent.click(reopen)
  const restoredCollapse = screen.getByRole('button', { name: 'Collapse Center workspace' })
  await waitFor(() => expect(document.activeElement).toBe(restoredCollapse))
  expect(restoredCollapse.getAttribute('aria-controls')).toBe('workbench-panel-center')
  expect(restoredCollapse.getAttribute('aria-expanded')).toBe('true')
})

test('pointer resizing tracks movement and every panel has a recovery affordance', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)
  const separator = screen.getByRole('separator', { name: 'Resize Job navigation and Center workspace' })

  fireEvent.pointerDown(separator, { clientX: 100, pointerId: 1 })
  fireEvent.pointerMove(window, { clientX: 140, pointerId: 1 })
  fireEvent.pointerUp(window, { clientX: 140, pointerId: 1 })
  expect(separator.getAttribute('aria-valuenow')).toBe('320')

  for (const panel of ['Job navigation', 'Center workspace', 'Agent chat']) {
    fireEvent.click(screen.getByRole('button', { name: `Collapse ${panel}` }))
    const reopen = screen.getByRole('button', { name: `Reopen ${panel}` })
    expect(reopen.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(reopen)
  }
})

test('drag reordering shows an insertion preview before changing presentation only', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)
  const source = screen.getByRole('button', { name: 'Reorder Agent chat' })
  const target = screen.getByTestId('panel-jobs')
  const transfer = { getData: vi.fn().mockReturnValue('agent'), setData: vi.fn(), effectAllowed: '' }

  fireEvent.dragStart(source, { dataTransfer: transfer })
  fireEvent.dragOver(target, { dataTransfer: transfer })
  expect(target.classList.contains('insertion-target')).toBe(true)
  fireEvent.drop(target, { dataTransfer: transfer })

  expect(panelDomOrder()).toEqual(['agent', 'jobs', 'center'])
  expect(target.classList.contains('insertion-target')).toBe(false)
})

function panelDomOrder() {
  return Array.from(document.querySelector('.workbench')?.children ?? []).map(panel =>
    panel.getAttribute('data-testid')?.replace('panel-', '')
  )
}

function restoredWorkspace(revision: number) {
  return {
    revision,
    selectedPreset: 'review' as const,
    layouts: {
      research: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
      review: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 300, center: 680, agent: 380 }, collapsed: [] },
      'agent-focus': { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
    },
    selectedJobId: null,
    activeCenterSurface: 'document' as const,
    repairedPresets: []
  }
}

function remoteWorkspace(revision: number) {
  return {
    revision,
    selectedPreset: 'agent-focus' as const,
    layouts: {
      research: { order: ['agent', 'jobs', 'center'] as const, widths: { jobs: 333, center: 811, agent: 377 }, collapsed: ['jobs'] as const },
      review: { order: ['center', 'agent', 'jobs'] as const, widths: { jobs: 301, center: 843, agent: 419 }, collapsed: ['agent'] as const },
      'agent-focus': { order: ['center', 'jobs', 'agent'] as const, widths: { jobs: 245, center: 465, agent: 721 }, collapsed: ['center'] as const }
    },
    selectedJobId: 'remote-job',
    activeCenterSurface: 'document' as const,
    repairedPresets: []
  }
}
