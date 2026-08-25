import { Bot, CircleAlert, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { AgentChatSelection } from '../../shared/contracts'
import type { useConnectedAgents } from '../hooks/useConnectedAgents'

interface NewAgentChatDialogProps {
  connectedAgents: ReturnType<typeof useConnectedAgents>
  atMaximum: boolean
  onArchiveCurrent: () => void
  onClose: () => void
  onCreate: (selection: AgentChatSelection) => Promise<boolean>
}

export function NewAgentChatDialog({ connectedAgents, atMaximum, onArchiveCurrent, onClose, onCreate }: NewAgentChatDialogProps) {
  const { snapshot, models, loadModels, loading } = connectedAgents
  const [agentId, setAgentId] = useState('')
  const [modelId, setModelId] = useState('')
  const [effort, setEffort] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const firstControl = useRef<HTMLSelectElement>(null)
  const dialog = useRef<HTMLDivElement>(null)
  const agents = useMemo(() => snapshot?.agents.filter(agent => agent.lifecycle === 'connected' && agent.health.providerAvailable && agent.health.toolsAvailable) ?? [], [snapshot])
  const selected = agents.find(agent => agent.id === agentId)
  const catalog = agentId ? models[agentId] : undefined
  const option = catalog?.models.find(item => item.modelId === modelId)

  useEffect(() => {
    const initial = snapshot?.defaultConnectedAgentId && agents.some(agent => agent.id === snapshot.defaultConnectedAgentId)
      ? snapshot.defaultConnectedAgentId
      : agents[0]?.id ?? ''
    setAgentId(initial)
    requestAnimationFrame(() => (firstControl.current ?? dialog.current)?.focus())
  }, [snapshot?.registryRevision])

  useEffect(() => {
    if (!selected) return
    let current = true
    setModelId('')
    setEffort('')
    setError(null)
    void loadModels(selected.id, true).then(value => {
      if (!current) return
      const preferred = value.models.find(item => item.modelId === selected.defaultModelId) ?? value.models[0]
      setModelId(preferred?.modelId ?? '')
      setEffort(preferred?.reasoningEfforts.includes(selected.defaultReasoningEffort ?? '') ? selected.defaultReasoningEffort! : preferred?.reasoningEfforts[0] ?? '')
    }).catch(cause => { if (current) setError(cause instanceof Error ? cause.message : 'Models unavailable') })
    return () => { current = false }
  }, [selected?.id])

  const create = async () => {
    if (!snapshot || !selected || !modelId || !effort) return
    setSubmitting(true)
    setError(null)
    try {
      const handled = await onCreate({
        connectedAgentId: selected.id,
        modelId,
        reasoningEffort: effort,
        expectedProfileRevision: snapshot.registryRevision,
        expectedAgentRegistryRevision: snapshot.registryRevision,
        idempotencyKey: `desktop-new-chat-${crypto.randomUUID()}`
      })
      if (handled) onClose()
      else setError('New chat could not be started. Refresh the agent and try again.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'New chat could not be started. Refresh the agent and try again.')
    } finally { setSubmitting(false) }
  }

  return (
    <div className="new-chat-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <div aria-labelledby="new-chat-title" aria-modal="true" className="new-chat-dialog" onKeyDown={event => {
        if (event.key === 'Escape') { onClose(); return }
        if (event.key !== 'Tab') return
        const controls = [...(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? [])]
        if (!controls.length) return
        const first = controls[0]!
        const last = controls.at(-1)!
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
      }} ref={dialog} role="dialog" tabIndex={-1}>
        <header><div><span className="settings-eyebrow">New Chat</span><h2 id="new-chat-title">Pick the agent for this chat</h2><p>This choice stays locked so the conversation never silently changes brains.</p></div><button aria-label="Close New Chat" onClick={onClose} type="button"><X aria-hidden="true" size={16} /></button></header>
        {atMaximum ? <div className="new-chat-limit" role="status"><CircleAlert aria-hidden="true" size={18} /><div><strong>Five chats are already open</strong><p>Archive the current chat first. Its history stays saved.</p><button onClick={onArchiveCurrent} type="button">Archive current chat</button></div></div> : (
          <>
            <label>Agent<select disabled={loading || agents.length === 0} onChange={event => { setAgentId(event.target.value); setModelId(''); setEffort('') }} ref={firstControl} value={agentId}><option value="">Choose an agent</option>{agents.map(agent => <option key={agent.id} value={agent.id}>{agent.displayName} · {agent.provider === 'codex' ? 'ChatGPT' : 'Hermes'}</option>)}</select></label>
            <label>Model<select disabled={!catalog?.live} onChange={event => { setModelId(event.target.value); setEffort(catalog?.models.find(item => item.modelId === event.target.value)?.reasoningEfforts[0] ?? '') }} value={modelId}><option value="">Choose a live model</option>{catalog?.models.map(model => <option key={model.modelId} value={model.modelId}>{model.displayName}</option>)}</select></label>
            <label>Reasoning effort<select disabled={!option} onChange={event => setEffort(event.target.value)} value={effort}><option value="">Choose effort</option>{option?.reasoningEfforts.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
            {selected ? <div className="new-chat-receipt"><Bot aria-hidden="true" size={18} /><span><strong>{selected.displayName}</strong><small>{modelId || 'Choose a model'}{effort ? ` · ${effort}` : ''}</small></span></div> : null}
            {agents.length === 0 && !loading ? <p className="settings-callout">No ready agents. Connect or repair one in Settings.</p> : null}
            {error ? <p className="settings-callout error" role="alert">{error}</p> : null}
            <footer><button onClick={onClose} type="button">Cancel</button><button disabled={!selected || !modelId || !effort || submitting} onClick={() => void create()} type="button">{submitting ? 'Starting…' : 'Start chat'}</button></footer>
          </>
        )}
      </div>
    </div>
  )
}
