import { CheckCircle2, CircleAlert, LoaderCircle, Settings } from 'lucide-react'

import type { ConnectivityState } from '../../shared/contracts'

interface StatusBarProps {
  apiVersion?: string
  message?: string
  onOpenSettings?: () => void
  state: ConnectivityState
}

function ConnectionLabel({ apiVersion, state }: Pick<StatusBarProps, 'apiVersion' | 'state'>) {
  if (state === 'connecting') {
    return <><LoaderCircle aria-hidden="true" className="spin" size={14} /> Connecting to Mac Mini…</>
  }
  if (state === 'connected') {
    return <><CheckCircle2 aria-hidden="true" size={14} /> Mac Mini connected <span className="api-version">API {apiVersion}</span></>
  }
  if (state === 'degraded') {
    return <><CircleAlert aria-hidden="true" size={14} /> Mac Mini authentication failed</>
  }
  return <><CircleAlert aria-hidden="true" size={14} /> Mac Mini unavailable</>
}

export function StatusBar({ apiVersion, message, onOpenSettings, state }: StatusBarProps) {
  return (
    <footer className="status-bar">
      <button aria-label="Open settings" className="icon-button settings-button" onClick={onOpenSettings} type="button">
        <Settings aria-hidden="true" size={16} strokeWidth={1.5} />
      </button>
      <span className={`connection-state ${state}`} role="status" title={message}>
        <ConnectionLabel apiVersion={apiVersion} state={state} />
      </span>
    </footer>
  )
}
