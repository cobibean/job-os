import { useEffect, useRef, useState, type KeyboardEvent, type SyntheticEvent } from 'react'
import { createPortal } from 'react-dom'
import { ArrowLeft, ArrowRight, BriefcaseBusiness, Check, Download, Globe2, LoaderCircle, Plus, RefreshCw, Search, Square, X } from 'lucide-react'

import type { BrowserTab, JobListItem } from '../../shared/contracts'
import type { SaveFeedback } from '../jobs/save-from-browser/useSaveJobFromBrowser'
import { browserRepairMessage, type BrowserRepairReason } from '../workspace/workspaceLayout'
import type { BrowserController } from './useBrowser'

interface BrowserWorkspaceProps {
  browser: BrowserController
  browserRepaired: boolean
  browserRepairReasons: BrowserRepairReason[]
  jobs: JobListItem[]
  onSaveJob: (tab: BrowserTab) => void | Promise<void>
  saveStates: Record<string, SaveFeedback>
}

export function BrowserWorkspace(props: BrowserWorkspaceProps) {
  const { browser } = props
  const [address, setAddress] = useState('')
  const [tooltip, setTooltip] = useState<{ text: string, x: number, y: number } | null>(null)
  const tabRefs = useRef(new Map<string, HTMLButtonElement>())
  const active = browser.activeTab
  const activeJob = props.jobs.find(item => item.jobId === active?.associatedJobId)
  const saveState: SaveFeedback = active && props.saveStates[active.tabId]
    ? props.saveStates[active.tabId]!
    : active?.associatedJobId
      ? { status: 'saved', message: activeJob ? `Saved to JobOS: ${activeJob.company} · ${activeJob.title}` : 'Saved to JobOS' }
      : { status: 'idle', message: '' }

  useEffect(() => {
    setAddress(active?.url ?? '')
  }, [active?.tabId, active?.url])

  const moveTab = (tabId: string, delta: number) => {
    const order = browser.state.tabs.map(tab => tab.tabId)
    const from = order.indexOf(tabId)
    const to = Math.max(0, Math.min(order.length - 1, from + delta))
    if (from === to) return
    order.splice(from, 1)
    order.splice(to, 0, tabId)
    browser.reorder(order)
  }

  const focusAndSelectTab = (index: number) => {
    const tab = browser.state.tabs[index]
    if (!tab) return
    browser.select(tab.tabId)
    tabRefs.current.get(tab.tabId)?.focus()
  }

  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const last = browser.state.tabs.length - 1
    const target = event.key === 'ArrowRight' ? Math.min(last, index + 1)
      : event.key === 'ArrowLeft' ? Math.max(0, index - 1)
        : event.key === 'Home' ? 0
          : event.key === 'End' ? last
            : null
    if (target !== null) {
      event.preventDefault()
      focusAndSelectTab(target)
    } else if (event.key === 'Delete') {
      event.preventDefault()
      const closing = browser.state.tabs[index]
      if (!closing) return
      const next = browser.state.tabs[index === last ? index - 1 : index + 1]
      browser.close(closing.tabId)
      if (next) tabRefs.current.get(next.tabId)?.focus()
    }
  }

  const activeIndex = browser.state.tabs.findIndex(tab => tab.tabId === browser.state.activeTabId)
  const tooltipTrigger = (text: string) => ({
    'aria-describedby': 'browser-control-tooltip',
    onBlur: () => setTooltip(null),
    onFocus: (event: SyntheticEvent<HTMLElement>) => showTooltip(event.currentTarget, text),
    onMouseEnter: (event: SyntheticEvent<HTMLElement>) => showTooltip(event.currentTarget, text),
    onMouseLeave: () => setTooltip(null)
  })
  const showTooltip = (element: HTMLElement, text: string) => {
    const bounds = element.getBoundingClientRect()
    setTooltip({
      text,
      x: Math.max(90, Math.min(window.innerWidth - 90, bounds.left + bounds.width / 2)),
      y: bounds.bottom + 6
    })
  }

  return (
    <main className="browser-workspace panel-region">
      <div className="browser-tabs">
        <div className="browser-tab-list" role="tablist" aria-label="Browser tabs">
          {browser.state.tabs.map((tab, index) => (
          <button
            aria-controls="browser-tabpanel"
            aria-selected={tab.tabId === browser.state.activeTabId}
            aria-label={`Select ${tab.title}`}
            className={`browser-tab${tab.tabId === browser.state.activeTabId ? ' active' : ''}`}
            data-tooltip={`Select ${tab.title}`}
            id={`browser-tab-${tab.tabId}`}
            key={tab.tabId}
            onClick={() => browser.select(tab.tabId)}
            onKeyDown={event => onTabKeyDown(event, index)}
            ref={element => { if (element) tabRefs.current.set(tab.tabId, element); else tabRefs.current.delete(tab.tabId) }}
            role="tab"
            tabIndex={tab.tabId === browser.state.activeTabId ? 0 : -1}
            type="button"
            {...tooltipTrigger(`Select ${tab.title}`)}
          >
            {tab.faviconUrl ? <img alt="" src={tab.faviconUrl} /> : <Globe2 aria-hidden="true" size={14} />}
            <span>{tab.title || 'New tab'}</span>
            {tab.loading ? <LoaderCircle aria-hidden="true" className="spin" size={12} /> : null}
          </button>
          ))}
        </div>
        {active ? <div className="browser-tab-actions" aria-label={`Actions for ${active.title}`} role="group">
          <button aria-label={`Move ${active.title} left`} className="tab-order" data-tooltip={`Move ${active.title} left`} disabled={activeIndex <= 0} onClick={() => moveTab(active.tabId, -1)} type="button" {...tooltipTrigger(`Move ${active.title} left`)}><ArrowLeft aria-hidden="true" size={11} /></button>
          <button aria-label={`Move ${active.title} right`} className="tab-order" data-tooltip={`Move ${active.title} right`} disabled={activeIndex === browser.state.tabs.length - 1} onClick={() => moveTab(active.tabId, 1)} type="button" {...tooltipTrigger(`Move ${active.title} right`)}><ArrowRight aria-hidden="true" size={11} /></button>
          <button aria-label={`Close ${active.title}`} className="tab-close" data-tooltip={`Close ${active.title}`} onClick={() => browser.close(active.tabId)} type="button" {...tooltipTrigger(`Close ${active.title}`)}><X aria-hidden="true" size={13} /></button>
        </div> : null}
        <button aria-label="Open a new tab" className="icon-button browser-tab-add" data-tooltip="Open a new tab" disabled={!browser.bridgeAvailable} onClick={() => browser.create()} type="button" {...tooltipTrigger('Open a new tab')}><Plus aria-hidden="true" size={16} /></button>
      </div>

      <div className="browser-toolbar">
        <button aria-label="Back" className="icon-button" data-tooltip="Back" disabled={!active?.canGoBack} onClick={() => active && browser.back(active.tabId)} type="button" {...tooltipTrigger('Back')}><ArrowLeft aria-hidden="true" size={15} /></button>
        <button aria-label="Forward" className="icon-button" data-tooltip="Forward" disabled={!active?.canGoForward} onClick={() => active && browser.forward(active.tabId)} type="button" {...tooltipTrigger('Forward')}><ArrowRight aria-hidden="true" size={15} /></button>
        <button aria-label={active?.loading ? 'Stop loading' : 'Reload'} className="icon-button" data-tooltip={active?.loading ? 'Stop loading' : 'Reload'} disabled={!active} onClick={() => active && (active.loading ? browser.stop(active.tabId) : browser.reload(active.tabId))} type="button" {...tooltipTrigger(active?.loading ? 'Stop loading' : 'Reload')}>
          {active?.loading ? <Square aria-hidden="true" size={13} /> : <RefreshCw aria-hidden="true" size={14} />}
        </button>
        <form className="address-form" onSubmit={event => { event.preventDefault(); if (active) browser.navigate(active.tabId, address) }}>
          <Globe2 aria-hidden="true" size={14} />
          <input aria-label="Address and search" disabled={!active} onChange={event => setAddress(event.target.value)} spellCheck="false" value={address} />
        </form>
        <button
          aria-label="Save this job to JobOS"
          className="save-job-button"
          data-state={saveState.status}
          disabled={!active || active.loading || Boolean(active.associatedJobId) || saveState.status === 'saving'}
          onClick={() => { if (active) void props.onSaveJob(active) }}
          type="button"
        >
          {saveState.status === 'saving'
            ? <LoaderCircle aria-hidden="true" className="spin" size={14} />
            : saveState.status === 'saved' || saveState.status === 'existing'
              ? <Check aria-hidden="true" size={14} />
              : <BriefcaseBusiness aria-hidden="true" size={14} />}
          <span>{saveState.status === 'saving' ? 'Agent working…' : saveState.status === 'saved' || saveState.status === 'existing' ? 'Saved' : 'Save job'}</span>
        </button>
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

      {saveState.message ? (
        <div className={`browser-save-feedback ${saveState.status}${saveState.status === 'saved' || saveState.status === 'existing' ? ' compact-success' : ''}`} role={saveState.status === 'error' ? 'alert' : 'status'}>
          {saveState.status === 'saved' || saveState.status === 'existing' ? <Check aria-hidden="true" size={13} /> : null}
          <span>{saveState.message}</span>
        </div>
      ) : null}

      {browser.state.download ? (
        <div className={`download-status ${browser.state.download.state}`} role="status">
          <Download aria-hidden="true" size={13} />
          <span>{browser.state.download.filename} · {browser.state.download.state}</span>
        </div>
      ) : null}
      {browser.state.notice ? <div className="browser-notice" role="status">{browser.state.notice}</div> : null}
      {browserRepairMessage(props.browserRepairReasons, props.browserRepaired) ? <div className="browser-notice" role="status">{browserRepairMessage(props.browserRepairReasons, props.browserRepaired)}</div> : null}

      <div aria-labelledby={active ? `browser-tab-${active.tabId}` : undefined} className="browser-viewport" id="browser-tabpanel" ref={browser.viewportRef} role="tabpanel">
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
            {active.blockedUrl ? <p className="blocked-link">{active.blockedUrl}</p> : null}
            <div>{active.blockedUrl ? <button onClick={() => browser.copyBlockedUrl(active.tabId)} type="button">Copy link</button> : <button onClick={() => browser.reload(active.tabId)} type="button">Reload page</button>}<button onClick={() => browser.close(active.tabId)} type="button">Close tab</button></div>
          </section>
        ) : null}
      </div>
      <p aria-live="polite" className="browser-announcement">{browser.message}</p>
      {tooltip ? createPortal(
        <div className="browser-tooltip" id="browser-control-tooltip" role="tooltip" style={{ left: tooltip.x, top: tooltip.y }}>{tooltip.text}</div>,
        document.body
      ) : null}
    </main>
  )
}
