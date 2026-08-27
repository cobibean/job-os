import { Bot, Check, CircleAlert, LoaderCircle, Plug, RefreshCw, Unplug } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { ConnectedAgentSummary } from '../../shared/contracts'
import type { useConnectedAgents } from '../hooks/useConnectedAgents'
import { SettingsSection } from './SettingsSection'

function operationKey(label: string): string {
  return `${label}-${crypto.randomUUID()}`
}

export function authTerminalNotice(status: string, errorCode: string | null): string {
  if (status === 'cleanup_required' || errorCode === 'AUTH_CLEANUP_REQUIRED') {
    return 'ChatGPT sign in could not be cleaned up safely. Retry sign in before using this agent.'
  }
  if (status === 'expired') return 'ChatGPT sign in expired. Start sign in again for a fresh code.'
  if (status === 'cancelled') return 'ChatGPT sign in was cancelled.'
  return 'ChatGPT sign in failed. Try again.'
}

interface ConnectedAgentsSettingsProps {
  state: ReturnType<typeof useConnectedAgents>
  onAgentsChanged?: () => Promise<void>
}

export function ConnectedAgentsSettings({ state, onAgentsChanged }: ConnectedAgentsSettingsProps) {
  const { bridge, snapshot, models, loading, error, refresh, loadModels } = state
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [modelId, setModelId] = useState('')
  const [effort, setEffort] = useState('')
  const [auth, setAuth] = useState<{ transactionId: string; status: string; userCode: string | null; verificationUrl: string | null; errorCode?: string | null } | null>(null)
  const pendingAuthRef = useRef<string | null>(null)
  const disconnectDialog = useRef<HTMLDivElement>(null)
  const disconnectCancel = useRef<HTMLButtonElement>(null)
  const [disconnecting, setDisconnecting] = useState<{ agent: ConnectedAgentSummary; activeChats: number; lockedChats: number; defaultProfileIds: string[] } | null>(null)
  const selected = useMemo(() => snapshot?.agents.find(item => item.id === selectedId) ?? snapshot?.agents[0] ?? null, [selectedId, snapshot])
  const catalog = selected ? models[selected.id] : undefined

  useEffect(() => {
    if (!selected) return
    setSelectedId(selected.id)
    setModelId(selected.defaultModelId ?? '')
    setEffort(selected.defaultReasoningEffort ?? '')
    if (selected.lifecycle === 'connected') void loadModels(selected.id).catch(() => undefined)
  }, [selected?.id])

  useEffect(() => {
    if (!auth || auth.status !== 'login_pending' || !bridge) return
    const timer = window.setInterval(() => {
      void bridge.readAuth(auth.transactionId).then(value => {
        setAuth(value)
        if (value.status === 'connected') {
          pendingAuthRef.current = null
          setNotice('ChatGPT connected. Models and tools are ready to refresh.')
          void refresh()
          void onAgentsChanged?.()
          if (selected) void loadModels(selected.id, true).catch(() => undefined)
        } else if (value.status !== 'login_pending') {
          pendingAuthRef.current = null
          setNotice(authTerminalNotice(value.status, value.errorCode ?? null))
        }
      }).catch(() => undefined)
    }, 1500)
    return () => window.clearInterval(timer)
  }, [auth?.transactionId, auth?.status, bridge, loadModels, onAgentsChanged, refresh, selected?.id])

  useEffect(() => () => {
    if (pendingAuthRef.current && bridge) void bridge.cancelAuth(pendingAuthRef.current)
  }, [bridge])

  useEffect(() => {
    if (!disconnecting) return
    requestAnimationFrame(() => disconnectCancel.current?.focus())
  }, [disconnecting])

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label)
    setNotice(null)
    try { await action() } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'Connected Agent action failed')
    } finally { setBusy(null) }
  }

  const connectCodex = () => run('connect', async () => {
    if (!bridge || !snapshot) return
    const created = await bridge.createCodex('Codex', 'spark', snapshot.registryRevision, operationKey('connect-codex'))
    await refresh()
    setSelectedId(created.id)
    const transaction = await bridge.startAuth(created.id, 'connect', null)
    pendingAuthRef.current = transaction.transactionId
    setAuth(transaction)
  })

  const saveDefaults = () => run('defaults', async () => {
    if (!bridge || !snapshot || !selected || !modelId || !effort) return
    await bridge.update(selected, modelId, effort, snapshot.registryRevision, operationKey('agent-defaults'))
    await refresh()
    setNotice('Agent defaults saved. Existing chats keep their original model.')
  })

  const setProfileDefault = () => run('profile-default', async () => {
    if (!bridge || !snapshot || !selected) return
    await bridge.setDefault(snapshot.profileId, selected.id, snapshot.registryRevision, operationKey('profile-default'))
    await refresh()
    setNotice(`${selected.displayName} is now the New Chat default for this profile.`)
  })

  const inspectDisconnect = () => run('impact', async () => {
    if (!bridge || !selected) return
    const value = await bridge.impact(selected.id)
    setDisconnecting({ agent: selected, activeChats: value.activeChats, lockedChats: value.lockedChats, defaultProfileIds: value.defaultProfileIds })
  })

  if (!bridge) return null

  return (
    <SettingsSection className="connected-agents-settings" id="connected-agents" title="Connected Agents">
      <div className="settings-section-heading">
        <div>
          <h3>Choose who works each chat</h3>
          <p>Agents are shared across this JobOS installation. New chats lock their agent and model so history stays honest.</p>
        </div>
        <button aria-label="Refresh Connected Agents" className="settings-icon-button" disabled={loading} onClick={() => void (async () => { await refresh(); await onAgentsChanged?.() })()} type="button">
          <RefreshCw aria-hidden="true" className={loading ? 'spin' : ''} size={16} />
        </button>
      </div>

      {error ? <p className="settings-callout error" role="alert"><CircleAlert aria-hidden="true" size={15} />{error}</p> : null}
      {!loading && snapshot?.agents.length === 0 ? (
        <div className="connected-agents-onboarding">
          <span className="connected-agents-orb"><Bot aria-hidden="true" size={28} /></span>
          <div><strong>Bring another brain into JobOS</strong><p>Connect ChatGPT through Codex, then pick its live model when you start a chat.</p></div>
          <button disabled={busy !== null} onClick={connectCodex} type="button"><Plug aria-hidden="true" size={15} /> Connect ChatGPT</button>
        </div>
      ) : null}

      {snapshot && snapshot.agents.length > 0 ? (
        <div className="connected-agents-layout">
          <div aria-label="Connected Agent roster" className="connected-agent-roster" role="list">
            {snapshot.agents.map(item => (
              <div key={item.id} role="listitem">
                <button aria-current={item.id === selected?.id ? 'true' : undefined} className={item.id === selected?.id ? 'selected' : ''} onClick={() => setSelectedId(item.id)} type="button">
                  <span className={`connected-agent-dot ${item.health.providerAvailable && item.health.toolsAvailable ? 'ready' : 'warning'}`} />
                  <span><strong>{item.displayName}</strong><small>{item.provider === 'codex' ? 'ChatGPT · Codex' : 'Hermes'} · {item.health.label}</small></span>
                  {snapshot.defaultConnectedAgentId === item.id ? <span className="connected-agent-default"><Check aria-hidden="true" size={12} /> Default</span> : null}
                </button>
              </div>
            ))}
            {!snapshot.agents.some(item => item.provider === 'codex') ? <div role="listitem"><button className="connected-agent-add" disabled={busy !== null} onClick={connectCodex} type="button"><Plug aria-hidden="true" size={15} /> Connect ChatGPT</button></div> : null}
          </div>

          {selected ? (
            <div className="connected-agent-inspector">
              <div className="connected-agent-title"><div><span>{selected.provider.toUpperCase()}</span><h4>{selected.displayName}</h4></div><span className={`status-pill ${selected.lifecycle}`}>{selected.lifecycle}</span></div>
              <p className="connected-agent-health">{selected.health.providerAvailable && selected.health.toolsAvailable ? 'Provider and JobOS tools are ready.' : selected.health.label}</p>
              {selected.accountSummary ? <p className="connected-agent-account">{Object.values(selected.accountSummary).join(' · ')}</p> : null}
              {selected.provider === 'codex' && (selected.lifecycle === 'disconnected' || !selected.accountSummary || !selected.health.providerAvailable) ? (
                <button disabled={busy !== null} onClick={() => void run('auth', async () => {
                  if (bridge) {
                    const transaction = await bridge.startAuth(selected.id, selected.lifecycle === 'disconnected' || (selected.accountSummary && !selected.accountFingerprint) ? 'replace' : selected.accountSummary ? 'reconnect' : 'connect', selected.accountFingerprint)
                    pendingAuthRef.current = transaction.transactionId
                    setAuth(transaction)
                  }
                })} type="button">{selected.lifecycle === 'disconnected' || (selected.accountSummary && !selected.accountFingerprint) ? 'Replace ChatGPT account' : selected.accountSummary ? 'Reconnect ChatGPT' : 'Finish ChatGPT sign in'}</button>
              ) : null}
              {selected.provider === 'codex' && selected.lifecycle === 'disconnected' ? <p className="connected-agent-account">JobOS cannot verify the original account. Old provider sessions may stay read-only.</p> : null}
              {auth?.status === 'login_pending' ? (
                <div className="connected-agent-auth" role="status"><LoaderCircle aria-hidden="true" className="spin" size={16} /><div><strong>Approve ChatGPT in your browser</strong><p>{auth.verificationUrl}</p><code>{auth.userCode}</code><button onClick={() => void run('cancel-auth', async () => { await bridge.cancelAuth(auth.transactionId); pendingAuthRef.current = null; setAuth(null); setNotice('ChatGPT sign in cancelled.') })} type="button">Cancel sign in</button></div></div>
              ) : null}
              <label>Default model<select disabled={!catalog?.live || busy !== null} onChange={event => { setModelId(event.target.value); const option = catalog?.models.find(item => item.modelId === event.target.value); setEffort(option?.reasoningEfforts[0] ?? '') }} value={modelId}><option value="">Choose a live model</option>{catalog?.models.map(item => <option key={item.modelId} value={item.modelId}>{item.displayName}</option>)}</select></label>
              <label>Reasoning effort<select disabled={!modelId || busy !== null} onChange={event => setEffort(event.target.value)} value={effort}><option value="">Choose effort</option>{catalog?.models.find(item => item.modelId === modelId)?.reasoningEfforts.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
              <div className="connected-agent-actions">
                <button disabled={!modelId || !effort || busy !== null} onClick={saveDefaults} type="button">Save defaults</button>
                <button disabled={snapshot.defaultConnectedAgentId === selected.id || busy !== null} onClick={setProfileDefault} type="button">Use for New Chat</button>
                <button disabled={busy !== null} onClick={() => void run('test', async () => { if (bridge) { const result = await bridge.test(selected.id); setNotice(result.health.label); await refresh(); await onAgentsChanged?.() } })} type="button">Test</button>
                <button className="danger-link" disabled={busy !== null} onClick={inspectDisconnect} type="button"><Unplug aria-hidden="true" size={14} /> Disconnect</button>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
      {notice ? <p aria-live="polite" className="settings-callout">{notice}</p> : null}
      {disconnecting ? (
        <div aria-labelledby="disconnect-agent-title" aria-modal="true" className="settings-dialog-backdrop" onKeyDown={event => {
          if (event.key === 'Escape') { setDisconnecting(null); return }
          if (event.key !== 'Tab') return
          const controls = [...(disconnectDialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? [])]
          if (!controls.length) return
          const first = controls[0]!
          const last = controls.at(-1)!
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
        }} ref={disconnectDialog} role="alertdialog"><div className="settings-dialog"><h4 id="disconnect-agent-title">Disconnect {disconnecting.agent.displayName}?</h4><p>{disconnecting.activeChats} active {disconnecting.activeChats === 1 ? 'chat' : 'chats'} will become read-only. {disconnecting.lockedChats} {disconnecting.lockedChats === 1 ? 'is' : 'are'} already locked. Chat history stays visible.</p>{disconnecting.defaultProfileIds.length > 0 ? <p>This agent is still the New Chat default for {disconnecting.defaultProfileIds.length} profile{disconnecting.defaultProfileIds.length === 1 ? '' : 's'}: {disconnecting.defaultProfileIds.join(', ')}.</p> : null}<div><button onClick={() => setDisconnecting(null)} ref={disconnectCancel} type="button">Keep connected</button><button className="danger" onClick={() => void run('disconnect', async () => { if (!bridge || !snapshot) return; await bridge.disconnect(disconnecting.agent.id, snapshot.registryRevision, operationKey('disconnect-agent')); setDisconnecting(null); await refresh(); await onAgentsChanged?.() })} type="button">Disconnect agent</button></div></div></div>
      ) : null}
    </SettingsSection>
  )
}
