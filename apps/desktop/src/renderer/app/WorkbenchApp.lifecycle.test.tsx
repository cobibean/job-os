import { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { DocumentArtifact, JobListItem, JobOsRendererBridge } from '../../shared/contracts'
import { WorkbenchApp } from './WorkbenchApp'

const lifecycle = vi.hoisted(() => ({
  pdfMounts: 0,
  installationProfileId: 'profile-1',
  jobContext: { selectedJobId: 'job-1', activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 }
}))

vi.mock('../agents/chat/AgentPanel', () => ({ AgentPanel: () => <aside>Agent</aside> }))
vi.mock('../jobs/navigator/JobNavigator', () => ({ JobNavigator: () => <aside>Jobs</aside> }))
vi.mock('../jobs/browse/BrowseWorkspace', () => ({ BrowseWorkspace: () => <main>Browse surface</main> }))
vi.mock('../documents/artifacts/PdfPreview', async () => {
  const React = await import('react')
  return {
    PdfPreview: ({ bytes }: { bytes: ArrayBuffer }) => {
      React.useEffect(() => {
        lifecycle.pdfMounts += 1
        return () => { lifecycle.pdfMounts -= 1 }
      }, [])
      return <canvas aria-label={`Document payload ${new Uint8Array(bytes)[0]}`} />
    }
  }
})
vi.mock('./runtime/useConnectivity', () => ({
  useConnectivity: () => ({
    state: 'connected',
    apiVersion: '0.1.0',
    message: 'Local service connected',
    installationProfileId: lifecycle.installationProfileId,
    installationProfileName: 'Personal'
  })
}))
vi.mock('../agents/avatar/useAgentAvatarPreference', () => ({
  useAgentAvatarPreference: () => ({ avatarId: 'default', selectAvatar: vi.fn() })
}))
vi.mock('./theme/useTheme', () => ({
  useTheme: () => ({ mode: 'dark', themeId: 'graphite', toggleMode: vi.fn(), selectTheme: vi.fn() })
}))
vi.mock('../agents/connected-agents/useConnectedAgents', () => ({
  useConnectedAgents: () => ({ snapshot: null, refresh: vi.fn(), loadModels: vi.fn() })
}))
vi.mock('../agents/chat/useAgentSessions', () => ({
  useAgentSessions: () => ({
    activeId: 'conversation-1',
    activeSession: {
      summary: {
        jobContext: lifecycle.jobContext,
        binding: null,
        activeTurn: null
      }
    },
    archive: vi.fn(),
    atMaximum: false,
    create: vi.fn(),
    createJobless: vi.fn(),
    order: [],
    refreshAvailability: vi.fn(),
    selectByIndex: vi.fn(),
    sessions: {},
    updateJobContext: vi.fn()
  })
}))

const job: JobListItem = {
  jobId: 'job-1',
  company: 'Northstar',
  title: 'Applied AI Builder',
  status: 'shortlisted',
  statusGroup: 'Considering',
  canonicalUrl: 'https://jobs.example.com/northstar',
  discoveredAt: '',
  lastSeenAt: ''
}

vi.mock('../jobs/useJobs', () => ({
  useJobs: () => ({
    changeSort: vi.fn(), changeStatus: vi.fn(), error: null, feedback: '', jobs: [job], loading: false,
    query: '', removeDemo: vi.fn(), reorder: vi.fn(), reorderTo: vi.fn(), selectJob: vi.fn(),
    selectJobForConversation: vi.fn().mockResolvedValue(true), selectedJob: job, selectedJobDetail: null,
    selectedJobId: job.jobId, setQuery: vi.fn(), setStatusGroup: vi.fn(), sortMode: 'manual', statusGroup: ''
  })
}))

afterEach(() => {
  cleanup()
  lifecycle.pdfMounts = 0
  lifecycle.installationProfileId = 'profile-1'
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
})

test('keeps workbench controllers alive while center presentations mount and clean up under StrictMode', async () => {
  const tab = {
    tabId: 'tab-1', url: job.canonicalUrl, title: job.title, faviconUrl: null, associatedJobId: null,
    loading: false, canGoBack: false, canGoForward: false, error: null, crashed: false, blockedUrl: null
  }
  const browserState = { tabs: [tab], activeTabId: tab.tabId, download: null, notice: null }
  let liveBrowserListeners = 0
  let liveSaveListeners = 0
  let liveDocumentListeners = 0
  const restore = vi.fn().mockResolvedValue(browserState)
  const browserSubscribe = vi.fn(() => {
    liveBrowserListeners += 1
    return () => { liveBrowserListeners -= 1 }
  })
  const agentSubscribe = vi.fn(() => {
    liveSaveListeners += 1
    return () => { liveSaveListeners -= 1 }
  })
  const documentSubscribe = vi.fn(() => {
    liveDocumentListeners += 1
    return () => { liveDocumentListeners -= 1 }
  })
  const setBrowserBounds = vi.fn().mockResolvedValue(undefined)
  const artifact: DocumentArtifact = {
    artifactId: 'art_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: job.jobId,
    documentKey: 'resume',
    documentLabel: 'Resume',
    renderSequence: 1,
    sourceRevision: 'source-1',
    artifactRevision: 'render-1',
    mediaType: 'application/pdf',
    sha256: 'a'.repeat(64),
    renderStatus: 'succeeded',
    filename: 'northstar-resume.pdf',
    failureMessage: null,
    createdAt: '',
    isCurrent: true,
    isLastSuccessful: true,
    isApproved: false,
    previewAvailable: true
  }
  const artifactsState = {
    jobId: job.jobId,
    artifacts: [artifact],
    currentArtifactId: artifact.artifactId,
    lastSuccessfulArtifactId: artifact.artifactId,
    approvedArtifactId: null
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
    browserTabs: [{ tabId: tab.tabId, url: tab.url, title: tab.title, faviconUrl: null, associatedJobId: null }],
    activeBrowserTabId: tab.tabId,
    repairedBrowser: false,
    browserRepairReasons: [],
    activeArtifactId: null,
    activeArtifactPage: 1,
    activeArtifactZoom: 1,
    activeTopLevelWorkspace: 'research' as const,
    browseMode: 'list' as const,
    browseFocusJobId: null,
    browseQuery: '',
    browseStatusGroup: '',
    browseSortMode: 'manual' as const,
    browseRailWidth: 292
  }
  const documents = {
    list: vi.fn().mockResolvedValue(artifactsState),
    refresh: vi.fn().mockResolvedValue(artifactsState),
    approve: vi.fn().mockResolvedValue(artifactsState),
    loadPdf: vi.fn().mockResolvedValue({
      artifactId: artifact.artifactId,
      artifactRevision: artifact.artifactRevision,
      sourceRevision: artifact.sourceRevision,
      sha256: artifact.sha256,
      bytes: Uint8Array.of(37).buffer
    }),
    loadOriginalDocx: vi.fn(),
    export: vi.fn(), reveal: vi.fn(), open: vi.fn()
  }
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    lifecycle: { subscribePrepareClose: vi.fn(() => () => undefined) },
    careerProfile: { availability: vi.fn().mockResolvedValue({ enabled: false }) },
    installationProfiles: { expectedProfileId: 'profile-1' },
    agent: { subscribe: agentSubscribe },
    jobs: { getState: vi.fn() },
    workspace: {
      get: vi.fn().mockResolvedValue(workspace),
      save: vi.fn().mockImplementation(value => Promise.resolve({ ...value, revision: value.revision + 1 })),
      saveDocumentView: vi.fn()
    },
    browser: {
      restore,
      getState: vi.fn().mockResolvedValue(browserState),
      subscribe: browserSubscribe,
      setBounds: setBrowserBounds,
      create: vi.fn(), select: vi.fn(), close: vi.fn(), reorder: vi.fn(), navigate: vi.fn(),
      back: vi.fn(), forward: vi.fn(), reload: vi.fn(), stop: vi.fn(), associate: vi.fn(), copyBlockedUrl: vi.fn()
    },
    documents,
    docxDocuments: { listBindings: vi.fn().mockResolvedValue([]), subscribe: documentSubscribe }
  } as unknown as JobOsRendererBridge })

  const view = render(<StrictMode><WorkbenchApp /></StrictMode>)

  expect(await screen.findByRole('tab', { name: `Select ${job.title}` })).not.toBeNull()
  await waitFor(() => {
    expect(liveBrowserListeners).toBe(1)
    expect(liveSaveListeners).toBe(1)
  })
  expect(restore).toHaveBeenCalledOnce()
  expect(liveDocumentListeners).toBe(0)
  const initialDocumentLoads = documents.list.mock.calls.length

  fireEvent.click(screen.getByRole('button', { name: 'Review' }))
  expect(await screen.findByLabelText('Document payload 37')).not.toBeNull()
  await waitFor(() => expect(liveDocumentListeners).toBe(1))
  expect(documents.list.mock.calls.length).toBeGreaterThan(initialDocumentLoads)
  expect(lifecycle.pdfMounts).toBe(1)
  fireEvent.click(screen.getByRole('button', { name: /^Export$/ }))
  expect(screen.getByRole('menu', { name: 'Export document' })).not.toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Research' }))
  expect(await screen.findByRole('tab', { name: `Select ${job.title}` })).not.toBeNull()
  expect(screen.queryByLabelText('Document payload 37')).toBeNull()
  expect(screen.queryByRole('menu', { name: 'Export document' })).toBeNull()
  expect(lifecycle.pdfMounts).toBe(0)
  expect(liveDocumentListeners).toBe(0)
  expect(restore).toHaveBeenCalledOnce()

  fireEvent.click(screen.getByRole('button', { name: 'Browse' }))
  expect(await screen.findByText('Browse surface')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Research' }))
  expect(await screen.findByRole('tab', { name: `Select ${job.title}` })).not.toBeNull()
  expect(restore).toHaveBeenCalledOnce()
  expect(liveBrowserListeners).toBe(1)
  expect(liveSaveListeners).toBe(1)

  lifecycle.installationProfileId = 'profile-2'
  view.rerender(<StrictMode><WorkbenchApp /></StrictMode>)
  expect(await screen.findByText('Restart JobOS to continue safely.')).not.toBeNull()
  await waitFor(() => expect(setBrowserBounds).toHaveBeenLastCalledWith({
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    visible: false
  }))

  view.unmount()
  expect(liveBrowserListeners).toBe(0)
  expect(liveSaveListeners).toBe(0)
  expect(liveDocumentListeners).toBe(0)
  expect(lifecycle.pdfMounts).toBe(0)
})
