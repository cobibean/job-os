import { Clock3 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import type { CareerProfileChangeRevision } from '../../../../shared/contracts'
import { readableLabel } from '../itemPresentation'
import type { CareerProfileProductController } from '../useCareerProfileProduct'
import { Dialog, DialogHeading } from './Dialog'

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

export function HistoryDialog({ onClose, online, product }: {
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [revisions, setRevisions] = useState<CareerProfileChangeRevision[] | null>(null)
  const [message, setMessage] = useState('')
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setMessage('')
    try { setRevisions((await product.getChangeHistory()).revisions) } catch { setMessage('Career Profile history could not load. Try again.') }
  }, [product.getChangeHistory])
  useEffect(() => { void load() }, [load])

  const undo = async (revision: CareerProfileChangeRevision) => {
    if (!product.current) return
    setSaving(true); setMessage('')
    try {
      await product.undoChange(revision.revisionId, {
        expectedProfileRevision: product.current.profileRevision,
        idempotencyKey: requestId('career_history_undo')
      })
      await product.load(false)
      await load()
      setMessage('Change undone as a new revision.')
    } catch {
      await product.load(false)
      setMessage('That change could not be undone without overwriting newer work. The latest profile is shown.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog className="drawer" label="Career Profile history" onClose={onClose}>
      <DialogHeading closeLabel="Close Career Profile history" eyebrow="Change log" onClose={onClose} title="Career Profile history" />
      <div className="career-product-dialog-body">
        {message ? <p className={`career-feedback ${/undone/.test(message) ? 'saved' : 'error'}`} role={/undone/.test(message) ? 'status' : 'alert'}>{message}</p> : null}
        {!revisions ? <p role="status">Loading history…</p> : revisions.length === 0 ? <div className="career-product-empty"><Clock3 aria-hidden="true" size={20} /><strong>No complete-profile changes yet</strong><p>Your work-arrangement history remains available in its own panel.</p></div> : <ol className="career-product-history">{revisions.map(revision => <li key={revision.revisionId}><div><strong>{readableLabel(revision.operation.replaceAll('.', ' '))}</strong><span>{revision.reason ?? `${readableLabel(revision.actorKind)} change`}</span><small>Revision {revision.profileRevision} · {new Date(revision.createdAt).toLocaleString()}</small></div>{revision.undoable ? <button className="career-secondary-button" disabled={!online || saving} onClick={() => { void undo(revision) }} type="button">Undo change</button> : <span className="career-product-not-undoable">Baseline</span>}</li>)}</ol>}
      </div>
    </Dialog>
  )
}
