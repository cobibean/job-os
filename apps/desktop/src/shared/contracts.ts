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

export type WorkArrangementMode = 'remote' | 'hybrid' | 'onsite' | 'flexible'
export type WorkArrangementStrength = 'requirement' | 'strong_preference' | 'preference' | 'dealbreaker'
export const CAREER_PROFILE_ADDITIONAL_CONTEXT_LIMIT = 1000
export function careerProfileAdditionalContextLength(value: string): number {
  return Array.from(value).length
}

export interface WorkArrangementValue {
  mode: WorkArrangementMode
  strength: WorkArrangementStrength
  note: string | null
}

export interface WorkArrangementRecord {
  actorPrincipal: string
  itemRevision: number
  profileRevision: number
  recordId: string
  updatedAt: string
  value: WorkArrangementValue
}

export interface WorkArrangementCurrent {
  cacheProof?: string
  profileRevision: number
  record: WorkArrangementRecord | null
}

export interface WorkArrangementRevision {
  actorPrincipal: string
  baseProfileRevision: number
  changedFields: string[]
  createdAt: string
  itemRevision: number
  operation: 'set' | 'restore'
  profileRevision: number
  recordId: string
  restoredFromProfileRevision: number | null
  revisionId: string
  value: WorkArrangementValue
}

export interface WorkArrangementHistory {
  profileRevision: number
  revisions: WorkArrangementRevision[]
}

export interface WorkArrangementMutationRequest {
  expectedProfileRevision: number
  idempotencyKey: string
  value: WorkArrangementValue
}

export interface WorkArrangementRestoreRequest {
  expectedProfileRevision: number
  idempotencyKey: string
  targetProfileRevision: number
}

export type WorkArrangementMutationResult =
  | { status: 'saved'; current: WorkArrangementCurrent }
  | { status: 'conflict'; current: WorkArrangementCurrent }

export type CareerProfileTrustMode = 'review' | 'direct'
export type CareerProfileActorKind =
  | 'direct_user'
  | 'authenticated_user_instruction'
  | 'deterministic_source_mapping'
  | 'autonomous_agent'
  | 'user_proposal_decision'

export interface ConnectedCareerProfileAgent {
  active: boolean
  agentId: string
  connectedAt: string
  disconnectedAt: string | null
  displayName: string
  principal: string
  trustMode: CareerProfileTrustMode
  updatedAt: string
}

export interface CareerProfileItemSnapshot {
  actorPrincipal: string
  area: 'my_career' | 'what_im_looking_for' | 'my_evidence'
  createdAt: string
  evidenceIds: string[]
  itemId: string
  itemRevision: number
  provenance: Record<string, unknown>
  reviewStatus: 'accepted' | 'proposed' | 'conflicting'
  updatedAt: string
  value: Record<string, unknown>
}

export interface CareerProfileChangeProposal {
  after: CareerProfileItemSnapshot | null
  agentDisplayName: string
  agentId: string
  baseProfileRevision: number
  before: CareerProfileItemSnapshot | null
  createdAt: string
  evidenceIds: string[]
  operation: 'item.create' | 'item.update' | 'item.remove'
  proposalId: string
  proposalSha256: string
  reason: string
  reviewReason: string
  status: 'pending' | 'accepted' | 'rejected'
  targetId: string
}

export interface CareerProfileProposalDecisionRequest {
  decision: 'accept' | 'reject'
  expectedProfileRevision: number
  idempotencyKey: string
  proposalSha256: string
}

export interface CareerProfileProposalDecisionResult {
  profileRevision: number
  proposal: CareerProfileChangeProposal
}

export interface CareerProfileChangeRevision {
  actorKind: CareerProfileActorKind
  actorPrincipal: string
  affectedFields: string[]
  after: Record<string, unknown> | null
  baseProfileRevision: number
  before: Record<string, unknown> | null
  createdAt: string
  evidenceId: string | null
  itemId: string | null
  operation: string
  profileRevision: number
  proposalId: string | null
  reason: string | null
  revisionId: string
  undoOfRevisionId: string | null
  undoable: boolean
}

export interface CareerProfileChangeHistory {
  profileRevision: number
  revisions: CareerProfileChangeRevision[]
}

export interface CareerProfileUndoRequest {
  expectedProfileRevision: number
  idempotencyKey: string
}

export type CareerProfileArea = 'my_career' | 'what_im_looking_for' | 'my_evidence'
export type CareerProfileContextMode = 'none' | 'selected' | 'broader'
export type CareerProfileEvidenceKind = 'resume' | 'portfolio' | 'supporting_document' | 'citation'
export type CareerProfileEvidenceMode = 'profile_only' | 'selected' | 'all'

export interface CareerProfileEvidence {
  active: boolean
  byteCount: number
  capturedAt: string | null
  evidenceId: string
  importedAt: string
  mediaType: string
  originalFilename: string
  provenance: {
    method: 'user_import' | 'agent_import' | 'migration_import'
    sourceKind: CareerProfileEvidenceKind
    sourceLabel: string
  }
  sha256: string
}

export interface CareerProfileCurrent {
  authorityEpoch: number
  items: CareerProfileItemSnapshot[]
  profileRevision: number
  sourceEvidence: CareerProfileEvidence[]
}

export interface CareerProfileItemMutationRequest {
  evidenceIds: string[]
  expectedProfileRevision: number
  idempotencyKey: string
  value: Record<string, unknown> & { kind: string }
}

export interface CareerProfileRemovalRequest {
  expectedProfileRevision: number
  idempotencyKey: string
}

export interface CareerProfileEvidenceImportRequest {
  capturedAt: string | null
  contentBase64: string
  expectedProfileRevision: number
  idempotencyKey: string
  mediaType: string
  originalFilename: string
  sourceKind: CareerProfileEvidenceKind
  sourceLabel: string
}

export type CareerProfileMutationResult =
  | { status: 'saved'; current: CareerProfileCurrent }
  | { status: 'conflict'; current: CareerProfileCurrent }

export interface CareerProfileContextScope {
  agentId: string
  mode: CareerProfileContextMode
  selectedAreas: CareerProfileArea[]
  selectedItemIds: string[]
  updatedAt: string
}

export interface CareerProfileContextUpdateRequest {
  expectedAuthorityEpoch: number
  expectedProfileRevision: number
  idempotencyKey: string
  mode: CareerProfileContextMode
  selectedAreas: CareerProfileArea[]
  selectedItemIds: string[]
}

export interface CareerProfileContextPreview {
  authorityEpoch: number
  contentHash: string
  createdAt: string
  profileRevision: number
  profile: CareerProfileCurrent
}

export interface CareerProfileExportRequest {
  evidenceMode: CareerProfileEvidenceMode
  expectedProfileRevision: number
  selectedEvidenceIds: string[]
}

export type CareerProfileExportResult =
  | { status: 'cancelled' }
  | {
      status: 'saved'
      byteCount: number
      filename: string
      includedEvidenceIds: string[]
      omittedEvidenceIds: string[]
      sha256: string
    }

export interface CareerProfileArchiveSelection {
  archiveToken: string
  byteCount: number
  filename: string
}

export interface CareerProfileRestoreRequest {
  archiveToken: string
  confirmation: 'RESTORE_CAREER_PROFILE_BASELINE'
  expectedProfileRevision: number
  idempotencyKey: string
}

export interface CareerProfileRestoreResult {
  archiveSha256: string
  baselineCreated: true
  profile: CareerProfileCurrent
  restoredEvidenceIds: string[]
  unavailableEvidenceIds: string[]
}

export interface CareerProfileBridge {
  availability: () => Promise<{ enabled: boolean }>
  validateCachedWorkArrangement: (candidate: unknown) => Promise<WorkArrangementCurrent | null>
  getWorkArrangement: () => Promise<WorkArrangementCurrent>
  saveWorkArrangement: (request: WorkArrangementMutationRequest) => Promise<WorkArrangementMutationResult>
  getWorkArrangementHistory: () => Promise<WorkArrangementHistory>
  restoreWorkArrangement: (request: WorkArrangementRestoreRequest) => Promise<WorkArrangementMutationResult>
  listConnectedAgents: () => Promise<ConnectedCareerProfileAgent[]>
  updateConnectedAgentTrustMode: (
    agentId: string,
    trustMode: CareerProfileTrustMode
  ) => Promise<ConnectedCareerProfileAgent>
  disconnectConnectedAgent: (agentId: string) => Promise<ConnectedCareerProfileAgent>
  listCareerProfileProposals: () => Promise<CareerProfileChangeProposal[]>
  decideCareerProfileProposal: (
    proposalId: string,
    request: CareerProfileProposalDecisionRequest
  ) => Promise<CareerProfileProposalDecisionResult>
  getCareerProfileChangeHistory: () => Promise<CareerProfileChangeHistory>
  undoCareerProfileChange: (
    revisionId: string,
    request: CareerProfileUndoRequest
  ) => Promise<{ profileRevision: number }>
  getCareerProfile: () => Promise<CareerProfileCurrent>
  createCareerProfileItem: (request: CareerProfileItemMutationRequest) => Promise<CareerProfileMutationResult>
  updateCareerProfileItem: (
    itemId: string,
    request: CareerProfileItemMutationRequest
  ) => Promise<CareerProfileMutationResult>
  removeCareerProfileItem: (
    itemId: string,
    request: CareerProfileRemovalRequest
  ) => Promise<CareerProfileMutationResult>
  importCareerProfileEvidence: (
    request: CareerProfileEvidenceImportRequest
  ) => Promise<CareerProfileMutationResult>
  removeCareerProfileEvidence: (
    evidenceId: string,
    request: CareerProfileRemovalRequest
  ) => Promise<CareerProfileMutationResult>
  getCareerProfileContext: (agentId: string) => Promise<CareerProfileContextScope>
  updateCareerProfileContext: (
    agentId: string,
    request: CareerProfileContextUpdateRequest
  ) => Promise<CareerProfileContextScope>
  previewCareerProfileContext: (agentId: string) => Promise<CareerProfileContextPreview>
  exportCareerProfile: (request: CareerProfileExportRequest) => Promise<CareerProfileExportResult>
  chooseCareerProfileArchive: () => Promise<CareerProfileArchiveSelection | null>
  restoreCareerProfile: (request: CareerProfileRestoreRequest) => Promise<CareerProfileRestoreResult>
}

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
  requestedTextStart: number
  textStart: number
  textLength: number
  totalTextLength: number
  hasMore: boolean
  pageRevision: string
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
  transport?: 'local-loopback' | 'private-remote'
  agent?: 'not-configured' | 'online' | 'connecting' | 'offline'
  desktop?: 'connected' | 'disconnected'
  artifactStorage?: 'available' | 'unavailable'
  artifactGateway?: 'not-configured' | 'available' | 'unavailable'
  installationProfileId?: string
  installationProfileName?: string
  profileRegistryRevision?: number
}

export interface InstallationProfileSummary {
  profileId: string
  displayName: string
  active: boolean
  createdAt: string
  updatedAt: string
}

export interface InstallationProfileListSnapshot {
  registryRevision: number
  activeProfileId: string
  profiles: InstallationProfileSummary[]
}

export interface SetupSnapshot {
  state: 'required' | 'working' | 'succeeded' | 'ready' | 'error'
  message: string
}

export interface DiagnosticsSnapshot {
  mode: 'local-service' | 'remote-client' | 'not-configured'
  appVersion: string
  apiVersion?: string
  installationProfile?: {
    id: string
    name: string
    switchStatus: 'idle' | 'switching'
  }
  capabilities: {
    localService: 'available' | 'unavailable' | 'not-configured'
    agent: 'available' | 'connecting' | 'offline' | 'not-configured'
    desktop: 'available' | 'disconnected'
    renderer: 'available' | 'unavailable'
    artifactStorage: 'available' | 'unavailable'
    artifactGateway: 'not-configured' | 'available' | 'unavailable'
    transport: 'local-loopback' | 'private-remote' | 'not-configured'
  }
}

export type AgentConnectionState = 'online' | 'connecting' | 'offline' | 'reconnecting'
export type AgentRecoveryState = 'ready' | 'recovering' | 'quarantined'
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

export interface AgentSessionJobContext {
  selectedJobId: string | null
  activeArtifactId: string | null
  activeArtifactPage: number
  activeArtifactZoom: number
}

export interface AgentConversationSnapshot {
  conversationId: string
  position: number
  title: string
  createdAt: string
  entries: ConversationEvent[]
  activeTurn: AgentTurn | null
  connection: Exclude<AgentConnectionState, 'reconnecting'>
  recoveryState: AgentRecoveryState
  latestEventId: number
  jobContext: AgentSessionJobContext
}

export interface AgentSessionSummary {
  conversationId: string
  position: number
  title: string
  createdAt: string
  activeTurn: AgentTurn | null
  connection: Exclude<AgentConnectionState, 'reconnecting'>
  recoveryState: AgentRecoveryState
  latestEventId: number
  jobContext: AgentSessionJobContext
}

export interface AgentTurnMutation {
  turnId: string
  messageId?: string | null
  sourceTurnId?: string | null
  status?: string | null
}

export type AgentSessionStreamUpdate =
  | { kind: 'event'; conversationId: string; recoveryState: AgentRecoveryState; event: ConversationEvent }
  | { kind: 'connection'; conversationId: string; state: AgentConnectionState }

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
    subscribePrepareClose: (
      handler: (reason: 'window-close' | 'profile-switch') => Promise<boolean>
    ) => () => void
  }
  shell: {
    openExternal: (url: string) => Promise<void>
  }
  connectivity: {
    get: () => Promise<ConnectivitySnapshot>
  }
  installationProfiles: {
    expectedProfileId?: string
    list: () => Promise<InstallationProfileListSnapshot>
    createAndSwitch: (displayName: string, idempotencyKey: string) => Promise<void>
    rename: (
      profileId: string,
      displayName: string,
      expectedRegistryRevision: number,
      idempotencyKey: string
    ) => Promise<InstallationProfileListSnapshot>
    activate: (
      profileId: string,
      expectedRegistryRevision: number,
      idempotencyKey: string
    ) => Promise<void>
    restart: () => Promise<void>
  }
  careerProfile: CareerProfileBridge
  agent: {
    list: () => Promise<AgentSessionSummary[]>
    create: (initialSelectedJobId?: string | null) => Promise<AgentConversationSnapshot>
    get: (conversationId: string) => Promise<AgentConversationSnapshot>
    archive: (conversationId: string) => Promise<void>
    send: (conversationId: string, text: string, idempotencyKey: string) => Promise<AgentTurnMutation>
    cancel: (conversationId: string, turnId: string) => Promise<AgentTurnMutation>
    retry: (conversationId: string, turnId: string, idempotencyKey: string) => Promise<AgentTurnMutation>
    subscribe: (listener: (update: AgentSessionStreamUpdate) => void) => () => void
  }
  jobs: {
    getState: () => Promise<JobWorkspaceSnapshot>

    list: (sort: JobSortMode, query?: string, statusGroup?: string) => Promise<JobListItem[]>
    inspect: (jobId: string) => Promise<JobDetail>
    select: (conversationId: string, jobId: string) => Promise<AgentSessionJobContext>
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
    saveDocumentView: (conversationId: string, artifactId: string | null, page: number, zoom: number) => Promise<AgentSessionJobContext>
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
