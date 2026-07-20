import { ArrowDown, ArrowUp, BriefcaseBusiness, Search, UserRound } from 'lucide-react'

import type { JobListItem, JobSortMode, JobStatus } from '../../shared/contracts'

const STATUSES: JobStatus[] = ['discovered', 'scored', 'reviewed', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'applied', 'interviewing', 'closed', 'archived']
const STATUS_GROUPS = ['Inbox', 'Considering', 'Applied', 'Interviewing', 'Closed', 'Inactive']

interface JobNavigatorProps {
  jobs: JobListItem[]
  selectedJobId: string | null
  sortMode: JobSortMode
  query: string
  statusGroup: string
  loading: boolean
  error: string | null
  feedback: string | null
  onQueryChange: (query: string) => void
  onStatusGroupChange: (statusGroup: string) => void
  onSortChange: (sort: JobSortMode) => void
  onSelect: (jobId: string) => void
  onStatusChange: (jobId: string, status: JobStatus) => void
  onMove: (jobId: string, direction: -1 | 1) => void
  onReorder: (sourceJobId: string, targetJobId: string) => void
}

function statusLabel(status: string) {
  return status.replaceAll('_', ' ')
}

export function JobNavigator(props: JobNavigatorProps) {
  const canReorder = props.sortMode === 'manual' && !props.query && !props.statusGroup
  return (
    <aside aria-label="Job navigation" className="job-navigator panel-region">
      <div className="navigator-controls">
        <div className="sort-row">
          <select
            aria-label="Job ordering"
            className="sort-control"
            onChange={event => props.onSortChange(event.target.value as JobSortMode)}
            value={props.sortMode}
          >
            <option value="manual">Manual</option>
            <option value="recent">Recent</option>
            <option value="alphabetical">Alphabetical</option>
            <option value="status">Status</option>
          </select>
        </div>
        <label className="navigator-search">
          <Search aria-hidden="true" size={14} strokeWidth={1.5} />
          <input aria-label="Filter jobs" onChange={event => props.onQueryChange(event.target.value)} placeholder="Filter jobs" value={props.query} />
        </label>
        <select aria-label="Filter by status group" className="group-filter" onChange={event => props.onStatusGroupChange(event.target.value)} value={props.statusGroup}>
          <option value="">All statuses</option>
          {STATUS_GROUPS.map(group => <option key={group} value={group}>{group}</option>)}
        </select>
      </div>

      <div className="job-list" role="list">
        {props.loading ? <p className="navigator-message">Loading opportunities…</p> : null}
        {!props.loading && props.jobs.length === 0 ? (
          <div className="navigator-empty">
            <BriefcaseBusiness aria-hidden="true" size={22} strokeWidth={1.4} />
            <h2>No matching opportunities</h2>
            <p>Try another filter or reconnect the shared job source.</p>
          </div>
        ) : null}
        {props.jobs.map((job, index) => (
          <div
            className={`job-row${job.jobId === props.selectedJobId ? ' selected' : ''}`}
            draggable={canReorder}
            key={job.jobId}
            onDragOver={event => { if (canReorder) event.preventDefault() }}
            onDragStart={event => {
              event.dataTransfer.effectAllowed = 'move'
              event.dataTransfer.setData('text/jobos-job-id', job.jobId)
            }}
            onDrop={event => {
              if (!canReorder) return
              event.preventDefault()
              const sourceJobId = event.dataTransfer.getData('text/jobos-job-id')
              if (sourceJobId && sourceJobId !== job.jobId) props.onReorder(sourceJobId, job.jobId)
            }}
            role="listitem"
          >
            {props.sortMode === 'status' && props.jobs[index - 1]?.statusGroup !== job.statusGroup ? <div className="status-heading">{job.statusGroup}</div> : null}
            <div className="job-row-main">
              <button aria-label={`Select ${job.company} ${job.title}`} className="job-select" onClick={() => props.onSelect(job.jobId)} type="button">
                <BriefcaseBusiness aria-hidden="true" size={17} strokeWidth={1.45} />
                <span><strong>{job.company}</strong><small>{job.title}</small></span>
              </button>
              {canReorder ? (
                <span className="order-buttons">
                  <button aria-label={`Move ${job.company} up`} className="icon-button" disabled={index === 0} onClick={() => props.onMove(job.jobId, -1)} type="button"><ArrowUp aria-hidden="true" size={13} /></button>
                  <button aria-label={`Move ${job.company} down`} className="icon-button" disabled={index === props.jobs.length - 1} onClick={() => props.onMove(job.jobId, 1)} type="button"><ArrowDown aria-hidden="true" size={13} /></button>
                </span>
              ) : null}
            </div>
            <select aria-label={`Change ${job.company} status`} className="status-select" onChange={event => props.onStatusChange(job.jobId, event.target.value as JobStatus)} value={job.status}>
              {STATUSES.map(status => <option key={status} value={status}>{statusLabel(status)}</option>)}
            </select>
          </div>
        ))}
      </div>

      <div aria-live="polite" className={`navigator-feedback${props.error ? ' error' : ''}`}>{props.error ?? props.feedback ?? ''}</div>
      <div className="profile-row">
        <span className="profile-avatar"><UserRound aria-hidden="true" size={18} strokeWidth={1.4} /></span>
        <span className="profile-copy"><strong>Jacobi Lange</strong><small>Personal workspace</small></span>
      </div>
    </aside>
  )
}
