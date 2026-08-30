import { useEffect, useRef, useState } from 'react'

import type { CareerProfileArea, CareerProfileItemSnapshot } from '../../../shared/contracts'
import { itemKind, itemSpecs, specsByKind, type EditableItemKind } from './itemSpecs'
import { buildItemValue, draftFor, itemTitle, preferenceGuidance, readableLabel, readableValue, validateItemValue } from './itemPresentation'
import type { CareerProfileProductController } from './useCareerProfileProduct'
import { Dialog, DialogHeading } from './dialogs/Dialog'

export function ItemEditor({ area, expectedProfileRevision, item, onClose, online, product }: {
  area: Exclude<CareerProfileArea, 'my_evidence'>
  expectedProfileRevision: number
  item: CareerProfileItemSnapshot | null
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const availableSpecs = itemSpecs.filter(spec => spec.area === area)
  const initialKind = (item ? itemKind(item) : null) ?? availableSpecs[0]!.kind
  const [kind, setKind] = useState<EditableItemKind>(initialKind)
  const spec = specsByKind.get(kind)!
  const [draft, setDraft] = useState<Record<string, string>>(() => draftFor(item, spec))
  const [selectedEvidence, setSelectedEvidence] = useState<string[]>(item?.evidenceIds ?? [])
  const [validation, setValidation] = useState('')
  const evidence = product.current?.sourceEvidence.filter(source => source.active) ?? []
  const guidance = preferenceGuidance(kind, draft)
  const conflict = product.itemConflict
  const conflictAnnouncement = useRef<HTMLElement>(null)

  useEffect(() => {
    if (conflict) conflictAnnouncement.current?.focus()
  }, [conflict])

  const close = () => {
    product.dismissItemConflict()
    onClose()
  }

  const evidenceName = (evidenceId: string) => (
    product.current?.sourceEvidence.find(source => source.evidenceId === evidenceId)?.originalFilename
      ?? `${evidenceId} (unavailable)`
  )

  const changeKind = (nextKind: EditableItemKind) => {
    const next = specsByKind.get(nextKind)!
    setKind(nextKind)
    setDraft(draftFor(null, next))
    setValidation('')
  }

  const save = async () => {
    const value = buildItemValue(spec, draft)
    const error = validateItemValue(spec, value)
    if (error) {
      setValidation(error)
      return
    }
    setValidation('')
    if (await product.saveItem(item, value, selectedEvidence, expectedProfileRevision)) onClose()
  }

  return (
    <Dialog label={item ? `Edit ${itemTitle(item)}` : `Add ${area === 'my_career' ? 'career detail' : 'preference'}`} onClose={close}>
      <DialogHeading
        closeLabel="Close editor"
        eyebrow={item ? 'Edit saved detail' : 'Add to your profile'}
        onClose={close}
        title={item ? itemTitle(item) : `Add ${area === 'my_career' ? 'career detail' : 'preference'}`}
      />
      <form className="career-product-editor" onSubmit={event => { event.preventDefault(); void save() }}>
        <label className="career-field">
          <span>Detail type</span>
          <select aria-label="Detail type" disabled={Boolean(item) || product.status === 'saving'} onChange={event => changeKind(event.target.value as EditableItemKind)} value={kind}>
            {availableSpecs.map(candidate => <option key={candidate.kind} value={candidate.kind}>{candidate.label}</option>)}
          </select>
        </label>
        <div className="career-product-editor-grid">
          {spec.fields.map(field => (
            <label className={`career-field ${field.kind === 'textarea' || field.kind === 'list' ? 'wide' : ''}`} key={field.key}>
              <span>{field.label}</span>
              {field.kind === 'textarea' || field.kind === 'list' ? (
                <textarea
                  aria-label={field.label}
                  disabled={product.status === 'saving'}
                  onChange={event => setDraft(current => ({ ...current, [field.key]: event.target.value }))}
                  placeholder={field.placeholder}
                  rows={field.kind === 'list' ? 3 : 4}
                  value={draft[field.key] ?? ''}
                />
              ) : field.kind === 'select' ? (
                <select aria-label={field.label} disabled={product.status === 'saving'} onChange={event => setDraft(current => ({ ...current, [field.key]: event.target.value }))} value={draft[field.key] ?? ''}>
                  {field.options?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                </select>
              ) : (
                <input
                  aria-label={field.label}
                  disabled={product.status === 'saving'}
                  min={field.kind === 'number' ? 0 : undefined}
                  onChange={event => setDraft(current => ({ ...current, [field.key]: event.target.value }))}
                  placeholder={field.placeholder}
                  type={field.kind === 'number' ? 'number' : 'text'}
                  value={draft[field.key] ?? ''}
                />
              )}
            </label>
          ))}
        </div>
        {guidance ? (
          <section aria-label={`${spec.label} behavior`} className="career-product-plain-note" role="region">
            <p><strong>Interpretation</strong><span>{guidance.interpretation}</span></p>
            <p><strong>Example</strong><span>{guidance.example}</span></p>
            <p><strong>Affects</strong><span>{guidance.affectedBehavior}</span></p>
          </section>
        ) : null}
        <fieldset className="career-product-evidence-picker">
          <legend>Link Evidence <small>Optional</small></legend>
          {evidence.length === 0 ? <p>No Evidence is available yet. You can save this detail without it.</p> : evidence.map(source => (
            <label key={source.evidenceId}>
              <input
                checked={selectedEvidence.includes(source.evidenceId)}
                disabled={product.status === 'saving'}
                onChange={event => setSelectedEvidence(current => event.target.checked ? [...current, source.evidenceId] : current.filter(id => id !== source.evidenceId))}
                type="checkbox"
              />
              <span>{source.originalFilename}</span>
            </label>
          ))}
        </fieldset>
        {validation ? <p className="career-inline-alert" role="alert">{validation}</p> : null}
        {conflict ? (
          <section aria-label="Resolve stale edit" aria-live="assertive" className="career-conflict-card" ref={conflictAnnouncement} role="alert" tabIndex={-1}>
            <h4>Choose what JobOS should keep</h4>
            <p>A newer version was saved before this draft. Nothing has been overwritten.</p>
            <div className="career-agent-change-grid">
              <section>
                <h5>Current saved version</h5>
                {conflict.latestItem ? <dl className="career-product-detail-list">{Object.entries(conflict.latestItem.value).filter(([key]) => key !== 'kind').map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{readableValue(value)}</dd></div>)}</dl> : <p>The original detail is no longer in the latest profile.</p>}
                <strong>Current linked sources</strong>
                {conflict.latestItem?.evidenceIds.length ? <ul>{conflict.latestItem.evidenceIds.map(evidenceId => <li key={evidenceId}>{evidenceName(evidenceId)}</li>)}</ul> : <p>None</p>}
              </section>
              <section>
                <h5>Your proposed draft</h5>
                <dl className="career-product-detail-list">{Object.entries(conflict.proposedValue).filter(([key]) => key !== 'kind').map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{readableValue(value)}</dd></div>)}</dl>
                <strong>Proposed linked sources</strong>
                {conflict.proposedEvidenceIds.length ? <ul>{conflict.proposedEvidenceIds.map(evidenceId => <li key={evidenceId}>{evidenceName(evidenceId)}</li>)}</ul> : <p>None</p>}
              </section>
            </div>
            <div className="career-conflict-actions">
              <button className="career-secondary-button" onClick={() => { product.keepItemConflict(); onClose() }} type="button">Keep current</button>
              <button className="career-secondary-button" disabled={!online || product.status === 'saving'} onClick={() => { void product.reapplyItemConflict().then(saved => { if (saved) onClose() }) }} type="button">Reapply my change</button>
              {conflict.canPreserveBoth ? <button className="career-secondary-button" disabled={!online || product.status === 'saving'} onClick={() => { void product.preserveBothItemConflict().then(saved => { if (saved) onClose() }) }} type="button">Preserve both</button> : null}
            </div>
          </section>
        ) : product.status === 'error' ? <p className="career-feedback error" role="alert">{product.message}</p> : null}
        <footer className="career-product-dialog-actions">
          <button className="career-primary-button" disabled={!online || product.status === 'saving'} type="submit">{product.status === 'saving' ? 'Saving…' : 'Save detail'}</button>
          <button className="career-secondary-button" disabled={product.status === 'saving'} onClick={close} type="button">Cancel</button>
        </footer>
      </form>
    </Dialog>
  )
}
