import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { DocumentArtifact, JobArtifactsState, JobListItem, JobOsRendererBridge } from '../../shared/contracts'
import { DocumentWorkspace } from './DocumentWorkspace'

vi.mock('./PdfPreview', async () => {
  const React = await import('react')
  return {
    PdfPreview: ({ page, zoom, onPageCount }: { page: number, zoom: number, onPageCount: (count: number) => void }) => {
      React.useEffect(() => onPageCount(3), [onPageCount])
      return <div>PDF page {page} at {Math.round(zoom * 100)}%</div>
    }
  }
})

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

function artifact(overrides: Partial<DocumentArtifact> = {}): DocumentArtifact {
  return {
    artifactId: 'art_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: job.jobId,
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
    previewAvailable: true,
    ...overrides
  }
}

function state(artifacts: DocumentArtifact[]): JobArtifactsState {
  return {
    jobId: job.jobId,
    artifacts,
    currentArtifactId: artifacts.find(item => item.isCurrent)?.artifactId ?? null,
    lastSuccessfulArtifactId: artifacts.find(item => item.isLastSuccessful)?.artifactId ?? null
  }
}

function installDocuments(overrides: Partial<JobOsRendererBridge['documents']> = {}) {
  const successful = state([artifact()])
  const documents: JobOsRendererBridge['documents'] = {
    list: vi.fn(async () => successful),
    refresh: vi.fn(async () => successful),
    loadPdf: vi.fn(async artifactId => ({
      artifactId,
      artifactRevision: 'render-2',
      sourceRevision: 'source-2',
      sha256: 'a'.repeat(64),
      bytes: new ArrayBuffer(8)
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

afterEach(() => {
  cleanup()
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  vi.restoreAllMocks()
})

describe('trusted document workspace', () => {
  it('discovers the selected job artifact and persists page and zoom', async () => {
    const documents = installDocuments()
    const onViewChange = vi.fn()
    render(<DocumentWorkspace hydrated job={job} onViewChange={onViewChange} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    await screen.findByText('Newest successful revision · render-2 · source source-2')
    expect(documents.refresh).toHaveBeenCalledWith(job.jobId)
    expect(await screen.findByText('PDF page 1 at 100%')).not.toBeNull()
    await screen.findByText('Page 1 of 3')

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }))

    await waitFor(() => expect(onViewChange).toHaveBeenLastCalledWith(
      'art_ABCDEFGHIJKLMNOPQRSTUVWX', 2, 1.1
    ))
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
    expect(await screen.findByText('PDF page 1 at 100%')).not.toBeNull()
  })

  it('presents DOCX as external-only while keeping native actions available', async () => {
    const docx = artifact({
      mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      filename: 'northstar-resume.docx',
      previewAvailable: false
    })
    const docxState = state([docx])
    const documents = installDocuments({ list: vi.fn(async () => docxState), refresh: vi.fn(async () => docxState) })

    render(<DocumentWorkspace hydrated job={job} onViewChange={vi.fn()} restoredArtifactId={null} restoredPage={1} restoredZoom={1} />)

    expect(await screen.findByText('DOCX stays external')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Open$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Reveal$/ }))
    fireEvent.click(screen.getByRole('button', { name: /^Export$/ }))

    await waitFor(() => expect(documents.open).toHaveBeenCalledWith(docx.artifactId))
    expect(documents.reveal).toHaveBeenCalledWith(docx.artifactId)
    expect(documents.export).toHaveBeenCalledWith(docx.artifactId)
    expect(documents.loadPdf).not.toHaveBeenCalled()
  })
})
