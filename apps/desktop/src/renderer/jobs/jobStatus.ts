import type { JobStatus } from '../../shared/contracts'

export const CANONICAL_STATUS_GROUPS = [
  'Inbox', 'Considering', 'Applied', 'Interviewing', 'Closed', 'Inactive'
] as const

// Lifecycle order, used to order status dropdown options.
export const JOB_STATUSES: readonly JobStatus[] = [
  'discovered', 'scored', 'reviewed', 'shortlisted', 'apply_now', 'maybe',
  'stretch', 'skipped', 'applied', 'interviewing', 'closed', 'archived'
]

// Any status may move to any other; the API records every change in job history.
// Kept equal to tests/fixtures/job-status-transitions.json; the API's
// ALLOWED_TRANSITIONS is contract-tested against the same fixture.
export const STATUS_TRANSITIONS = Object.fromEntries(
  JOB_STATUSES.map(status => [status, JOB_STATUSES.filter(target => target !== status)])
) as Record<JobStatus, JobStatus[]>

export function statusLabel(status: string) {
  return status.replaceAll('_', ' ')
}

export function statusOptionLabel(currentStatus: JobStatus, optionStatus: JobStatus) {
  if (optionStatus === 'shortlisted') return 'Considering'
  if (currentStatus !== 'applied' && optionStatus === 'applied') return 'Mark applied'
  return statusLabel(optionStatus)
}
