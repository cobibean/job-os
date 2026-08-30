import { RotateCcw } from 'lucide-react'

import type { CareerProfileItemSnapshot } from '../../../shared/contracts'
import type { useCareerProfileCollaboration } from './useCareerProfileCollaboration'

interface SnapshotRow {
  key: string
  label: string
  value: string
}

const snapshotLabels: Record<string, string> = {
  actorPrincipal: 'Changed by',
  area: 'Profile area',
  createdAt: 'Created',
  evidenceIds: 'Evidence links',
  itemId: 'Item ID',
  itemRevision: 'Item revision',
  provenance: 'Source',
  reviewStatus: 'Status',
  updatedAt: 'Updated',
  value: 'Value'
}

function readableLabel(path: string): string {
  const key = path.split('.').at(-1) ?? path
  const known = snapshotLabels[key]
  if (known) return known
  const words = key.replace(/([a-z])([A-Z])/g, '$1 $2').replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function readableValue(path: string, value: string | number | boolean | null): string {
  if (value === null) return 'None'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return String(value)
  if (path.endsWith('.kind') || path === 'area' || path === 'reviewStatus' || path.startsWith('provenance.')) {
    return value.replaceAll('_', ' ')
  }
  return value
}

function flattenSnapshotValue(path: string, value: unknown, rows: SnapshotRow[]): void {
  if (Array.isArray(value)) {
    if (value.length === 0) {
      rows.push({ key: path, label: readableLabel(path), value: 'None' })
      return
    }
    if (value.every(item => typeof item !== 'object' || item === null)) {
      rows.push({
        key: path,
        label: readableLabel(path),
        value: value.map(item => readableValue(path, item as string | number | boolean | null)).join(', ')
      })
      return
    }
    value.forEach((item, index) => flattenSnapshotValue(`${path}.${index + 1}`, item, rows))
    return
  }
  if (typeof value === 'object' && value !== null) {
    Object.entries(value).forEach(([key, item]) => {
      flattenSnapshotValue(path ? `${path}.${key}` : key, item, rows)
    })
    return
  }
  rows.push({
    key: path,
    label: readableLabel(path),
    value: readableValue(path, value as string | number | boolean | null)
  })
}

function ProposalSnapshot({ snapshot, emptyLabel }: {
  snapshot: CareerProfileItemSnapshot | null
  emptyLabel: string
}) {
  if (!snapshot) return <p className="career-agent-snapshot-empty">{emptyLabel}</p>
  const rows: SnapshotRow[] = []
  flattenSnapshotValue('value', snapshot.value, rows)
  flattenSnapshotValue('evidenceIds', snapshot.evidenceIds, rows)
  flattenSnapshotValue('area', snapshot.area, rows)
  flattenSnapshotValue('itemId', snapshot.itemId, rows)
  flattenSnapshotValue('itemRevision', snapshot.itemRevision, rows)
  flattenSnapshotValue('reviewStatus', snapshot.reviewStatus, rows)
  flattenSnapshotValue('actorPrincipal', snapshot.actorPrincipal, rows)
  flattenSnapshotValue('provenance', snapshot.provenance, rows)
  flattenSnapshotValue('createdAt', snapshot.createdAt, rows)
  flattenSnapshotValue('updatedAt', snapshot.updatedAt, rows)
  return (
    <dl className="career-agent-snapshot">
      {rows.map(row => (
        <div key={row.key}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  )
}

function agentDisplayName(principal: string): string {
  return principal
    .replace(/^agent:/, '')
    .split(/[-._]+/)
    .filter(Boolean)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ') || 'Connected agent'
}

export function CollaborationArea({ collaboration, online }: {
  collaboration: ReturnType<typeof useCareerProfileCollaboration>
  online: boolean
}) {
  return (
    <>
      {collaboration.proposals.length > 0 ? (
        <section aria-label="Agent changes to review" className="career-agent-review-list">
          {collaboration.proposals.map(proposal => (
            <article className="career-agent-review-card" key={proposal.proposalId}>
              <div className="career-agent-review-heading">
                <div>
                  <span className="career-kicker">Waiting for you</span>
                  <h3>Review {proposal.agentDisplayName}’s change</h3>
                </div>
                <span className="career-revision-badge">Based on revision {proposal.baseProfileRevision}</span>
              </div>
              <p className="career-agent-reason">{proposal.reason}</p>
              <p className="career-agent-review-note">{proposal.reviewReason}</p>
              <div className="career-agent-evidence">
                <strong>Evidence</strong>
                {proposal.evidenceIds.length > 0
                  ? <ul>{proposal.evidenceIds.map(evidenceId => <li key={evidenceId}>{evidenceId}</li>)}</ul>
                  : <p>No Evidence attached — that’s okay.</p>}
              </div>
              <div className="career-agent-change-grid">
                <section><h4>Before</h4><ProposalSnapshot emptyLabel="Nothing yet" snapshot={proposal.before} /></section>
                <section><h4>After</h4><ProposalSnapshot emptyLabel="Removed" snapshot={proposal.after} /></section>
              </div>
              <div className="career-agent-review-actions">
                <button
                  className="career-primary-button"
                  disabled={!online || collaboration.status === 'saving'}
                  onClick={() => { void collaboration.decide(proposal, 'accept') }}
                  type="button"
                >Accept exact change</button>
                <button
                  className="career-secondary-button"
                  disabled={!online || collaboration.status === 'saving'}
                  onClick={() => { void collaboration.decide(proposal, 'reject') }}
                  type="button"
                >Reject change</button>
              </div>
            </article>
          ))}
        </section>
      ) : null}

      {collaboration.directRevision ? (
        <section className="career-agent-direct-confirmation" role="status">
          <div>
            <strong>{agentDisplayName(collaboration.directRevision.actorPrincipal)} updated your Career Profile</strong>
            <p>{collaboration.directRevision.reason ?? 'An ordinary profile edit was applied directly and added to history.'}</p>
          </div>
          <button
            aria-label="Undo agent change"
            className="career-agent-undo-button"
            disabled={!online || collaboration.status === 'saving'}
            onClick={() => { void collaboration.undo(collaboration.directRevision!) }}
            type="button"
          ><RotateCcw aria-hidden="true" size={15} />Undo</button>
        </section>
      ) : null}

      {collaboration.message ? (
        <p className={`career-collaboration-message ${collaboration.status}`} role={collaboration.status === 'error' ? 'alert' : 'status'}>{collaboration.message}</p>
      ) : null}
    </>
  )
}
