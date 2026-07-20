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
}
