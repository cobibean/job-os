import { ChevronRight, FileCheck2, FileText, RefreshCw, Trash2, Upload } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'

import type {
  CareerProfileEvidence,
  CareerProfileEvidenceImportRequest,
  CareerProfileEvidenceKind
} from '../../../shared/contracts'
import { Dialog, DialogHeading } from './dialogs/Dialog'
import { evidenceProvenanceLabel, formatBytes, itemTitle, readableLabel } from './itemPresentation'
import type { CareerProfileProductController } from './useCareerProfileProduct'

interface ImportQueueEntry {
  error: string
  expectedProfileRevision: number | null
  file: File
  id: string
  idempotencyKey: string
  status: 'queued' | 'reading' | 'importing' | 'imported' | 'conflict' | 'error'
}

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

function guessEvidenceKind(filename: string): CareerProfileEvidenceKind {
  const lower = filename.toLowerCase()
  if (lower.includes('resume') || lower.includes('cv')) return 'resume'
  if (lower.includes('portfolio')) return 'portfolio'
  if (lower.startsWith('http') || lower.includes('citation')) return 'citation'
  return 'supporting_document'
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer)
  let binary = ''
  const chunkSize = 32_768
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, Math.min(offset + chunkSize, bytes.length)))
  }
  return btoa(binary)
}

function EvidenceDetails({ evidence, onClose, online, product }: {
  evidence: CareerProfileEvidence
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [removing, setRemoving] = useState(false)
  const linkedItems = product.current?.items.filter(item => item.evidenceIds.includes(evidence.evidenceId)) ?? []

  const remove = async () => {
    setRemoving(true)
    if (await product.removeEvidence(evidence.evidenceId)) onClose()
    setRemoving(false)
  }

  return (
    <Dialog className="drawer" label={`${evidence.originalFilename} details`} onClose={onClose}>
      <DialogHeading closeLabel="Close details" eyebrow="Evidence source" onClose={onClose} title={evidence.originalFilename} />
      <div className="career-product-dialog-body">
        <div className="career-product-provenance"><FileCheck2 aria-hidden="true" size={18} /><div><strong>{evidenceProvenanceLabel(evidence)}</strong><span>{readableLabel(evidence.provenance.sourceKind)} · {formatBytes(evidence.byteCount)}</span></div></div>
        <dl className="career-product-detail-list">
          <div><dt>Source label</dt><dd>{evidence.provenance.sourceLabel}</dd></div>
          <div><dt>Imported</dt><dd>{new Date(evidence.importedAt).toLocaleString()}</dd></div>
          <div><dt>Status</dt><dd>{evidence.active ? 'Available' : 'Unavailable'}</dd></div>
          <div><dt>Linked details</dt><dd>{linkedItems.length === 0 ? 'None' : linkedItems.map(itemTitle).join(', ')}</dd></div>
        </dl>
        <p className="career-product-plain-note">Evidence supports context and provenance. Not having Evidence is never treated as a defect or used to rate your career story.</p>
      </div>
      <footer className="career-product-dialog-actions">
        <button className="career-secondary-button danger" disabled={!online || removing || !evidence.active} onClick={() => { void remove() }} type="button"><Trash2 aria-hidden="true" size={14} />{removing ? 'Removing…' : 'Remove from active use'}</button>
      </footer>
    </Dialog>
  )
}

export function EvidenceArea({ active, online, product }: { active: boolean; online: boolean; product: CareerProfileProductController }) {
  const [queue, setQueue] = useState<ImportQueueEntry[]>([])
  const [detail, setDetail] = useState<CareerProfileEvidence | null>(null)
  const processing = useRef(false)
  const evidence = product.current?.sourceEvidence ?? []

  useEffect(() => {
    if (!active) setDetail(null)
  }, [active])

  const addFiles = useCallback((files: File[]) => {
    if (!online) return
    setQueue(current => [
      ...current,
      ...files.map(file => ({
        error: file.size > 10 * 1024 * 1024 ? 'Files must be 10 MiB or smaller.' : '',
        expectedProfileRevision: null,
        file,
        id: requestId('evidence_queue'),
        idempotencyKey: requestId('career_evidence'),
        status: file.size > 10 * 1024 * 1024 ? 'error' as const : 'queued' as const
      }))
    ])
  }, [online])

  useEffect(() => {
    if (!online || product.status === 'saving' || processing.current) return
    const next = queue.find(entry => entry.status === 'queued')
    if (!next) return
    processing.current = true
    const run = async () => {
      setQueue(current => current.map(entry => entry.id === next.id ? { ...entry, status: 'reading', error: '' } : entry))
      try {
        const buffer = await next.file.arrayBuffer()
        if (buffer.byteLength < 1 || buffer.byteLength > 10 * 1024 * 1024) throw new Error('Files must be between 1 byte and 10 MiB.')
        const expectedProfileRevision = next.expectedProfileRevision ?? product.current?.profileRevision
        if (expectedProfileRevision === undefined) throw new Error('The current Career Profile revision is unavailable. Reconnect and try again.')
        const request: CareerProfileEvidenceImportRequest = {
          capturedAt: null,
          contentBase64: arrayBufferToBase64(buffer),
          expectedProfileRevision,
          idempotencyKey: next.idempotencyKey,
          mediaType: next.file.type || 'application/octet-stream',
          originalFilename: next.file.name,
          sourceKind: guessEvidenceKind(next.file.name),
          sourceLabel: next.file.name
        }
        setQueue(current => current.map(entry => entry.id === next.id ? { ...entry, expectedProfileRevision, status: 'importing' } : entry))
        const result = await product.importEvidence(request)
        setQueue(current => current.map(entry => entry.id !== next.id ? entry : result === 'saved'
          ? { ...entry, expectedProfileRevision, status: 'imported', error: '' }
          : result === 'conflict'
            ? {
                ...entry,
                expectedProfileRevision,
                status: 'conflict',
                error: 'Your profile changed first. This confirmed conflict did not overwrite anything; choose whether to import against the latest profile.'
              }
            : {
                ...entry,
                expectedProfileRevision,
                status: 'error',
                error: 'This source could not be imported. Retry preserves the exact original revision and request identity.'
              }))
      } catch (error) {
        setQueue(current => current.map(entry => entry.id === next.id ? {
          ...entry,
          status: 'error',
          error: error instanceof Error ? error.message : 'This source could not be imported.'
        } : entry))
      } finally {
        processing.current = false
      }
    }
    void run()
  }, [online, product.current?.profileRevision, product.importEvidence, product.status, queue])

  const selectFiles = (event: ChangeEvent<HTMLInputElement>) => {
    if (!online) return
    addFiles(Array.from(event.target.files ?? []))
    event.target.value = ''
  }
  const dropFiles = (event: DragEvent<HTMLLabelElement>) => {
    if (!online) return
    event.preventDefault()
    addFiles(Array.from(event.dataTransfer.files))
  }
  const retry = (entry: ImportQueueEntry) => {
    if (!online) return
    setQueue(current => current.map(candidate => candidate.id === entry.id ? {
      ...candidate,
      error: '',
      status: 'queued'
    } : candidate))
  }
  const retryAgainstLatest = (entry: ImportQueueEntry) => {
    if (!online || !product.current) return
    setQueue(current => current.map(candidate => candidate.id === entry.id ? {
      ...candidate,
      error: '',
      expectedProfileRevision: product.current!.profileRevision,
      idempotencyKey: requestId('career_evidence'),
      status: 'queued'
    } : candidate))
  }

  return (
    <section className="career-product-area career-evidence-area" aria-label="My Evidence">
      <label
        aria-disabled={!online}
        className={`career-evidence-dropzone ${!online ? 'disabled' : ''}`}
        onDragOver={online ? event => event.preventDefault() : undefined}
        onDrop={online ? dropFiles : undefined}
      >
        <Upload aria-hidden="true" size={24} />
        <strong>Drop resumes, portfolios, or supporting files here</strong>
        <span>{online ? 'Or choose files. Each source imports independently, up to 10 MiB.' : 'Offline — saved Evidence remains readable. Reconnect before choosing or dropping files.'}</span>
        <input aria-label="Choose Evidence files" disabled={!online} multiple onChange={online ? selectFiles : undefined} type="file" />
      </label>
      {queue.length > 0 ? (
        <section aria-label="Evidence import progress" aria-live="polite" className="career-import-queue">
          <div className="career-product-area-heading compact"><div><span className="career-kicker">Import progress</span><h3>Sources in this batch</h3></div></div>
          <ul>
            {queue.map(entry => {
              const progressText = entry.status === 'imported'
                ? `Imported ${entry.file.name}`
                : entry.status === 'reading'
                  ? 'Reading file…'
                  : entry.status === 'importing'
                    ? 'Importing…'
                    : entry.status === 'queued'
                      ? 'Queued'
                      : entry.error
              return (
                <li className={entry.status} key={entry.id}>
                  <FileText aria-hidden="true" size={16} />
                  <div>
                    <strong>{entry.file.name}</strong>
                    {entry.status === 'error' || entry.status === 'conflict'
                      ? <span aria-label={`${entry.file.name} import ${entry.status}`} role="alert">{progressText}</span>
                      : <span>{progressText}</span>}
                  </div>
                  {entry.status === 'error' ? <button aria-label={`Retry ${entry.file.name}`} className="career-secondary-button" disabled={!online} onClick={() => retry(entry)} type="button"><RefreshCw aria-hidden="true" size={13} />Retry</button> : null}
                  {entry.status === 'conflict' ? <button aria-label={`Import ${entry.file.name} against latest profile`} className="career-secondary-button" disabled={!online} onClick={() => retryAgainstLatest(entry)} type="button"><RefreshCw aria-hidden="true" size={13} />Import against latest</button> : null}
                  {entry.status === 'imported' ? <FileCheck2 aria-label="Imported" size={17} /> : null}
                </li>
              )
            })}
          </ul>
        </section>
      ) : null}
      <div className="career-product-area-heading">
        <div><span className="career-kicker">Your sources</span><h3>Evidence library</h3><p>Evidence is optional. It helps explain where a detail came from; it never makes your profile better or worse.</p></div>
      </div>
      {evidence.length === 0 ? (
        <div className="career-product-empty"><FileText aria-hidden="true" size={22} /><strong>No Evidence yet</strong><p>Your Career Profile still works without it. Add a source only when it is useful.</p></div>
      ) : (
        <div className="career-product-card-grid">
          {evidence.map(source => (
            <button aria-label={`${source.originalFilename} details`} className={`career-product-card evidence ${source.active ? '' : 'inactive'}`} key={source.evidenceId} onClick={() => setDetail(source)} type="button">
              <div><span className="career-product-kind">{readableLabel(source.provenance.sourceKind)}</span><span className={`career-product-review ${source.active ? 'accepted' : 'inactive'}`}>{source.active ? 'Available' : 'Unavailable'}</span></div>
              <strong>{source.originalFilename}</strong>
              <p>{source.provenance.sourceLabel}</p>
              <footer><span>{formatBytes(source.byteCount)}</span><span>{new Date(source.importedAt).toLocaleDateString()}</span><ChevronRight aria-hidden="true" size={16} /></footer>
            </button>
          ))}
        </div>
      )}
      {detail ? <EvidenceDetails evidence={detail} onClose={() => setDetail(null)} online={online} product={product} /> : null}
    </section>
  )
}
