import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type {
  AgentConversationSnapshot,
  AgentSessionStreamUpdate,
  BrowserState,
  BrowserTab,
  JobListItem
} from '../../../shared/contracts'
import { useSaveJobFromBrowser } from './useSaveJobFromBrowser'

afterEach(cleanup)

const sourceTab: BrowserTab = {
  tabId: 'source-tab',
  url: 'https://jobs.example.com/northstar',
  title: 'Applied AI Builder',
  faviconUrl: null,
  associatedJobId: null,
  loading: false,
  canGoBack: false,
  canGoForward: false,
  error: null,
  crashed: false,
  blockedUrl: null
}

function browserState(tab: BrowserTab = sourceTab): BrowserState {
  return { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
}

function conversation(
  conversationId: string,
  turnId: string,
  response: string,
  type: 'assistant_message' | 'error' = 'assistant_message'
): AgentConversationSnapshot {
  return {
    conversationId,
    position: 1,
    title: 'Save job',
    createdAt: '',
    entries: [{
      eventId: 1,
      turnId,
      type,
      state: type === 'assistant_message' ? 'completed' : 'failed',
      summary: response,
      detail: type === 'assistant_message' ? { text: response } : {},
      occurredAt: ''
    }],
    activeTurn: null,
    connection: 'online',
    recoveryState: 'ready',
    latestEventId: 1,
    jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 },
    binding: null,
    availability: { state: 'ready', reason: null }
  }
}

test('reconciles a terminal failure that arrives before send resolves', async () => {
  let listener: ((update: AgentSessionStreamUpdate) => void) | null = null
  const get = vi.fn().mockResolvedValue(conversation('conversation-1', 'turn-1', 'Agent connection unavailable', 'error'))
  const send = vi.fn().mockImplementation(async () => {
    listener?.({
      kind: 'event',
      conversationId: 'conversation-1',
      recoveryState: 'ready',
      event: conversation('conversation-1', 'turn-1', 'Agent connection unavailable', 'error').entries[0]!
    })
    return { turnId: 'turn-1' }
  })
  const { result } = renderHook(() => useSaveJobFromBrowser({
    agent: { get, send, subscribe: vi.fn(next => { listener = next; return () => { listener = null } }) },
    browser: { getState: vi.fn().mockResolvedValue(browserState()), reconcileExternalState: vi.fn() },
    jobs: { getState: vi.fn() },
    onCreateSaveSession: vi.fn().mockResolvedValue('conversation-1'),
    onJobSaved: vi.fn()
  }))

  await act(() => result.current.saveJob(sourceTab))

  await waitFor(() => expect(result.current.saveStates[sourceTab.tabId]).toEqual({
    status: 'error',
    message: 'Agent connection unavailable'
  }))
  expect(send.mock.calls[0]?.[1]).toContain(sourceTab.tabId)
  expect(send.mock.calls[0]?.[1]).toContain(sourceTab.url)
  expect(send.mock.calls[0]?.[2]).toMatch(/^browser-save-/)
})

test('confirms creation and association before transferring success to a recovered tab', async () => {
  const savedJob: JobListItem = {
    jobId: 'job-1',
    company: 'Northstar Labs',
    title: sourceTab.title,
    status: 'discovered',
    statusGroup: 'Inbox',
    canonicalUrl: sourceTab.url,
    discoveredAt: '',
    lastSeenAt: ''
  }
  const recoveredTab = {
    ...sourceTab,
    tabId: 'recovered-tab',
    url: 'https://jobs.example.com/jobs/northstar',
    associatedJobId: savedJob.jobId
  }
  const terminal = `JOBOS_SAVE_RESULT:${JSON.stringify({ jobId: savedJob.jobId, created: true, tabId: recoveredTab.tabId })}`
  const getState = vi.fn().mockResolvedValue(browserState(recoveredTab))
  const reconcileExternalState = vi.fn().mockResolvedValue(undefined)
  const onJobSaved = vi.fn().mockResolvedValue(undefined)
  const { result } = renderHook(() => useSaveJobFromBrowser({
    agent: {
      get: vi.fn().mockResolvedValue(conversation('conversation-1', 'turn-1', terminal)),
      send: vi.fn().mockResolvedValue({ turnId: 'turn-1' }),
      subscribe: vi.fn(() => () => undefined)
    },
    browser: { getState, reconcileExternalState },
    jobs: { getState: vi.fn().mockResolvedValue({ jobs: [savedJob], selectedJobId: null, sortMode: 'manual', manualOrder: [] }) },
    onCreateSaveSession: vi.fn().mockResolvedValue('conversation-1'),
    onJobSaved
  }))

  await act(() => result.current.saveJob(sourceTab))

  await waitFor(() => expect(result.current.saveStates[recoveredTab.tabId]).toEqual({
    status: 'saved',
    message: `Saved to JobOS: ${savedJob.company} · ${savedJob.title}`
  }))
  expect(result.current.saveStates[sourceTab.tabId]).toBeUndefined()
  expect(onJobSaved).toHaveBeenCalledWith(savedJob.jobId, 'conversation-1')
  expect(getState).toHaveBeenCalledTimes(2)
  expect(reconcileExternalState).toHaveBeenCalledWith(browserState(recoveredTab))
})

test('ignores terminal events owned by a different conversation', async () => {
  let listener: ((update: AgentSessionStreamUpdate) => void) | null = null
  let resolveSend!: (value: { turnId: string }) => void
  const sendPromise = new Promise<{ turnId: string }>(resolve => { resolveSend = resolve })
  const get = vi.fn().mockResolvedValue({
    ...conversation('conversation-1', 'different-turn', 'Still running'),
    entries: [],
    latestEventId: 0
  })
  const { result } = renderHook(() => useSaveJobFromBrowser({
    agent: {
      get,
      send: vi.fn().mockReturnValue(sendPromise),
      subscribe: vi.fn(next => { listener = next; return () => { listener = null } })
    },
    browser: { getState: vi.fn().mockResolvedValue(browserState()), reconcileExternalState: vi.fn() },
    jobs: { getState: vi.fn() },
    onCreateSaveSession: vi.fn().mockResolvedValue('conversation-1'),
    onJobSaved: vi.fn()
  }))

  let savePromise!: Promise<void>
  act(() => { savePromise = result.current.saveJob(sourceTab) })
  await waitFor(() => expect(result.current.saveStates[sourceTab.tabId]?.status).toBe('saving'))
  act(() => resolveSend({ turnId: 'turn-1' }))
  await act(() => savePromise)
  expect(get).toHaveBeenCalledOnce()
  get.mockClear()
  act(() => listener?.({
    kind: 'event',
    conversationId: 'conversation-2',
    recoveryState: 'ready',
    event: conversation('conversation-2', 'turn-1', 'Wrong owner stopped', 'error').entries[0]!
  }))

  expect(get).not.toHaveBeenCalled()
  expect(result.current.saveStates[sourceTab.tabId]?.status).toBe('saving')
})

test('cancels subscriptions and in-flight saves when the controller becomes inactive', async () => {
  let resolveSession!: (value: string | null) => void
  const sessionPromise = new Promise<string | null>(resolve => { resolveSession = resolve })
  const unsubscribe = vi.fn()
  const send = vi.fn()
  const onJobSaved = vi.fn()
  const options = {
    agent: { get: vi.fn(), send, subscribe: vi.fn(() => unsubscribe) },
    browser: { getState: vi.fn().mockResolvedValue(browserState()), reconcileExternalState: vi.fn() },
    jobs: { getState: vi.fn() },
    onCreateSaveSession: vi.fn().mockReturnValue(sessionPromise),
    onJobSaved
  }
  const { result, rerender } = renderHook(
    ({ active }: { active: boolean }) => useSaveJobFromBrowser({ ...options, active }),
    { initialProps: { active: true } }
  )

  let savePromise!: Promise<void>
  act(() => { savePromise = result.current.saveJob(sourceTab) })
  await waitFor(() => expect(result.current.saveStates[sourceTab.tabId]?.status).toBe('saving'))

  rerender({ active: false })
  expect(unsubscribe).toHaveBeenCalledOnce()
  act(() => resolveSession('conversation-1'))
  await act(() => savePromise)

  expect(send).not.toHaveBeenCalled()
  expect(onJobSaved).not.toHaveBeenCalled()
})
