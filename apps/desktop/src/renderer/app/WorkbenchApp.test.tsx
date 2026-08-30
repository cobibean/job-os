import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type {
  AgentSessionStreamUpdate,
  ConnectedAgentSummary,
  ConnectedAgentsSnapshot,
  JobListItem
} from '../../shared/contracts'
import { WorkbenchApp, requireDefaultAgentModel, requireDefaultConnectedAgent } from './WorkbenchApp'

afterEach(() => { cleanup(); window.localStorage.clear() })

const emptyJobContext = {
  selectedJobId: null,
  activeArtifactId: null,
  activeArtifactPage: 1,
  activeArtifactZoom: 1
}

const readyDefaultAgent: ConnectedAgentSummary = {
  id: 'agent-codex',
  provider: 'codex',
  displayName: 'Codex',
  avatarId: 'codex',
  defaultModelId: 'gpt-live',
  defaultReasoningEffort: 'medium',
  lifecycle: 'connected',
  accountSummary: null,
  accountFingerprint: null,
  health: {
    state: 'ready',
    label: 'Ready',
    providerAvailable: true,
    toolsAvailable: true,
    retryAfterSeconds: null
  },
  activeChats: 0,
  lockedChats: 0
}

const connectedAgentsSnapshot: ConnectedAgentsSnapshot = {
  registryRevision: 2,
  profileId: 'profile-1',
  defaultConnectedAgentId: readyDefaultAgent.id,
  agents: [readyDefaultAgent]
}

test('browser save requires the exact configured default agent without silent fallback', () => {
  expect(requireDefaultConnectedAgent(connectedAgentsSnapshot)).toBe(readyDefaultAgent)
  expect(() => requireDefaultConnectedAgent({
    ...connectedAgentsSnapshot,
    defaultConnectedAgentId: 'missing-agent',
    agents: [readyDefaultAgent, { ...readyDefaultAgent, id: 'other-ready-agent' }]
  })).toThrow('Default Connected Agent is unavailable')
})

test('browser save requires the exact live default model and reasoning effort', () => {
  expect(requireDefaultAgentModel(readyDefaultAgent, {
    live: true,
    models: [{ modelId: 'gpt-live', displayName: 'GPT Live', reasoningEfforts: ['low', 'medium'] }]
  })).toEqual({ modelId: 'gpt-live', reasoningEffort: 'medium' })
  expect(() => requireDefaultAgentModel(readyDefaultAgent, {
    live: true,
    models: [{ modelId: 'different-model', displayName: 'Different', reasoningEfforts: ['medium'] }]
  })).toThrow('Default model is unavailable')
  expect(() => requireDefaultAgentModel({ ...readyDefaultAgent, defaultReasoningEffort: 'high' }, {
    live: true,
    models: [{ modelId: 'gpt-live', displayName: 'GPT Live', reasoningEfforts: ['medium'] }]
  })).toThrow('Default reasoning effort is unavailable')
})

test('the shell reports authenticated local connectivity without exposing credentials', async () => {
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

  render(<WorkbenchApp />)

  expect(screen.getByText('Connecting to local service…')).not.toBeNull()
  expect(await screen.findByText('Local service connected')).not.toBeNull()
  expect(screen.getByText('API 0.1.0')).not.toBeNull()
  expect(getConnectivity).toHaveBeenCalledOnce()
  expect(JSON.stringify(window.jobos)).not.toContain('test-device-token')
})

test('a profile change before the first probe replaces the workspace with restart recovery', async () => {
  const profileA = 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
  const profileB = 'jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
  const connectivity = vi.fn().mockResolvedValue({
      state: 'connected',
      checkedAt: '2026-08-23T12:01:00Z',
      message: 'Connected',
      installationProfileId: profileB,
      installationProfileName: 'Fresh setup',
      profileRegistryRevision: 2
    })
  const restart = vi.fn()
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: connectivity },
    installationProfiles: {
      expectedProfileId: profileA,
      list: vi.fn().mockResolvedValue({
        registryRevision: 1,
        activeProfileId: profileA,
        profiles: [{
          profileId: profileA,
          displayName: 'Personal',
          active: true,
          createdAt: '2026-08-23T12:00:00Z',
          updatedAt: '2026-08-23T12:00:00Z'
        }]
      }),
      activate: vi.fn(),
      createAndSwitch: vi.fn(),
      rename: vi.fn(),
      restart
    }
  } })

  render(<WorkbenchApp />)
  await waitFor(() => expect(connectivity).toHaveBeenCalledOnce())

  expect(await screen.findByRole('heading', {
    name: 'JobOS switched to “Fresh setup” on another device.'
  })).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Restart JobOS' }))
  expect(restart).toHaveBeenCalledOnce()
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

  render(<WorkbenchApp />)
  const research = screen.getByRole('button', { name: 'Research' })
  fireEvent.click(research)
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))

  expect(research.getAttribute('aria-pressed')).toBe('true')
})

test('later-phase controls stay disabled while the browser surface is recoverable', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<WorkbenchApp />)

  for (const name of [
    'New agent session',
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

test('exact Command shortcuts create and select sessions from the composer without handling missing positions', async () => {
  let created = 1
  const create = vi.fn(async () => {
    created += 1
    return {
      conversationId: `conv-${created}`, position: created, title: `Session ${created}`, createdAt: '',
      entries: [], activeTurn: null, connection: 'online' as const, latestEventId: 0, jobContext: emptyJobContext
    }
  })
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent: {
    list: vi.fn().mockResolvedValue([{ conversationId: 'conv-1', position: 1, title: 'Session 1', createdAt: '', activeTurn: null, connection: 'online', latestEventId: 0, jobContext: emptyJobContext }]),
    get: vi.fn().mockResolvedValue({ conversationId: 'conv-1', position: 1, title: 'Session 1', createdAt: '', entries: [], activeTurn: null, connection: 'online', latestEventId: 0, jobContext: emptyJobContext }),
    create, archive: vi.fn(), send: vi.fn(), cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn(() => () => undefined)
  } } })
  render(<WorkbenchApp />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'keep this draft' } })
  const createEvent = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'n', metaKey: true })
  composer.dispatchEvent(createEvent)
  expect(createEvent.defaultPrevented).toBe(true)
  expect(await screen.findByRole('dialog', { name: 'Pick the agent for this chat' })).not.toBeNull()
  expect(create).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Close New Chat' }))

  const missing = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: '5', metaKey: true })
  composer.dispatchEvent(missing)
  expect(missing.defaultPrevented).toBe(false)
  const select = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: '1', metaKey: true })
  act(() => { composer.dispatchEvent(select) })
  expect(select.defaultPrevented).toBe(true)
  expect(screen.getByRole('tab', { name: 'Session 1, Idle' }).getAttribute('aria-selected')).toBe('true')
  expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('keep this draft')
  expect(create).not.toHaveBeenCalled()
})

test('Command 1 through Command 5 map to visible positions and Command N announces the five-session cap', async () => {
  const summaries = Array.from({ length: 5 }, (_, index) => ({
    conversationId: `conv-${index + 1}`, position: index + 1, title: `Session ${index + 1}`, createdAt: '',
    activeTurn: null, connection: 'online' as const, latestEventId: 0, jobContext: emptyJobContext
  }))
  const create = vi.fn()
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent: {
    list: vi.fn().mockResolvedValue(summaries),
    get: vi.fn((id: string) => Promise.resolve({ ...summaries.find(item => item.conversationId === id)!, entries: [] })),
    create, archive: vi.fn(), send: vi.fn(), cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn(() => () => undefined)
  } } })
  render(<WorkbenchApp />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' })
  await screen.findByRole('tab', { name: 'Session 5, Idle' })
  for (const key of ['1', '2', '3', '4', '5']) {
    const shortcut = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key, metaKey: true })
    act(() => { composer.dispatchEvent(shortcut) })
    expect(shortcut.defaultPrevented).toBe(true)
    expect(screen.getByRole('tab', { name: `Session ${key}, Idle` }).getAttribute('aria-selected')).toBe('true')
  }
  const capped = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'n', metaKey: true })
  act(() => { composer.dispatchEvent(capped) })
  expect(capped.defaultPrevented).toBe(true)
  expect(await screen.findByText('Five chats are already open')).not.toBeNull()
  expect(create).not.toHaveBeenCalled()
})

test('Command session shortcuts are suppressed while Settings or any modal is open', async () => {
  const summaries = [1, 2].map(position => ({
    conversationId: `conv-${position}`, position, title: `Session ${position}`, createdAt: '',
    activeTurn: null, connection: 'online' as const, latestEventId: 0, jobContext: emptyJobContext
  }))
  const create = vi.fn()
  Object.defineProperty(window, 'jobos', { configurable: true, value: { agent: {
    list: vi.fn().mockResolvedValue(summaries),
    get: vi.fn((id: string) => Promise.resolve({ ...summaries.find(item => item.conversationId === id)!, entries: [] })),
    create, archive: vi.fn(), send: vi.fn(), cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn(() => () => undefined)
  } } })
  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('tab', { name: 'Session 2, Idle' }))
  fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))
  const settings = await screen.findByRole('dialog', { name: 'Settings' })

  for (const key of ['n', '1']) {
    const shortcut = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key, metaKey: true })
    settings.dispatchEvent(shortcut)
    expect(shortcut.defaultPrevented).toBe(true)
  }
  expect(create).not.toHaveBeenCalled()
  expect(screen.getByRole('tab', { name: 'Session 2, Idle', hidden: true }).getAttribute('aria-selected')).toBe('true')

  fireEvent.click(screen.getByRole('button', { name: 'Close settings' }))
  const modal = document.createElement('div')
  modal.setAttribute('role', 'alertdialog')
  document.body.append(modal)
  const modalShortcut = new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'n', metaKey: true })
  modal.dispatchEvent(modalShortcut)
  expect(modalShortcut.defaultPrevented).toBe(true)
  expect(create).not.toHaveBeenCalled()
  modal.remove()
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
      message: 'Local service unavailable'
    })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectivity: { get } }
  })

  render(<WorkbenchApp />)
  expect(await screen.findByText('Local service authentication failed')).not.toBeNull()
  fireEvent.focus(window)
  expect(await screen.findByText('Local service unavailable')).not.toBeNull()
})

test('real jobs render compactly and user selection and status use the shared bridge', async () => {
  const select = vi.fn().mockImplementation(async (_conversationId, jobId) => ({ ...emptyJobContext, selectedJobId: jobId }))
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

  render(<WorkbenchApp />)
  const job = await screen.findByRole('button', { name: 'Select Example Co Product Builder' })
  fireEvent.click(job)
  await waitFor(() => expect(select).toHaveBeenCalledWith('conv_unavailable', 'job-1'))
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
  const createBrowserTab = vi.fn()
  let saveOutcome: 'idle' | 'failed' | 'completed' | 'running' | 'interrupted' = 'idle'
  let currentTurnId = ''
  let currentConversationId = ''
  let sendCount = 0
  let browserListener: (state: typeof browserState) => void = () => undefined
  const agentListeners: Array<(update: AgentSessionStreamUpdate) => void> = []
  const cancel = vi.fn().mockResolvedValue(undefined)
  let successfulJob: JobListItem | null = null
  const saveFromBrowser = vi.fn().mockImplementation(async () => ({
    eventId: 9,
    created: true,
    associated: true,
    job: successfulJob
  }))
  const send = vi.fn().mockImplementation(async (conversationId: string) => {
    sendCount += 1
    currentConversationId = conversationId
    currentTurnId = `turn-save-job-${sendCount}`
    if (saveOutcome === 'idle') saveOutcome = 'failed'
    if (successfulJob) browserListener({
      ...browserState,
      tabs: [{ ...browserTab, associatedJobId: successfulJob.jobId }]
    })
    return { turnId: currentTurnId, status: saveOutcome }
  })
  const getConversation = vi.fn().mockImplementation(async (conversationId: string) => ({
    conversationId, position: conversationId === 'conv-2' ? 2 : 1,
    title: conversationId === 'conv-2' ? 'Session 2' : 'Session 1', createdAt: '',
    entries: conversationId === currentConversationId && ['failed', 'completed', 'interrupted'].includes(saveOutcome) ? [{
      eventId: 1,
      turnId: currentTurnId,
      type: saveOutcome === 'failed' ? 'error' : saveOutcome === 'interrupted' ? 'status' : 'assistant_message',
      state: saveOutcome === 'failed' ? 'failed' : saveOutcome === 'interrupted' ? 'interrupted' : 'completed',
      summary: saveOutcome === 'failed' ? 'Agent connection unavailable'
      : successfulJob ? `JOBOS_SAVE_RESULT:${JSON.stringify({
        jobId: successfulJob.jobId,
        created: true,
        tabId: browserTab.tabId
      })}` : 'Could not identify a job listing',
      detail: {}, occurredAt: ''
    }] : [],
    activeTurn: null, connection: 'online', latestEventId: conversationId === currentConversationId && ['failed', 'completed', 'interrupted'].includes(saveOutcome) ? 1 : 0,
    jobContext: emptyJobContext
  }))
  const createSession = vi.fn().mockImplementation(async (initialJobId: string | null) => {
    const position = createSession.mock.calls.length
    return {
      conversationId: `conv-${position}`, position, title: `Session ${position}`, createdAt: '', entries: [],
      activeTurn: null, connection: 'online', latestEventId: 0,
      jobContext: { ...emptyJobContext, selectedJobId: initialJobId }
    }
  })
  const selectJob = vi.fn().mockImplementation(async (conversationId: string, jobId: string) => ({
    ...emptyJobContext,
    selectedJobId: jobId
  }))
  const connectedAgentModels = vi.fn().mockResolvedValue({ live: true, models: [{ modelId: '(FAKE)-model-stable', displayName: '(FAKE) Stable', reasoningEfforts: ['medium'] }] })
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
    connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', apiVersion: '0.1.0', checkedAt: '', message: 'Private API authenticated' }) },
    agent: {
      list: vi.fn().mockResolvedValue([]),
      create: createSession, archive: vi.fn(),
      get: getConversation,
      send, cancel, retry: vi.fn(),
      subscribe: vi.fn(listener => { agentListeners.push(listener); return () => undefined })
    },
    connectedAgents: {
      list: vi.fn().mockResolvedValue({
        registryRevision: 3,
        profileId: 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        defaultConnectedAgentId: 'agent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        agents: [{
          id: 'agent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', provider: 'hermes', displayName: 'Devonte', avatarId: 'default',
          defaultModelId: '(FAKE)-model-stable', defaultReasoningEffort: 'medium', lifecycle: 'connected', accountSummary: null,
          health: { state: 'ready', label: 'Ready', providerAvailable: true, toolsAvailable: true, retryAfterSeconds: null },
          activeChats: 0, lockedChats: 0
        }]
      }),
      models: connectedAgentModels
    },
    jobs: {
      getState: vi.fn().mockImplementation(async () => ({
        jobs: successfulJob ? [successfulJob] : [], selectedJobId: null,
        sortMode: 'manual', manualOrder: []
      })),
      list: vi.fn().mockImplementation(async () => successfulJob ? [successfulJob] : []), select: selectJob, reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), saveFromBrowser,
      subscribe: vi.fn(() => () => undefined)
    },
    workspace: { get: vi.fn().mockResolvedValue(workspace), save: vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 })) },
    browser: {
      getState: vi.fn().mockImplementation(async () => successfulJob ? {
        ...browserState,
        tabs: [{ ...browserTab, associatedJobId: successfulJob.jobId }]
      } : browserState),
      restore: vi.fn().mockResolvedValue(browserState),
      associate, create: createBrowserTab, select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
      reload: vi.fn(), stop: vi.fn(), copyBlockedUrl: vi.fn(), setBounds: vi.fn().mockResolvedValue(undefined),
      subscribe: vi.fn(listener => { browserListener = listener; return () => undefined })
    }
  } })

  render(<WorkbenchApp />)
  await screen.findByRole('tab', { name: `Select ${listing.title}` })
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))

  await waitFor(() => expect(send).toHaveBeenCalledOnce())
  expect(createSession).toHaveBeenLastCalledWith(expect.objectContaining({
    connectedAgentId: 'agent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    modelId: '(FAKE)-model-stable',
    reasoningEffort: 'medium'
  }))
  expect(connectedAgentModels).toHaveBeenCalledWith('agent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
  expect(send.mock.calls[0]?.[0]).toBe('conv-1')
  expect(send.mock.calls[0]?.[1]).toContain('job-tab')
  expect(send.mock.calls[0]?.[1]).toContain(listing.canonicalUrl)
  expect(send.mock.calls[0]?.[2]).toMatch(/^browser-save-/)
  expect(associate).not.toHaveBeenCalled()
  await waitFor(() => expect(getConversation).toHaveBeenCalledWith('conv-1'))
  expect(await screen.findByText('Agent connection unavailable')).not.toBeNull()
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
  expect(cancel).not.toHaveBeenCalledWith('conv-3', 'turn-save-job-3')
  const firstConcurrentTurnId = currentTurnId
  const secondTab = {
    ...browserTab,
    tabId: 'second-job-tab',
    url: 'https://jobs.example.com/second-role',
    title: 'Second role'
  }
  act(() => browserListener({
    ...browserState,
    activeTabId: secondTab.tabId,
    tabs: [browserTab, secondTab]
  }))
  expect((screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Save this job to JobOS' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(4))
  expect(send.mock.calls[3]?.[1]).toContain(secondTab.tabId)
  expect(send.mock.calls[3]?.[1]).toContain(secondTab.url)
  expect(cancel).not.toHaveBeenCalled()

  const getsBeforeWrongOwner = getConversation.mock.calls.length
  act(() => {
    agentListeners.forEach(listener => listener({ kind: 'event', conversationId: 'conv-2', recoveryState: 'ready', event: {
      eventId: 2, turnId: currentTurnId, type: 'status', state: 'interrupted',
      summary: 'Wrong owner stopped', detail: {}, occurredAt: ''
    } }))
  })
  await waitFor(() => expect(getConversation).toHaveBeenCalledTimes(getsBeforeWrongOwner))

  saveOutcome = 'interrupted'
  act(() => {
    agentListeners.forEach(listener => listener({ kind: 'event', conversationId: 'conv-4', recoveryState: 'ready', event: {
      eventId: 2, turnId: currentTurnId, type: 'status', state: 'interrupted',
      summary: 'Turn stopped', detail: {}, occurredAt: ''
    } }))
  })
  expect(await screen.findByText('Job hunter finished without returning usable job details. You can retry.')).not.toBeNull()

  currentConversationId = 'conv-3'
  currentTurnId = firstConcurrentTurnId
  act(() => {
    agentListeners.forEach(listener => listener({ kind: 'event', conversationId: 'conv-3', recoveryState: 'ready', event: {
      eventId: 3, turnId: firstConcurrentTurnId, type: 'status', state: 'interrupted',
      summary: 'First turn stopped', detail: {}, occurredAt: ''
    } }))
  })

  browserListener(browserState)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Save this job to JobOS' }).textContent).toContain('Save job'))

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
  expect(createBrowserTab).not.toHaveBeenCalled()
  expect(createSession).toHaveBeenCalledTimes(5)
  expect(createSession.mock.calls.every(call => (
    call[0]?.connectedAgentId === 'agent_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    && call[0]?.modelId === '(FAKE)-model-stable'
    && call[0]?.reasoningEffort === 'medium'
  ))).toBe(true)
  expect(selectJob).toHaveBeenCalledWith('conv-5', 'job-saved-by-turn')
  expect(await screen.findByText(`Saved to JobOS: Northstar Labs · ${listing.title}`)).not.toBeNull()
  expect(await screen.findByRole('button', {
    name: `Select Northstar Labs ${listing.title}`
  })).not.toBeNull()
  expect((screen.getByRole('button', { name: 'Save this job to JobOS' }) as HTMLButtonElement).disabled).toBe(true)

  const composer = screen.getByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Continue ordinary Hermes work' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(send).toHaveBeenCalledTimes(6))
  expect(send.mock.calls[5]).toEqual([
    'conv-5', 'Continue ordinary Hermes work', expect.stringMatching(/^desktop-message-/)
  ])
})


const navigationJobs = [
  { jobId: 'job-1', company: 'Example Co', title: 'Product Builder', status: 'reviewed' as const, statusGroup: 'Inbox', canonicalUrl: 'https://example.com/jobs/1', discoveredAt: '', lastSeenAt: '' },
  { jobId: 'job-2', company: 'Northstar', title: 'Staff PM', status: 'shortlisted' as const, statusGroup: 'Considering', canonicalUrl: 'https://example.com/jobs/2', discoveredAt: '', lastSeenAt: '' }
]

function setupJobListingNavigation(initialTabs: Array<{
  tabId: string
  url: string
  title: string
  associatedJobId: string | null
}>, selectedJobId: string | null = null) {
  const browserTabs = initialTabs.map(tab => ({
    ...tab,
    faviconUrl: null,
    loading: false,
    canGoBack: false,
    canGoForward: false,
    error: null,
    crashed: false,
    blockedUrl: null
  }))
  const browserState = (activeTabId: string, tabs = browserTabs) => ({
    tabs, activeTabId, download: null, notice: null
  })
  const create = vi.fn((url: string, jobId: string) => Promise.resolve(browserState('created', [
    ...browserTabs,
    { ...browserTabs[0]!, tabId: 'created', url, title: 'Northstar', associatedJobId: jobId }
  ])))
  const select = vi.fn((tabId: string) => Promise.resolve(browserState(tabId)))
  const sessionJobContext = { ...emptyJobContext, selectedJobId }
  const selectJob = vi.fn().mockImplementation(async (_conversationId: string, jobId: string) => ({
    ...emptyJobContext,
    selectedJobId: jobId
  }))
  const workspace = {
    revision: 1,
    selectedPreset: 'research' as const,
    layouts: {
      research: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
      review: { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 280, center: 700, agent: 380 }, collapsed: [] },
      'agent-focus': { order: ['jobs', 'center', 'agent'] as const, widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
    },
    selectedJobId: null,
    activeCenterSurface: 'document' as const,
    repairedPresets: [],
    browserTabs: initialTabs.map(tab => ({ ...tab, faviconUrl: null })),
    activeBrowserTabId: initialTabs[0]!.tabId,
    repairedBrowser: false
  }
  const close = vi.fn()
  const navigate = vi.fn()
  const associate = vi.fn()
  const workspaceSave = vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 }))
  let jobListener: ((event: { eventId: number; eventType: string; origin: 'mcp'; jobId?: string }) => void) | undefined
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
      agent: {
        list: vi.fn().mockResolvedValue([{
          conversationId: 'conv-1', position: 1, title: 'Session 1', createdAt: '',
          activeTurn: null, connection: 'online', latestEventId: 0, jobContext: sessionJobContext
        }]),
        get: vi.fn().mockResolvedValue({
          conversationId: 'conv-1', position: 1, title: 'Session 1', createdAt: '', entries: [],
          activeTurn: null, connection: 'online', latestEventId: 0, jobContext: sessionJobContext
        }),
        create: vi.fn(), archive: vi.fn(), send: vi.fn(), cancel: vi.fn(), retry: vi.fn(),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      },
      jobs: {
        getState: vi.fn().mockResolvedValue({ jobs: navigationJobs, selectedJobId, sortMode: 'manual', manualOrder: ['job-1', 'job-2'] }),
        list: vi.fn().mockResolvedValue(navigationJobs), select: selectJob, reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockImplementation(listener => {
          jobListener = listener
          return () => undefined
        })
      },
      workspace: { get: vi.fn().mockResolvedValue(workspace), save: workspaceSave },
      browser: {
        getState: vi.fn(), restore: vi.fn().mockResolvedValue(browserState(initialTabs[0]!.tabId)),
        create, select, close, navigate, associate,
        reorder: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), setBounds: vi.fn().mockResolvedValue(undefined),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })
  return {
    associate, close, create, navigate, select, selectJob, workspaceSave,
    emitJobEvent: (event: { eventId: number; eventType: string; origin: 'mcp'; jobId?: string }) => jobListener?.(event)
  }
}

test('clicking a job opens its listing in a new associated tab without disturbing the active tab', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null }
  ])

  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Northstar Staff PM' }))

  await waitFor(() => expect(actions.create).toHaveBeenCalledWith(navigationJobs[1]!.canonicalUrl, navigationJobs[1]!.jobId))
  expect(actions.selectJob).toHaveBeenCalledWith('conv-1', navigationJobs[1]!.jobId)
  expect(screen.getByRole('tab', { name: 'Select Gmail' })).not.toBeNull()
  expect(await screen.findByRole('tab', { name: 'Select Northstar' })).not.toBeNull()
  expect(actions.close).not.toHaveBeenCalled()
  expect(actions.navigate).not.toHaveBeenCalled()
  expect(actions.associate).not.toHaveBeenCalled()
})

test('a layout save failure does not suppress job listing navigation', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null }
  ])
  actions.workspaceSave.mockRejectedValue(new Error('disk unavailable'))

  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Northstar Staff PM' }))

  await waitFor(() => expect(actions.create).toHaveBeenCalledWith(navigationJobs[1]!.canonicalUrl, navigationJobs[1]!.jobId))
})

test('overlapping navigator selections only open the latest clicked job', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null }
  ])
  const resolveSelection = new Map<string, () => void>()
  actions.selectJob.mockImplementation((_conversationId: string, jobId: string) => new Promise(resolve => {
    resolveSelection.set(jobId, () => resolve({ ...emptyJobContext, selectedJobId: jobId }))
  }))

  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Example Co Product Builder' }))
  await waitFor(() => expect(resolveSelection.has('job-1')).toBe(true))
  fireEvent.click(screen.getByRole('button', { name: 'Select Northstar Staff PM' }))
  await act(async () => { resolveSelection.get('job-1')?.() })
  await waitFor(() => expect(resolveSelection.has('job-2')).toBe(true))
  await act(async () => { resolveSelection.get('job-2')?.() })
  await waitFor(() => expect(actions.create).toHaveBeenCalledWith(navigationJobs[1]!.canonicalUrl, navigationJobs[1]!.jobId))

  expect(actions.selectJob.mock.calls.map(([, jobId]) => jobId)).toEqual(['job-1', 'job-2'])
  expect(actions.create).toHaveBeenCalledTimes(1)
  expect(screen.getByText('Northstar · Staff PM')).not.toBeNull()
})

test('clicking a job focuses its associated listing tab without creating a duplicate', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null },
    { tabId: 'northstar', url: 'https://example.com/company', title: 'Northstar', associatedJobId: 'job-2' }
  ])

  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Northstar Staff PM' }))

  await waitFor(() => expect(actions.select).toHaveBeenCalledWith('northstar'))
  expect(actions.create).not.toHaveBeenCalled()
  expect(screen.getByRole('tab', { name: 'Select Gmail' })).not.toBeNull()
})

test('clicking a job focuses its normalized-URL listing tab without creating a duplicate', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null },
    { tabId: 'northstar', url: 'https://EXAMPLE.com/jobs/2#details', title: 'Northstar', associatedJobId: null }
  ])

  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Northstar Staff PM' }))

  await waitFor(() => expect(actions.select).toHaveBeenCalledWith('northstar'))
  expect(actions.create).not.toHaveBeenCalled()
})

test('startup selection does not open a job listing tab', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null }
  ], 'job-2')

  render(<WorkbenchApp />)

  expect(await screen.findByText('Northstar · Staff PM')).not.toBeNull()
  expect(actions.create).not.toHaveBeenCalled()
  expect(actions.select).not.toHaveBeenCalled()
})

test('a global MCP job selection cannot replace the active session job', async () => {
  const actions = setupJobListingNavigation([
    { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', associatedJobId: null }
  ], 'job-1')

  render(<WorkbenchApp />)
  await screen.findByRole('button', { name: 'Select Northstar Staff PM' })
  await act(async () => {
    actions.emitJobEvent({ eventId: 4, eventType: 'job_selected', origin: 'mcp', jobId: 'job-2' })
  })

  expect(screen.getByText('Example Co · Product Builder')).not.toBeNull()
  expect(screen.queryByText('Northstar · Staff PM')).toBeNull()
  expect(actions.create).not.toHaveBeenCalled()
  expect(actions.select).not.toHaveBeenCalled()
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

  render(<WorkbenchApp />)
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
        list: vi.fn().mockResolvedValue([apollo]), select: vi.fn().mockImplementation(async (_conversationId, jobId) => ({ ...emptyJobContext, selectedJobId: jobId })),
        reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(),
        subscribe: vi.fn().mockReturnValue(() => undefined)
      }
    }
  })

  render(<WorkbenchApp />)
  fireEvent.click(await screen.findByRole('button', { name: 'Select Northstar Product Manager' }))
  expect(await screen.findByText('Northstar · Product Manager')).not.toBeNull()

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
    conversationId: 'conv-current', position: 1, title: 'Session 1', createdAt: '', activeTurn: null, connection: 'online', latestEventId: 1,
    entries: [{ eventId: 1, turnId: 'turn-1', type: 'assistant_message', state: 'completed', summary: 'Persistent response', detail: { type: 'message.complete' }, occurredAt: '2026-07-20T10:00:00Z' }],
    jobContext: { ...emptyJobContext, selectedJobId: 'job-1' }
  })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', apiVersion: '0.1.0', checkedAt: '', message: 'Private API authenticated' }) },
    jobs: {
      getState: vi.fn().mockResolvedValue({ jobs, selectedJobId: 'job-1', sortMode: 'manual', manualOrder: ['job-1', 'job-2'] }),
      list: vi.fn().mockResolvedValue(jobs), select: vi.fn().mockImplementation(async (_conversationId, jobId) => ({ ...emptyJobContext, selectedJobId: jobId })), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), subscribe: vi.fn(() => () => undefined)
    },
    agent: {
      list: vi.fn().mockResolvedValue([{ conversationId: 'conv-current', position: 1, title: 'Session 1', createdAt: '', activeTurn: null, connection: 'online', latestEventId: 1, jobContext: { ...emptyJobContext, selectedJobId: 'job-1' } }]),
      create: vi.fn(), archive: vi.fn(), get: conversationGet, send: vi.fn(), cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn(() => () => undefined)
    }
  } })

  render(<WorkbenchApp />)
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

  render(<WorkbenchApp />)
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

  render(<WorkbenchApp />)
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

  render(<WorkbenchApp />)
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
    selectedJobId: null
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

  render(<WorkbenchApp />)
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

test('opening Settings detaches an active native browser surface before showing the panel', async () => {
  const tab = {
    tabId: 'listing', url: 'https://jobs.example.com/7', title: 'Listing', faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
  let resolveDetach!: () => void
  const detached = new Promise<void>(resolve => { resolveDetach = resolve })
  const setBounds = vi.fn().mockImplementation(bounds => bounds.visible ? Promise.resolve() : detached)
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ x: 100, y: 120, width: 800, height: 500 })
  )
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', checkedAt: '', message: 'Private API authenticated' }) },
      workspace: {
        get: vi.fn().mockResolvedValue({
          ...restoredWorkspace(3), selectedPreset: 'research', activeCenterSurface: 'browser',
          browserTabs: [{ tabId: tab.tabId, url: tab.url, title: tab.title, faviconUrl: null, associatedJobId: null }],
          activeBrowserTabId: tab.tabId, repairedBrowser: false
        }),
        save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
      },
      browser: {
        restore: vi.fn().mockResolvedValue(browserState), getState: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined), setBounds,
        create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
        reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
      }
    }
  })

  render(<WorkbenchApp />)
  await screen.findByRole('tab', { name: 'Select Listing' })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  setBounds.mockClear()

  fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: false })))
  expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull()
  setBounds.mockClear()
  window.dispatchEvent(new Event('resize'))
  expect(setBounds).not.toHaveBeenCalledWith(expect.objectContaining({ visible: true }))
  resolveDetach()
  expect(await screen.findByRole('dialog', { name: 'Settings' })).not.toBeNull()
  setBounds.mockClear()

  fireEvent.click(screen.getByRole('button', { name: 'Close settings' }))
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  rect.mockRestore()
})

test('creating an additive session leaves the active native browser surface attached and shows no modal', async () => {
  const tab = {
    tabId: 'listing', url: 'https://jobs.example.com/7', title: 'Listing', faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
  let resolveDetach!: () => void
  const detached = new Promise<void>(resolve => { resolveDetach = resolve })
  const setBounds = vi.fn().mockImplementation(bounds => bounds.visible ? Promise.resolve() : detached)
  const createSession = vi.fn().mockResolvedValue({
    conversationId: 'conv-2', position: 2, title: 'Session 2', createdAt: '', entries: [],
    activeTurn: null, connection: 'online', latestEventId: 0, jobContext: emptyJobContext
  })
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ x: 100, y: 120, width: 800, height: 500 })
  )
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', checkedAt: '', message: 'Private API authenticated' }) },
      agent: {
        list: vi.fn().mockResolvedValue([{ conversationId: 'conv-1', position: 1, title: 'Session 1', createdAt: '', activeTurn: null, connection: 'online', latestEventId: 0, jobContext: emptyJobContext }]),
        create: createSession, archive: vi.fn(),
        get: vi.fn().mockResolvedValue({ conversationId: 'conv-1', position: 1, title: 'Session 1', createdAt: '', entries: [], activeTurn: null, connection: 'online', latestEventId: 0, jobContext: emptyJobContext }),
        send: vi.fn(), cancel: vi.fn(), retry: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined)
      },
      workspace: {
        get: vi.fn().mockResolvedValue({
          ...restoredWorkspace(3), selectedPreset: 'research', activeCenterSurface: 'browser',
          browserTabs: [{ tabId: tab.tabId, url: tab.url, title: tab.title, faviconUrl: null, associatedJobId: null }],
          activeBrowserTabId: tab.tabId, repairedBrowser: false
        }),
        save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
      },
      browser: {
        restore: vi.fn().mockResolvedValue(browserState), getState: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined), setBounds,
        create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
        reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
      }
    }
  })

  render(<WorkbenchApp />)
  await screen.findByRole('tab', { name: 'Select Listing' })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  setBounds.mockClear()

  fireEvent.click(screen.getByRole('button', { name: 'New agent session' }))
  expect(await screen.findByRole('dialog', { name: 'Pick the agent for this chat' })).not.toBeNull()
  expect(createSession).not.toHaveBeenCalled()
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: false })))
  resolveDetach()
  rect.mockRestore()
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

  render(<WorkbenchApp />)
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

  render(<WorkbenchApp />)

  await waitFor(() => {
    const messages = screen.getAllByText(expected)
    expect(messages.some(message => message.getAttribute('role') === 'status')).toBe(true)
  })
})

test('layout changes preserve mounted content surface identities', async () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<WorkbenchApp />)
  const center = screen.getByRole('main')
  const agent = screen.getByRole('complementary', { name: 'Agent chat' })

  fireEvent.click(screen.getByRole('button', { name: 'Agent Focus' }))

  expect(panelDomOrder()).toEqual(['jobs', 'agent', 'center'])
  expect(screen.getByRole('main')).toBe(center)
  expect(screen.getByRole('complementary', { name: 'Agent chat' })).toBe(agent)
})

test('reordering aligns DOM, focus, and reading order without remounting surfaces', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<WorkbenchApp />)
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
  render(<WorkbenchApp />)
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
  render(<WorkbenchApp />)
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

test('drag reordering shows an insertion preview before changing presentation only', async () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const panel = this.getAttribute('data-testid')
    const x = panel === 'panel-jobs' ? 0 : panel === 'panel-center' ? 100 : panel === 'panel-agent' ? 200 : 0
    return DOMRect.fromRect({ x, y: 0, width: 100, height: 500 })
  })
  render(<WorkbenchApp />)
  const source = screen.getByTitle('Drag to reorder Agent chat; click for move controls')
  const target = screen.getByTestId('panel-jobs')

  fireEvent.pointerDown(source, { button: 0, clientX: 250, pointerId: 1 })
  await act(async () => undefined)
  fireEvent.pointerMove(window, { clientX: 50, pointerId: 1 })
  expect(target.classList.contains('insertion-target')).toBe(true)
  fireEvent.pointerUp(window, { clientX: 50, pointerId: 1 })

  expect(panelDomOrder()).toEqual(['agent', 'jobs', 'center'])
  expect(target.classList.contains('insertion-target')).toBe(false)
  rect.mockRestore()
})

test('panel reordering detaches the native browser so the center panel can receive the drop', async () => {
  const tab = {
    tabId: 'listing', url: 'https://jobs.example.com/7', title: 'Listing', faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
  const detachResolvers: Array<() => void> = []
  const setBounds = vi.fn().mockImplementation(bounds => bounds.visible
    ? Promise.resolve()
    : new Promise<void>(resolve => detachResolvers.push(resolve)))
  const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const panel = this.getAttribute('data-testid')
    if (panel) {
      const x = panel === 'panel-jobs' ? 0 : panel === 'panel-center' ? 100 : 200
      return DOMRect.fromRect({ x, y: 0, width: 100, height: 500 })
    }
    return DOMRect.fromRect({ x: 100, y: 120, width: 800, height: 500 })
  })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', checkedAt: '', message: 'Private API authenticated' }) },
    workspace: {
      get: vi.fn().mockResolvedValue({
        ...restoredWorkspace(3), selectedPreset: 'research', activeCenterSurface: 'browser',
        browserTabs: [{ tabId: tab.tabId, url: tab.url, title: tab.title, faviconUrl: null, associatedJobId: null }],
        activeBrowserTabId: tab.tabId, repairedBrowser: false
      }),
      save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
    },
    browser: {
      restore: vi.fn().mockResolvedValue(browserState), getState: vi.fn(), subscribe: vi.fn().mockReturnValue(() => undefined), setBounds,
      create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
      reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  await screen.findByRole('tab', { name: 'Select Listing' })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  setBounds.mockClear()
  detachResolvers.length = 0
  const source = screen.getByTitle('Drag to reorder Agent chat; click for move controls')
  const target = screen.getByTestId('panel-center')

  fireEvent.pointerDown(source, { button: 0, clientX: 250, pointerId: 1 })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: false })))
  fireEvent.pointerMove(window, { clientX: 150, pointerId: 1 })
  expect(target.classList.contains('insertion-target')).toBe(false)
  expect(panelDomOrder()).toEqual(['jobs', 'center', 'agent'])
  await act(async () => detachResolvers.shift()?.())
  await waitFor(() => expect(target.classList.contains('insertion-target')).toBe(true))
  fireEvent.pointerUp(window, { clientX: 150, pointerId: 1 })

  expect(panelDomOrder()).toEqual(['jobs', 'agent', 'center'])
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))

  setBounds.mockClear()
  detachResolvers.length = 0
  fireEvent.pointerDown(source, { button: 0, clientX: 150, pointerId: 2 })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: false })))
  fireEvent.pointerUp(window, { clientX: 150, pointerId: 2 })
  await act(async () => detachResolvers.shift()?.())
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  rect.mockRestore()
})

test('a delayed Browse action survives hydration and Reset Layout leaves Browse state intact', async () => {
  let resolveWorkspace!: (workspace: ReturnType<typeof restoredWorkspace>) => void
  const get = vi.fn().mockReturnValue(new Promise(resolve => { resolveWorkspace = resolve }))
  const save = vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    workspace: { get, save }
  } })

  render(<WorkbenchApp />)
  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  expect(await screen.findByRole('heading', { name: 'Browse' })).not.toBeNull()
  fireEvent.change(screen.getByRole('textbox', { name: 'Search saved jobs' }), { target: { value: 'platform' } })
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))
  expect((screen.getByRole('textbox', { name: 'Search saved jobs' }) as HTMLInputElement).value).toBe('platform')

  await act(async () => resolveWorkspace(restoredWorkspace(7)))
  expect(screen.getByRole('button', { name: 'Browse' }).getAttribute('aria-pressed')).toBe('true')
  expect((screen.getByRole('textbox', { name: 'Search saved jobs' }) as HTMLInputElement).value).toBe('platform')
  await waitFor(() => expect(save).toHaveBeenCalled())
  expect(save.mock.calls.at(-1)?.[0]).toMatchObject({
    revision: 7, activeTopLevelWorkspace: 'browse', browseQuery: 'platform'
  })
})

test('Browse waits for native browser detach, keeps it hidden, and restores the same workbench', async () => {
  const tab = {
    tabId: 'listing', url: 'https://jobs.example.com/7', title: 'Listing', faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
  let resolveDetach!: () => void
  const detached = new Promise<void>(resolve => { resolveDetach = resolve })
  const setBounds = vi.fn().mockImplementation(bounds => bounds.visible ? Promise.resolve() : detached)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ x: 100, y: 120, width: 800, height: 500 })
  )
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    workspace: {
      get: vi.fn().mockResolvedValue({
        ...restoredWorkspace(3), selectedPreset: 'research', activeTopLevelWorkspace: 'research', activeCenterSurface: 'browser',
        browserTabs: [{ tabId: tab.tabId, url: tab.url, title: tab.title, faviconUrl: null, associatedJobId: null }],
        activeBrowserTabId: tab.tabId
      }),
      save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
    },
    browser: {
      restore: vi.fn().mockResolvedValue(browserState), subscribe: vi.fn(() => () => undefined), setBounds,
      create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
      reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  await screen.findByRole('tab', { name: 'Select Listing' })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  const workbench = document.querySelector('.workbench')
  const centerSurface = screen.getByRole('main')
  const listingTab = screen.getByRole('tab', { name: 'Select Listing' })
  const agent = screen.getByRole('complementary', { name: 'Agent chat' })
  const composer = screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'Keep this Browse draft' } })
  setBounds.mockClear()
  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: false })))
  expect(screen.queryByRole('heading', { name: 'Browse' })).toBeNull()
  resolveDetach()
  expect(await screen.findByRole('heading', { name: 'Browse' })).not.toBeNull()
  setBounds.mockClear()
  window.dispatchEvent(new Event('resize'))
  expect(setBounds).not.toHaveBeenCalledWith(expect.objectContaining({ visible: true }))

  fireEvent.click(screen.getByRole('button', { name: 'Research' }))
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  expect(document.querySelector('.workbench')).toBe(workbench)
  expect(screen.getByRole('main')).toBe(centerSurface)
  expect(screen.getByRole('tab', { name: 'Select Listing' })).toBe(listingTab)
  expect(screen.getByRole('complementary', { name: 'Agent chat' })).toBe(agent)
  expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Keep this Browse draft')
  vi.restoreAllMocks()
})

test.each(['Research', 'Review'] as const)('a stale Browse detach cannot replace %s or reattach during preparation', async chosenWorkspace => {
  const tab = {
    tabId: 'listing', url: 'https://jobs.example.com/7', title: 'Listing', faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
  let resolveDetach!: () => void
  const detached = new Promise<void>(resolve => { resolveDetach = resolve })
  const setBounds = vi.fn().mockImplementation(bounds => bounds.visible ? Promise.resolve() : detached)
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(
    DOMRect.fromRect({ x: 100, y: 120, width: 800, height: 500 })
  )
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    workspace: {
      get: vi.fn().mockResolvedValue({
        ...restoredWorkspace(3), selectedPreset: 'research', activeTopLevelWorkspace: 'research', activeCenterSurface: 'browser',
        browserTabs: [{ tabId: tab.tabId, url: tab.url, title: tab.title, faviconUrl: null, associatedJobId: null }],
        activeBrowserTabId: tab.tabId
      }),
      save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
    },
    browser: {
      restore: vi.fn().mockResolvedValue(browserState), subscribe: vi.fn(() => () => undefined), setBounds,
      create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(),
      reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  await screen.findByRole('tab', { name: 'Select Listing' })
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: true })))
  setBounds.mockClear()

  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  await waitFor(() => expect(setBounds).toHaveBeenCalledWith(expect.objectContaining({ visible: false })))
  fireEvent.click(screen.getByRole('button', { name: chosenWorkspace }))
  setBounds.mockClear()
  window.dispatchEvent(new Event('resize'))
  expect(setBounds).not.toHaveBeenCalledWith(expect.objectContaining({ visible: true }))

  await act(async () => resolveDetach())
  expect(screen.queryByRole('heading', { name: 'Browse' })).toBeNull()
  expect(screen.getByRole('button', { name: chosenWorkspace }).getAttribute('aria-pressed')).toBe('true')
  vi.restoreAllMocks()
})

test('Browse roundtrip preserves the selected document surface and chat DOM state', async () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<WorkbenchApp />)
  const documentSurface = screen.getByRole('main')
  const agent = screen.getByRole('complementary', { name: 'Agent chat' })
  const composer = screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'Document review draft' } })

  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  expect(await screen.findByRole('heading', { name: 'Browse' })).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Review' }))

  expect(screen.getByRole('main')).toBe(documentSurface)
  expect(screen.getByRole('complementary', { name: 'Agent chat' })).toBe(agent)
  expect((screen.getByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement).value).toBe('Document review draft')
})

test('restored Browse waits for hydration and a successful invisible native-browser bound', async () => {
  let resolveWorkspace!: (workspace: ReturnType<typeof restoredWorkspace> & { activeTopLevelWorkspace: 'browse' }) => void
  const invisibleResolvers: Array<() => void> = []
  const setBounds = vi.fn(bounds => bounds.visible
    ? Promise.resolve()
    : new Promise<void>(resolve => invisibleResolvers.push(resolve)))
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    workspace: {
      get: vi.fn(() => new Promise(resolve => { resolveWorkspace = resolve })),
      save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
    },
    browser: {
      restore: vi.fn().mockResolvedValue({ tabs: [], activeTabId: null, download: null, notice: null }),
      subscribe: vi.fn(() => () => undefined), setBounds, create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(),
      navigate: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  expect(screen.queryByRole('heading', { name: 'Browse' })).toBeNull()
  await act(async () => resolveWorkspace({ ...restoredWorkspace(4), activeTopLevelWorkspace: 'browse' }))
  await waitFor(() => expect(invisibleResolvers.length).toBeGreaterThanOrEqual(2))
  expect(screen.queryByRole('heading', { name: 'Browse' })).toBeNull()
  await act(async () => invisibleResolvers.at(-1)?.())
  expect(await screen.findByRole('heading', { name: 'Browse' })).not.toBeNull()
  expect(setBounds).not.toHaveBeenCalledWith(expect.objectContaining({ visible: true }))
})

test('restored Browse fails closed when native-browser detach rejects and can retry', async () => {
  const setBounds = vi.fn().mockRejectedValue(new Error('detach failed'))
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    workspace: {
      get: vi.fn().mockResolvedValue({ ...restoredWorkspace(5), activeTopLevelWorkspace: 'browse' }),
      save: vi.fn().mockImplementation(snapshot => Promise.resolve({ ...snapshot, revision: snapshot.revision + 1 }))
    },
    browser: {
      restore: vi.fn().mockResolvedValue({ tabs: [], activeTabId: null, download: null, notice: null }),
      subscribe: vi.fn(() => () => undefined), setBounds, create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(),
      navigate: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  expect(await screen.findByText('Browse could not open because the browser view could not be hidden.')).not.toBeNull()
  expect(screen.queryByRole('heading', { name: 'Browse' })).toBeNull()
  expect(document.querySelector('.workbench')).not.toBeNull()
  setBounds.mockResolvedValue(undefined)
  fireEvent.click(screen.getByRole('button', { name: 'Retry Browse' }))
  expect(await screen.findByRole('heading', { name: 'Browse' })).not.toBeNull()
})

test('Browse focus stays local until Open job commits selection and listing navigation', async () => {
  const jobs: JobListItem[] = [
    { jobId: 'one', company: 'Alpha', title: 'Builder', status: 'discovered', statusGroup: 'Inbox', canonicalUrl: 'https://example.com/one', discoveredAt: '2026-01-01', lastSeenAt: '2026-01-01' },
    { jobId: 'two', company: 'Beta', title: 'Designer', status: 'reviewed', statusGroup: 'Considering', canonicalUrl: 'https://example.com/two', discoveredAt: '2026-01-02', lastSeenAt: '2026-01-02' }
  ]
  const select = vi.fn().mockImplementation(async (_conversationId, jobId) => ({ ...emptyJobContext, selectedJobId: jobId }))
  const create = vi.fn().mockImplementation((url, jobId) => Promise.resolve({
    tabs: [{ tabId: 'opened', url, title: 'Opened', faviconUrl: null, associatedJobId: jobId, loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null }],
    activeTabId: 'opened', download: null, notice: null
  }))
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    jobs: {
      getState: vi.fn().mockResolvedValue({ jobs, selectedJobId: null, sortMode: 'manual', manualOrder: ['one', 'two'] }),
      list: vi.fn().mockResolvedValue(jobs),
      inspect: vi.fn(async jobId => ({ ...jobs.find(job => job.jobId === jobId)!, description: `Detail ${jobId}`, location: null })),
      select, reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), subscribe: vi.fn(() => () => undefined)
    },
    browser: {
      restore: vi.fn().mockResolvedValue({ tabs: [], activeTabId: null, download: null, notice: null }),
      subscribe: vi.fn(() => () => undefined), setBounds: vi.fn().mockResolvedValue(undefined), create,
      select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  await screen.findByRole('button', { name: 'Select Alpha Builder' })
  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  await screen.findByText('Detail one')
  fireEvent.click(screen.getByRole('button', { name: 'Beta Designer' }))
  await screen.findByText('Detail two')
  expect(select).not.toHaveBeenCalled()
  expect(create).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'Open job' }))
  await waitFor(() => expect(select).toHaveBeenCalledWith('conv_unavailable', 'two'))
  await waitFor(() => expect(create).toHaveBeenCalledWith('https://example.com/two', 'two'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Research' }).getAttribute('aria-pressed')).toBe('true'))
})

test('failed Open job navigation remains in Browse and is announced', async () => {
  const jobs: JobListItem[] = [
    { jobId: 'one', company: 'Alpha', title: 'Builder', status: 'discovered', statusGroup: 'Inbox', canonicalUrl: 'https://example.com/one', discoveredAt: '2026-01-01', lastSeenAt: '2026-01-01' }
  ]
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    connectivity: { get: vi.fn().mockRejectedValue(new Error('offline')) },
    jobs: {
      getState: vi.fn().mockResolvedValue({ jobs, selectedJobId: null, sortMode: 'manual', manualOrder: ['one'] }),
      list: vi.fn().mockResolvedValue(jobs), inspect: vi.fn(async () => ({ ...jobs[0]!, description: 'Detail', location: null })),
      select: vi.fn().mockImplementation(async (_conversationId, jobId) => ({ ...emptyJobContext, selectedJobId: jobId })), reorder: vi.fn(), setSort: vi.fn(), updateStatus: vi.fn(), subscribe: vi.fn(() => () => undefined)
    },
    browser: {
      restore: vi.fn().mockResolvedValue({ tabs: [], activeTabId: null, download: null, notice: null }),
      subscribe: vi.fn(() => () => undefined), setBounds: vi.fn().mockResolvedValue(undefined), create: vi.fn().mockRejectedValue(new Error('create failed')),
      select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(), back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    }
  } })

  render(<WorkbenchApp />)
  await screen.findByRole('button', { name: 'Select Alpha Builder' })
  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  await screen.findByText('Detail')
  fireEvent.click(screen.getByRole('button', { name: 'Open job' }))
  expect((await screen.findByRole('alert')).textContent).toContain('Could not open this job')
  expect(screen.getByRole('button', { name: 'Browse' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('heading', { name: 'Browse' })).not.toBeNull()
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
