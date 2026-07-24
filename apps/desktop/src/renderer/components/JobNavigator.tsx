import { useState } from 'react'
import { ArrowDown, ArrowUp, BriefcaseBusiness, ChevronRight, Search, UserRound } from 'lucide-react'

import type { JobDetail, JobListItem, JobSortMode, JobStatus } from '../../shared/contracts'

const STATUS_TRANSITIONS: Record<JobStatus, JobStatus[]> = {
  discovered: ['scored', 'reviewed', 'skipped', 'archived'],
  scored: ['reviewed', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'archived'],
  reviewed: ['shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'archived'],
  shortlisted: ['apply_now', 'maybe', 'stretch', 'applied'],
  apply_now: ['applied', 'interviewing', 'closed'],
  maybe: ['reviewed', 'apply_now', 'skipped', 'archived'],
  stretch: ['reviewed', 'apply_now', 'skipped', 'archived'],
  skipped: ['reviewed', 'archived'],
  applied: ['interviewing', 'closed', 'archived'],
  interviewing: ['closed', 'archived'],
  closed: ['archived'],
  archived: []
}
const STATUS_GROUPS = ['Inbox', 'Considering', 'Applied', 'Interviewing', 'Closed', 'Inactive']

interface JobNavigatorProps {
  jobs: JobListItem[]
  selectedJobId: string | null
  selectedJobDetail?: JobDetail | null
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
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set())
  const canReorder = props.sortMode === 'manual' && !props.query && !props.statusGroup
  const groupedJobs = props.jobs.reduce<Array<{ name: string, jobs: Array<{ job: JobListItem, index: number }> }>>((groups, job, index) => {
    const existingGroup = groups.find(group => group.name === job.statusGroup)
    if (existingGroup) existingGroup.jobs.push({ job, index })
    else groups.push({ name: job.statusGroup, jobs: [{ job, index }] })
    return groups
  }, [])

  const toggleGroup = (group: string) => {
    setExpandedGroups(current => {
      const next = new Set(current)
      if (next.has(group)) next.delete(group)
      else next.add(group)
      return next
    })
  }

  const renderJob = (job: JobListItem, index: number) => {
    const detail = job.jobId === props.selectedJobId ? props.selectedJobDetail : null
    const preview = detail?.description.length && detail.description.length > 180
      ? `${detail.description.slice(0, 177).trimEnd()}…`
      : detail?.description
    return (
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
        {[job.status, ...STATUS_TRANSITIONS[job.status]].map(status => <option key={status} value={status}>{statusLabel(status)}</option>)}
      </select>
      {detail ? (
        <div className="job-description-card">
          {detail.location ? <span className="job-location">{detail.location}</span> : null}
          <p>{preview}</p>
          <details>
            <summary>Full listing</summary>
            <div className="full-listing-text">{detail.description}</div>
          </details>
        </div>
      ) : null}
    </div>
    )
  }

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

      <div className="job-list" role={props.sortMode === 'status' ? undefined : 'list'}>
        {props.loading ? <p className="navigator-message">Loading opportunities…</p> : null}
        {!props.loading && props.jobs.length === 0 ? (
          <div className="navigator-empty">
            <BriefcaseBusiness aria-hidden="true" size={22} strokeWidth={1.4} />
            <h2>No matching opportunities</h2>
            <p>Try another filter or reconnect the shared job source.</p>
          </div>
        ) : null}
        {props.sortMode === 'status' ? groupedJobs.map(group => {
          const expanded = expandedGroups.has(group.name)
          const panelId = `job-status-${group.name.toLowerCase().replaceAll(' ', '-')}`
          const jobCount = `${group.jobs.length} ${group.jobs.length === 1 ? 'job' : 'jobs'}`
          return (
            <section className="status-section" key={group.name}>
              <button
                aria-controls={panelId}
                aria-expanded={expanded}
                aria-label={`${group.name}, ${jobCount}`}
                className="status-heading"
                onClick={() => toggleGroup(group.name)}
                type="button"
              >
                <ChevronRight aria-hidden="true" className="status-heading-chevron" size={13} strokeWidth={1.7} />
                <span>{group.name}</span>
                <small>{group.jobs.length}</small>
              </button>
              <div aria-label={`${group.name} jobs`} hidden={!expanded} id={panelId} role="list">
                {group.jobs.map(({ job, index }) => renderJob(job, index))}
              </div>
            </section>
          )
        }) : props.jobs.map(renderJob)}
      </div>

      <div aria-live="polite" className={`navigator-feedback${props.error ? ' error' : ''}`}>{props.error ?? props.feedback ?? ''}</div>
      <div className="profile-row">
        <span className="profile-avatar"><UserRound aria-hidden="true" size={18} strokeWidth={1.4} /></span>
        <span className="profile-copy"><strong>Jacobi Lange</strong><small>Personal workspace</small></span>
      </div>
    </aside>
  )
}
