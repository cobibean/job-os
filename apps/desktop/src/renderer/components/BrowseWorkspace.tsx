import { ArrowLeft, ArrowRight, BriefcaseBusiness, ExternalLink, Search } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'

import type { BrowseMode } from '../workspaceLayout'
import type { JobSortMode, JobStatus } from '../../shared/contracts'
import { CANONICAL_STATUS_GROUPS, STATUS_TRANSITIONS, statusLabel, statusOptionLabel } from '../jobStatus'
import { useBrowseJobs } from '../hooks/useBrowseJobs'

interface BrowseWorkspaceProps {
  activeJobId: string | null
  focusJobId: string | null
  mode: BrowseMode
  query: string
  railWidth: number
  sortMode: JobSortMode
  statusGroup: string
  onOpenJob: (jobId: string, canonicalUrl: string) => Promise<boolean>
  onUpdate: (update: Partial<{
    mode: BrowseMode
    focusJobId: string | null
    query: string
    statusGroup: string
    sortMode: JobSortMode
  }>, message?: string) => void
}

export function isBrowseInteractionControl(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || Boolean(target.closest([
    'button', 'a', 'input', 'select', 'textarea', 'menu', 'summary',
    '[contenteditable]:not([contenteditable="false"])',
    '[role="button"]', '[role="link"]', '[role="menu"]', '[role="menuitem"]',
    '[role="checkbox"]', '[role="radio"]', '[role="switch"]', '[role="slider"]',
    '[role="spinbutton"]', '[role="textbox"]', '[role="combobox"]', '[role="listbox"]',
    '[role="option"]', '[role="tab"]'
  ].join(', ')))
}

function CompanyMark({ company }: { company: string }) {
  const initials = company.trim().split(/\s+/).slice(0, 2).map(part => part[0]).join('').toLocaleUpperCase()
  return <span aria-hidden="true" className="browse-company-mark">{initials || '•'}</span>
}

export function BrowseWorkspace(props: BrowseWorkspaceProps) {
  const updateFocus = useCallback((focusJobId: string | null, message = '') => {
    props.onUpdate({ focusJobId }, message)
  }, [props.onUpdate])
  const state = useBrowseJobs({
    active: true,
    activeJobId: props.activeJobId,
    persistedFocusJobId: props.focusJobId,
    query: props.query,
    statusGroup: props.statusGroup,
    sortMode: props.sortMode,
    onFocusChange: updateFocus
  })
  const focusIndex = state.results.findIndex(job => job.jobId === state.focusJobId)
  const focusedJob = focusIndex >= 0 ? state.results[focusIndex] ?? null : null
  const detail = state.detail?.jobId === focusedJob?.jobId ? state.detail : null
  const detailLoading = state.detailJobId !== focusedJob?.jobId || state.detailLoading
  const pointerStart = useRef<{ pointerId: number, clientX: number } | null>(null)
  const [openingJobId, setOpeningJobId] = useState<string | null>(null)
  const [openError, setOpenError] = useState<string | null>(null)

  useEffect(() => setOpenError(null), [focusedJob?.jobId])

  const openFocusedJob = useCallback(async () => {
    if (!focusedJob || openingJobId !== null) return
    setOpeningJobId(focusedJob.jobId)
    setOpenError(null)
    try {
      if (!await props.onOpenJob(focusedJob.jobId, focusedJob.canonicalUrl)) {
        setOpenError('Could not open this job. Browse is still active; try again.')
      }
    } catch {
      setOpenError('Could not open this job. Browse is still active; try again.')
    } finally {
      setOpeningJobId(null)
    }
  }, [focusedJob, openingJobId, props.onOpenJob])

  const move = useCallback((direction: -1 | 1) => {
    const nextIndex = focusIndex + direction
    if (nextIndex < 0 || nextIndex >= state.results.length) return
    const next = state.results[nextIndex]
    if (next) updateFocus(next.jobId, `${next.company}, ${next.title}, ${nextIndex + 1} of ${state.results.length}`)
  }, [focusIndex, state.results, updateFocus])

  useEffect(() => {
    if (props.mode !== 'swipe') return
    const onKeyDown = (event: KeyboardEvent) => {
      if (isBrowseInteractionControl(event.target)) return
      if (event.key === 'ArrowLeft') {
        event.preventDefault()
        move(-1)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        move(1)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [move, props.mode])

  const counts = useMemo(() => new Map(CANONICAL_STATUS_GROUPS.map(group => [
    group, state.jobs.filter(job => job.statusGroup === group).length
  ])), [state.jobs])

  const statusControl = focusedJob ? (
    <div className="browse-status-field">
      <select
        aria-label={`Change ${focusedJob.company} status`}
        className="browse-status-control"
        disabled={STATUS_TRANSITIONS[focusedJob.status].length === 0}
        onChange={event => { void state.changeStatus(focusedJob.jobId, event.target.value as JobStatus) }}
        value={focusedJob.status}
      >
        {[focusedJob.status, ...STATUS_TRANSITIONS[focusedJob.status]].map(status => (
          <option key={status} value={status}>{statusOptionLabel(focusedJob.status, status)}</option>
        ))}
      </select>
      {state.mutationError ? <p className="browse-status-error" role="alert">{state.mutationError}</p> : null}
    </div>
  ) : null

  return (
    <main className={`browse-workspace browse-${props.mode}`} style={{ '--browse-rail-width': `${props.railWidth}px` } as CSSProperties}>
      <aside aria-label="Browse filters" className="browse-rail">
        <header className="browse-rail-header">
          <h1>Browse</h1>
          <div aria-label="Browse mode" className="browse-mode-picker" role="group">
            {(['list', 'swipe'] as const).map(mode => (
              <button
                aria-pressed={props.mode === mode}
                key={mode}
                onClick={() => props.onUpdate({ mode }, `${mode} browse mode`)}
                type="button"
              >
                {mode === 'list' ? 'List' : 'Swipe'}
              </button>
            ))}
          </div>
        </header>
        <label className="browse-search">
          <Search aria-hidden="true" size={16} strokeWidth={1.5} />
          <input aria-label="Search saved jobs" onChange={event => props.onUpdate({ query: event.target.value })} placeholder="Search saved jobs…" value={props.query} />
        </label>
        <nav aria-label="Job groups" className="browse-groups">
          <button aria-pressed={!props.statusGroup} onClick={() => props.onUpdate({ statusGroup: '' })} type="button">
            <span>All jobs</span><small>{state.jobs.length}</small>
          </button>
          {CANONICAL_STATUS_GROUPS.map(group => (
            <button aria-pressed={props.statusGroup === group} key={group} onClick={() => props.onUpdate({ statusGroup: group })} type="button">
              <span>{group}</span><small>{counts.get(group) ?? 0}</small>
            </button>
          ))}
        </nav>
        <div className="browse-filter-section">
          <label htmlFor="browse-order">Sort</label>
          <select id="browse-order" onChange={event => props.onUpdate({ sortMode: event.target.value as JobSortMode })} value={props.sortMode}>
            <option value="manual">Manual</option>
            <option value="recent">Recent</option>
            <option value="alphabetical">Alphabetical</option>
            <option value="status">Status</option>
          </select>
        </div>
      </aside>

      {state.loading ? <BrowseMessage message="Loading opportunities…" /> : null}
      {!state.loading && state.error ? <BrowseMessage error message={state.error} /> : null}
      {!state.loading && !state.error && state.results.length === 0 ? <BrowseMessage message="No matching opportunities" /> : null}

      {!state.loading && !state.error && focusedJob && props.mode === 'list' ? (
        <>
          <section aria-label={`${state.results.length} matching jobs`} className="browse-list-pane">
            <header><strong>Opportunities</strong><span>{state.results.length} jobs</span></header>
            <div className="browse-result-list" role="list">
              {state.results.map(job => (
                <div key={job.jobId} role="listitem">
                  <button
                    aria-label={`${job.company} ${job.title}`}
                    aria-current={job.jobId === focusedJob.jobId ? 'true' : undefined}
                    className="browse-result-row"
                    onClick={() => updateFocus(job.jobId, `${job.company}, ${job.title}`)}
                    type="button"
                  >
                    <CompanyMark company={job.company} />
                    <span className="browse-result-copy"><strong>{job.company}</strong><small>{job.title}</small></span>
                    <span className="browse-result-status">{statusLabel(job.status)}</span>
                  </button>
                </div>
              ))}
            </div>
          </section>
          <BrowseDetail
            detail={detail}
            detailError={state.detailError}
            detailLoading={detailLoading}
            job={focusedJob}
            onOpen={openFocusedJob}
            openError={openError}
            openPending={openingJobId !== null}
            statusControl={statusControl}
          />
        </>
      ) : null}

      {!state.loading && !state.error && focusedJob && props.mode === 'swipe' ? (
        <section
          aria-label="Opportunity swipe browser"
          className="browse-swipe-stage"
          onLostPointerCapture={() => { pointerStart.current = null }}
          onPointerCancel={event => {
            if (pointerStart.current?.pointerId === event.pointerId) pointerStart.current = null
          }}
          onPointerDown={event => {
            pointerStart.current = null
            if (isBrowseInteractionControl(event.target)) return
            pointerStart.current = { pointerId: event.pointerId, clientX: event.clientX }
            event.currentTarget.setPointerCapture?.(event.pointerId)
          }}
          onPointerUp={event => {
            const start = pointerStart.current
            pointerStart.current = null
            if (!start || start.pointerId !== event.pointerId || isBrowseInteractionControl(event.target)) return
            const delta = event.clientX - start.clientX
            if (Math.abs(delta) >= 60) move(delta < 0 ? 1 : -1)
          }}
        >
          <button aria-label="Previous job" className="swipe-arrow swipe-previous" disabled={focusIndex === 0} onClick={() => move(-1)} type="button"><ArrowLeft aria-hidden="true" /></button>
          <article className="browse-opportunity-sheet">
            <header>
              <CompanyMark company={focusedJob.company} />
              <div><h2>{focusedJob.company}</h2><p>{focusedJob.title}</p></div>
            </header>
            <p className="browse-meta">{detail?.location ? `${detail.location} · ` : ''}{statusLabel(focusedJob.status)}</p>
            <div className="browse-sheet-description">
              {detailLoading ? <p>Loading job detail…</p> : state.detailError ? <p className="browse-error" role="alert">{state.detailError}</p> : <p>{detail?.description || 'No description available.'}</p>}
            </div>
            <footer>
              <button className="browse-open-job" disabled={openingJobId !== null} onClick={() => { void openFocusedJob() }} type="button"><ExternalLink aria-hidden="true" size={16} />{openingJobId !== null ? 'Opening…' : 'Open job'}</button>
              {statusControl}
              <span>{focusIndex + 1} of {state.results.length}</span>
            </footer>
            {openError ? <p className="browse-open-error" role="alert">{openError}</p> : null}
          </article>
          <button aria-label="Next job" className="swipe-arrow swipe-next" disabled={focusIndex === state.results.length - 1} onClick={() => move(1)} type="button"><ArrowRight aria-hidden="true" /></button>
          <div aria-hidden="true" className="browse-preview-stack">
            {state.results.slice(focusIndex + 1, focusIndex + 3).map((job, index) => (
              <div className="browse-preview-card" key={job.jobId} style={{ '--preview-index': index } as CSSProperties}>
                <CompanyMark company={job.company} /><strong>{job.company}</strong><small>{job.title}</small>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      <p aria-live="polite" className="sr-only">{openError ?? (focusedJob ? `${props.mode} view, ${focusIndex + 1} of ${state.results.length}, ${focusedJob.company}, ${focusedJob.title}` : `${state.results.length} matching jobs`)}</p>
    </main>
  )
}

function BrowseMessage({ message, error = false }: { message: string, error?: boolean }) {
  return <div className={`browse-message${error ? ' browse-error' : ''}`} role={error ? 'alert' : undefined}><BriefcaseBusiness aria-hidden="true" /><p>{message}</p></div>
}

function BrowseDetail(props: {
  detail: ReturnType<typeof useBrowseJobs>['detail']
  detailError: string | null
  detailLoading: boolean
  job: ReturnType<typeof useBrowseJobs>['results'][number]
  onOpen: () => Promise<void>
  openError: string | null
  openPending: boolean
  statusControl: ReactNode
}) {
  return (
    <article className="browse-reading-pane">
      <header><CompanyMark company={props.job.company} /><div><h2>{props.job.company}</h2><p>{props.job.title}</p></div></header>
      <p className="browse-meta">{props.detail?.location ? `${props.detail.location} · ` : ''}{statusLabel(props.job.status)}</p>
      <div className="browse-reading-description">
        <h3>About the role</h3>
        {props.detailLoading ? <p>Loading job detail…</p> : props.detailError ? <p className="browse-error" role="alert">{props.detailError}</p> : <p>{props.detail?.description || 'No description available.'}</p>}
      </div>
      <footer><button className="browse-open-job" disabled={props.openPending} onClick={() => { void props.onOpen() }} type="button"><ExternalLink aria-hidden="true" size={16} />{props.openPending ? 'Opening…' : 'Open job'}</button>{props.statusControl}</footer>
      {props.openError ? <p className="browse-open-error" role="alert">{props.openError}</p> : null}
    </article>
  )
}
