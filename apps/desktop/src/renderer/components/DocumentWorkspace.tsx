import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  CheckCircle2,
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

import type {
  ArtifactMediaType,
  DocumentArtifact,
  DocumentKey,
  JobArtifactsState,
  JobListItem,
  PdfArtifactPayload
} from '../../shared/contracts'
import { PdfPreview } from './PdfPreview'

interface DocumentWorkspaceProps {
  job: JobListItem | null
  restoredArtifactId: string | null
  restoredPage: number
  restoredZoom: number
  hydrated: boolean
  onViewChange: (artifactId: string | null, page: number, zoom: number) => void
}

const PDF: ArtifactMediaType = 'application/pdf'
const DOCX: ArtifactMediaType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const documentOrder: DocumentKey[] = ['resume', 'cover_letter']

interface LogicalRevision {
  documentKey: DocumentKey
  documentLabel: string
  sourceRevision: string
  renderSequence: number
  artifacts: DocumentArtifact[]
  representative: DocumentArtifact
}

interface LogicalDocument {
  documentKey: DocumentKey
  documentLabel: string
  revisions: LogicalRevision[]
}

const emptyState = (jobId = ''): JobArtifactsState => ({
  jobId,
  artifacts: [],
  currentArtifactId: null,
  lastSuccessfulArtifactId: null,
  approvedArtifactId: null
})

function latestByFormat(artifacts: DocumentArtifact[], mediaType: ArtifactMediaType) {
  return artifacts
    .filter(artifact => artifact.mediaType === mediaType)
    .sort((left, right) => right.renderSequence - left.renderSequence)[0]
}

function revisionRepresentative(artifacts: DocumentArtifact[]) {
  const succeeded = artifacts.filter(artifact => artifact.renderStatus === 'succeeded')
  return latestByFormat(succeeded, PDF)
    ?? latestByFormat(succeeded, DOCX)
    ?? latestByFormat(artifacts, PDF)
    ?? latestByFormat(artifacts, DOCX)
}

function buildDocuments(artifacts: DocumentArtifact[]): LogicalDocument[] {
  return documentOrder.flatMap(documentKey => {
    const matching = artifacts.filter(artifact => artifact.documentKey === documentKey)
    if (!matching.length) return []
    const grouped = new Map<string, DocumentArtifact[]>()
    for (const artifact of matching) {
      const revision = grouped.get(artifact.sourceRevision) ?? []
      revision.push(artifact)
      grouped.set(artifact.sourceRevision, revision)
    }
    const revisions = Array.from(grouped.entries()).map(([sourceRevision, variants]) => ({
      documentKey,
      documentLabel: variants[0]?.documentLabel ?? (documentKey === 'resume' ? 'Resume' : 'Cover Letter'),
      sourceRevision,
      renderSequence: Math.max(...variants.map(artifact => artifact.renderSequence)),
      artifacts: variants,
      representative: revisionRepresentative(variants)!
    })).sort((left, right) => right.renderSequence - left.renderSequence)
    return [{
      documentKey,
      documentLabel: revisions[0]?.documentLabel ?? (documentKey === 'resume' ? 'Resume' : 'Cover Letter'),
      revisions
    }]
  })
}

function findRevision(documents: LogicalDocument[], artifactId: string | null) {
  if (!artifactId) return null
  for (const document of documents) {
    const revision = document.revisions.find(item => item.artifacts.some(artifact => artifact.artifactId === artifactId))
    if (revision) return { document, revision }
  }
  return null
}

function chooseLogicalPreview(next: JobArtifactsState, preferredId: string | null) {
  const documents = buildDocuments(next.artifacts)
  const preferred = findRevision(documents, preferredId)
  if (preferred?.revision.representative.renderStatus === 'succeeded') return preferred
  for (const document of documents) {
    const revision = document.revisions.find(item => item.representative.renderStatus === 'succeeded')
    if (revision) return { document, revision }
  }
  const document = documents[0]
  const revision = document?.revisions[0]
  return document && revision ? { document, revision } : null
}

export function DocumentWorkspace(props: DocumentWorkspaceProps) {
  const bridge = useRef(window.jobos?.documents).current
  const [state, setState] = useState<JobArtifactsState>(emptyState(props.job?.jobId))
  const [activeId, setActiveId] = useState<string | null>(props.restoredArtifactId)
  const [page, setPage] = useState(Math.max(1, props.restoredPage))
  const [zoom, setZoom] = useState(Math.max(0.5, Math.min(3, props.restoredZoom)))
  const [pageCount, setPageCount] = useState(0)
  const [payload, setPayload] = useState<PdfArtifactPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const [exportBusy, setExportBusy] = useState(false)
  const exportButton = useRef<HTMLButtonElement>(null)
  const exportMenu = useRef<HTMLDivElement>(null)
  const incomingViewKey = `${props.restoredArtifactId ?? ''}:${props.restoredPage}:${props.restoredZoom}`
  const persistedView = useRef(incomingViewKey)
  const restoredPropsView = useRef(incomingViewKey)
  const restoringIncomingView = props.hydrated && incomingViewKey !== restoredPropsView.current
  const selectedId = useRef<string | null>(props.restoredArtifactId)
  const restoredArtifactId = useRef(props.restoredArtifactId)
  restoredArtifactId.current = props.restoredArtifactId
  const jobId = props.job?.jobId ?? null
  const activeJobId = useRef(jobId)
  activeJobId.current = jobId
  const lastNonNullJobId = useRef<string | null>(null)

  useEffect(() => {
    if (!props.hydrated) return
    restoredPropsView.current = incomingViewKey
    setActiveId(props.restoredArtifactId)
    selectedId.current = props.restoredArtifactId
    setPage(Math.max(1, props.restoredPage))
    setZoom(Math.max(0.5, Math.min(3, props.restoredZoom)))
    persistedView.current = incomingViewKey
  }, [incomingViewKey, props.hydrated, props.restoredArtifactId, props.restoredPage, props.restoredZoom])

  const stateMatchesJob = state.jobId === props.job?.jobId
  const documents = useMemo(
    () => buildDocuments(stateMatchesJob ? state.artifacts : []),
    [state.artifacts, stateMatchesJob]
  )
  const activeSelection = useMemo(() => findRevision(documents, activeId), [activeId, documents])
  const activeDocument = activeSelection?.document ?? null
  const activeRevision = activeSelection?.revision ?? null
  const activeArtifact = activeRevision?.representative ?? null
  const activeDocumentIndex = activeDocument
    ? documents.findIndex(document => document.documentKey === activeDocument.documentKey)
    : -1
  const currentRevision = activeDocument?.revisions[0] ?? null
  const currentArtifact = currentRevision?.artifacts
    .slice()
    .sort((left, right) => right.renderSequence - left.renderSequence)[0] ?? null
  const lastSuccessful = activeDocument?.revisions.find(revision => revision.representative.renderStatus === 'succeeded')?.representative ?? null
  const exportArtifacts = activeRevision?.artifacts.filter(artifact => artifact.renderStatus === 'succeeded') ?? []
  const exportPdf = latestByFormat(exportArtifacts, PDF)
  const exportDocx = latestByFormat(exportArtifacts, DOCX)

  const choosePreview = useCallback((next: JobArtifactsState, preferredId: string | null) => {
    const chosen = chooseLogicalPreview(next, preferredId)
    const nextId = chosen?.revision.representative.artifactId ?? null
    const changed = selectedId.current !== nextId
    const restoredLogicalRevision = findRevision(buildDocuments(next.artifacts), restoredArtifactId.current)
    const preservesRestoredRevision = Boolean(
      restoredLogicalRevision
      && chosen
      && restoredLogicalRevision.document.documentKey === chosen.document.documentKey
      && restoredLogicalRevision.revision.sourceRevision === chosen.revision.sourceRevision
    )
    selectedId.current = nextId
    setActiveId(nextId)
    if (changed) {
      setPayload(null)
      setExportOpen(false)
    }
    if (changed && !preservesRestoredRevision) {
      setPage(1)
      setZoom(1)
      setPageCount(0)
    }
    return nextId
  }, [])

  useEffect(() => {
    const jobChanged = Boolean(jobId && lastNonNullJobId.current && lastNonNullJobId.current !== jobId)
    if (jobId) lastNonNullJobId.current = jobId
    if (!jobId || !bridge) {
      setState(emptyState(jobId ?? undefined))
      selectedId.current = restoredArtifactId.current
      setActiveId(restoredArtifactId.current)
      setPayload(null)
      return
    }
    let active = true
    const pendingId = jobChanged ? null : restoredArtifactId.current
    setState(emptyState(jobId))
    selectedId.current = pendingId
    setActiveId(pendingId)
    setPayload(null)
    setExportOpen(false)
    setLoading(true)
    setMessage('Loading registered artifacts…')
    bridge.list(jobId).then(listed => {
      if (!active) return
      setState(listed)
      const listedId = choosePreview(listed, pendingId)
      return bridge.refresh(jobId).then(refreshed => ({ refreshed, listedId }))
    }).then(result => {
      if (!active || !result) return
      const { refreshed, listedId } = result
      setState(refreshed)
      choosePreview(refreshed, listedId)
      setMessage(refreshed.artifacts.length ? 'Artifact registry is current' : 'No artifacts registered for this job')
    }).catch(error => {
      if (active) setMessage(error instanceof Error ? error.message : 'Artifact refresh failed')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [bridge, choosePreview, jobId])

  useEffect(() => {
    setPayload(null)
    if (!activeArtifact?.previewAvailable || !bridge) return
    let active = true
    setLoading(true)
    bridge.loadPdf(activeArtifact.artifactId).then(value => {
      if (!active) return
      if (value.artifactId !== activeArtifact.artifactId) {
        setMessage('PDF preview identity mismatch')
        return
      }
      setPayload(value)
    }).catch(error => {
      if (active) setMessage(error instanceof Error ? error.message : 'PDF preview failed')
    }).finally(() => {
      if (active) setLoading(false)
    })
    return () => { active = false }
  }, [activeArtifact?.artifactId, activeArtifact?.previewAvailable, bridge])

  useEffect(() => {
    const key = `${activeId ?? ''}:${page}:${zoom}`
    if (!props.hydrated || restoringIncomingView || key === persistedView.current) return
    persistedView.current = key
    props.onViewChange(activeId, page, zoom)
  }, [activeId, page, props.hydrated, props.onViewChange, restoringIncomingView, zoom])

  useEffect(() => {
    if (pageCount > 0 && page > pageCount) setPage(pageCount)
  }, [page, pageCount])

  useEffect(() => {
    if (!exportOpen) return
    const closeOnPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (!exportMenu.current?.contains(target) && !exportButton.current?.contains(target)) {
        setExportOpen(false)
      }
    }
    document.addEventListener('pointerdown', closeOnPointerDown)
    return () => document.removeEventListener('pointerdown', closeOnPointerDown)
  }, [exportOpen])

  const refresh = async () => {
    if (!props.job || !bridge) return
    const requestJobId = props.job.jobId
    setLoading(true)
    try {
      const refreshed = await bridge.refresh(requestJobId)
      if (activeJobId.current !== requestJobId) return
      setState(refreshed)
      choosePreview(refreshed, selectedId.current)
      setMessage('Checked for newer artifacts')
    } catch (error) {
      if (activeJobId.current === requestJobId) setMessage(error instanceof Error ? error.message : 'Artifact refresh failed')
    } finally {
      if (activeJobId.current === requestJobId) setLoading(false)
    }
  }

  const nativeAction = async (name: 'reveal' | 'open') => {
    const presentedArtifact = activeArtifact
    if (!presentedArtifact || !bridge) return
    try {
      setMessage(await bridge[name](presentedArtifact.artifactId))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Document action failed')
    }
  }

  const exportArtifact = async (artifact: DocumentArtifact) => {
    const requestJobId = props.job?.jobId
    if (!bridge || !requestJobId || exportBusy) return
    setExportOpen(false)
    setExportBusy(true)
    try {
      const result = await bridge.export(artifact.artifactId)
      if (activeJobId.current === requestJobId) setMessage(result)
    } catch (error) {
      if (activeJobId.current === requestJobId) {
        setMessage(error instanceof Error ? error.message : 'Document export failed')
      }
    } finally {
      setExportBusy(false)
    }
  }

  const selectRevision = (revision: LogicalRevision) => {
    const nextId = revision.representative.artifactId
    if (nextId === activeId) return
    selectedId.current = nextId
    setActiveId(nextId)
    setPayload(null)
    setExportOpen(false)
    setPage(1)
    setZoom(1)
    setPageCount(0)
  }

  const selectDocument = (index: number) => {
    const document = documents[index]
    if (!document) return
    const revision = document.revisions.find(item => item.representative.renderStatus === 'succeeded') ?? document.revisions[0]
    if (revision) selectRevision(revision)
  }

  const approve = async () => {
    const presentedArtifact = activeArtifact
    if (!props.job || !presentedArtifact || presentedArtifact.documentKey !== 'resume' || !bridge || presentedArtifact.renderStatus !== 'succeeded') return
    const requestJobId = props.job.jobId
    setLoading(true)
    try {
      const approved = await bridge.approve(requestJobId, presentedArtifact.artifactId)
      if (activeJobId.current !== requestJobId) return
      setState(approved)
      setMessage(`Approved revision ${presentedArtifact.artifactRevision}`)
    } catch (error) {
      if (activeJobId.current !== requestJobId) return
      setMessage(error instanceof Error ? error.message : 'Document approval failed')
    } finally {
      if (activeJobId.current === requestJobId) setLoading(false)
    }
  }

  if (!props.job) {
    return (
      <main className="document-workspace panel-region">
        <section className="workspace-empty">
          <span className="empty-orbit"><FileText aria-hidden="true" size={23} /></span>
          <h1>Select a job to review its documents</h1>
          <p>Registered document artifacts appear here without filesystem browsing.</p>
        </section>
      </main>
    )
  }

  return (
    <main className="document-workspace panel-region">
      <div className="document-heading">
        <div>
          <span className="document-job">Documents for {props.job.company} · {props.job.title}</span>
          <strong>{activeArtifact?.filename ?? 'Document artifacts'}</strong>
        </div>
        <nav aria-label="Job documents" className="document-navigation">
          <button aria-label="Previous document" disabled={activeDocumentIndex <= 0} onClick={() => selectDocument(activeDocumentIndex - 1)} type="button"><ChevronLeft aria-hidden="true" size={14} /></button>
          <span className="document-position">
            <strong>{activeDocument?.documentLabel ?? 'Document'}</strong>
            <span>{activeDocumentIndex >= 0 ? `${activeDocumentIndex + 1} of ${documents.length}` : `0 of ${documents.length}`}</span>
          </span>
          <button aria-label="Next document" disabled={activeDocumentIndex < 0 || activeDocumentIndex >= documents.length - 1} onClick={() => selectDocument(activeDocumentIndex + 1)} type="button"><ChevronRight aria-hidden="true" size={14} /></button>
        </nav>
        <select
          aria-label={`${activeDocument?.documentLabel ?? 'Document'} revision`}
          onChange={event => {
            const revision = activeDocument?.revisions.find(item => item.representative.artifactId === event.target.value)
            if (revision) selectRevision(revision)
          }}
          value={activeArtifact?.artifactId ?? ''}
        >
          <option value="">No artifact</option>
          {activeDocument?.revisions.map(revision => {
            const artifact = revision.representative
            return (
              <option key={`${revision.documentKey}:${revision.sourceRevision}`} value={artifact.artifactId}>
                {artifact.artifactRevision} · {artifact.renderStatus}
                {artifact.isApproved ? ' · approved' : revision === currentRevision ? ' · newest' : ''}
              </option>
            )
          })}
        </select>
      </div>

      <div className="document-toolbar" role="toolbar" aria-label="Document controls">
        <button disabled={!bridge || loading} onClick={refresh} type="button"><RefreshCw aria-hidden="true" size={14} /> Refresh</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => nativeAction('open')} type="button"><ExternalLink aria-hidden="true" size={14} /> Open</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => nativeAction('reveal')} type="button"><FolderOpen aria-hidden="true" size={14} /> Reveal</button>
        <div className="document-export">
          <button ref={exportButton} aria-expanded={exportOpen} aria-haspopup="menu" disabled={!bridge || exportBusy || (!exportPdf && !exportDocx)} onClick={() => setExportOpen(value => !value)} type="button"><Download aria-hidden="true" size={14} /> Export</button>
          {exportOpen ? (
            <div ref={exportMenu} aria-label="Export document" className="document-export-menu" onKeyDown={event => { if (event.key === 'Escape') { setExportOpen(false); exportButton.current?.focus() } }} role="menu">
              {exportPdf ? <button disabled={exportBusy} onClick={() => exportArtifact(exportPdf)} role="menuitem" type="button">Export PDF</button> : null}
              {exportDocx ? <button disabled={exportBusy} onClick={() => exportArtifact(exportDocx)} role="menuitem" type="button">Export DOCX</button> : null}
            </div>
          ) : null}
        </div>
        {activeArtifact?.documentKey === 'resume' && activeArtifact.mediaType === PDF ? (
          <button
            className="approve-revision"
            disabled={activeArtifact.renderStatus !== 'succeeded' || activeArtifact.isApproved || !bridge || loading}
            onClick={approve}
            type="button"
          >
            <CheckCircle2 aria-hidden="true" size={14} />
            {activeArtifact.isApproved ? 'Approved revision' : 'Approve this revision'}
          </button>
        ) : null}
        <span className="document-toolbar-spacer" />
        <button aria-label="Previous page" disabled={page <= 1 || !payload} onClick={() => setPage(value => Math.max(1, value - 1))} type="button"><ChevronLeft aria-hidden="true" size={14} /></button>
        <span className="page-count">Page {page} of {pageCount || '—'}</span>
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
        <div className="render-progress" role="status">A newer revision is rendering. The last successful document remains available.</div>
      ) : currentArtifact ? (
        <div className="render-current" role="status">Newest successful revision · {currentArtifact.artifactRevision} · source {currentArtifact.sourceRevision}</div>
      ) : (
        <div className="render-progress" role="status">No registered render is available for this document.</div>
      )}

      {activeArtifact ? (
        <div className="viewed-artifact" role="status">
          Viewing {activeArtifact.filename ?? 'unnamed artifact'} · revision {activeArtifact.artifactRevision} · source {activeArtifact.sourceRevision} · {activeArtifact.mediaType} · {activeArtifact.renderStatus}
        </div>
      ) : null}

      <section className="document-canvas">
        {payload && activeArtifact && payload.artifactId === activeArtifact.artifactId && activeArtifact.previewAvailable ? (
          <PdfPreview key={payload.artifactId} bytes={payload.bytes} onPageCount={setPageCount} page={page} zoom={zoom} />
        ) : activeArtifact?.mediaType === DOCX ? (
          <div className="document-external-only">
            <FileText aria-hidden="true" size={28} />
            <h1>DOCX stays external</h1>
            <p>This authoritative file is available to export or open in its default app. JobOS does not substitute a lower-fidelity preview.</p>
          </div>
        ) : (
          <div className="document-external-only">
            <FileText aria-hidden="true" size={28} />
            <h1>No trusted preview yet</h1>
            <p>Refresh after the agent produces and registers a PDF document for this job.</p>
          </div>
        )}
      </section>
      <p aria-live="polite" className="document-announcement">{loading ? 'Loading document…' : message}</p>
    </main>
  )
}
