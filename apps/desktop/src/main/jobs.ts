import {
  createJobOsApiClient,
  jobUpdateStatusV1JobsJobIdStatusPut,
  jobsListV1JobsGet,
  jobsReorderV1JobsOrderPut,
  workspaceJobsV1WorkspaceJobsGet,
  workspaceSelectJobV1WorkspaceJobsSelectionPut,
  workspaceSortJobsV1WorkspaceJobsSortPut
} from '@jobos/contracts'
import type {
  JobEvent as ApiJobEvent,
  JobListItem as ApiJobListItem,
  JobListResponse,
  JobMutationResponse,
  StatusChangeResponse,
  WorkspaceJobsResponse
} from '@jobos/contracts'

import type {
  JobEvent,
  JobListItem,
  JobMutationResult,
  JobSortMode,
  JobStatus,
  JobStatusMutationResult,
  JobWorkspaceSnapshot
} from '../shared/contracts.js'

export interface JobsConfig {
  baseUrl: string
  deviceToken: string
}

interface ApiResult<T> {
  data?: T
  error?: unknown
  response?: Response
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}

function errorMessage(error: unknown, fallback: string) {
  if (isRecord(error) && typeof error.detail === 'string') return error.detail
  return fallback
}

function unwrap<T>(result: ApiResult<T>, fallback: string): T {
  if (result.response?.status === 200 && result.data !== undefined) return result.data
  throw new Error(errorMessage(result.error, fallback))
}

function toJob(job: ApiJobListItem): JobListItem {
  return {
    jobId: job.job_id,
    company: job.company,
    title: job.title,
    status: job.status as JobStatus,
    statusGroup: job.status_group,
    canonicalUrl: job.canonical_url,
    discoveredAt: job.discovered_at,
    lastSeenAt: job.last_seen_at
  }
}

export function createMainJobsClient(config: JobsConfig) {
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)

  return {
    async getState(): Promise<JobWorkspaceSnapshot> {
      const [jobsResult, workspaceResult] = await Promise.all([
        jobsListV1JobsGet({ client }),
        workspaceJobsV1WorkspaceJobsGet({ client })
      ])
      const jobs = unwrap<JobListResponse>(jobsResult, 'Jobs unavailable')
      const workspace = unwrap<WorkspaceJobsResponse>(workspaceResult, 'Job workspace unavailable')
      return {
        jobs: jobs.jobs.map(toJob),
        selectedJobId: workspace.selected_job_id,
        sortMode: workspace.sort_mode,
        manualOrder: workspace.manual_order
      }
    },

    async list(sort: JobSortMode, query?: string, statusGroup?: string): Promise<JobListItem[]> {
      const result = await jobsListV1JobsGet({
        client,
        query: { sort, query, status_group: statusGroup }
      })
      return unwrap<JobListResponse>(result, 'Jobs unavailable').jobs.map(toJob)
    },

    async select(jobId: string): Promise<JobMutationResult> {
      const result = await workspaceSelectJobV1WorkspaceJobsSelectionPut({
        client,
        body: { job_id: jobId, origin: 'user' }
      })
      const mutation = unwrap<JobMutationResponse>(result, 'Job selection failed')
      return { eventId: mutation.event_id }
    },

    async reorder(jobIds: string[]): Promise<JobMutationResult> {
      const result = await jobsReorderV1JobsOrderPut({
        client,
        body: { job_ids: jobIds, origin: 'user' }
      })
      const mutation = unwrap<JobMutationResponse>(result, 'Job reordering failed')
      return { eventId: mutation.event_id }
    },

    async setSort(sortMode: JobSortMode): Promise<JobMutationResult> {
      const result = await workspaceSortJobsV1WorkspaceJobsSortPut({
        client,
        body: { sort_mode: sortMode, origin: 'user' }
      })
      const mutation = unwrap<JobMutationResponse>(result, 'Job ordering failed')
      return { eventId: mutation.event_id }
    },

    async updateStatus(jobId: string, status: JobStatus): Promise<JobStatusMutationResult> {
      const result = await jobUpdateStatusV1JobsJobIdStatusPut({
        client,
        path: { job_id: jobId },
        body: { target_status: status, origin: 'user' }
      })
      const mutation = unwrap<StatusChangeResponse>(result, 'Status change failed')
      return { eventId: mutation.event_id, job: toJob(mutation.job) }
    }
  }
}

function toJobEvent(event: ApiJobEvent): JobEvent {
  return {
    eventId: event.event_id,
    eventType: event.event_type,
    origin: event.origin,
    jobId: event.job_id
  }
}

export class JobEventDecoder {
  private buffer = ''

  push(chunk: string): JobEvent[] {
    this.buffer += chunk.replaceAll('\r\n', '\n')
    const blocks = this.buffer.split('\n\n')
    this.buffer = blocks.pop() ?? ''
    const events: JobEvent[] = []
    for (const block of blocks) {
      const data = block.split('\n')
        .filter(line => line.startsWith('data:'))
        .map(line => line.slice(5).trimStart())
        .join('\n')
      if (!data) continue
      const parsed: unknown = JSON.parse(data)
      if (!isRecord(parsed) || typeof parsed.event_id !== 'number'
        || typeof parsed.event_type !== 'string'
        || (parsed.origin !== 'user' && parsed.origin !== 'mcp')) continue
      events.push(toJobEvent(parsed as unknown as ApiJobEvent))
    }
    return events
  }
}

export function startJobEventStream(
  target: { isDestroyed: () => boolean; send: (channel: string, event: JobEvent) => void },
  config: JobsConfig
): () => void {
  const controller = new AbortController()
  let cursor = 0

  const connect = async () => {
    while (!controller.signal.aborted && !target.isDestroyed()) {
      try {
        const url = new URL('/v1/events/stream', config.baseUrl)
        url.searchParams.set('after', String(cursor))
        const response = await fetch(url, {
          headers: { Authorization: `Bearer ${config.deviceToken}` },
          signal: controller.signal
        })
        if (!response.ok || !response.body) throw new Error('Job event stream unavailable')
        const reader = response.body.getReader()
        const textDecoder = new TextDecoder()
        const eventDecoder = new JobEventDecoder()
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read()
          if (done) break
          for (const event of eventDecoder.push(textDecoder.decode(value, { stream: true }))) {
            cursor = Math.max(cursor, event.eventId)
            target.send('jobos:jobs:event', event)
          }
        }
      } catch {
        if (controller.signal.aborted) return
      }
      await new Promise(resolve => setTimeout(resolve, 750))
    }
  }
  void connect()
  return () => controller.abort()
}
