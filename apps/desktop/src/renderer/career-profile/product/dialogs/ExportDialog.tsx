import { Download } from 'lucide-react'
import { useState } from 'react'

import type { CareerProfileEvidenceMode } from '../../../../shared/contracts'
import type { CareerProfileProductController } from '../useCareerProfileProduct'
import { Dialog, DialogHeading } from './Dialog'

export function ExportDialog({ onClose, online, product }: {
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [mode, setMode] = useState<CareerProfileEvidenceMode | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const evidence = product.current?.sourceEvidence.filter(source => source.active) ?? []

  const incompleteChoice = mode === null || (mode === 'selected' && selected.length === 0)

  const save = async () => {
    if (!product.current || incompleteChoice || mode === null) return
    setSaving(true); setMessage('')
    try {
      const result = await product.exportProfile({
        evidenceMode: mode,
        expectedProfileRevision: product.current.profileRevision,
        selectedEvidenceIds: mode === 'selected' ? selected : []
      })
      setMessage(result.status === 'cancelled' ? 'Export cancelled. Nothing was written.' : `${result.filename} saved with ${result.includedEvidenceIds.length} Evidence source${result.includedEvidenceIds.length === 1 ? '' : 's'}.`)
    } catch {
      setMessage('The export could not be saved. Your Career Profile was not changed.')
    } finally { setSaving(false) }
  }

  return (
    <Dialog label="Export Career Profile" onClose={onClose}>
      <DialogHeading closeLabel="Close export" eyebrow="Portable current state" onClose={onClose} title="Export Career Profile" />
      <div className="career-product-dialog-body">
        <p className="career-product-plain-note">Every export includes current profile data and provenance. Choose separately whether the actual Evidence files travel with it.</p>
        <fieldset className="career-context-options" disabled={saving}>
          <legend>Evidence files to include</legend>
          <label><input aria-label="Profile only" checked={mode === 'profile_only'} name="career-profile-export-evidence-mode" onChange={() => { setMode('profile_only'); setSelected([]); setMessage('') }} type="radio" /><span><strong>Profile only</strong><small>Keep Evidence metadata, but do not copy any source files.</small></span></label>
          <label><input aria-label="Selected Evidence" checked={mode === 'selected'} disabled={evidence.length === 0} name="career-profile-export-evidence-mode" onChange={() => { setMode('selected'); setMessage('') }} type="radio" /><span><strong>Selected Evidence</strong><small>Copy only the source files you choose below.</small></span></label>
          <label><input checked={mode === 'all'} name="career-profile-export-evidence-mode" onChange={() => { setMode('all'); setSelected([]); setMessage('') }} type="radio" /><span><strong>All active Evidence</strong><small>Copy every currently available source file.</small></span></label>
        </fieldset>
        {mode === 'selected' ? <section className="career-context-selection">{evidence.map(source => <label key={source.evidenceId}><input aria-label={source.originalFilename} checked={selected.includes(source.evidenceId)} onChange={event => { setSelected(current => event.target.checked ? [...current, source.evidenceId] : current.filter(id => id !== source.evidenceId)); setMessage('') }} type="checkbox" /><span>{source.originalFilename}</span></label>)}</section> : null}
        {mode === null ? <p className="career-product-plain-note" role="status">Choose one Evidence-file option before saving the export.</p> : null}
        {mode === 'selected' && selected.length === 0 ? <p className="career-inline-alert" id="career-export-selection-status" role="status">Select at least one Evidence source to enable Save export.</p> : null}
        {message ? <p className={`career-feedback ${/could not/.test(message) ? 'error' : 'saved'}`} role={/could not/.test(message) ? 'alert' : 'status'}>{message}</p> : null}
      </div>
      <footer className="career-product-dialog-actions"><button aria-describedby={mode === 'selected' && selected.length === 0 ? 'career-export-selection-status' : undefined} className="career-primary-button" disabled={!online || saving || incompleteChoice} onClick={() => { void save() }} type="button"><Download aria-hidden="true" size={14} />{saving ? 'Saving…' : 'Save export'}</button></footer>
    </Dialog>
  )
}
