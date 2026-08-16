import type { DocxDocumentsBridge } from './docxDocuments.js'
import type {
  ApplyEditableDocumentOperationsRequest,
  CreateEditableDocumentSnapshotRequest,
  DocumentKey as EditableDocumentKey,
  EditableDocument,
  EditableDocumentExportResult,
  EditableDocumentPreview,
  EditableDocumentSnapshot,
  EditableDocumentSummary,
  OperationReceipt,
  RestoreEditableDocumentSnapshotRequest,
  SaveEditableDocumentRequest
} from './editableDocuments.js'

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
  syntheticDemo?: boolean
  datasetVersion?: string | null
}

export interface JobDetail extends JobListItem {
  description: string
  location: string | null
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

export interface BrowserJobExtraction {
  companyName: string
  title: string
  canonicalUrl: string
  locationText: string
  descriptionText: string
  applicationUrl: string
}

export interface BrowserJobSaveResult extends JobMutationResult {
  created: boolean
  associated: boolean
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
export type TopLevelWorkspace = LayoutPreset | 'browse'
export type BrowseMode = 'list' | 'swipe'
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

export interface BrowserSemanticElement {
  targetId: string
  role: string
  name: string
  disabled: boolean
  href: string | null
}

export interface BrowserSemanticSnapshot {
  tabId: string
  url: string
  title: string
  text: string
  textStart: number
  textLength: number
  scrollY: number
  scrollHeight: number
  viewportHeight: number
  elements: BrowserSemanticElement[]
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
  activeTopLevelWorkspace?: TopLevelWorkspace
  browseMode?: BrowseMode
  browseFocusJobId?: string | null
  browseQuery?: string
  browseStatusGroup?: string
  browseSortMode?: JobSortMode
  browseRailWidth?: number
}

export type ArtifactMediaType = 'application/pdf' | 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
export type ArtifactRenderStatus = 'succeeded' | 'failed' | 'rendering'
export type DocumentKey = EditableDocumentKey

export interface DocumentArtifact {
  artifactId: string
  jobId: string
  documentKey: DocumentKey
  documentLabel: string
  renderSequence: number
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
  isApproved: boolean
  previewAvailable: boolean
}

export interface JobArtifactsState {
  jobId: string
  artifacts: DocumentArtifact[]
  currentArtifactId: string | null
  lastSuccessfulArtifactId: string | null
  approvedArtifactId: string | null
}

export interface PdfArtifactPayload {
  artifactId: string
  artifactRevision: string
  sourceRevision: string
  sha256: string
  bytes: ArrayBuffer
}

export interface OriginalDocxPayload {
  artifactId: string
  filename: string
  sha256: string
  bytes: ArrayBuffer
}

export interface ConnectivitySnapshot {
  state: Exclude<ConnectivityState, 'connecting'>
  apiVersion?: string
  checkedAt: string
  message: string
}

export interface SetupSnapshot {
  state: 'required' | 'working' | 'succeeded' | 'ready' | 'error'
  message: string
}

export interface DiagnosticsSnapshot {
  mode: 'local-service' | 'remote-client' | 'not-configured'
  appVersion: string
  apiVersion?: string
  capabilities: {
    localService: 'available' | 'unavailable' | 'not-configured'
    agent: 'available' | 'offline' | 'not-configured'
    desktop: 'available' | 'unavailable'
  }
}

export type AgentConnectionState = 'online' | 'connecting' | 'offline' | 'reconnecting'
export type ConversationEntryType = 'user_message' | 'turn' | 'activity' | 'assistant_message' | 'status' | 'error'
export type ConversationEntryState = 'queued' | 'working' | 'waiting' | 'completed' | 'failed' | 'interrupted'

export type SafeConversationDetailValue = string | number | boolean | null | SafeConversationDetailValue[] | { [key: string]: SafeConversationDetailValue }

export interface ConversationEvent {
  eventId: number
  turnId: string | null
  type: ConversationEntryType
  state: ConversationEntryState
  summary: string
  detail: Record<string, SafeConversationDetailValue>
  occurredAt: string
  messageId?: string
  text?: string
  sourceTurnId?: string | null
}

export interface AgentTurn {
  turnId: string
  status: 'queued' | 'running' | 'waiting'
  cancelRequested: boolean
}

export interface AgentConversationSnapshot {
  conversationId: string
  entries: ConversationEvent[]
  activeTurn: AgentTurn | null
  connection: Exclude<AgentConnectionState, 'reconnecting'>
  latestEventId: number
}

export interface AgentTurnMutation {
  turnId: string
  messageId?: string | null
  sourceTurnId?: string | null
  status?: string | null
}

export type AgentStreamUpdate =
  | { kind: 'event'; event: ConversationEvent }
  | { kind: 'connection'; state: AgentConnectionState }

export interface JobOsRendererBridge {
  setup: {
    get: () => Promise<SetupSnapshot>
    initialize: (resetDemo?: boolean, confirmed?: boolean) => Promise<SetupSnapshot>
    restart: () => Promise<void>
  }
  diagnostics: {
    get: () => Promise<DiagnosticsSnapshot>
    openData: () => Promise<void>
    openLogs: () => Promise<void>
  }
  lifecycle: {
    subscribePrepareClose: (handler: () => Promise<boolean>) => () => void
  }
  shell: {
    openExternal: (url: string) => Promise<void>
  }
  connectivity: {
    get: () => Promise<ConnectivitySnapshot>
  }
  agent: {
    get: () => Promise<AgentConversationSnapshot>
    reset: () => Promise<AgentConversationSnapshot>
    send: (text: string, idempotencyKey: string) => Promise<AgentTurnMutation>
    cancel: (turnId: string) => Promise<AgentTurnMutation>
    retry: (turnId: string, idempotencyKey: string) => Promise<AgentTurnMutation>
    subscribe: (listener: (update: AgentStreamUpdate) => void) => () => void
  }
  jobs: {
    getState: () => Promise<JobWorkspaceSnapshot>

    list: (sort: JobSortMode, query?: string, statusGroup?: string) => Promise<JobListItem[]>
    inspect: (jobId: string) => Promise<JobDetail>
    select: (jobId: string) => Promise<JobMutationResult>
    reorder: (jobIds: string[]) => Promise<JobMutationResult>
    setSort: (sort: JobSortMode) => Promise<JobMutationResult>
    updateStatus: (jobId: string, status: JobStatus) => Promise<JobStatusMutationResult>
    removeDemo: (jobId: string) => Promise<JobMutationResult>
    saveFromBrowser: (
      tabId: string,
      expectedUrl: string,
      extraction: BrowserJobExtraction,
      idempotencyKey: string
    ) => Promise<BrowserJobSaveResult>
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
    approve: (jobId: string, artifactId: string) => Promise<JobArtifactsState>
    loadPdf: (artifactId: string) => Promise<PdfArtifactPayload>
    loadOriginalDocx: (artifactId: string) => Promise<OriginalDocxPayload>
    export: (artifactId: string) => Promise<string>
    reveal: (artifactId: string) => Promise<string>
    open: (artifactId: string) => Promise<string>
  }
  docxDocuments: DocxDocumentsBridge
  editableDocuments: {
    list: (jobId: string) => Promise<EditableDocumentSummary[]>
    getForJob: (jobId: string, documentKey: EditableDocumentKey) => Promise<EditableDocument>
    get: (documentId: string) => Promise<EditableDocument>
    createBlank: (jobId: string, documentKey: EditableDocumentKey, idempotencyKey: string) => Promise<EditableDocument>
    save: (documentId: string, request: SaveEditableDocumentRequest) => Promise<EditableDocument>
    listSnapshots: (documentId: string) => Promise<EditableDocumentSnapshot[]>
    createSnapshot: (documentId: string, request: CreateEditableDocumentSnapshotRequest) => Promise<EditableDocumentSnapshot>
    restoreSnapshot: (
      documentId: string,
      snapshotId: string,
      request: RestoreEditableDocumentSnapshotRequest
    ) => Promise<EditableDocument>
    applyOperations: (documentId: string, request: ApplyEditableDocumentOperationsRequest) => Promise<OperationReceipt>
    importRegisteredArtifact: (
      jobId: string,
      documentKey: EditableDocumentKey,
      artifactId: string
    ) => Promise<EditableDocument>
    importFile: (jobId: string, documentKey: EditableDocumentKey) => Promise<EditableDocument | null>
    preview: (documentId: string) => Promise<EditableDocumentPreview>
    export: (documentId: string, format: 'docx' | 'pdf') => Promise<EditableDocumentExportResult>
    publish: (documentId: string) => Promise<EditableDocument>
  }
}
