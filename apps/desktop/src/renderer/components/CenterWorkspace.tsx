import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, Download, FileText, Globe2, LoaderCircle, Plus, RefreshCw, Search, Square, X } from 'lucide-react'

import type { BrowserRestoreState, JobListItem } from '../../shared/contracts'
import { useBrowser } from '../hooks/useBrowser'

interface CenterWorkspaceProps {
  activeSurface: 'browser' | 'document'
  browserState: BrowserRestoreState
  browserVisible: boolean
  jobs: JobListItem[]
  layoutSignal: string
  workspaceHydrated: boolean
  onBrowserPersist: (state: BrowserRestoreState) => void
}

export function CenterWorkspace(props: CenterWorkspaceProps) {
  const browser = useBrowser(
    props.browserState,
    props.workspaceHydrated,
    props.activeSurface === 'browser' && props.browserVisible,
    props.layoutSignal,
    props.onBrowserPersist
  )
  const [address, setAddress] = useState('')
  const active = browser.activeTab

  useEffect(() => setAddress(active?.url ?? ''), [active?.tabId, active?.url])

  if (props.activeSurface === 'document') {
    return (
      <main className="center-workspace document-placeholder panel-region">
        <div className="workspace-tabs"><span className="surface-tab active"><FileText aria-hidden="true" size={15} /> Document review</span></div>
        <section className="workspace-empty">
          <span className="empty-orbit"><FileText aria-hidden="true" size={23} /></span>
          <h1>Document workspace</h1>
          <p>Faithful resume preview arrives in Phase 5. Your live browser tabs remain open in the background.</p>
        </section>
      </main>
    )
  }

  const moveTab = (tabId: string, delta: number) => {
    const order = browser.state.tabs.map(tab => tab.tabId)
    const from = order.indexOf(tabId)
    const to = Math.max(0, Math.min(order.length - 1, from + delta))
    if (from === to) return
    order.splice(from, 1)
    order.splice(to, 0, tabId)
    browser.reorder(order)
  }

  return (
    <main className="browser-workspace panel-region">
      <div className="browser-tabs" role="tablist" aria-label="Browser tabs">
        {browser.state.tabs.map((tab, index) => (
          <div
            aria-selected={tab.tabId === browser.state.activeTabId}
            className={`browser-tab${tab.tabId === browser.state.activeTabId ? ' active' : ''}`}
            key={tab.tabId}
            role="tab"
          >
            <button aria-label={`Select ${tab.title}`} className="browser-tab-select" onClick={() => browser.select(tab.tabId)} type="button">
              {tab.faviconUrl ? <img alt="" src={tab.faviconUrl} /> : <Globe2 aria-hidden="true" size={14} />}
              <span>{tab.title || 'New tab'}</span>
              {tab.loading ? <LoaderCircle aria-hidden="true" className="spin" size={12} /> : null}
            </button>
            <button aria-label={`Move ${tab.title} left`} className="tab-order" disabled={index === 0} onClick={() => moveTab(tab.tabId, -1)} type="button"><ArrowLeft aria-hidden="true" size={11} /></button>
            <button aria-label={`Move ${tab.title} right`} className="tab-order" disabled={index === browser.state.tabs.length - 1} onClick={() => moveTab(tab.tabId, 1)} type="button"><ArrowRight aria-hidden="true" size={11} /></button>
            <button aria-label={`Close ${tab.title}`} className="tab-close" onClick={() => browser.close(tab.tabId)} type="button"><X aria-hidden="true" size={13} /></button>
          </div>
        ))}
        <button aria-label="Open a new tab" className="icon-button browser-tab-add" disabled={!browser.bridgeAvailable} onClick={() => browser.create()} type="button"><Plus aria-hidden="true" size={16} /></button>
      </div>

      <div className="browser-toolbar">
        <button aria-label="Back" className="icon-button" disabled={!active?.canGoBack} onClick={() => active && browser.back(active.tabId)} type="button"><ArrowLeft aria-hidden="true" size={15} /></button>
        <button aria-label="Forward" className="icon-button" disabled={!active?.canGoForward} onClick={() => active && browser.forward(active.tabId)} type="button"><ArrowRight aria-hidden="true" size={15} /></button>
        <button aria-label={active?.loading ? 'Stop loading' : 'Reload'} className="icon-button" disabled={!active} onClick={() => active && (active.loading ? browser.stop(active.tabId) : browser.reload(active.tabId))} type="button">
          {active?.loading ? <Square aria-hidden="true" size={13} /> : <RefreshCw aria-hidden="true" size={14} />}
        </button>
        <form className="address-form" onSubmit={event => { event.preventDefault(); if (active) browser.navigate(active.tabId, address) }}>
          <Globe2 aria-hidden="true" size={14} />
          <input aria-label="Address and search" disabled={!active} onChange={event => setAddress(event.target.value)} spellCheck="false" value={address} />
        </form>
        <select
          aria-label="Associate active tab with a job"
          className="tab-association"
          disabled={!active}
          onChange={event => active && browser.associate(active.tabId, event.target.value || null)}
          value={active?.associatedJobId ?? ''}
        >
          <option value="">No job</option>
          {props.jobs.map(job => <option key={job.jobId} value={job.jobId}>{job.company}</option>)}
        </select>
      </div>

      {browser.state.download ? (
        <div className={`download-status ${browser.state.download.state}`} role="status">
          <Download aria-hidden="true" size={13} />
          <span>{browser.state.download.filename} · {browser.state.download.state}</span>
        </div>
      ) : null}
      {browser.state.notice ? <div className="browser-notice" role="status">{browser.state.notice}</div> : null}

      <div className="browser-viewport" ref={browser.viewportRef}>
        {!browser.bridgeAvailable ? (
          <section className="workspace-empty browser-fallback">
            <span className="empty-orbit"><Search aria-hidden="true" size={23} /></span>
            <h1>Browser available in the desktop app</h1>
            <p>The trusted web surface is owned by Electron and is not available in this renderer-only preview.</p>
          </section>
        ) : active?.error ? (
          <section className="browser-error" role="alert">
            <Globe2 aria-hidden="true" size={26} />
            <h1>{active.crashed ? 'Page stopped working' : 'Page unavailable'}</h1>
            <p>{active.error}</p>
            <div><button onClick={() => browser.reload(active.tabId)} type="button">Reload page</button><button onClick={() => browser.close(active.tabId)} type="button">Close tab</button></div>
          </section>
        ) : null}
      </div>
      <p aria-live="polite" className="browser-announcement">{browser.message}</p>
    </main>
  )
}
