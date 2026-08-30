import { Link2, ShieldCheck, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { CareerProfileItemSnapshot } from '../../../shared/contracts'
import { itemKind, specsByKind } from './itemSpecs'
import { draftFor, itemTitle, preferenceGuidance, provenanceLabel, readableLabel, readableValue } from './itemPresentation'
import type { CareerProfileProductController } from './useCareerProfileProduct'
import { Dialog, DialogHeading } from './dialogs/Dialog'

export function ItemDetails({ item, online, onClose, onEdit, product }: {
  item: CareerProfileItemSnapshot
  online: boolean
  onClose: () => void
  onEdit: () => void
  product: CareerProfileProductController
}) {
  const [removing, setRemoving] = useState(false)
  const linked = product.current?.sourceEvidence.filter(source => item.evidenceIds.includes(source.evidenceId)) ?? []
  const values = Object.entries(item.value).filter(([key]) => key !== 'kind')
  const kind = itemKind(item)
  const spec = kind ? specsByKind.get(kind) : undefined
  const guidance = item.area === 'what_im_looking_for' && kind && spec
    ? preferenceGuidance(kind, draftFor(item, spec))
    : null

  const remove = async () => {
    if (!online || removing) return
    setRemoving(true)
    if (await product.removeItem(item)) onClose()
    setRemoving(false)
  }

  return (
    <Dialog className="drawer" label={`${itemTitle(item)} details`} onClose={onClose}>
      <DialogHeading closeLabel="Close details" eyebrow={specsByKind.get(itemKind(item)!)?.label ?? 'Career detail'} onClose={onClose} title={itemTitle(item)} />
      <div className="career-product-dialog-body">
        <div className="career-product-provenance">
          <ShieldCheck aria-hidden="true" size={18} />
          <div><strong>{provenanceLabel(item)}</strong><span>{readableLabel(item.reviewStatus)} · Revision {item.itemRevision} · Updated {new Date(item.updatedAt).toLocaleDateString()}</span></div>
        </div>
        <dl className="career-product-detail-list">
          {values.map(([key, value]) => <div key={key}><dt>{readableLabel(key)}</dt><dd>{readableValue(value)}</dd></div>)}
        </dl>
        {guidance && spec ? (
          <section aria-label={`${spec.label} behavior`} className="career-product-plain-note" role="region">
            <p><strong>Interpretation</strong><span>{guidance.interpretation}</span></p>
            <p><strong>Example</strong><span>{guidance.example}</span></p>
            <p><strong>Affects</strong><span>{guidance.affectedBehavior}</span></p>
          </section>
        ) : null}
        <section className="career-product-linked-evidence">
          <h4><Link2 aria-hidden="true" size={15} />Linked Evidence</h4>
          {linked.length === 0
            ? <p>No Evidence linked — that’s okay. Evidence is optional and is never treated as a quality score.</p>
            : <ul>{linked.map(source => <li key={source.evidenceId}>{source.originalFilename}</li>)}</ul>}
        </section>
      </div>
      <footer className="career-product-dialog-actions">
        <button className="career-primary-button" disabled={!online} onClick={onEdit} type="button">Edit detail</button>
        <button className="career-secondary-button danger" disabled={!online || removing} onClick={() => { void remove() }} type="button"><Trash2 aria-hidden="true" size={14} />{removing ? 'Removing…' : 'Remove detail'}</button>
      </footer>
    </Dialog>
  )
}
