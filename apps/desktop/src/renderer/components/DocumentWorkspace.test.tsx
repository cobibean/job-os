import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentArtifact, JobArtifactsState, JobListItem, JobOsRendererBridge } from '../../shared/contracts'
import type { DocxExternalChangeEvent, DocxOpenResult } from '../../shared/docxDocuments'
import { DocumentWorkspace } from './DocumentWorkspace'

vi.mock('./PdfPreview', async () => {
  const React = await import('react')
  return {
    PdfPreview: ({ bytes, page, zoom, onPageCount }: { bytes: ArrayBuffer, page: number, zoom: number, onPageCount: (count: number) => void }) => {
      React.useEffect(() => onPageCount(3), [onPageCount])
      return <div>PDF bytes {new Uint8Array(bytes)[0]} · page {page} at {Math.round(zoom * 100)}%</div>
    }
  }
})

vi.mock('../document-editor/DocxBytesPreview', () => ({
  DocxBytesPreview: ({ filename, label, sha256 }: { filename: string, label: string, sha256: string }) => (
    <div>{label} · {filename} · {sha256}</div>
  )
}))

vi.mock('../document-editor/OriginalDocxPreview', () => ({
  OriginalDocxPreview: ({ artifactId, sourceFilename }: { artifactId: string, sourceFilename: string | null }) => (
    <div>Packet DOCX · {sourceFilename} · {artifactId}</div>
  )
}))

const job: JobListItem = {
  jobId: 'job-1',
  company: 'Northstar',
  title: 'Staff Product Manager',
  status: 'shortlisted',
  statusGroup: 'Considering',
  canonicalUrl: 'https://example.com/jobs/1',
  discoveredAt: '2026-07-20T00:00:00Z',
  lastSeenAt: '2026-07-20T00:00:00Z'
}

const otherJob: JobListItem = {
  ...job,
  jobId: 'job-2',
  company: 'Acme',
  title: 'Product Director',
  canonicalUrl: 'https://example.com/jobs/2'
}

function artifact(overrides: Partial<DocumentArtifact> = {}): DocumentArtifact {
  return {
    artifactId: 'art_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: job.jobId,
    documentKey: 'resume',
    documentLabel: 'Resume',
    renderSequence: 2,
    sourceRevision: 'source-2',
    artifactRevision: 'render-2',
    mediaType: 'application/pdf',
    sha256: 'a'.repeat(64),
    renderStatus: 'succeeded',
    filename: 'northstar-resume.pdf',
    failureMessage: null,
    createdAt: '2026-07-20T00:00:00Z',
    isCurrent: true,
    isLastSuccessful: true,
    isApproved: false,
    previewAvailable: true,
    ...overrides
  }
}

function state(artifacts: DocumentArtifact[]): JobArtifactsState {
  return {
    jobId: job.jobId,
    artifacts,
    currentArtifactId: artifacts.find(item => item.isCurrent)?.artifactId ?? null,
    lastSuccessfulArtifactId: artifacts.find(item => item.isLastSuccessful)?.artifactId ?? null,
    approvedArtifactId: artifacts.find(item => item.isApproved)?.artifactId ?? null
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function installDocuments(overrides: Partial<JobOsRendererBridge['documents']> = {}) {
  const successful = state([artifact()])
  const documents: JobOsRendererBridge['documents'] = {
    list: vi.fn(async () => successful),
    refresh: vi.fn(async () => successful),
    approve: vi.fn(async () => successful),
    loadPdf: vi.fn(async artifactId => ({
      artifactId,
      artifactRevision: 'render-2',
      sourceRevision: 'source-2',
      sha256: 'a'.repeat(64),
      bytes: Uint8Array.of(2).buffer
    })),
    loadOriginalDocx: vi.fn(async artifactId => ({
      artifactId,
      filename: 'northstar-resume.docx',
      sha256: 'b'.repeat(64),
      bytes: Uint8Array.of(0x50, 0x4b).buffer
    })),
    export: vi.fn(async () => 'Exported northstar-resume.pdf'),
    reveal: vi.fn(async () => 'Revealed northstar-resume.pdf'),
    open: vi.fn(async () => 'Opened northstar-resume.pdf'),
    ...overrides
  }
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { documents } as JobOsRendererBridge
  })
  return documents
}

function installDocxDocuments(overrides: Partial<JobOsRendererBridge['docxDocuments']> = {}) {
  let listener: ((event: DocxExternalChangeEvent) => void) | null = null
  const opened: DocxOpenResult = {
    binding: {
      schemaVersion: 1,
      bindingId: 'docx_northstar_resume_fake',
      jobId: job.jobId,
      documentKey: 'resume',
      documentLabel: 'Resume',
      canonicalPath: '/tmp/(FAKE)-Northstar-AI-Labs-Resume.docx',
      filename: '(FAKE)-Northstar-AI-Labs-Resume.docx',
      sha256: 'b'.repeat(64),
      byteLength: 2,
      modifiedAtMs: 1,
      revision: 1,
      capabilities: { mode: 'editable', protectedBlockCount: 0, editableBlockCount: 1, reasons: [] },
      createdAt: '2026-08-08T00:00:00Z',
      updatedAt: '2026-08-08T00:00:00Z'
    },
    bytes: Uint8Array.of(0x50, 0x4b).buffer
  }
  const docxDocuments = {
    listBindings: vi.fn(async () => []),
    openBound: vi.fn(async () => null),
    openArtifact: vi.fn(async () => opened),
    chooseFile: vi.fn(async () => null),
    createBlank: vi.fn(async () => null),
    reload: vi.fn(async () => opened),
    save: vi.fn(),
    saveAs: vi.fn(),
    createRecovery: vi.fn(),
    listRecoveries: vi.fn(async () => []),
    restoreRecovery: vi.fn(),
    unbind: vi.fn(),
    subscribe: vi.fn((next: (event: DocxExternalChangeEvent) => void) => {
      listener = next
      return () => { listener = null }
    }),
    ...overrides
  } as unknown as JobOsRendererBridge['docxDocuments']
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { ...window.jobos, docxDocuments } as JobOsRendererBridge
  })
  return { docxDocuments, emit: (event: DocxExternalChangeEvent) => listener?.(event), opened }
}

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  vi.restoreAllMocks()
})

describe('trusted document workspace', () => {
  it('keeps local documents usable when the optional artifact refresh is unavailable', async () => {
    const local = state([artifact()])
    installDocuments({
      list: vi.fn(async () => local),
      refresh: vi.fn(async () => { throw new Error('Document request failed (503)') })
    })

    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    expect(await screen.findByText('Local documents loaded; optional artifact refresh is unavailable')).not.toBeNull()
    expect(screen.getByText('Newest successful revision · render-2 · source source-2')).not.toBeNull()
    expect(screen.queryByText(/Document request failed/)).toBeNull()
  })

  it('labels each document action as an editor entry point', async () => {
    installDocuments()
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    await screen.findByText('Newest successful revision · render-2 · source source-2')
    expect(screen.getByRole('button', { name: 'Open Resume in Editor' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Open Cover Letter in Editor' })).not.toBeNull()
    expect(screen.getByRole('button', { name: 'Open References in Editor' })).not.toBeNull()
    expect(screen.getByText('Resume Editor')).not.toBeNull()
    expect(screen.getByText('Cover Letter Editor')).not.toBeNull()
    expect(screen.getByText('References Editor')).not.toBeNull()
    expect(screen.queryByRole('button', { name: /^(Create|Edit) (Resume|Cover Letter|References)$/ })).toBeNull()
  })

  it('opens the viewed packet DOCX directly instead of showing the file picker', async () => {
    const pdf = artifact()
    const docx = artifact({
      artifactId: 'art_DOCXABCDEFGHIJKLMNOPQRST',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: '(FAKE)-Northstar-AI-Labs-Resume.docx',
      previewAvailable: false,
      isCurrent: false,
      isLastSuccessful: false
    })
    const packet = state([pdf, docx])
    installDocuments({ list: vi.fn(async () => packet), refresh: vi.fn(async () => packet) })
    const { docxDocuments, opened } = installDocxDocuments()
    const onOpenEditor = vi.fn()

    render(
      <DocumentWorkspace
        hydrated
        job={job}
        onOpenEditor={onOpenEditor}
        onViewChange={vi.fn()}
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )

    await screen.findByText('Newest successful revision · render-2 · source source-2')
    fireEvent.click(screen.getByRole('button', { name: 'Open Resume in Editor' }))

    await waitFor(() => expect(docxDocuments.openArtifact).toHaveBeenCalledWith(
      job.jobId,
      'resume',
      docx.artifactId
    ))
    expect(docxDocuments.openBound).not.toHaveBeenCalled()
    expect(docxDocuments.chooseFile).not.toHaveBeenCalled()
    expect(onOpenEditor).toHaveBeenCalledWith(opened)
  })

  it('discovers the selected job artifact and persists page and zoom', async () => {
    const documents = installDocuments()
    const onViewChange = vi.fn()
    render(<DocumentWorkspace hydrated job={job} onViewChange={onViewChange} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    await screen.findByText('Newest successful revision · render-2 · source source-2')
    expect(documents.refresh).toHaveBeenCalledWith(job.jobId)
    expect(await screen.findByText('PDF bytes 2 · page 1 at 100%')).not.toBeNull()
    await screen.findByText('Page 1 of 3')

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))

    await waitFor(() => expect(onViewChange).toHaveBeenLastCalledWith(
      'art_ABCDEFGHIJKLMNOPQRSTUVWX', 2, 1.1
    ))
  })

  it('approves the exact visible successful revision and shows its durable state', async () => {
    const pending = artifact()
    const approved = artifact({ isApproved: true })
    const documents = installDocuments({
      list: vi.fn(async () => state([pending])),
      refresh: vi.fn(async () => state([pending])),
      approve: vi.fn(async () => state([approved]))
    })
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Approve this revision' }))

    await waitFor(() => expect(documents.approve).toHaveBeenCalledWith(job.jobId, pending.artifactId))
    expect(await screen.findByText('Approved revision')).not.toBeNull()
    expect((screen.getByRole('button', { name: 'Approved revision' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('discards a late approval response after the active job changes', async () => {
    const jobAArtifact = artifact()
    const jobBArtifact = artifact({
      artifactId: 'art_BBBBBBBBBBBBBBBBBBBBBBBB',
      jobId: otherJob.jobId,
      artifactRevision: 'render-b',
      sourceRevision: 'source-b',
      filename: 'acme-resume.pdf'
    })
    const jobBState = { ...state([jobBArtifact]), jobId: otherJob.jobId }
    const pendingApproval = deferred<JobArtifactsState>()
    const documents = installDocuments({
      list: vi.fn(jobId => Promise.resolve(jobId === job.jobId ? state([jobAArtifact]) : jobBState)),
      refresh: vi.fn(jobId => Promise.resolve(jobId === job.jobId ? state([jobAArtifact]) : jobBState)),
      approve: vi.fn(() => pendingApproval.promise)
    })
    const view = render(
      <DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Approve this revision' }))
    await waitFor(() => expect(documents.approve).toHaveBeenCalledOnce())
    view.rerender(
      <DocumentWorkspace hydrated job={otherJob} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )
    expect(await screen.findByText(/Viewing acme-resume.pdf/)).not.toBeNull()

    pendingApproval.resolve(state([artifact({ isApproved: true })]))

    await waitFor(() => expect(screen.getByText(/Viewing acme-resume.pdf/)).not.toBeNull())
    expect(screen.queryByText(/Viewing northstar-resume.pdf/)).toBeNull()
  })

  it('keeps the last successful preview when the newest render fails', async () => {
    const retained = artifact({ isCurrent: false, isLastSuccessful: true })
    const failed = artifact({
      artifactId: 'art_ZYXWVUTSRQPONMLKJIHGFEDC',
      artifactRevision: 'render-3',
      sourceRevision: 'source-3',
      sha256: null,
      renderStatus: 'failed',
      filename: null,
      failureMessage: 'Fixture render failed',
      isCurrent: true,
      isLastSuccessful: false,
      previewAvailable: false
    })
    const failedState = state([failed, retained])
    installDocuments({ list: vi.fn(async () => failedState), refresh: vi.fn(async () => failedState) })

    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    expect(await screen.findByText(/Newest render failed/)).not.toBeNull()
    expect(screen.getByText(/Showing last successful revision render-2/)).not.toBeNull()
    expect(await screen.findByText('PDF bytes 2 · page 1 at 100%')).not.toBeNull()
  })

  it('previews a DOCX-only revision while keeping native actions available', async () => {
    const docx = artifact({
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: 'northstar-resume.docx',
      previewAvailable: false
    })
    const docxState = state([docx])
    const documents = installDocuments({ list: vi.fn(async () => docxState), refresh: vi.fn(async () => docxState) })

    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    expect(await screen.findByText(`Packet DOCX · ${docx.filename} · ${docx.artifactId}`)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Open$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Reveal$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Export$/ }))
    expect(screen.queryByRole('menuitem', { name: 'Export PDF' })).toBeNull()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export DOCX' }))

    await waitFor(() => expect(documents.open).toHaveBeenCalledWith(docx.artifactId))
    expect(documents.reveal).toHaveBeenCalledWith(docx.artifactId)
    expect(documents.export).toHaveBeenCalledWith(docx.artifactId)
    expect(documents.loadPdf).not.toHaveBeenCalled()
  })

  it('refreshes paired export formats when publication completes while documents are already open', async () => {
    const pdf = artifact()
    const docx = artifact({
      artifactId: 'art_DOCXABCDEFGHIJKLMNOPQRST',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: 'northstar-resume.docx',
      previewAvailable: false,
      isCurrent: false,
      isLastSuccessful: false
    })
    const initial = state([pdf])
    const paired = state([pdf, docx])
    const documents = installDocuments({
      list: vi.fn(async () => initial),
      refresh: vi.fn()
        .mockResolvedValueOnce(initial)
        .mockResolvedValue(paired)
    })
    const view = render(
      <DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} refreshGeneration={0} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )

    fireEvent.click(await screen.findByRole('button', { name: /^Export$/ }))
    expect(screen.getByRole('menuitem', { name: 'Export PDF' })).not.toBeNull()
    expect(screen.queryByRole('menuitem', { name: 'Export DOCX' })).toBeNull()

    view.rerender(
      <DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} refreshGeneration={1} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )

    await waitFor(() => expect(documents.refresh).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('menuitem', { name: 'Export DOCX' })).not.toBeNull()
  })

  it('invalidates job A bytes and actions while job B is pending, then keeps them cleared on failure', async () => {
    const jobAArtifact = artifact()
    const jobBArtifact = artifact({
      artifactId: 'art_BBBBBBBBBBBBBBBBBBBBBBBB',
      jobId: otherJob.jobId,
      artifactRevision: 'render-b',
      sourceRevision: 'source-b',
      filename: 'acme-resume.pdf'
    })
    const jobBState = { ...state([jobBArtifact]), jobId: otherJob.jobId }
    const pendingB = deferred<ReturnType<typeof state>>()
    const loadB = deferred<Awaited<ReturnType<JobOsRendererBridge['documents']['loadPdf']>>>()
    const documents = installDocuments({
      list: vi.fn(jobId => jobId === job.jobId ? Promise.resolve(state([jobAArtifact])) : pendingB.promise),
      refresh: vi.fn(jobId => Promise.resolve(jobId === job.jobId ? state([jobAArtifact]) : jobBState)),
      loadPdf: vi.fn(artifactId => artifactId === jobAArtifact.artifactId
        ? Promise.resolve({ artifactId, artifactRevision: 'render-2', sourceRevision: 'source-2', sha256: 'a'.repeat(64), bytes: Uint8Array.of(1).buffer })
        : loadB.promise)
    })
    const view = render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)
    expect(await screen.findByText('PDF bytes 1 · page 1 at 100%')).not.toBeNull()

    view.rerender(<DocumentWorkspace hydrated job={otherJob} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)
    expect(screen.queryByText(/PDF bytes 1/)).toBeNull()
    expect((screen.getByRole('button', { name: /^Open$/ }) as HTMLButtonElement).disabled).toBe(true)

    pendingB.resolve(jobBState)
    await waitFor(() => expect(documents.loadPdf).toHaveBeenCalledWith(jobBArtifact.artifactId))
    loadB.reject(new Error('job B PDF unavailable'))
    expect(await screen.findByText('job B PDF unavailable')).not.toBeNull()
    expect(screen.queryByText(/PDF bytes 1/)).toBeNull()
    expect(screen.getByText(/Viewing acme-resume.pdf · revision render-b · source source-b/)).not.toBeNull()
  })

  it('keeps bytes, viewed metadata, and native actions aligned during revision transitions', async () => {
    const revisionA = artifact({ isCurrent: false, isLastSuccessful: false })
    const revisionB = artifact({
      artifactId: 'art_CCCCCCCCCCCCCCCCCCCCCCCC',
      artifactRevision: 'render-3',
      sourceRevision: 'source-3',
      filename: 'northstar-resume-v3.pdf'
    })
    const revisions = state([revisionB, revisionA])
    const pendingB = deferred<Awaited<ReturnType<JobOsRendererBridge['documents']['loadPdf']>>>()
    const documents = installDocuments({
      list: vi.fn(async () => revisions),
      refresh: vi.fn(async () => revisions),
      loadPdf: vi.fn(artifactId => artifactId === revisionA.artifactId
        ? Promise.resolve({ artifactId, artifactRevision: 'render-2', sourceRevision: 'source-2', sha256: 'a'.repeat(64), bytes: Uint8Array.of(1).buffer })
        : pendingB.promise)
    })
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={revisionA.artifactId} restoredPage={2} restoredZoom={1.4} />)
    expect(await screen.findByText('PDF bytes 1 · page 2 at 140%')).not.toBeNull()

    fireEvent.change(screen.getByRole('combobox', { name: 'Resume revision' }), { target: { value: revisionB.artifactId } })
    expect(screen.queryByText(/PDF bytes 1/)).toBeNull()
    expect(screen.getByText(/Viewing northstar-resume-v3.pdf · revision render-3 · source source-3/)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Export$/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export PDF' }))
    await waitFor(() => expect(documents.export).toHaveBeenCalledWith(revisionB.artifactId))

    pendingB.reject(new Error('revision B failed to load'))
    expect(await screen.findByText('revision B failed to load')).not.toBeNull()
    expect(screen.queryByText(/PDF bytes 1/)).toBeNull()
  })

  it('preserves an older restored revision and its page and zoom across automatic refresh', async () => {
    const older = artifact({ isCurrent: false, isLastSuccessful: false })
    const newest = artifact({
      artifactId: 'art_DDDDDDDDDDDDDDDDDDDDDDDD',
      artifactRevision: 'render-4',
      sourceRevision: 'source-4'
    })
    const revisions = state([newest, older])
    const documents = installDocuments({
      list: vi.fn(async () => revisions),
      refresh: vi.fn(async () => revisions),
      loadPdf: vi.fn(async artifactId => ({
        artifactId,
        artifactRevision: artifactId === older.artifactId ? 'render-2' : 'render-4',
        sourceRevision: artifactId === older.artifactId ? 'source-2' : 'source-4',
        sha256: 'a'.repeat(64),
        bytes: Uint8Array.of(artifactId === older.artifactId ? 1 : 4).buffer
      }))
    })
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={older.artifactId} restoredPage={2} restoredZoom={1.4} />)

    expect(await screen.findByText('PDF bytes 1 · page 2 at 140%')).not.toBeNull()
    await waitFor(() => expect(documents.refresh).toHaveBeenCalledOnce())
    expect((screen.getByRole('combobox', { name: 'Resume revision' }) as HTMLSelectElement).value).toBe(older.artifactId)
    expect(screen.queryByText(/PDF bytes 4/)).toBeNull()
  })

  it('does not clear a valid restored document view while its artifact registry loads', async () => {
    const restored = artifact({ isApproved: true })
    const restoredState = state([restored])
    const pendingList = deferred<JobArtifactsState>()
    const onViewChange = vi.fn()
    const documents = installDocuments({
      list: vi.fn(() => pendingList.promise),
      refresh: vi.fn(async () => restoredState)
    })

    render(
      <DocumentWorkspace
        hydrated
        job={job}
        onViewChange={onViewChange}
        restoredArtifactId={restored.artifactId}
        restoredPage={2}
        restoredZoom={1.1}
      />
    )

    await waitFor(() => expect(documents.list).toHaveBeenCalledOnce())
    expect(onViewChange).not.toHaveBeenCalled()

    pendingList.resolve(restoredState)

    expect(await screen.findByText('PDF bytes 2 · page 2 at 110%')).not.toBeNull()
    expect(onViewChange).not.toHaveBeenCalled()
    expect(documents.loadPdf).toHaveBeenCalledOnce()
  })

  it('preserves a restored document while the selected job is still hydrating', async () => {
    const restoredArtifactId = artifact().artifactId
    const onViewChange = vi.fn()
    const documents = installDocuments()
    const view = render(
      <DocumentWorkspace
        hydrated={false}
        job={null}
        onViewChange={onViewChange}
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )

    view.rerender(
      <DocumentWorkspace
        hydrated
        job={null}
        onViewChange={onViewChange}
        restoredArtifactId={restoredArtifactId}
        restoredPage={2}
        restoredZoom={1.1}
      />
    )

    await waitFor(() => expect(screen.getByText('Select a job to review its documents')).not.toBeNull())
    expect(onViewChange).not.toHaveBeenCalled()

    view.rerender(
      <DocumentWorkspace
        hydrated
        job={job}
        onViewChange={onViewChange}
        restoredArtifactId={restoredArtifactId}
        restoredPage={2}
        restoredZoom={1.1}
      />
    )

    expect(await screen.findByText('PDF bytes 2 · page 2 at 110%')).not.toBeNull()
    expect(await screen.findByText('Page 2 of 3')).not.toBeNull()
    expect(screen.getByText('110%')).not.toBeNull()
    expect(documents.loadPdf).toHaveBeenCalledOnce()
    expect(onViewChange).not.toHaveBeenCalled()
  })

  it('does not reload artifacts when workspace persistence rerenders the same job', async () => {
    const documents = installDocuments()
    const onViewChange = vi.fn()
    const view = render(
      <DocumentWorkspace hydrated job={job} onViewChange={onViewChange} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )

    await waitFor(() => expect(onViewChange).toHaveBeenCalledWith(artifact().artifactId, 1, 1))
    view.rerender(
      <DocumentWorkspace hydrated job={{ ...job }} onViewChange={onViewChange} restoredArtifactId={artifact().artifactId} restoredPage={1} restoredZoom={1} />
    )

    await waitFor(() => expect(documents.list).toHaveBeenCalledOnce())
    expect(documents.refresh).toHaveBeenCalledOnce()
  })

  it('keeps loaded PDF bytes when refresh returns the same artifact', async () => {
    const current = state([artifact()])
    const refreshed = deferred<JobArtifactsState>()
    installDocuments({
      list: vi.fn(async () => current),
      refresh: vi.fn(() => refreshed.promise)
    })
    render(
      <DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )

    expect(await screen.findByText('PDF bytes 2 · page 1 at 100%')).not.toBeNull()
    refreshed.resolve(current)

    await screen.findByText('Artifact registry is current')
    expect(screen.getByText('PDF bytes 2 · page 1 at 100%')).not.toBeNull()
  })

  it('captures the context bridge once across renderer state updates', async () => {
    const documents = installDocuments()
    Object.defineProperty(window, 'jobos', {
      configurable: true,
      get: () => ({ documents: { ...documents } } as JobOsRendererBridge)
    })

    render(
      <DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />
    )

    expect(await screen.findByText('PDF bytes 2 · page 1 at 100%')).not.toBeNull()
    await waitFor(() => expect(documents.list).toHaveBeenCalledOnce())
    expect(documents.refresh).toHaveBeenCalledOnce()
    expect(documents.loadPdf).toHaveBeenCalledOnce()
  })

  it('falls back deterministically and resets view when the selected revision disappears', async () => {
    const older = artifact({ isCurrent: false, isLastSuccessful: false })
    const newest = artifact({
      artifactId: 'art_EEEEEEEEEEEEEEEEEEEEEEEE',
      artifactRevision: 'render-5',
      sourceRevision: 'source-5'
    })
    const listed = state([newest, older])
    const refreshed = state([newest])
    installDocuments({
      list: vi.fn(async () => listed),
      refresh: vi.fn(async () => refreshed),
      loadPdf: vi.fn(async artifactId => ({
        artifactId,
        artifactRevision: artifactId === older.artifactId ? 'render-2' : 'render-5',
        sourceRevision: artifactId === older.artifactId ? 'source-2' : 'source-5',
        sha256: 'a'.repeat(64),
        bytes: Uint8Array.of(artifactId === older.artifactId ? 1 : 5).buffer
      }))
    })
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={older.artifactId} restoredPage={3} restoredZoom={1.6} />)

    expect(await screen.findByText('PDF bytes 5 · page 1 at 100%')).not.toBeNull()
    expect(screen.queryByText(/PDF bytes 1/)).toBeNull()
  })

  it('shows a last-successful DOCX instead of an older PDF when the newest render failed', async () => {
    const olderPdf = artifact({
      isCurrent: false,
      isLastSuccessful: false,
      sourceRevision: 'source-1',
      artifactRevision: 'render-1',
      renderSequence: 1
    })
    const lastGoodDocx = artifact({
      artifactId: 'art_FFFFFFFFFFFFFFFFFFFFFFFF',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      sourceRevision: 'source-2',
      artifactRevision: 'render-2',
      renderSequence: 2,
      filename: 'northstar-resume.docx',
      previewAvailable: false,
      isCurrent: false,
      isLastSuccessful: true
    })
    const failed = artifact({
      artifactId: 'art_GGGGGGGGGGGGGGGGGGGGGGGG',
      sourceRevision: 'source-3',
      artifactRevision: 'render-3',
      renderSequence: 3,
      renderStatus: 'failed',
      failureMessage: 'newest failed',
      filename: null,
      sha256: null,
      previewAvailable: false,
      isCurrent: true,
      isLastSuccessful: false
    })
    const failedState = state([failed, lastGoodDocx, olderPdf])
    const documents = installDocuments({ list: vi.fn(async () => failedState), refresh: vi.fn(async () => failedState) })
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    expect(await screen.findByText(`Packet DOCX · ${lastGoodDocx.filename} · ${lastGoodDocx.artifactId}`)).not.toBeNull()
    expect(screen.getByText(/Viewing packet DOCX · revision render-2/)).not.toBeNull()
    expect(screen.queryByText(/PDF bytes/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Export$/ }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export DOCX' }))
    await waitFor(() => expect(documents.export).toHaveBeenCalledWith(lastGoodDocx.artifactId))
  })

  it('navigates ordered logical documents, scopes revisions, resets the view, and permits cover letter approval', async () => {
    const resumePdf = artifact({
      artifactId: 'art_RESUMEPDFABCDEFGHIJKLMNOP',
      sourceRevision: 'resume-source',
      artifactRevision: 'resume-pdf',
      renderSequence: 4,
      filename: 'northstar-resume.pdf'
    })
    const resumeDocx = artifact({
      artifactId: 'art_RESUMEDOCXABCDEFGHIJKLMNO',
      sourceRevision: 'resume-source',
      artifactRevision: 'resume-docx',
      renderSequence: 5,
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: 'northstar-resume.docx',
      previewAvailable: false
    })
    const coverOlder = artifact({
      artifactId: 'art_COVEROLDABCDEFGHIJKLMNOPQ',
      documentKey: 'cover_letter',
      documentLabel: 'Cover Letter',
      sourceRevision: 'cover-source-1',
      artifactRevision: 'cover-1',
      renderSequence: 6,
      filename: 'northstar-cover-1.pdf',
      isCurrent: false
    })
    const coverNewest = artifact({
      artifactId: 'art_COVERNEWABCDEFGHIJKLMNOPQ',
      documentKey: 'cover_letter',
      documentLabel: 'Cover Letter',
      sourceRevision: 'cover-source-2',
      artifactRevision: 'cover-2',
      renderSequence: 8,
      filename: 'northstar-cover-2.pdf'
    })
    const artifacts = state([coverNewest, resumeDocx, coverOlder, resumePdf])
    installDocuments({ list: vi.fn(async () => artifacts), refresh: vi.fn(async () => artifacts) })

    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={resumePdf.artifactId} restoredPage={2} restoredZoom={1.4} />)

    expect(await screen.findByText('PDF bytes 2 · page 2 at 140%')).not.toBeNull()
    expect(screen.getByText('1 of 2')).not.toBeNull()
    expect((screen.getByRole('button', { name: 'Previous document' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('combobox', { name: 'Resume revision' }) as HTMLSelectElement).options).toHaveLength(2)

    fireEvent.click(screen.getByRole('button', { name: 'Next document' }))

    expect(await screen.findByText('PDF bytes 2 · page 1 at 100%')).not.toBeNull()
    expect(screen.getByText('2 of 2')).not.toBeNull()
    expect((screen.getByRole('button', { name: 'Next document' }) as HTMLButtonElement).disabled).toBe(true)
    const coverSelector = screen.getByRole('combobox', { name: 'Cover Letter revision' }) as HTMLSelectElement
    expect(Array.from(coverSelector.options).map(option => option.textContent)).toEqual([
      'No artifact',
      'cover-2 · succeeded · newest',
      'cover-1 · succeeded'
    ])
    expect(screen.getByRole('button', { name: /Approve/ })).not.toBeNull()
  })

  it('exports the chosen succeeded PDF or latest DOCX variant from one logical revision', async () => {
    const pdf = artifact({
      artifactId: 'art_PAIREDPDFABCDEFGHIJKLMNOP',
      sourceRevision: 'paired-source',
      artifactRevision: 'paired-pdf',
      renderSequence: 9
    })
    const olderDocx = artifact({
      artifactId: 'art_OLDDOCXABCDEFGHIJKLMNOPQ',
      sourceRevision: 'paired-source',
      artifactRevision: 'paired-docx-old',
      renderSequence: 8,
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      previewAvailable: false
    })
    const latestDocx = artifact({
      artifactId: 'art_NEWDOCXABCDEFGHIJKLMNOPQ',
      sourceRevision: 'paired-source',
      artifactRevision: 'paired-docx-new',
      renderSequence: 10,
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      previewAvailable: false
    })
    const failedPdf = artifact({
      artifactId: 'art_FAILEDPDFABCDEFGHIJKLMNOP',
      sourceRevision: 'paired-source',
      artifactRevision: 'paired-pdf-failed',
      renderSequence: 11,
      renderStatus: 'failed',
      previewAvailable: false
    })
    const artifacts = state([failedPdf, latestDocx, pdf, olderDocx])
    const documents = installDocuments({ list: vi.fn(async () => artifacts), refresh: vi.fn(async () => artifacts) })
    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    await screen.findByText(/Viewing northstar-resume.pdf/)
    const exportButton = screen.getByRole('button', { name: /^Export$/ })
    fireEvent.click(exportButton)
    const exportMenu = screen.getByRole('menu', { name: 'Export document' })
    expect(screen.getByRole('menuitem', { name: 'Export PDF' })).not.toBeNull()
    expect(screen.getByRole('menuitem', { name: 'Export DOCX' })).not.toBeNull()
    expect(exportMenu.closest('.document-toolbar')).toBeNull()
    fireEvent.keyDown(exportMenu, { key: 'Escape' })
    expect(screen.queryByRole('menu', { name: 'Export document' })).toBeNull()
    expect(document.activeElement).toBe(exportButton)
    fireEvent.click(exportButton)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export PDF' }))
    await waitFor(() => expect(documents.export).toHaveBeenCalledTimes(1))
    await waitFor(() => expect((exportButton as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(exportButton)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export DOCX' }))

    await waitFor(() => expect(documents.export).toHaveBeenCalledTimes(2))
    expect(documents.export).toHaveBeenNthCalledWith(1, pdf.artifactId)
    expect(documents.export).toHaveBeenNthCalledWith(2, latestDocx.artifactId)
    expect(documents.export).not.toHaveBeenCalledWith(failedPdf.artifactId)
    expect(documents.export).not.toHaveBeenCalledWith(olderDocx.artifactId)
  })

  it('switches a paired revision between publication PDF and packet DOCX preview', async () => {
    const pdf = artifact()
    const docx = artifact({
      artifactId: 'art_PREVIEWDOCXABCDEFGHIJKLM',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: '(FAKE)-Northstar-Resume.docx',
      previewAvailable: false
    })
    const paired = state([pdf, docx])
    installDocuments({ list: vi.fn(async () => paired), refresh: vi.fn(async () => paired) })
    installDocxDocuments()
    const onPreviewModeChange = vi.fn()
    const view = render(
      <DocumentWorkspace
        hydrated
        job={job}
        onPreviewModeChange={onPreviewModeChange}
        onViewChange={vi.fn()}
        previewMode="pdf"
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )

    await screen.findByText(/PDF bytes 2/)
    fireEvent.click(screen.getByRole('button', { name: 'DOCX' }))
    expect(onPreviewModeChange).toHaveBeenCalledWith('docx')

    view.rerender(
      <DocumentWorkspace
        hydrated
        job={job}
        onPreviewModeChange={onPreviewModeChange}
        onViewChange={vi.fn()}
        previewMode="docx"
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )
    expect(await screen.findByText(`Packet DOCX · ${docx.filename} · ${docx.artifactId}`)).not.toBeNull()
    expect(screen.getByRole('button', { name: 'DOCX' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.queryByRole('button', { name: 'Next page' })).toBeNull()
  })

  it('reloads current editable DOCX bytes when the editor mutation generation changes', async () => {
    const pdf = artifact()
    const docx = artifact({
      artifactId: 'art_CURRENTDOCXABCDEFGHIJKLM',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: '(FAKE)-Northstar-Resume.docx',
      previewAvailable: false
    })
    const paired = state([pdf, docx])
    installDocuments({ list: vi.fn(async () => paired), refresh: vi.fn(async () => paired) })
    const { docxDocuments, opened } = installDocxDocuments()
    vi.mocked(docxDocuments.listBindings).mockResolvedValue([opened.binding])
    vi.mocked(docxDocuments.openBound).mockResolvedValue(opened)
    const view = render(
      <DocumentWorkspace
        hydrated
        job={job}
        onViewChange={vi.fn()}
        previewMode="docx"
        refreshGeneration={0}
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )

    expect(await screen.findByText(`Current editable DOCX · ${opened.binding.filename} · ${opened.binding.sha256}`)).not.toBeNull()

    const updated: DocxOpenResult = {
      binding: { ...opened.binding, revision: 2, sha256: 'c'.repeat(64) },
      bytes: Uint8Array.of(0x50, 0x4b, 0x03, 0x04).buffer
    }
    vi.mocked(docxDocuments.listBindings).mockResolvedValue([updated.binding])
    vi.mocked(docxDocuments.openBound).mockResolvedValue(updated)
    view.rerender(
      <DocumentWorkspace
        hydrated
        job={job}
        onViewChange={vi.fn()}
        previewMode="docx"
        refreshGeneration={1}
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )

    expect(await screen.findByText(`Current editable DOCX · ${updated.binding.filename} · ${updated.binding.sha256}`)).not.toBeNull()
    expect(docxDocuments.openBound).toHaveBeenLastCalledWith(job.jobId, 'resume')
  })

  it('refreshes the visible editable DOCX when an agent operation publishes new canonical bytes', async () => {
    const pdf = artifact()
    const docx = artifact({
      artifactId: 'art_AGENTDOCXABCDEFGHIJKLMNOP',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: '(FAKE)-Northstar-Resume.docx',
      previewAvailable: false
    })
    const paired = state([pdf, docx])
    installDocuments({ list: vi.fn(async () => paired), refresh: vi.fn(async () => paired) })
    const { docxDocuments, emit, opened } = installDocxDocuments()
    vi.mocked(docxDocuments.listBindings).mockResolvedValue([opened.binding])
    vi.mocked(docxDocuments.openBound).mockResolvedValue(opened)
    render(
      <DocumentWorkspace
        hydrated
        job={job}
        onViewChange={vi.fn()}
        previewMode="docx"
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )
    expect(await screen.findByText(`Current editable DOCX · ${opened.binding.filename} · ${opened.binding.sha256}`)).not.toBeNull()

    const updated: DocxOpenResult = {
      binding: { ...opened.binding, revision: 2, sha256: 'd'.repeat(64), modifiedAtMs: 2 },
      bytes: Uint8Array.of(0x50, 0x4b, 0x05).buffer
    }
    vi.mocked(docxDocuments.openBound).mockResolvedValue(updated)
    emit({
      bindingId: updated.binding.bindingId,
      jobId: updated.binding.jobId,
      documentKey: updated.binding.documentKey,
      kind: 'changed',
      sha256: updated.binding.sha256,
      modifiedAtMs: updated.binding.modifiedAtMs
    })

    expect(await screen.findByText(`Current editable DOCX · ${updated.binding.filename} · ${updated.binding.sha256}`)).not.toBeNull()
    expect(screen.getByText('Current editable DOCX refreshed')).not.toBeNull()
  })

  it('keeps the newest DOCX mutation when older reloads resolve later', async () => {
    const pdf = artifact()
    const docx = artifact({
      artifactId: 'art_RACEDOCXABCDEFGHIJKLMNOPQ',
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: '(FAKE)-Northstar-Resume.docx',
      previewAvailable: false
    })
    const paired = state([pdf, docx])
    installDocuments({ list: vi.fn(async () => paired), refresh: vi.fn(async () => paired) })
    const { docxDocuments, emit, opened } = installDocxDocuments()
    vi.mocked(docxDocuments.listBindings).mockResolvedValue([opened.binding])
    vi.mocked(docxDocuments.openBound).mockResolvedValue(opened)
    render(
      <DocumentWorkspace
        hydrated
        job={job}
        onViewChange={vi.fn()}
        previewMode="docx"
        restoredArtifactId={null}
        restoredPage={1}
        restoredZoom={1}
      />
    )
    expect(await screen.findByText(`Current editable DOCX · ${opened.binding.filename} · ${opened.binding.sha256}`)).not.toBeNull()

    const older: DocxOpenResult = {
      binding: { ...opened.binding, revision: 2, sha256: 'c'.repeat(64), modifiedAtMs: 2 },
      bytes: Uint8Array.of(0x50, 0x4b, 0x05).buffer
    }
    const newer: DocxOpenResult = {
      binding: { ...opened.binding, revision: 3, sha256: 'd'.repeat(64), modifiedAtMs: 3 },
      bytes: Uint8Array.of(0x50, 0x4b, 0x06).buffer
    }
    let resolveOlder!: (value: DocxOpenResult) => void
    vi.mocked(docxDocuments.openBound)
      .mockImplementationOnce(() => new Promise(resolve => { resolveOlder = resolve }))
      .mockResolvedValue(newer)

    emit({
      bindingId: older.binding.bindingId,
      jobId: older.binding.jobId,
      documentKey: older.binding.documentKey,
      kind: 'changed',
      sha256: older.binding.sha256,
      modifiedAtMs: older.binding.modifiedAtMs
    })
    emit({
      bindingId: newer.binding.bindingId,
      jobId: newer.binding.jobId,
      documentKey: newer.binding.documentKey,
      kind: 'changed',
      sha256: newer.binding.sha256,
      modifiedAtMs: newer.binding.modifiedAtMs
    })

    expect(await screen.findByText(`Current editable DOCX · ${newer.binding.filename} · ${newer.binding.sha256}`)).not.toBeNull()
    resolveOlder(older)
    await waitFor(() => {
      expect(screen.queryByText(`Current editable DOCX · ${older.binding.filename} · ${older.binding.sha256}`)).toBeNull()
      expect(screen.getByText(`Current editable DOCX · ${newer.binding.filename} · ${newer.binding.sha256}`)).not.toBeNull()
    })
  })
})
