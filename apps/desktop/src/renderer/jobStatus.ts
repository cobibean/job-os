import type { JobStatus } from '../shared/contracts'

export const CANONICAL_STATUS_GROUPS = [
  'Inbox', 'Considering', 'Applied', 'Interviewing', 'Closed', 'Inactive'
] as const

export const STATUS_TRANSITIONS: Record<JobStatus, JobStatus[]> = {
  discovered: ['shortlisted', 'applied', 'scored', 'reviewed', 'skipped', 'archived'],
  scored: ['applied', 'reviewed', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'archived'],
  reviewed: ['applied', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'archived'],
  shortlisted: ['apply_now', 'maybe', 'stretch', 'applied'],
  apply_now: ['applied', 'interviewing', 'closed'],
  maybe: ['applied', 'reviewed', 'apply_now', 'skipped', 'archived'],
  stretch: ['applied', 'reviewed', 'apply_now', 'skipped', 'archived'],
  skipped: ['applied', 'reviewed', 'archived'],
  applied: ['interviewing', 'closed', 'archived'],
  interviewing: ['closed', 'archived'],
  closed: ['archived'],
  archived: []
}

export function statusLabel(status: string) {
  return status.replaceAll('_', ' ')
}

export function statusOptionLabel(currentStatus: JobStatus, optionStatus: JobStatus) {
  if (optionStatus === 'shortlisted') return 'Considering'
  if (currentStatus !== 'applied' && optionStatus === 'applied') return 'Mark applied'
  return statusLabel(optionStatus)
}
