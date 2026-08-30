import { ChevronDown, ChevronRight, FilePlus2, Plus, Search, ShieldCheck, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { CareerProfileArea, CareerProfileItemSnapshot } from '../../../shared/contracts'
import { Dialog, DialogHeading } from './dialogs/Dialog'
import { ItemDetails } from './ItemDetails'
import { ItemEditor } from './ItemEditor'
import {
  areaLabels,
  careerGroupDescriptions,
  careerGroupLabels,
  itemKind,
  itemSpecs,
  specsByKind,
  type EditableItemKind
} from './itemSpecs'
import { itemSearchText, itemSummary, itemTitle, normalizeCareerSearch, provenanceLabel, readableLabel } from './itemPresentation'
import type { CareerProfileProductController } from './useCareerProfileProduct'

interface ItemEditorSession {
  expectedProfileRevision: number
  item: CareerProfileItemSnapshot | null
}

export function ItemArea({ active, area, online, product }: {
  active: boolean
  area: Exclude<CareerProfileArea, 'my_evidence'>
  online: boolean
  product: CareerProfileProductController
}) {
  const [detailItemId, setDetailItemId] = useState<string | null>(null)
  const [editorSession, setEditorSession] = useState<ItemEditorSession | null>(null)
  const [query, setQuery] = useState('')
  const [collapsedKinds, setCollapsedKinds] = useState<Set<EditableItemKind>>(() => new Set())
  const items = (product.current?.items ?? []).filter(item => item.area === area && itemKind(item) !== null)
  const normalizedQuery = normalizeCareerSearch(query)
  const filteredItems = area === 'my_career' && normalizedQuery
    ? items.filter(item => itemSearchText(item).includes(normalizedQuery))
    : items
  const careerGroups = useMemo(() => itemSpecs
    .filter(spec => spec.area === 'my_career')
    .map(spec => ({
      description: careerGroupDescriptions[spec.kind] ?? '',
      kind: spec.kind,
      label: careerGroupLabels[spec.kind] ?? spec.label,
      items: filteredItems.filter(item => itemKind(item) === spec.kind)
    }))
    .filter(group => group.items.length > 0), [filteredItems])
  const detailItem = detailItemId
    ? product.current?.items.find(item => item.itemId === detailItemId) ?? null
    : null
  const areaName = area === 'my_career' ? 'career detail' : 'preference'

  const openEditor = (item: CareerProfileItemSnapshot | null) => {
    const expectedProfileRevision = product.current?.profileRevision
    if (expectedProfileRevision === undefined) return
    setEditorSession({ expectedProfileRevision, item })
  }

  const toggleGroup = (kind: EditableItemKind) => {
    setCollapsedKinds(current => {
      const next = new Set(current)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  const itemCard = (item: CareerProfileItemSnapshot) => (
    <button aria-label={`${itemTitle(item)} details`} className="career-product-card" key={item.itemId} onClick={() => setDetailItemId(item.itemId)} type="button">
      <div><span className="career-product-kind">{specsByKind.get(itemKind(item)!)?.label}</span><span className={`career-product-review ${item.reviewStatus}`}>{readableLabel(item.reviewStatus)}</span></div>
      <strong>{itemTitle(item)}</strong>
      <p>{itemSummary(item)}</p>
      <footer><span>{provenanceLabel(item)}</span><span>{item.evidenceIds.length} Evidence</span><ChevronRight aria-hidden="true" size={16} /></footer>
    </button>
  )

  useEffect(() => {
    if (!active) {
      setDetailItemId(null)
      setEditorSession(null)
    }
  }, [active])

  return (
    <section className="career-product-area" aria-label={areaLabels[area]}>
      <div className="career-product-area-heading">
        <div>
          <span className="career-kicker">{area === 'my_career' ? 'Your story' : 'Beyond work arrangement'}</span>
          <h3>{area === 'my_career' ? 'Career details' : 'Other preferences'}</h3>
          <p>{area === 'my_career' ? 'The experience, skills, and positioning you want JobOS to remember.' : 'Roles, location, compensation, priorities, and boundaries—each kept as its own clear choice.'}</p>
        </div>
        <button className="career-primary-button" disabled={!online || !product.current} onClick={() => openEditor(null)} type="button"><Plus aria-hidden="true" size={15} />Add {areaName}</button>
      </div>
      {area === 'my_career' ? (
        <aside aria-label="How JobOS uses My Career" className="career-product-usage-card" role="region">
          <ShieldCheck aria-hidden="true" size={20} />
          <div>
            <span className="career-kicker">How JobOS uses this</span>
            <strong>Accepted details become shared career context.</strong>
            <p>They help JobOS when researching roles, comparing opportunities, and drafting documents. They inform the work—they are not a completeness score or automatic decision.</p>
          </div>
        </aside>
      ) : null}
      {area === 'my_career' && items.length > 0 ? (
        <div className="career-product-search">
          <label>
            <Search aria-hidden="true" size={16} />
            <input aria-label="Search career details" onChange={event => setQuery(event.target.value)} placeholder="Search skills, roles, projects, or details" type="search" value={query} />
          </label>
          {query ? <button aria-label="Clear search field" onClick={() => setQuery('')} type="button"><X aria-hidden="true" size={15} /></button> : null}
          <span role="status">{filteredItems.length} of {items.length} details</span>
        </div>
      ) : null}
      {items.length === 0 ? (
        <div className="career-product-empty">
          <FilePlus2 aria-hidden="true" size={22} />
          <strong>No {area === 'my_career' ? 'career details' : 'other preferences'} yet</strong>
          <p>Start with one useful fact. There is no completeness score to chase.</p>
        </div>
      ) : area === 'my_career' && filteredItems.length === 0 ? (
        <div className="career-product-empty">
          <Search aria-hidden="true" size={22} />
          <strong>No career details match your search</strong>
          <p>Try a skill, role, company, project, or another word from the detail.</p>
          <button className="career-secondary-button" onClick={() => setQuery('')} type="button">Clear search</button>
        </div>
      ) : area === 'my_career' ? (
        <div className="career-product-groups">
          {careerGroups.map(group => {
            const expanded = normalizedQuery.length > 0 || !collapsedKinds.has(group.kind)
            const groupId = `career-product-group-${group.kind}`
            return (
              <section className="career-product-group" key={group.kind}>
                <button
                  aria-controls={groupId}
                  aria-expanded={expanded}
                  aria-label={`${group.label}, ${group.items.length} ${group.items.length === 1 ? 'detail' : 'details'}`}
                  className="career-product-group-toggle"
                  onClick={() => toggleGroup(group.kind)}
                  type="button"
                >
                  <div><span><strong>{group.label}</strong><small>{group.items.length}</small></span><p>{group.description}</p></div>
                  <ChevronDown aria-hidden="true" size={18} />
                </button>
                {expanded ? <div className="career-product-card-grid" id={groupId}>{group.items.map(itemCard)}</div> : null}
              </section>
            )
          })}
        </div>
      ) : (
        <div className="career-product-card-grid">
          {items.map(itemCard)}
        </div>
      )}
      {detailItem ? <ItemDetails
        item={detailItem}
        online={online}
        onClose={() => setDetailItemId(null)}
        onEdit={() => { openEditor(detailItem); setDetailItemId(null) }}
        product={product}
      /> : null}
      {detailItemId && !detailItem ? (
        <Dialog label="Career detail no longer available" onClose={() => setDetailItemId(null)}>
          <DialogHeading closeLabel="Close missing detail" eyebrow="Profile updated" onClose={() => setDetailItemId(null)} title="Career detail no longer available" />
          <div className="career-product-dialog-body"><p className="career-feedback error" role="alert">This detail is no longer in the current Career Profile.</p></div>
        </Dialog>
      ) : null}
      {editorSession ? <ItemEditor
        area={area}
        expectedProfileRevision={editorSession.expectedProfileRevision}
        item={editorSession.item}
        onClose={() => setEditorSession(null)}
        online={online}
        product={product}
      /> : null}
    </section>
  )
}
