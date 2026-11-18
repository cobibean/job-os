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

// Which list each status files a job into. Mirrors STATUS_GROUPS in the API
// (services/api/jobos_api/jobs.py), which stays authoritative for job.statusGroup.
export const STATUS_GROUP_BY_STATUS: Record<JobStatus, typeof CANONICAL_STATUS_GROUPS[number]> = {
  discovered: 'Inbox', scored: 'Inbox', reviewed: 'Inbox',
  shortlisted: 'Considering', apply_now: 'Considering', maybe: 'Considering', stretch: 'Considering',
  applied: 'Applied', interviewing: 'Interviewing', closed: 'Closed',
  skipped: 'Inactive', archived: 'Inactive'
}

// The one user-facing name for each status, used everywhere a status is shown.
export function statusLabel(status: string) {
  if (status === 'shortlisted') return 'Considering'
  const label = status.replaceAll('_', ' ')
  return label.charAt(0).toUpperCase() + label.slice(1)
}

export function statusOptionLabel(currentStatus: JobStatus, optionStatus: JobStatus) {
  if (currentStatus !== 'applied' && optionStatus === 'applied') return 'Mark applied'
  return statusLabel(optionStatus)
}
