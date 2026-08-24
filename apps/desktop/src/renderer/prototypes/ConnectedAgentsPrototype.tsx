// THROWAWAY PROTOTYPE for #106.
// Three variants of Connected Agents + New Chat, switchable with ?variant=A|B|C.
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  CloudOff,
  Laptop,
  LoaderCircle,
  LockKeyhole,
  MessageSquarePlus,
  MoreHorizontal,
  Plus,
  RefreshCcw,
  Settings,
  ShieldCheck,
  Sparkles,
  Unplug,
  UserRound,
  X
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import './connected-agents-prototype.css'

type Variant = 'A' | 'B' | 'C'
type Surface = 'agents' | 'new-chat' | 'chat'
type Scenario = 'ready' | 'loading' | 'error' | 'empty' | 'host-offline' | 'default-unavailable' | 'migrated'
type Provider = 'hermes' | 'codex'

type PrototypeAgent = {
  id: string
  name: string
  provider: Provider
  avatar: string
  defaultModel: string
  models: string[]
  status: 'connected' | 'unavailable' | 'attention'
  detail: string
  profileDefault?: boolean
}

const variants: Array<{ id: Variant; name: string; thesis: string }> = [
  { id: 'A', name: 'Agent directory', thesis: 'Scan the roster, then edit one connection in a focused inspector.' },
  { id: 'B', name: 'Guided connection cards', thesis: 'Make setup and recovery feel friendly, visual, and sequential.' },
  { id: 'C', name: 'Compact control center', thesis: 'Put connection health, defaults, and models in one dense view.' }
]

const baseAgents: PrototypeAgent[] = [
  {
    id: 'hermes-jobhunter',
    name: 'Job Hunter',
    provider: 'hermes',
    avatar: '🥷',
    defaultModel: 'GPT 5.6 Sol · Medium',
    models: ['GPT 5.6 Sol · Medium', 'GPT 5.6 Sol · High', 'Claude Sonnet 4.6'],
    status: 'connected',
    detail: 'Hermes · Mac host',
    profileDefault: true
  },
  {
    id: 'hermes-resume',
    name: 'Resume Coach',
    provider: 'hermes',
    avatar: '🧠',
    defaultModel: 'GPT 5.6 Sol · Medium',
    models: ['GPT 5.6 Sol · Medium', 'GPT 5.6 Sol · High'],
    status: 'connected',
    detail: 'Hermes · Mac host'
  },
  {
    id: 'codex',
    name: 'Codex',
    provider: 'codex',
    avatar: '✦',
    defaultModel: 'GPT 5.6 Codex · Medium',
    models: ['GPT 5.6 Codex · Medium', 'GPT 5.6 Codex · High', 'GPT 5.5 Codex Mini'],
    status: 'connected',
    detail: 'ChatGPT · Connected as cobi@example.com'
  }
]

const scenarioLabels: Record<Scenario, string> = {
  ready: 'Ready — three connected agents',
  loading: 'Loading connections',
  error: 'Connection registry failed to load',
  empty: 'First run — no connected agents',
  'host-offline': 'MacBook — host unavailable',
  'default-unavailable': 'Profile default unavailable',
  migrated: 'Migrated Hermes + locked Codex chat'
}

function agentsForScenario(scenario: Scenario): PrototypeAgent[] {
  if (scenario === 'empty') return []
  if (scenario === 'host-offline') return baseAgents.map(agent => ({ ...agent, status: 'unavailable', detail: 'JobOS host unavailable' }))
  if (scenario === 'default-unavailable') return baseAgents.map(agent => agent.id === 'hermes-jobhunter'
    ? { ...agent, status: 'unavailable', detail: 'Hermes connection unavailable' }
    : agent)
  if (scenario === 'migrated') return baseAgents.map(agent => agent.id === 'hermes-jobhunter'
    ? { ...agent, detail: 'Migrated from existing JobOS · Ready' }
    : agent.id === 'codex'
      ? { ...agent, status: 'attention', detail: 'Original account unavailable' }
      : agent)
  return baseAgents
}

function providerLabel(provider: Provider) {
  return provider === 'codex' ? 'ChatGPT / Codex' : 'Hermes'
}

function StatusPill({ status }: { status: PrototypeAgent['status'] }) {
  const copy = status === 'connected' ? 'Connected' : status === 'attention' ? 'Reconnect required' : 'Unavailable'
  return <span className={`cap-status ${status}`}><span aria-hidden="true" />{copy}</span>
}

function AppChrome({ children, scenario, setScenario }: {
  children: ReactNode
  scenario: Scenario
  setScenario: (scenario: Scenario) => void
}) {
  return (
    <div className="cap-app-shell">
      <header className="cap-titlebar">
        <div className="cap-window-dots" aria-hidden="true"><i /><i /><i /></div>
        <strong>JobOS</strong>
        <span className="cap-profile"><UserRound size={14} /> Cobi · Product</span>
      </header>
      <div className="cap-workspace">
        <aside className="cap-rail" aria-label="JobOS navigation">
          <div className="cap-brand">J</div>
          <button aria-label="Jobs"><span>17</span>Jobs</button>
          <button aria-label="Browse">⌁<span>Browse</span></button>
          <button aria-label="Documents">▤<span>Docs</span></button>
          <button className="active" aria-label="Agent chat"><Bot size={18} /><span>Agent</span></button>
          <button aria-label="Career Profile">◫<span>Profile</span></button>
          <button aria-label="Settings"><Settings size={18} /><span>Settings</span></button>
        </aside>
        <main className="cap-stage">{children}</main>
      </div>
      <div className="cap-scenario-bar">
        <label htmlFor="prototype-scenario">Test state</label>
        <select id="prototype-scenario" value={scenario} onChange={event => setScenario(event.target.value as Scenario)}>
          {Object.entries(scenarioLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <span><Laptop size={14} /> Authorized MacBook view</span>
      </div>
    </div>
  )
}

function SurfaceNav({ surface, setSurface }: { surface: Surface; setSurface: (surface: Surface) => void }) {
  return (
    <div className="cap-surface-nav" role="tablist" aria-label="Prototype surface">
      <button role="tab" aria-selected={surface === 'agents'} className={surface === 'agents' ? 'active' : ''} onClick={() => setSurface('agents')}>Connected Agents</button>
      <button role="tab" aria-selected={surface === 'new-chat'} className={surface === 'new-chat' ? 'active' : ''} onClick={() => setSurface('new-chat')}>New Chat</button>
      <button role="tab" aria-selected={surface === 'chat'} className={surface === 'chat' ? 'active' : ''} onClick={() => setSurface('chat')}>Chat identity</button>
    </div>
  )
}

function EmptyState({ onAdd }: { onAdd: () => void }) {
  return (
    <section className="cap-empty">
      <div className="cap-empty-art"><Bot size={32} /><Plus size={17} /></div>
      <h2>Bring your first agent into JobOS</h2>
      <p>Connect the Hermes agent you already use, or sign in to ChatGPT to add Codex. Your jobs and documents stay in JobOS.</p>
      <div><button className="cap-primary" onClick={onAdd}><Plus size={15} /> Add Hermes agent</button><button className="cap-secondary" onClick={onAdd}><Sparkles size={15} /> Connect ChatGPT / Codex</button></div>
    </section>
  )
}

function LoadingState() {
  return <section className="cap-loading" role="status"><LoaderCircle className="spin" size={22} /><div><strong>Checking connected agents…</strong><p>Verifying the host, credentials, models, and JobOS tools separately.</p></div></section>
}

function ErrorState() {
  return <section className="cap-error" role="alert"><AlertTriangle size={22} /><div><strong>Connected Agents could not load</strong><p>Your existing chats and profile data are unchanged. Retry the registry check or open diagnostics.</p><div><button className="cap-primary"><RefreshCcw size={14} /> Retry</button><button className="cap-secondary">Open diagnostics</button></div></div></section>
}

function AgentIdentity({ agent, compact = false }: { agent: PrototypeAgent; compact?: boolean }) {
  return (
    <div className={`cap-agent-identity ${compact ? 'compact' : ''}`}>
      <span className={`cap-avatar ${agent.provider}`}>{agent.avatar}</span>
      <span><strong>{agent.name}</strong><small>{providerLabel(agent.provider)}</small></span>
    </div>
  )
}

function ModelSelect({ agent, label = 'Default model' }: { agent: PrototypeAgent; label?: string }) {
  return <label className="cap-field"><span>{label}</span><select defaultValue={agent.defaultModel}>{agent.models.map(model => <option key={model}>{model}</option>)}</select></label>
}

function DeviceCodePanel({ onDone }: { onDone: () => void }) {
  return (
    <div className="cap-device-code" role="status">
      <div className="cap-device-icon"><ShieldCheck size={23} /></div>
      <div><strong>Finish connecting on any device</strong><p>Open <u>auth.openai.com/device</u> and enter this one-time code.</p><code>JOBOS-7M4K</code><small>Expires in 11:42 · Credentials stay on the JobOS host</small></div>
      <button className="cap-secondary" onClick={onDone}>Simulate success</button>
    </div>
  )
}

function ImpactNotice({ agent, onCancel }: { agent: PrototypeAgent; onCancel: () => void }) {
  return (
    <div className="cap-impact" role="alertdialog" aria-label={`Disconnect ${agent.name}`}>
      <AlertTriangle size={18} />
      <div><strong>Disconnect {agent.name} from JobOS?</strong><p>2 profile defaults become unavailable and 3 active chats lock. Local history stays readable.</p><div><button className="cap-danger">Disconnect from JobOS</button><button className="cap-text" onClick={onCancel}>Cancel</button></div></div>
    </div>
  )
}

function VariantA({ agents, scenario, selectedId, setSelectedId, deviceCode, setDeviceCode, disconnecting, setDisconnecting }: VariantProps) {
  const selected = agents.find(agent => agent.id === selectedId) ?? agents[0]
  if (scenario === 'loading') return <LoadingState />
  if (scenario === 'error') return <ErrorState />
  if (!agents.length) return <EmptyState onAdd={() => setDeviceCode(true)} />
  return (
    <div className="cap-directory-layout">
      <section className="cap-directory-list" aria-label="Connected agent directory">
        <div className="cap-list-tools"><span>{agents.length} {scenario === 'host-offline' ? 'configured' : 'connected'} agents</span><button className="cap-primary"><Plus size={15} /> Add agent</button></div>
        {agents.map(agent => (
          <button className={`cap-agent-row ${selected?.id === agent.id ? 'selected' : ''}`} key={agent.id} onClick={() => setSelectedId(agent.id)}>
            <AgentIdentity agent={agent} /><span className="cap-row-meta">{agent.profileDefault ? <em>Profile default</em> : null}<StatusPill status={agent.status} /></span><ChevronRight size={16} />
          </button>
        ))}
        <div className="cap-directory-note"><CircleHelp size={15} /><span>Connections belong to this JobOS installation. Each profile chooses its own default.</span></div>
      </section>
      {selected ? (
        <section className="cap-inspector" aria-label={`${selected.name} settings`}>
          <header><AgentIdentity agent={selected} /><button aria-label={`More options for ${selected.name}`} className="cap-icon"><MoreHorizontal size={18} /></button></header>
          <div className="cap-health-line"><StatusPill status={selected.status} /><span>{selected.detail}</span>{scenario !== 'host-offline' ? <button className="cap-text"><RefreshCcw size={13} /> Test connection</button> : null}</div>
          {selected.status !== 'connected' && scenario !== 'host-offline' ? <div className="cap-warning"><CloudOff size={17} /><span><strong>{selected.detail}</strong><small>History stays readable. Reconnect this exact agent to continue.</small></span><button className="cap-secondary" onClick={() => setDeviceCode(true)}>Reconnect</button></div> : null}
          <div className="cap-form-grid">
            <label className="cap-field"><span>Display name</span><input defaultValue={selected.name} /></label>
            <label className="cap-field"><span>Agent icon</span><button className="cap-avatar-picker"><span>{selected.avatar}</span> Change icon</button></label>
          </div>
          <ModelSelect agent={selected} />
          <label className="cap-check"><input defaultChecked={selected.profileDefault} type="checkbox" /><span><strong>Default for Cobi · Product</strong><small>New chats start with this agent, but you can choose another.</small></span></label>
          <div className="cap-capabilities"><strong>JobOS access</strong><span><Check size={14} /> Career Profile</span><span><Check size={14} /> Jobs & listings</span><span><Check size={14} /> Documents</span><span><Check size={14} /> Browser workflows</span></div>
          <footer><button className="cap-danger-quiet" onClick={() => setDisconnecting(selected.id)}><Unplug size={14} /> Disconnect from JobOS</button><span>Connected agents share global JobOS permissions.</span></footer>
          {disconnecting === selected.id ? <ImpactNotice agent={selected} onCancel={() => setDisconnecting(null)} /> : null}
        </section>
      ) : null}
      {deviceCode ? <div className="cap-overlay"><DeviceCodePanel onDone={() => setDeviceCode(false)} /></div> : null}
    </div>
  )
}

function VariantB({ agents, scenario, deviceCode, setDeviceCode, disconnecting, setDisconnecting }: VariantProps) {
  if (scenario === 'loading') return <LoadingState />
  if (scenario === 'error') return <ErrorState />
  if (!agents.length) return <EmptyState onAdd={() => setDeviceCode(true)} />
  return (
    <div className="cap-card-flow">
      <div className="cap-flow-intro"><div><span className="cap-eyebrow">Your agent team</span><h2>Choose who helps with your job search</h2><p>Every chat stays with the agent and model you choose when it starts.</p></div><button className="cap-primary"><Plus size={15} /> Add an agent</button></div>
      <div className="cap-card-grid">
        {agents.map(agent => (
          <article className={`cap-agent-card ${agent.status}`} key={agent.id}>
            <header><AgentIdentity agent={agent} /><StatusPill status={agent.status} /></header>
            {agent.profileDefault ? <span className="cap-default-ribbon"><CheckCircle2 size={14} /> Default for this profile</span> : null}
            <p className="cap-card-detail">{agent.detail}</p>
            <ModelSelect agent={agent} />
            <div className="cap-card-actions">
              {agent.status === 'connected' ? <button className="cap-secondary">Edit agent</button> : scenario === 'host-offline' ? <button className="cap-secondary" disabled>Host offline</button> : <button className="cap-primary" onClick={() => setDeviceCode(true)}>Reconnect</button>}
              <button className="cap-text" onClick={() => setDisconnecting(agent.id)}>Disconnect</button>
            </div>
            {disconnecting === agent.id ? <ImpactNotice agent={agent} onCancel={() => setDisconnecting(null)} /> : null}
          </article>
        ))}
        <button className="cap-add-card"><Plus size={24} /><strong>Add another Hermes agent</strong><span>Connect by URL and credential</span></button>
      </div>
      <aside className="cap-flow-tip"><ShieldCheck size={18} /><span><strong>One Codex connection, multiple Hermes agents</strong><small>Connections are shared across JobOS profiles. Profiles choose their own default.</small></span></aside>
      {deviceCode ? <div className="cap-overlay"><DeviceCodePanel onDone={() => setDeviceCode(false)} /></div> : null}
    </div>
  )
}

function VariantC({ agents, scenario, selectedId, setSelectedId, deviceCode, setDeviceCode, disconnecting, setDisconnecting }: VariantProps) {
  const selected = agents.find(agent => agent.id === selectedId) ?? agents[0]
  if (scenario === 'loading') return <LoadingState />
  if (scenario === 'error') return <ErrorState />
  if (!agents.length) return <EmptyState onAdd={() => setDeviceCode(true)} />
  return (
    <div className="cap-control-center">
      <div className="cap-metrics"><div><strong>{agents.filter(agent => agent.status === 'connected').length}</strong><span>Ready</span></div><div><strong>{agents.filter(agent => agent.status !== 'connected').length}</strong><span>Need attention</span></div><div><strong>4/5</strong><span>Active chats</span></div><button className="cap-primary"><Plus size={15} /> Connect</button></div>
      <div className="cap-table-wrap">
        <table>
          <thead><tr><th>Agent</th><th>Health</th><th>Default model</th><th>Profile default</th><th><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{agents.map(agent => <tr className={selected?.id === agent.id ? 'selected' : ''} key={agent.id} onClick={() => setSelectedId(agent.id)}><td><AgentIdentity compact agent={agent} /></td><td><StatusPill status={agent.status} /><small>{agent.detail}</small></td><td>{agent.defaultModel}</td><td>{agent.profileDefault ? <span className="cap-yes"><Check size={13} /> Cobi · Product</span> : <button className="cap-text">Make default</button>}</td><td><button className="cap-icon" aria-label={`Open ${agent.name}`}><ChevronRight size={16} /></button></td></tr>)}</tbody>
        </table>
      </div>
      {selected ? <aside className="cap-drawer">
        <header><AgentIdentity agent={selected} /><button className="cap-icon" aria-label={`Close ${selected.name} settings`}><X size={17} /></button></header>
        <div className="cap-health-line"><StatusPill status={selected.status} /><button className="cap-text">Run diagnostics</button></div>
        <ModelSelect agent={selected} />
        <label className="cap-field"><span>Display name</span><input defaultValue={selected.name} /></label>
        <div className="cap-drawer-actions">{selected.status !== 'connected' ? scenario === 'host-offline' ? <button className="cap-secondary" disabled>Host offline</button> : <button className="cap-primary" onClick={() => setDeviceCode(true)}>Reconnect</button> : <button className="cap-secondary">Save changes</button>}<button className="cap-danger-quiet" onClick={() => setDisconnecting(selected.id)}>Disconnect</button></div>
        {disconnecting === selected.id ? <ImpactNotice agent={selected} onCancel={() => setDisconnecting(null)} /> : null}
      </aside> : null}
      {deviceCode ? <div className="cap-overlay"><DeviceCodePanel onDone={() => setDeviceCode(false)} /></div> : null}
    </div>
  )
}

type VariantProps = {
  agents: PrototypeAgent[]
  scenario: Scenario
  selectedId: string
  setSelectedId: (id: string) => void
  deviceCode: boolean
  setDeviceCode: (value: boolean) => void
  disconnecting: string | null
  setDisconnecting: (id: string | null) => void
}

function NewChatSurface({ agents, scenario, variant }: { agents: PrototypeAgent[]; scenario: Scenario; variant: Variant }) {
  const available = useMemo(() => agents.filter(agent => agent.status === 'connected'), [agents])
  const fallback = available[0]
  const unavailableDefault = scenario === 'default-unavailable'
  const [selectedId, setSelectedId] = useState(unavailableDefault ? '' : fallback?.id ?? '')
  const selected = available.find(agent => agent.id === selectedId)
  useEffect(() => {
    if (!available.some(agent => agent.id === selectedId)) setSelectedId(unavailableDefault ? '' : fallback?.id ?? '')
  }, [available, fallback?.id, selectedId, unavailableDefault])
  return (
    <div className={`cap-new-chat variant-${variant.toLowerCase()}`}>
      <section className="cap-new-chat-dialog" role="dialog" aria-modal="true" aria-labelledby="cap-new-chat-title">
        <header><div><span className="cap-eyebrow">New agent chat</span><h2 id="cap-new-chat-title">Choose an agent for this chat</h2><p>The agent, model, and reasoning level are sealed for this chat.</p></div><button className="cap-icon" aria-label="Close"><X size={18} /></button></header>
        {unavailableDefault ? <div className="cap-warning prominent"><AlertTriangle size={18} /><span><strong>Your default agent, Job Hunter, is unavailable</strong><small>Choose another available agent. JobOS will not switch it for you.</small></span></div> : null}
        {!available.length ? <div className="cap-empty compact"><LockKeyhole size={24} /><h3>No agent is ready</h3><p>Connect or recover an agent before opening a new chat.</p><button className="cap-primary">Open Connected Agents</button></div> : <>
          <fieldset className="cap-agent-choices"><legend>Agent</legend>{available.map(agent => <label className={selected?.id === agent.id ? 'selected' : ''} key={agent.id}><input checked={selected?.id === agent.id} name="agent" onChange={() => setSelectedId(agent.id)} type="radio" /><AgentIdentity agent={agent} /><span>{agent.profileDefault && !unavailableDefault ? 'Profile default' : 'Available'}</span></label>)}</fieldset>
          {selected ? <div className="cap-chat-config"><ModelSelect agent={selected} label="Model for this chat" /><label className="cap-field"><span>Reasoning effort</span><select defaultValue="Medium"><option>Low</option><option>Medium</option><option>High</option></select></label></div> : null}
          <div className="cap-seal-note"><LockKeyhole size={16} /><span><strong>Locked after creation</strong><small>To use another agent or model later, start a new chat.</small></span></div>
        </>}
        <footer><button className="cap-text">Cancel</button><button className="cap-primary" disabled={!selected}><MessageSquarePlus size={15} /> Create chat</button></footer>
      </section>
    </div>
  )
}

function ChatIdentitySurface({ agents, scenario }: { agents: PrototypeAgent[]; scenario: Scenario }) {
  const agent = agents.find(item => item.id === (scenario === 'migrated' ? 'codex' : 'hermes-jobhunter')) ?? baseAgents[0]!
  const locked = agent.status !== 'connected'
  return (
    <div className="cap-chat-preview">
      <aside className="cap-chat-list">
        <header><strong>Agent Chat</strong><button className="cap-icon"><Plus size={17} /></button></header>
        <button className="selected"><span className={`cap-mini-avatar ${agent.provider}`}>{agent.avatar}</span><span><strong>Tailor resume for Linear</strong><small>{agent.name} · {agent.defaultModel.replace(' · Medium', '')}</small></span>{locked ? <LockKeyhole size={14} /> : <span className="cap-live-dot" />}</button>
        <button><span className="cap-mini-avatar hermes">🧠</span><span><strong>Interview practice</strong><small>Resume Coach · GPT 5.6 Sol</small></span></button>
      </aside>
      <section className="cap-chat-room">
        <header><AgentIdentity agent={agent} /><div><span>{agent.defaultModel}</span><StatusPill status={agent.status} /></div></header>
        {scenario === 'migrated' ? <div className="cap-migration-note"><RefreshCcw size={16} /><span><strong>Preserved from your existing JobOS setup</strong><small>We kept the original agent and model. JobOS did not substitute another connection.</small></span></div> : null}
        <div className="cap-transcript"><article className="user"><small>You</small><p>Tailor my resume for this product role.</p></article><article className="assistant"><span className={`cap-mini-avatar ${agent.provider}`}>{agent.avatar}</span><div><small>{agent.name}</small><p>I reviewed the role and your Career Profile. I’ll emphasize agent-platform leadership and measurable product outcomes.</p></div></article></div>
        {locked ? <div className="cap-locked-composer"><LockKeyhole size={19} /><span><strong>{agent.detail}</strong><small>This history is safe and readable. Reconnect this exact agent to continue.</small></span><button className="cap-primary">Reconnect {agent.name}</button></div> : <div className="cap-composer"><textarea aria-label="Message agent" placeholder={`Message ${agent.name}…`} /><button className="cap-primary">Send</button></div>}
      </section>
    </div>
  )
}

function PrototypeSwitcher({ variant, setVariant }: { variant: Variant; setVariant: (variant: Variant) => void }) {
  const index = variants.findIndex(item => item.id === variant)
  const move = useCallback((delta: number) => setVariant(variants[(index + delta + variants.length) % variants.length]!.id), [index, setVariant])
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes((event.target as HTMLElement).tagName) || (event.target as HTMLElement).isContentEditable) return
      if (event.key === 'ArrowLeft') move(-1)
      if (event.key === 'ArrowRight') move(1)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [move])
  return (
    <div className="cap-switcher" role="toolbar" aria-label="Prototype variants">
      <button aria-label="Previous variant" onClick={() => move(-1)}><ArrowLeft size={16} /></button>
      <span><strong>{variant} · {variants[index]?.name}</strong><small>{variants[index]?.thesis}</small></span>
      <button aria-label="Next variant" onClick={() => move(1)}><ArrowRight size={16} /></button>
    </div>
  )
}

export function ConnectedAgentsPrototype() {
  const query = new URLSearchParams(window.location.search)
  const initialVariant = query.get('variant')?.toUpperCase()
  const initialSurface = query.get('surface')
  const initialScenario = query.get('scenario')
  const [variant, setVariantState] = useState<Variant>(initialVariant === 'B' || initialVariant === 'C' ? initialVariant : 'A')
  const [surface, setSurface] = useState<Surface>(initialSurface === 'new-chat' || initialSurface === 'chat' ? initialSurface : 'agents')
  const [scenario, setScenario] = useState<Scenario>(initialScenario && initialScenario in scenarioLabels ? initialScenario as Scenario : 'ready')
  const [selectedId, setSelectedId] = useState('hermes-jobhunter')
  const [deviceCode, setDeviceCode] = useState(false)
  const [disconnecting, setDisconnecting] = useState<string | null>(null)
  const agents = useMemo(() => agentsForScenario(scenario), [scenario])
  const setVariant = (next: Variant) => {
    setVariantState(next)
    const url = new URL(window.location.href)
    url.searchParams.set('prototype', 'connected-agents')
    url.searchParams.set('variant', next)
    window.history.replaceState(null, '', url)
  }
  const common: VariantProps = { agents, scenario, selectedId, setSelectedId, deviceCode, setDeviceCode, disconnecting, setDisconnecting }
  return (
    <AppChrome scenario={scenario} setScenario={value => { setScenario(value); setDisconnecting(null); setDeviceCode(false) }}>
      <div className="cap-prototype-head"><div><span className="cap-eyebrow">Settings</span><h1>Connected Agents</h1><p>Manage the agents available across this JobOS installation.</p></div><SurfaceNav surface={surface} setSurface={setSurface} /></div>
      <div className="cap-prototype-body">
        {surface === 'agents' && scenario === 'host-offline' ? (
          <div className="cap-warning prominent" role="alert">
            <CloudOff size={18} />
            <span><strong>JobOS host unavailable</strong><small>Make sure the Mac host is awake and connected to Tailscale. Your saved history remains readable.</small></span>
            <button className="cap-secondary"><RefreshCcw size={14} /> Retry host connection</button>
          </div>
        ) : null}
        {surface === 'agents' && variant === 'A' ? <VariantA {...common} /> : null}
        {surface === 'agents' && variant === 'B' ? <VariantB {...common} /> : null}
        {surface === 'agents' && variant === 'C' ? <VariantC {...common} /> : null}
        {surface === 'new-chat' ? <NewChatSurface agents={agents} scenario={scenario} variant={variant} /> : null}
        {surface === 'chat' ? <ChatIdentitySurface agents={agents} scenario={scenario} /> : null}
      </div>
      <PrototypeSwitcher variant={variant} setVariant={setVariant} />
    </AppChrome>
  )
}
