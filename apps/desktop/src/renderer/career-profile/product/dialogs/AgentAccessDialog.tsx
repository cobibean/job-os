import { FileCheck2, ShieldCheck } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type {
  CareerProfileArea,
  CareerProfileContextMode,
  CareerProfileContextPreview,
  CareerProfileContextScope,
  ConnectedCareerProfileAgent
} from '../../../../shared/contracts'
import { areaLabels } from '../itemSpecs'
import { itemTitle } from '../itemPresentation'
import type { CareerProfileProductController } from '../useCareerProfileProduct'
import { Dialog, DialogHeading } from './Dialog'

function requestId(prefix: string): string {
  const id = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)
  return `${prefix}_${id}`
}

export function AgentAccessDialog({ onClose, online, product }: {
  onClose: () => void
  online: boolean
  product: CareerProfileProductController
}) {
  const [agents, setAgents] = useState<ConnectedCareerProfileAgent[]>([])
  const [agentId, setAgentId] = useState('')
  const [scope, setScope] = useState<CareerProfileContextScope | null>(null)
  const [mode, setMode] = useState<CareerProfileContextMode>('none')
  const [selectedAreas, setSelectedAreas] = useState<CareerProfileArea[]>([])
  const [selectedItems, setSelectedItems] = useState<string[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'saving' | 'error'>('loading')
  const [message, setMessage] = useState('')
  const [messageKind, setMessageKind] = useState<'info' | 'error'>('info')
  const [preview, setPreview] = useState<CareerProfileContextPreview | null>(null)
  const pendingKey = useRef('')

  useEffect(() => {
    let cancelled = false
    void product.listConnectedAgents().then(result => {
      if (cancelled) return
      const active = result.filter(agent => agent.active)
      setAgents(active)
      setAgentId(active[0]?.agentId ?? '')
      if (active.length === 0) setStatus('ready')
    }).catch(() => {
      if (!cancelled) { setStatus('error'); setMessageKind('error'); setMessage('Connected agents could not load. Try again after reconnecting.') }
    })
    return () => { cancelled = true }
  }, [product.listConnectedAgents])

  useEffect(() => {
    setScope(null)
    setMode('none')
    setSelectedAreas([])
    setSelectedItems([])
    setPreview(null)
    setMessage('')
    setMessageKind('info')
    pendingKey.current = ''
    if (!agentId) return
    let cancelled = false
    setStatus('loading')
    void product.getAgentContext(agentId).then(result => {
      if (cancelled) return
      setScope(result)
      setMode(result.mode)
      setSelectedAreas(result.selectedAreas)
      setSelectedItems(result.selectedItemIds)
      setPreview(null)
      setMessage('')
      setMessageKind('info')
      setStatus('ready')
      pendingKey.current = ''
    }).catch(() => {
      if (!cancelled) { setStatus('error'); setMessageKind('error'); setMessage('This agent’s Career Profile access could not load.') }
    })
    return () => { cancelled = true }
  }, [agentId, product.getAgentContext])

  const scopeReady = status === 'ready' && scope?.agentId === agentId

  const chooseMode = (nextMode: CareerProfileContextMode) => {
    setMode(nextMode)
    setMessage('')
    setMessageKind('info')
    setPreview(null)
    pendingKey.current = ''
    if (nextMode !== 'selected') { setSelectedAreas([]); setSelectedItems([]) }
  }
  const toggleArea = (area: CareerProfileArea, checked: boolean) => {
    setSelectedAreas(current => checked ? [...current, area] : current.filter(candidate => candidate !== area))
    setPreview(null)
    setMessage('')
    setMessageKind('info')
    pendingKey.current = ''
  }
  const toggleItem = (itemId: string, checked: boolean) => {
    setSelectedItems(current => checked ? [...current, itemId] : current.filter(candidate => candidate !== itemId))
    setPreview(null)
    setMessage('')
    setMessageKind('info')
    pendingKey.current = ''
  }

  const save = async () => {
    if (!product.current || !scopeReady || !scope || !agentId) return
    if (mode === 'selected' && selectedAreas.length === 0 && selectedItems.length === 0) {
      setStatus('ready'); setMessageKind('error'); setMessage('Choose at least one whole area or exact detail.')
      return
    }
    if (!pendingKey.current) pendingKey.current = requestId('career_context')
    setStatus('saving'); setMessage(''); setMessageKind('info')
    try {
      const result = await product.updateAgentContext(agentId, {
        expectedAuthorityEpoch: product.current.authorityEpoch,
        expectedProfileRevision: product.current.profileRevision,
        idempotencyKey: pendingKey.current,
        mode,
        selectedAreas,
        selectedItemIds: selectedItems
      })
      pendingKey.current = ''
      setScope(result)
      setMode(result.mode)
      setSelectedAreas(result.selectedAreas)
      setSelectedItems(result.selectedItemIds)
      setPreview(null)
      setStatus('ready')
      setMessageKind('info')
      setMessage('Access saved. New agent turns will use this choice.')
    } catch {
      setStatus('ready')
      setMessageKind('error')
      setMessage('Access could not be saved. Your exact draft is still here; retry uses the same request identity so an uncertain response cannot create a second change.')
    }
  }

  const sameSelection = (left: string[], right: string[]) => (
    left.length === right.length && [...left].sort().every((value, index) => value === [...right].sort()[index])
  )
  const draftDiffersFromSaved = !scopeReady || !scope
    || mode !== scope.mode
    || !sameSelection(selectedAreas, scope.selectedAreas)
    || !sameSelection(selectedItems, scope.selectedItemIds)

  const makePreview = async () => {
    if (!agentId || !scopeReady || draftDiffersFromSaved) return
    setStatus('loading'); setMessage(''); setMessageKind('info')
    try {
      setPreview(await product.previewAgentContext(agentId))
      setStatus('ready')
    } catch {
      setStatus('ready'); setMessageKind('error'); setMessage('The shared-context preview could not be created.')
    }
  }

  const acceptedItems = product.current?.items.filter(item => item.reviewStatus === 'accepted') ?? []
  const selectedMode = mode === 'selected'
  const agent = agents.find(candidate => candidate.agentId === agentId)

  return (
    <Dialog label="Agent Career Profile access" onClose={onClose}>
      <DialogHeading closeLabel="Close access" eyebrow="You choose what is shared" onClose={onClose} title="Agent Career Profile access" />
      <div className="career-product-dialog-body career-context-dialog">
        {agents.length === 0 && status !== 'loading' ? <div className="career-product-empty"><ShieldCheck aria-hidden="true" size={20} /><strong>No connected agents</strong><p>Connect an agent before sharing Career Profile context.</p></div> : (
          <>
            <label className="career-field"><span>Connected agent</span><select aria-label="Connected agent" disabled={status === 'saving'} onChange={event => setAgentId(event.target.value)} value={agentId}>{agents.map(candidate => <option key={candidate.agentId} value={candidate.agentId}>{candidate.displayName}</option>)}</select></label>
            <fieldset className="career-context-options" disabled={!scopeReady}>
              <legend>What can {agent?.displayName ?? 'this agent'} use in new turns?</legend>
              <label><input checked={mode === 'none'} name="career-profile-context-mode" onChange={() => chooseMode('none')} type="radio" /><span><strong>No Career Profile context</strong><small>The agent receives none of this profile.</small></span></label>
              <label><input aria-label="Only selected details" checked={mode === 'selected'} name="career-profile-context-mode" onChange={() => chooseMode('selected')} type="radio" /><span><strong>Only selected details</strong><small>Share exact items or whole areas you choose below. Linked Evidence is not included unless you explicitly select My Evidence.</small></span></label>
              <label><input checked={mode === 'broader'} name="career-profile-context-mode" onChange={() => chooseMode('broader')} type="radio" /><span><strong>Broader accepted profile</strong><small>Explicitly grant every accepted detail and every active Evidence source.</small></span></label>
            </fieldset>
            {selectedMode ? (
              <section className="career-context-selection">
                <div><strong>Whole areas</strong><small>Choosing an area also includes future accepted details in that area.</small></div>
                {(Object.entries(areaLabels) as Array<[CareerProfileArea, string]>).map(([area, label]) => <label key={area}><input aria-label={`All of ${label}`} checked={selectedAreas.includes(area)} disabled={!scopeReady} onChange={event => toggleArea(area, event.target.checked)} type="checkbox" /><span>All of {label}</span></label>)}
                <div><strong>Exact saved details</strong><small>These stay exact even when other details change.</small></div>
                {acceptedItems.length === 0 ? <p>No accepted profile details are available yet.</p> : acceptedItems.map(item => <label key={item.itemId}><input checked={selectedItems.includes(item.itemId)} disabled={!scopeReady} onChange={event => toggleItem(item.itemId, event.target.checked)} type="checkbox" /><span>{itemTitle(item)} <small>{areaLabels[item.area]}</small></span></label>)}
              </section>
            ) : null}
            {draftDiffersFromSaved && scope ? <p className="career-product-plain-note" role="status">Save access before previewing. Preview always shows the saved scope, never unsaved draft choices.</p> : null}
            {preview ? (
              <div className="career-context-preview" role="status">
                <FileCheck2 aria-hidden="true" size={18} />
                <div>
                  <strong>Saved-scope preview created</strong>
                  <span>{preview.profile.items.length} profile detail{preview.profile.items.length === 1 ? '' : 's'} and {preview.profile.sourceEvidence.length} Evidence source{preview.profile.sourceEvidence.length === 1 ? '' : 's'} · Revision {preview.profileRevision}</span>
                  <strong>Profile details</strong>
                  {preview.profile.items.length === 0
                    ? <span>None</span>
                    : <ul>{preview.profile.items.map(item => <li key={item.itemId}>{itemTitle(item)} — {areaLabels[item.area]}</li>)}</ul>}
                  <strong>Evidence files</strong>
                  {preview.profile.sourceEvidence.length === 0
                    ? <span>None</span>
                    : <ul>{preview.profile.sourceEvidence.map(source => <li key={source.evidenceId}>{source.originalFilename}</li>)}</ul>}
                </div>
              </div>
            ) : null}
          </>
        )}
        {message ? <p className={`career-feedback ${messageKind === 'error' ? 'error' : 'saved'}`} role={messageKind === 'error' ? 'alert' : 'status'}>{message}</p> : null}
      </div>
      {agents.length > 0 ? <footer className="career-product-dialog-actions"><button className="career-primary-button" disabled={!online || !scopeReady} onClick={() => { void save() }} type="button">{status === 'saving' ? 'Saving…' : 'Save access'}</button><button aria-label="Preview saved-scope context" className="career-secondary-button" disabled={!online || !scopeReady || draftDiffersFromSaved} onClick={() => { void makePreview() }} type="button">Preview saved scope</button></footer> : null}
    </Dialog>
  )
}
