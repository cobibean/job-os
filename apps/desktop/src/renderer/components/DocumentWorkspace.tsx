import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  FolderOpen,
  Minus,
  Plus,
  RefreshCw
} from 'lucide-react'

import type { JobArtifactsState, JobListItem, PdfArtifactPayload } from '../../shared/contracts'
import { PdfPreview } from './PdfPreview'

interface DocumentWorkspaceProps {
  job: JobListItem | null
  restoredArtifactId: string | null
  restoredPage: number
  restoredZoom: number
  hydrated: boolean
  onViewChange: (artifactId: string | null, page: number, zoom: number) => void
}

const emptyState = (jobId = ''): JobArtifactsState => ({
  jobId,
  artifacts: [],
  currentArtifactId: null,
  lastSuccessfulArtifactId: null
})

export function DocumentWorkspace(props: DocumentWorkspaceProps) {
  const bridge = window.jobos?.documents
  const [state, setState] = useState<JobArtifactsState>(emptyState(props.job?.jobId))
  const [activeId, setActiveId] = useState<string | null>(props.restoredArtifactId)
  const [page, setPage] = useState(Math.max(1, props.restoredPage))
  const [zoom, setZoom] = useState(Math.max(0.5, Math.min(3, props.restoredZoom)))
  const [pageCount, setPageCount] = useState(1)
  const [payload, setPayload] = useState<PdfArtifactPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const persistedView = useRef(
    `${props.restoredArtifactId ?? ''}:${props.restoredPage}:${props.restoredZoom}`
  )

  useEffect(() => {
    if (!props.hydrated) return
    setActiveId(props.restoredArtifactId)
    setPage(Math.max(1, props.restoredPage))
    setZoom(Math.max(0.5, Math.min(3, props.restoredZoom)))
    persistedView.current = `${props.restoredArtifactId ?? ''}:${props.restoredPage}:${props.restoredZoom}`
  }, [props.hydrated, props.restoredArtifactId, props.restoredPage, props.restoredZoom])

  const artifactById = useMemo(
    () => new Map(state.artifacts.map(artifact => [artifact.artifactId, artifact])),
    [state.artifacts]
  )
  const activeArtifact = activeId ? artifactById.get(activeId) ?? null : null
  const currentArtifact = state.currentArtifactId
    ? artifactById.get(state.currentArtifactId) ?? null
    : null
  const lastSuccessful = state.lastSuccessfulArtifactId
    ? artifactById.get(state.lastSuccessfulArtifactId) ?? null
    : null

  const choosePreview = useCallback((next: JobArtifactsState, preferRestored: boolean) => {
    const byId = new Map(next.artifacts.map(artifact => [artifact.artifactId, artifact]))
    const current = next.currentArtifactId ? byId.get(next.currentArtifactId) : undefined
    const lastGood = next.lastSuccessfulArtifactId
      ? byId.get(next.lastSuccessfulArtifactId)
      : undefined
    const restored = props.restoredArtifactId ? byId.get(props.restoredArtifactId) : undefined
    const chosen = preferRestored && restored
      ? restored
      : current?.previewAvailable
        ? current
        : lastGood?.previewAvailable
          ? lastGood
          : current?.renderStatus === 'succeeded'
            ? current
            : next.artifacts.find(artifact => artifact.previewAvailable)
    setActiveId(chosen?.artifactId ?? null)
  }, [props.restoredArtifactId])

  useEffect(() => {
    if (!props.job || !bridge) {
      setState(emptyState(props.job?.jobId))
      setActiveId(null)
      setPayload(null)
      return
    }
    let active = true
    setLoading(true)
    setMessage('Loading registered artifacts…')
    bridge.list(props.job.jobId).then(listed => {
      if (!active) return
      setState(listed)
      choosePreview(listed, true)
      return bridge.refresh(props.job!.jobId)
    }).then(refreshed => {
      if (!active || !refreshed) return
      setState(refreshed)
      choosePreview(refreshed, false)
      setMessage(refreshed.artifacts.length ? 'Artifact registry is current' : 'No artifacts registered for this job')
    }).catch(error => {
      if (active) setMessage(error instanceof Error ? error.message : 'Artifact refresh failed')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [bridge, choosePreview, props.job])

  useEffect(() => {
    if (!activeArtifact?.previewAvailable || !bridge) {
      setPayload(null)
      return
    }
    let active = true
    setLoading(true)
    bridge.loadPdf(activeArtifact.artifactId).then(value => {
      if (active) setPayload(value)
    }).catch(error => {
      if (active) setMessage(error instanceof Error ? error.message : 'PDF preview failed')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [activeArtifact?.artifactId, activeArtifact?.previewAvailable, bridge])

  useEffect(() => {
    const key = `${activeId ?? ''}:${page}:${zoom}`
    if (!props.hydrated || key === persistedView.current) return
    persistedView.current = key
    props.onViewChange(activeId, page, zoom)
  }, [activeId, page, props.hydrated, props.onViewChange, zoom])

  useEffect(() => {
    if (page > pageCount) setPage(pageCount)
  }, [page, pageCount])

  const refresh = async () => {
    if (!props.job || !bridge) return
    setLoading(true)
    try {
      const refreshed = await bridge.refresh(props.job.jobId)
      setState(refreshed)
      choosePreview(refreshed, false)
      setMessage('Checked for newer artifacts')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Artifact refresh failed')
    } finally {
      setLoading(false)
    }
  }

  const action = async (name: 'export' | 'reveal' | 'open') => {
    if (!activeArtifact || !bridge) return
    try {
      setMessage(await bridge[name](activeArtifact.artifactId))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Document action failed')
    }
  }

  if (!props.job) {
    return (
      <main className="document-workspace panel-region">
        <section className="workspace-empty">
          <span className="empty-orbit"><FileText aria-hidden="true" size={23} /></span>
          <h1>Select a job to review its resume</h1>
          <p>Registered artifacts appear here without filesystem browsing.</p>
        </section>
      </main>
    )
  }

  return (
    <main className="document-workspace panel-region">
      <div className="document-heading">
        <div>
          <span className="document-job">Resume for {props.job.company} · {props.job.title}</span>
          <strong>{activeArtifact?.filename ?? 'Resume artifacts'}</strong>
        </div>
        <select
          aria-label="Resume revision"
          onChange={event => { setActiveId(event.target.value || null); setPage(1) }}
          value={activeId ?? ''}
        >
          <option value="">No artifact</option>
          {state.artifacts.map(artifact => (
            <option key={artifact.artifactId} value={artifact.artifactId}>
              {artifact.artifactRevision} · {artifact.renderStatus}{artifact.isCurrent ? ' · newest' : ''}
            </option>
          ))}
        </select>
      </div>

      <div className="document-toolbar" role="toolbar" aria-label="Document controls">
        <button disabled={!bridge || loading} onClick={refresh} type="button"><RefreshCw aria-hidden="true" size={14} /> Refresh</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => action('open')} type="button"><ExternalLink aria-hidden="true" size={14} /> Open</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => action('reveal')} type="button"><FolderOpen aria-hidden="true" size={14} /> Reveal</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => action('export')} type="button"><Download aria-hidden="true" size={14} /> Export</button>
        <span className="document-toolbar-spacer" />
        <button aria-label="Previous page" disabled={page <= 1 || !payload} onClick={() => setPage(value => Math.max(1, value - 1))} type="button"><ChevronLeft aria-hidden="true" size={14} /></button>
        <span className="page-count">Page {page} of {pageCount}</span>
        <button aria-label="Next page" disabled={page >= pageCount || !payload} onClick={() => setPage(value => Math.min(pageCount, value + 1))} type="button"><ChevronRight aria-hidden="true" size={14} /></button>
        <button aria-label="Zoom out" disabled={zoom <= 0.5} onClick={() => setZoom(value => Math.max(0.5, Number((value - 0.1).toFixed(1))))} type="button"><Minus aria-hidden="true" size={14} /></button>
        <span className="zoom-value">{Math.round(zoom * 100)}%</span>
        <button aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom(value => Math.min(3, Number((value + 0.1).toFixed(1))))} type="button"><Plus aria-hidden="true" size={14} /></button>
      </div>

      {currentArtifact?.renderStatus === 'failed' ? (
        <div className="render-failure" role="alert">
          <strong>Newest render failed.</strong> {currentArtifact.failureMessage ?? 'The latest artifact is unavailable.'}
          {lastSuccessful ? ` Showing last successful revision ${lastSuccessful.artifactRevision}.` : ''}
        </div>
      ) : currentArtifact?.renderStatus === 'rendering' ? (
        <div className="render-progress" role="status">A newer revision is rendering. The last successful artifact remains available.</div>
      ) : currentArtifact ? (
        <div className="render-current" role="status">Newest successful revision · {currentArtifact.artifactRevision} · source {currentArtifact.sourceRevision}</div>
      ) : (
        <div className="render-progress" role="status">No registered render is available for this job.</div>
      )}

      <section className="document-canvas">
        {payload && activeArtifact?.previewAvailable ? (
          <PdfPreview bytes={payload.bytes} onPageCount={setPageCount} page={page} zoom={zoom} />
        ) : activeArtifact?.mediaType === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ? (
          <div className="document-external-only">
            <FileText aria-hidden="true" size={28} />
            <h1>DOCX stays external</h1>
            <p>This authoritative file is available to export or open in its default app. JobOS does not substitute a lower-fidelity preview.</p>
          </div>
        ) : (
          <div className="document-external-only">
            <FileText aria-hidden="true" size={28} />
            <h1>No trusted preview yet</h1>
            <p>Refresh after the agent produces and registers a PDF resume for this job.</p>
          </div>
        )}
      </section>
      <p aria-live="polite" className="document-announcement">{loading ? 'Loading document…' : message}</p>
    </main>
  )
}
