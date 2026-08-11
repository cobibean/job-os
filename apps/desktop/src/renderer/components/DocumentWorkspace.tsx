import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
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
import type { DocxBinding, DocxOpenResult } from '../../shared/docxDocuments'
import { DocxBytesPreview } from '../document-editor/DocxBytesPreview'
import { displayDocxFilename } from '../document-editor/docxDisplay'
import { OriginalDocxPreview } from '../document-editor/OriginalDocxPreview'
import { PdfPreview } from './PdfPreview'

export type DocumentPreviewMode = 'pdf' | 'docx'

interface DocumentWorkspaceProps {
  job: JobListItem | null
  restoredArtifactId: string | null
  restoredPage: number
  restoredZoom: number
  hydrated: boolean
  refreshGeneration?: number
  previewMode?: DocumentPreviewMode
  onViewChange: (artifactId: string | null, page: number, zoom: number) => void
  onOpenEditor?: (document: DocxOpenResult) => void
  onPreviewModeChange?: (mode: DocumentPreviewMode) => void
}

const PDF: ArtifactMediaType = 'application/pdf'
const DOCX: ArtifactMediaType = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
const documentOrder: DocumentKey[] = ['resume', 'cover_letter', 'references']
const documentLabels: Record<DocumentKey, string> = {
  resume: 'Resume',
  cover_letter: 'Cover Letter',
  references: 'References'
}

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
      documentLabel: variants[0]?.documentLabel ?? documentLabels[documentKey],
      sourceRevision,
      renderSequence: Math.max(...variants.map(artifact => artifact.renderSequence)),
      artifacts: variants,
      representative: revisionRepresentative(variants)!
    })).sort((left, right) => right.renderSequence - left.renderSequence)
    return [{
      documentKey,
      documentLabel: revisions[0]?.documentLabel ?? documentLabels[documentKey],
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
  const docxBridge = useRef(window.jobos?.docxDocuments).current
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
  const [exportMenuPosition, setExportMenuPosition] = useState({ left: 0, top: 0 })
  const [bindings, setBindings] = useState<DocxBinding[]>([])
  const [docxPreview, setDocxPreview] = useState<DocxOpenResult | null>(null)
  const bindingListEpoch = useRef(0)
  const docxMutationEpoch = useRef(new Map<string, number>())
  const [editorBusyKey, setEditorBusyKey] = useState<DocumentKey | null>(null)
  const [importKey, setImportKey] = useState<DocumentKey>('resume')
  const [blankChoiceKey, setBlankChoiceKey] = useState<DocumentKey | null>(null)
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
  const refreshGeneration = props.refreshGeneration ?? 0
  const observedRefreshGeneration = useRef(refreshGeneration)

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
  const requestedPreviewMode = props.previewMode ?? 'pdf'
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
  const activeBinding = activeDocument
    ? bindings.find(binding => binding.documentKey === activeDocument.documentKey) ?? null
    : null
  const previewMode: DocumentPreviewMode = requestedPreviewMode === 'pdf' && !exportPdf && (activeBinding || exportDocx)
    ? 'docx'
    : requestedPreviewMode
  const currentDocxPreview = docxPreview
    && docxPreview.binding.jobId === jobId
    && docxPreview.binding.documentKey === activeDocument?.documentKey
    ? docxPreview
    : null
  const previewBinding = currentDocxPreview?.binding ?? activeBinding
  const previewFilename = previewBinding ? displayDocxFilename(previewBinding) : null

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
    if (!jobId || !docxBridge) {
      setBindings([])
      return
    }
    let active = true
    const refreshBindings = async () => {
      const epoch = bindingListEpoch.current
      try {
        const value = await docxBridge.listBindings(jobId)
        if (active && bindingListEpoch.current === epoch) setBindings(value)
      } catch (error) {
        if (active) setMessage(error instanceof Error ? error.message : 'DOCX bindings unavailable')
      }
    }
    void refreshBindings()
    const unsubscribe = docxBridge.subscribe(event => {
      if (!active || event.jobId !== jobId) return
      bindingListEpoch.current += 1
      const epoch = (docxMutationEpoch.current.get(event.bindingId) ?? 0) + 1
      docxMutationEpoch.current.set(event.bindingId, epoch)
      if (event.kind === 'missing') {
        setBindings(current => current.filter(binding => binding.bindingId !== event.bindingId))
        setDocxPreview(current => current?.binding.bindingId === event.bindingId ? null : current)
        setMessage('The current editable DOCX is no longer available on this device')
        return
      }
      void docxBridge.openBound(jobId, event.documentKey).then(opened => {
        if (!active || docxMutationEpoch.current.get(event.bindingId) !== epoch || !opened || opened.binding.sha256 !== event.sha256) return
        setBindings(current => [
          ...current.filter(binding => binding.bindingId !== opened.binding.bindingId),
          opened.binding
        ])
        if (previewMode === 'docx' && activeDocument?.documentKey === event.documentKey) {
          setDocxPreview(opened)
          setMessage('Current editable DOCX refreshed')
        }
      }).catch(error => {
        if (active && docxMutationEpoch.current.get(event.bindingId) === epoch) {
          setMessage(error instanceof Error ? error.message : 'Current DOCX refresh failed')
        }
      })
    })
    return () => {
      active = false
      unsubscribe()
    }
  }, [activeDocument?.documentKey, docxBridge, jobId, previewMode, refreshGeneration])

  useEffect(() => {
    setDocxPreview(null)
    if (previewMode !== 'docx' || !jobId || !docxBridge || !activeBinding) return
    let active = true
    const epoch = docxMutationEpoch.current.get(activeBinding.bindingId) ?? 0
    docxBridge.openBound(jobId, activeBinding.documentKey).then(opened => {
      if (!active || (docxMutationEpoch.current.get(activeBinding.bindingId) ?? 0) !== epoch) return
      if (opened) setDocxPreview(opened)
      else setMessage('The current editable DOCX is no longer bound on this device')
    }).catch(error => {
      if (active && (docxMutationEpoch.current.get(activeBinding.bindingId) ?? 0) === epoch) {
        setMessage(error instanceof Error ? error.message : 'Current DOCX preview failed')
      }
    })
    return () => { active = false }
  }, [
    activeBinding?.bindingId,
    activeBinding?.documentKey,
    activeBinding?.revision,
    activeBinding?.sha256,
    docxBridge,
    jobId,
    previewMode,
    refreshGeneration
  ])

  useEffect(() => {
    setPayload(null)
    if (previewMode !== 'pdf' || !activeArtifact?.previewAvailable || !bridge) return
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
  }, [activeArtifact?.artifactId, activeArtifact?.previewAvailable, bridge, previewMode])

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

  const positionExportMenu = useCallback(() => {
    const bounds = exportButton.current?.getBoundingClientRect()
    if (!bounds) return
    setExportMenuPosition({
      left: Math.max(8, Math.min(bounds.left, window.innerWidth - 124)),
      top: bounds.bottom + 3
    })
  }, [])

  useEffect(() => {
    if (!exportOpen) return
    positionExportMenu()
    window.addEventListener('resize', positionExportMenu)
    document.addEventListener('scroll', positionExportMenu, true)
    return () => {
      window.removeEventListener('resize', positionExportMenu)
      document.removeEventListener('scroll', positionExportMenu, true)
    }
  }, [exportOpen, positionExportMenu])

  const refresh = useCallback(async () => {
    if (!jobId || !bridge) return
    const requestJobId = jobId
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
  }, [bridge, choosePreview, jobId])

  useEffect(() => {
    if (observedRefreshGeneration.current === refreshGeneration) return
    observedRefreshGeneration.current = refreshGeneration
    void refresh()
  }, [refresh, refreshGeneration])

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

  const openEditor = async (documentKey: DocumentKey) => {
    if (!jobId || !docxBridge || editorBusyKey) return
    const successfulDocx = stateMatchesJob
      ? state.artifacts.filter(artifact => (
          artifact.documentKey === documentKey
          && artifact.mediaType === DOCX
          && artifact.renderStatus === 'succeeded'
        ))
      : []
    const viewedDocx = latestByFormat(
      activeRevision
        ? successfulDocx.filter(artifact => artifact.sourceRevision === activeRevision.sourceRevision)
        : successfulDocx,
      DOCX
    ) ?? latestByFormat(successfulDocx, DOCX) ?? null

    setEditorBusyKey(documentKey)
    setMessage(viewedDocx ? 'Opening this packet DOCX…' : 'Opening the original DOCX…')
    try {
      const bound = bindings.some(binding => binding.documentKey === documentKey)
      const document = bound
        ? await docxBridge.openBound(jobId, documentKey)
        : viewedDocx
          ? await docxBridge.openArtifact(jobId, documentKey, viewedDocx.artifactId)
          : await docxBridge.chooseFile(jobId, documentKey)
      if (document) props.onOpenEditor?.(document)
      else setMessage('No packet DOCX is available. Choose the original DOCX to edit it in place')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not open the DOCX editor')
    } finally {
      setEditorBusyKey(null)
    }
  }

  const startBlankFromChoice = async () => {
    if (!blankChoiceKey || !jobId || !docxBridge) return
    const documentKey = blankChoiceKey
    setBlankChoiceKey(null)
    setEditorBusyKey(documentKey)
    try {
      const document = await docxBridge.createBlank(jobId, documentKey)
      if (document) props.onOpenEditor?.(document)
      else setMessage('Create blank DOCX cancelled')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not create a blank DOCX')
    } finally {
      setEditorBusyKey(null)
    }
  }

  const importDocx = async (requestedKey: DocumentKey = importKey) => {
    if (!jobId || !docxBridge || editorBusyKey) return
    setEditorBusyKey(requestedKey)
    setMessage(`Choosing canonical ${documentLabels[requestedKey]} DOCX…`)
    try {
      const document = await docxBridge.chooseFile(jobId, requestedKey)
      if (document) props.onOpenEditor?.(document)
      else setMessage('Choose DOCX cancelled')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Could not open the DOCX')
    } finally {
      setEditorBusyKey(null)
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
      <div className="document-toolbar" role="toolbar" aria-label="Document controls">
        <div className="document-heading">
          <div>
            <span className="document-job">Documents for {props.job.company} · {props.job.title}</span>
            <strong>{previewMode === 'docx' && previewFilename
              ? previewFilename
              : activeArtifact?.filename ?? 'Document artifacts'}</strong>
          </div>
          <div aria-label="Preview format" className="document-preview-switch" role="group">
          <button
            aria-pressed={previewMode === 'pdf'}
            disabled={!exportPdf}
            onClick={() => props.onPreviewModeChange?.('pdf')}
            type="button"
          >PDF</button>
          <button
            aria-pressed={previewMode === 'docx'}
            disabled={!activeBinding && !exportDocx}
            onClick={() => props.onPreviewModeChange?.('docx')}
            type="button"
          >DOCX</button>
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
        <button disabled={!bridge || loading} onClick={refresh} type="button"><RefreshCw aria-hidden="true" size={14} /> Refresh</button>
        {documentOrder.map(documentKey => {
          return (
            <button
              aria-label={editorBusyKey === documentKey
                ? `Opening ${documentLabels[documentKey]} editor`
                : `Open ${documentLabels[documentKey]} in Editor`}
              className="edit-document-button"
              disabled={!docxBridge || !props.onOpenEditor || Boolean(editorBusyKey)}
              key={documentKey}
              onClick={() => { void openEditor(documentKey) }}
              type="button"
            >
              <FileText aria-hidden="true" size={14} />
              {editorBusyKey === documentKey
                ? 'Opening…'
                : `${documentLabels[documentKey]} Editor`}
            </button>
          )
        })}
        <select
          aria-label="Document type to edit"
          disabled={!docxBridge || Boolean(editorBusyKey)}
          onChange={event => setImportKey(event.target.value as DocumentKey)}
          value={importKey}
        >
          {documentOrder.map(documentKey => <option key={documentKey} value={documentKey}>{documentLabels[documentKey]}</option>)}
        </select>
        <button disabled={!docxBridge || !props.onOpenEditor || Boolean(editorBusyKey)} onClick={() => { void importDocx() }} type="button"><FolderOpen aria-hidden="true" size={14} /> Choose DOCX</button>
                <button disabled={!docxBridge || !props.onOpenEditor || Boolean(editorBusyKey)} onClick={() => setBlankChoiceKey(importKey)} type="button"><Plus aria-hidden="true" size={14} /> New blank DOCX</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => nativeAction('open')} type="button"><ExternalLink aria-hidden="true" size={14} /> Open</button>
        <button disabled={!activeArtifact || !bridge} onClick={() => nativeAction('reveal')} type="button"><FolderOpen aria-hidden="true" size={14} /> Reveal</button>
        <div className="document-export">
          <button ref={exportButton} aria-expanded={exportOpen} aria-haspopup="menu" disabled={!bridge || exportBusy || (!exportPdf && !exportDocx)} onClick={() => { if (!exportOpen) positionExportMenu(); setExportOpen(value => !value) }} type="button"><Download aria-hidden="true" size={14} /> Export</button>
        </div>
        {exportOpen ? createPortal(
          <div
            ref={exportMenu}
            aria-label="Export document"
            className="document-export-menu"
            onKeyDown={event => { if (event.key === 'Escape') { setExportOpen(false); exportButton.current?.focus() } }}
            role="menu"
            style={exportMenuPosition}
          >
            {exportPdf ? <button disabled={exportBusy} onClick={() => exportArtifact(exportPdf)} role="menuitem" type="button">Export PDF</button> : null}
            {exportDocx ? <button disabled={exportBusy} onClick={() => exportArtifact(exportDocx)} role="menuitem" type="button">Export DOCX</button> : null}
          </div>,
          document.body
        ) : null}
        {previewMode === 'pdf' && activeArtifact?.documentKey === 'resume' && activeArtifact.mediaType === PDF ? (
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
        {previewMode === 'pdf' ? <>
          <button aria-label="Previous page" disabled={page <= 1 || !payload} onClick={() => setPage(value => Math.max(1, value - 1))} type="button"><ChevronLeft aria-hidden="true" size={14} /></button>
          <span className="page-count">Page {page} of {pageCount || '—'}</span>
          <button aria-label="Next page" disabled={page >= pageCount || !payload} onClick={() => setPage(value => Math.min(pageCount, value + 1))} type="button"><ChevronRight aria-hidden="true" size={14} /></button>
          <button aria-label="Zoom out" disabled={zoom <= 0.5} onClick={() => setZoom(value => Math.max(0.5, Number((value - 0.1).toFixed(1))))} type="button"><Minus aria-hidden="true" size={14} /></button>
          <span className="zoom-value">{Math.round(zoom * 100)}%</span>
          <button aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom(value => Math.min(3, Number((value + 0.1).toFixed(1))))} type="button"><Plus aria-hidden="true" size={14} /></button>
        </> : null}
      </div>

      <div className="document-status-line">
        {currentArtifact?.renderStatus === 'failed' ? (
          <div className="render-failure" role="alert">
            <strong>Newest render failed.</strong> {currentArtifact.failureMessage ?? 'The latest artifact is unavailable.'}
            {lastSuccessful ? ` Showing last successful revision ${lastSuccessful.artifactRevision}.` : ''}
          </div>
        ) : currentArtifact?.renderStatus === 'rendering' ? (
          <div className="render-progress" role="status">A newer revision is rendering. The last successful document remains available.</div>
        ) : currentArtifact ? (
          <div className="render-current" role="status"><CheckCircle2 aria-hidden="true" size={13} /> Current · {currentArtifact.artifactRevision}</div>
        ) : (
          <div className="render-progress" role="status">No registered render is available for this document.</div>
        )}

        {currentArtifact || previewBinding || exportDocx || activeArtifact ? (
          <details className="viewed-artifact">
            <summary>Details</summary>
            <div>
              {lastSuccessful ? <span>Newest successful revision · {lastSuccessful.artifactRevision} · source {lastSuccessful.sourceRevision}</span> : null}
              {previewMode === 'docx' && previewBinding ? (
                <span>Viewing current editable DOCX · local revision {previewBinding.revision} · SHA-256 {previewBinding.sha256}</span>
              ) : previewMode === 'docx' && exportDocx ? (
                <span>Viewing packet DOCX · revision {exportDocx.artifactRevision} · source {exportDocx.sourceRevision} · {exportDocx.renderStatus}</span>
              ) : activeArtifact ? (
                <span>Viewing {activeArtifact.filename ?? 'unnamed artifact'} · revision {activeArtifact.artifactRevision} · source {activeArtifact.sourceRevision} · {activeArtifact.mediaType} · {activeArtifact.renderStatus}</span>
              ) : null}
            </div>
          </details>
        ) : null}
      </div>

      <section className="document-canvas">
        {previewMode === 'docx' && currentDocxPreview ? (
          <DocxBytesPreview
            bytes={currentDocxPreview.bytes}
            filename={displayDocxFilename(currentDocxPreview.binding)}
            label="Current editable DOCX"
            sha256={currentDocxPreview.binding.sha256}
          />
        ) : previewMode === 'docx' && activeBinding ? (
          <div className="document-external-only" role="status">
            <FileText aria-hidden="true" size={28} />
            <h1>Loading current DOCX…</h1>
            <p>Reading the latest saved version from this device.</p>
          </div>
        ) : previewMode === 'docx' && exportDocx ? (
          <OriginalDocxPreview artifactId={exportDocx.artifactId} sourceFilename={exportDocx.filename} />
        ) : payload && activeArtifact && payload.artifactId === activeArtifact.artifactId && activeArtifact.previewAvailable ? (
          <PdfPreview key={payload.artifactId} bytes={payload.bytes} onPageCount={setPageCount} page={page} zoom={zoom} />
        ) : (
          <div className="document-external-only">
            <FileText aria-hidden="true" size={28} />
            <h1>No trusted preview yet</h1>
            <p>Refresh after the agent produces and registers a PDF document for this job.</p>
          </div>
        )}
      </section>
      {blankChoiceKey ? (
        <div aria-labelledby="document-source-choice-title" aria-modal="true" className="document-exit-dialog" role="dialog">
          <div>
            <FileText aria-hidden="true" size={22} />
            <h2 id="document-source-choice-title">Create or choose a DOCX</h2>
            <p>Choose the original Word file to edit in place, or create a new {documentLabels[blankChoiceKey]} DOCX.</p>
            <div>
              <button onClick={() => { const key = blankChoiceKey; setBlankChoiceKey(null); void importDocx(key) }} type="button">Choose existing DOCX</button>
              <button onClick={() => { void startBlankFromChoice() }} type="button">Start blank</button>
              <button onClick={() => setBlankChoiceKey(null)} type="button">Cancel</button>
            </div>
          </div>
        </div>
      ) : null}
      <p aria-live="polite" className="document-announcement">{loading ? 'Loading document…' : message}</p>
    </main>
  )
}
