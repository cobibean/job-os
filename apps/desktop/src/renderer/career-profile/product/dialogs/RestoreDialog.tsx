import { ArchiveRestore, FileCheck2 } from 'lucide-react'
import { useRef, useState } from 'react'

import type { CareerProfileArchiveSelection, CareerProfileBridge } from '../../../../shared/contracts'
import { formatBytes } from '../itemPresentation'
import type { CareerProfileProductController } from '../useCareerProfileProduct'
import { Dialog, DialogHeading } from './Dialog'

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

export function RestoreDialog({ hasActiveTurn, onClose, onRestored, online, product }: {
  hasActiveTurn: boolean
  onClose: () => void
  onRestored: () => Promise<boolean>
  online: boolean
  product: CareerProfileProductController
}) {
  const [archive, setArchive] = useState<CareerProfileArchiveSelection | null>(null)
  const [confirmation, setConfirmation] = useState('')
  const [status, setStatus] = useState<'ready' | 'choosing' | 'restoring' | 'error'>('ready')
  const [message, setMessage] = useState('')
  const pendingRequest = useRef<Parameters<CareerProfileBridge['restoreCareerProfile']>[0] | null>(null)

  const choose = async () => {
    setStatus('choosing'); setMessage('')
    try {
      const nextArchive = await product.chooseArchive()
      if (nextArchive?.archiveToken !== archive?.archiveToken) {
        setConfirmation('')
        pendingRequest.current = null
      }
      setArchive(nextArchive)
      setStatus('ready')
    } catch {
      setStatus('error'); setMessage('That archive could not be read. Choose a regular JobOS Career Profile ZIP smaller than 100 MiB.')
    }
  }
  const restore = async () => {
    if (!pendingRequest.current) {
      if (!archive || !product.current || confirmation !== 'RESTORE_CAREER_PROFILE_BASELINE') return
      pendingRequest.current = {
        archiveToken: archive.archiveToken,
        confirmation: 'RESTORE_CAREER_PROFILE_BASELINE',
        expectedProfileRevision: product.current.profileRevision,
        idempotencyKey: requestId('career_restore')
      }
    }
    setStatus('restoring'); setMessage('')
    try {
      await product.restoreBaseline(pendingRequest.current, onRestored)
      pendingRequest.current = null
      onClose()
    } catch {
      setStatus('error'); setMessage('JobOS could not confirm whether the baseline restore completed. The outcome is uncertain. Retry restore to safely check or complete it with the same request identity.')
    }
  }

  return (
    <Dialog label="Restore Career Profile baseline" onClose={onClose}>
      <DialogHeading closeLabel="Close restore" eyebrow="High-impact profile change" onClose={onClose} title="Restore Career Profile baseline" />
      <div className="career-product-dialog-body">
        <div className="career-restore-warning"><ArchiveRestore aria-hidden="true" size={20} /><div><strong>This creates a new baseline.</strong><span>The archive’s current state replaces the current Career Profile. The old timeline is not restored or mixed into it.</span></div></div>
        {hasActiveTurn ? <p className="career-feedback error" role="alert">Finish or stop the active agent turn before restoring the Career Profile.</p> : null}
        <button className="career-secondary-button" disabled={!online || hasActiveTurn || status === 'choosing' || status === 'restoring'} onClick={() => { void choose() }} type="button">{status === 'choosing' ? 'Choosing…' : 'Choose archive'}</button>
        {archive ? <div className="career-archive-selection"><FileCheck2 aria-hidden="true" size={18} /><div><strong>{archive.filename}</strong><span>{formatBytes(archive.byteCount)}</span></div></div> : null}
        <label className="career-field"><span>Type the restore confirmation</span><input aria-label="Type the restore confirmation" autoComplete="off" disabled={!archive || hasActiveTurn || status === 'restoring'} onChange={event => setConfirmation(event.target.value)} placeholder="RESTORE_CAREER_PROFILE_BASELINE" value={confirmation} /></label>
        {message ? <p className="career-feedback error" role="alert">{message}</p> : null}
      </div>
      <footer className="career-product-dialog-actions">
        {status === 'error' ? (
          <button aria-disabled={!online || hasActiveTurn} className="career-primary-button danger" key="retry-restore" onClick={() => { void restore() }} type="button">Retry restore</button>
        ) : (
          <button className="career-primary-button danger" disabled={!online || hasActiveTurn || !archive || confirmation !== 'RESTORE_CAREER_PROFILE_BASELINE' || status === 'restoring'} key="start-restore" onClick={() => { void restore() }} type="button">{status === 'restoring' ? 'Restoring…' : 'Restore as new baseline'}</button>
        )}
      </footer>
    </Dialog>
  )
}
