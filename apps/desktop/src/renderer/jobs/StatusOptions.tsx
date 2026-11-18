import type { JobStatus } from '../../shared/contracts'
import { CANONICAL_STATUS_GROUPS, JOB_STATUSES, STATUS_GROUP_BY_STATUS, STATUS_TRANSITIONS, statusOptionLabel } from './jobStatus'

/** Options for a job status <select>, grouped under the list each status files the job into. */
export function StatusOptions({ current }: { current: JobStatus }) {
  const available = new Set<JobStatus>([current, ...STATUS_TRANSITIONS[current]])
  return (
    <>
      {CANONICAL_STATUS_GROUPS.map(group => {
        const statuses = JOB_STATUSES.filter(status => STATUS_GROUP_BY_STATUS[status] === group && available.has(status))
        return statuses.length ? (
          <optgroup key={group} label={group}>
            {statuses.map(status => <option key={status} value={status}>{statusOptionLabel(current, status)}</option>)}
          </optgroup>
        ) : null
      })}
    </>
  )
}
