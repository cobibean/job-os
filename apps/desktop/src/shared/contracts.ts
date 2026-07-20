export type ConnectivityState = 'connecting' | 'connected' | 'degraded' | 'disconnected'
export type JobSortMode = 'manual' | 'recent' | 'alphabetical' | 'status'
export type JobStatus = 'discovered' | 'scored' | 'reviewed' | 'shortlisted' | 'apply_now' | 'maybe' | 'stretch' | 'skipped' | 'applied' | 'interviewing' | 'closed' | 'archived'

export interface JobListItem {
  jobId: string
  company: string
  title: string
  status: JobStatus
  statusGroup: string
  canonicalUrl: string
  discoveredAt: string
  lastSeenAt: string
}

export interface JobWorkspaceSnapshot {
  jobs: JobListItem[]
  selectedJobId: string | null
  sortMode: JobSortMode
  manualOrder: string[]
}

export interface JobMutationResult {
  eventId: number
}

export interface JobStatusMutationResult extends JobMutationResult {
  job: JobListItem
}

export interface JobEvent {
  eventId: number
  eventType: string
  origin: 'user' | 'mcp'
  jobId?: string | null
}

export type PanelId = 'jobs' | 'center' | 'agent'
export type LayoutPreset = 'research' | 'review' | 'agent-focus'
export type CenterSurface = 'browser' | 'document'
export type BrowserRepairReason = 'protected_title' | 'dropped_tabs' | 'reselected_active_tab' | 'metadata_adjusted'

export interface BrowserTabMetadata {
  tabId: string
  url: string
  title: string
  faviconUrl: string | null
  associatedJobId: string | null
}

export interface BrowserTab extends BrowserTabMetadata {
  loading: boolean
  canGoBack: boolean
  canGoForward: boolean
  error: string | null
  crashed: boolean
  blockedUrl: string | null
}

export interface BrowserDownload {
  id: string
  filename: string
  state: 'starting' | 'progressing' | 'completed' | 'cancelled' | 'interrupted' | 'failed'
  receivedBytes: number
  totalBytes: number
  message?: string
}

export interface BrowserState {
  tabs: BrowserTab[]
  activeTabId: string | null
  download: BrowserDownload | null
  notice: string | null
}

export interface BrowserRestoreState {
  tabs: BrowserTabMetadata[]
  activeTabId: string | null
}

export interface BrowserBounds {
  x: number
  y: number
  width: number
  height: number
  visible: boolean
}

export interface WorkspaceSnapshot {
  revision: number
  selectedPreset: LayoutPreset
  layouts: Record<LayoutPreset, {
    order: PanelId[]
    widths: Record<PanelId, number>
    collapsed: PanelId[]
  }>
  selectedJobId: string | null
  activeCenterSurface: CenterSurface
  repairedPresets: LayoutPreset[]
  browserTabs?: BrowserTabMetadata[]
  activeBrowserTabId?: string | null
  repairedBrowser?: boolean
  browserRepairReasons?: BrowserRepairReason[]
  activeArtifactId?: string | null
  activeArtifactPage?: number
  activeArtifactZoom?: number
}

export type ArtifactMediaType = 'application/pdf' | 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
export type ArtifactRenderStatus = 'succeeded' | 'failed' | 'rendering'

export interface DocumentArtifact {
  artifactId: string
  jobId: string
  sourceRevision: string
  artifactRevision: string
  mediaType: ArtifactMediaType
  sha256: string | null
  renderStatus: ArtifactRenderStatus
  filename: string | null
  failureMessage: string | null
  createdAt: string
  isCurrent: boolean
  isLastSuccessful: boolean
  previewAvailable: boolean
}

export interface JobArtifactsState {
  jobId: string
  artifacts: DocumentArtifact[]
  currentArtifactId: string | null
  lastSuccessfulArtifactId: string | null
}

export interface PdfArtifactPayload {
  artifactId: string
  artifactRevision: string
  sourceRevision: string
  sha256: string
  bytes: ArrayBuffer
}

export interface ConnectivitySnapshot {
  state: Exclude<ConnectivityState, 'connecting'>
  apiVersion?: string
  checkedAt: string
  message: string
}

export interface JobOsRendererBridge {
  connectivity: {
    get: () => Promise<ConnectivitySnapshot>
  }
  jobs: {
    getState: () => Promise<JobWorkspaceSnapshot>
    list: (sort: JobSortMode, query?: string, statusGroup?: string) => Promise<JobListItem[]>
    select: (jobId: string) => Promise<JobMutationResult>
    reorder: (jobIds: string[]) => Promise<JobMutationResult>
    setSort: (sort: JobSortMode) => Promise<JobMutationResult>
    updateStatus: (jobId: string, status: JobStatus) => Promise<JobStatusMutationResult>
    subscribe: (listener: (event: JobEvent) => void) => () => void
  }
  workspace: {
    get: () => Promise<WorkspaceSnapshot>
    save: (snapshot: WorkspaceSnapshot) => Promise<WorkspaceSnapshot>
  }
  browser: {
    getState: () => Promise<BrowserState>
    restore: (state: BrowserRestoreState) => Promise<BrowserState>
    create: (url?: string, associatedJobId?: string | null) => Promise<BrowserState>
    select: (tabId: string) => Promise<BrowserState>
    close: (tabId: string) => Promise<BrowserState>
    reorder: (tabIds: string[]) => Promise<BrowserState>
    navigate: (tabId: string, input: string) => Promise<BrowserState>
    back: (tabId: string) => Promise<BrowserState>
    forward: (tabId: string) => Promise<BrowserState>
    reload: (tabId: string) => Promise<BrowserState>
    stop: (tabId: string) => Promise<BrowserState>
    associate: (tabId: string, jobId: string | null) => Promise<BrowserState>
    copyBlockedUrl: (tabId: string) => Promise<BrowserState>
    setBounds: (bounds: BrowserBounds) => Promise<void>
    subscribe: (listener: (state: BrowserState) => void) => () => void
  }
  documents: {
    list: (jobId: string) => Promise<JobArtifactsState>
    refresh: (jobId: string) => Promise<JobArtifactsState>
    loadPdf: (artifactId: string) => Promise<PdfArtifactPayload>
    export: (artifactId: string) => Promise<string>
    reveal: (artifactId: string) => Promise<string>
    open: (artifactId: string) => Promise<string>
  }
}
