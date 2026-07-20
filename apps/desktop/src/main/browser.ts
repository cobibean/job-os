import { randomUUID } from 'node:crypto'
import path from 'node:path'

import type {
  BrowserWindow,
  Dialog,
  DownloadItem,
  Session,
  WebContents,
  WebContentsView,
  WebPreferences
} from 'electron'

import type {
  BrowserBounds,
  BrowserDownload,
  BrowserRestoreState,
  BrowserState,
  BrowserTab,
  BrowserTabMetadata
} from '../shared/contracts.js'

export const BROWSER_PARTITION = 'persist:jobos-browser-v1'
export const DEFAULT_BROWSER_URL = 'https://www.google.com/'

export function remoteBrowserPreferences(): WebPreferences {
  return {
    allowRunningInsecureContent: false,
    contextIsolation: true,
    devTools: false,
    javascript: true,
    nodeIntegration: false,
    partition: BROWSER_PARTITION,
    sandbox: true,
    webSecurity: true,
    webviewTag: false
  }
}

export function normalizeBrowserInput(input: string): string {
  const value = input.trim()
  if (!value) return DEFAULT_BROWSER_URL
  try {
    const parsed = new URL(value)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.toString()
  } catch {
    // A hostname or search query is handled below.
  }
  if (/^[\w-]+(?:\.[\w-]+)+(?:[/:?#].*)?$/u.test(value)) {
    return new URL(`https://${value}`).toString()
  }
  return `https://www.google.com/search?q=${encodeURIComponent(value)}`
}

export function isOrdinaryWebUrl(value: string): boolean {
  if (value === 'about:blank') return true
  try {
    const protocol = new URL(value).protocol
    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

export function isBrowserTabMetadata(value: unknown): value is BrowserTabMetadata {
  if (!value || typeof value !== 'object') return false
  const tab = value as Partial<BrowserTabMetadata>
  return (
    typeof tab.tabId === 'string'
    && tab.tabId.length > 0
    && tab.tabId.length <= 128
    && typeof tab.url === 'string'
    && isOrdinaryWebUrl(tab.url)
    && typeof tab.title === 'string'
    && tab.title.length <= 512
    && (tab.faviconUrl === null || (typeof tab.faviconUrl === 'string' && isOrdinaryWebUrl(tab.faviconUrl)))
    && (tab.associatedJobId === null || (typeof tab.associatedJobId === 'string' && tab.associatedJobId.length <= 512))
  )
}

interface ManagedTab {
  state: BrowserTab
  view: WebContentsView
}

interface BrowserManagerOptions {
  window: BrowserWindow
  browserSession: Session
  createView: () => WebContentsView
  dialog: Pick<Dialog, 'showSaveDialog'>
  downloadsPath: string
}

export class BrowserManager {
  readonly #window: BrowserWindow
  readonly #session: Session
  readonly #createView: () => WebContentsView
  readonly #dialog: Pick<Dialog, 'showSaveDialog'>
  readonly #downloadsPath: string
  readonly #tabs = new Map<string, ManagedTab>()
  #order: string[] = []
  #activeTabId: string | null = null
  #attachedTabId: string | null = null
  #bounds: BrowserBounds = { x: 0, y: 0, width: 0, height: 0, visible: false }
  #download: BrowserDownload | null = null
  #notice: string | null = null
  readonly #downloadHandler = (_event: Electron.Event, item: DownloadItem, contents: WebContents) => this.#handleDownload(item, contents)

  constructor(options: BrowserManagerOptions) {
    this.#window = options.window
    this.#session = options.browserSession
    this.#createView = options.createView
    this.#dialog = options.dialog
    this.#downloadsPath = options.downloadsPath
    this.#installSessionPolicies()
  }

  getState(): BrowserState {
    return {
      tabs: this.#order.flatMap(tabId => {
        const tab = this.#tabs.get(tabId)
        return tab ? [{ ...tab.state }] : []
      }),
      activeTabId: this.#activeTabId,
      download: this.#download ? { ...this.#download } : null,
      notice: this.#notice
    }
  }

  async restore(restored: BrowserRestoreState): Promise<BrowserState> {
    if (this.#order.length) return this.getState()
    const unique = new Set<string>()
    for (const tab of restored.tabs.slice(0, 50)) {
      if (!isBrowserTabMetadata(tab) || unique.has(tab.tabId)) continue
      unique.add(tab.tabId)
      this.#createTab(tab)
    }
    if (!this.#order.length) this.#createTab({
      tabId: randomUUID(),
      url: DEFAULT_BROWSER_URL,
      title: 'Google',
      faviconUrl: null,
      associatedJobId: null
    })
    this.#activeTabId = restored.activeTabId && this.#tabs.has(restored.activeTabId)
      ? restored.activeTabId
      : this.#order[0] ?? null
    this.#syncAttachedView()
    this.#emit()
    return this.getState()
  }

  async create(url = DEFAULT_BROWSER_URL, associatedJobId: string | null = null): Promise<BrowserState> {
    const tabId = randomUUID()
    this.#createTab({ tabId, url: normalizeBrowserInput(url), title: 'New tab', faviconUrl: null, associatedJobId })
    this.#activeTabId = tabId
    this.#syncAttachedView()
    this.#emit()
    return this.getState()
  }

  select(tabId: string): BrowserState {
    this.#requireTab(tabId)
    this.#activeTabId = tabId
    this.#syncAttachedView()
    this.#emit()
    return this.getState()
  }

  async close(tabId: string): Promise<BrowserState> {
    const index = this.#order.indexOf(tabId)
    const tab = this.#requireTab(tabId)
    if (this.#attachedTabId === tabId) {
      this.#window.contentView.removeChildView(tab.view)
      this.#attachedTabId = null
    }
    tab.view.webContents.close({ waitForBeforeUnload: false })
    this.#tabs.delete(tabId)
    this.#order = this.#order.filter(id => id !== tabId)
    if (this.#activeTabId === tabId) {
      this.#activeTabId = this.#order[Math.min(index, this.#order.length - 1)] ?? null
    }
    if (!this.#order.length) return this.create()
    this.#syncAttachedView()
    this.#emit()
    return this.getState()
  }

  reorder(tabIds: string[]): BrowserState {
    if (tabIds.length !== this.#order.length || new Set(tabIds).size !== tabIds.length || tabIds.some(id => !this.#tabs.has(id))) {
      throw new Error('Tab order must contain every open tab exactly once')
    }
    this.#order = [...tabIds]
    this.#emit()
    return this.getState()
  }

  async navigate(tabId: string, input: string): Promise<BrowserState> {
    const tab = this.#requireTab(tabId)
    const url = normalizeBrowserInput(input)
    tab.state.error = null
    tab.state.crashed = false
    await tab.view.webContents.loadURL(url).catch(() => undefined)
    return this.getState()
  }

  back(tabId: string): BrowserState {
    const contents = this.#requireTab(tabId).view.webContents
    if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack()
    return this.getState()
  }

  forward(tabId: string): BrowserState {
    const contents = this.#requireTab(tabId).view.webContents
    if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward()
    return this.getState()
  }

  reload(tabId: string): BrowserState {
    const tab = this.#requireTab(tabId)
    tab.state.error = null
    tab.state.crashed = false
    tab.view.webContents.reload()
    this.#emit()
    return this.getState()
  }

  stop(tabId: string): BrowserState {
    this.#requireTab(tabId).view.webContents.stop()
    return this.getState()
  }

  associate(tabId: string, jobId: string | null): BrowserState {
    const tab = this.#requireTab(tabId)
    tab.state.associatedJobId = jobId
    this.#emit()
    return this.getState()
  }

  setBounds(bounds: BrowserBounds): void {
    this.#bounds = {
      x: Math.max(0, Math.round(bounds.x)),
      y: Math.max(0, Math.round(bounds.y)),
      width: Math.max(0, Math.round(bounds.width)),
      height: Math.max(0, Math.round(bounds.height)),
      visible: Boolean(bounds.visible)
    }
    this.#syncAttachedView()
  }

  dispose(): void {
    this.#session.removeListener('will-download', this.#downloadHandler)
    for (const tab of this.#tabs.values()) {
      if (!tab.view.webContents.isDestroyed()) tab.view.webContents.close()
    }
    this.#tabs.clear()
    this.#order = []
  }

  #createTab(restored: BrowserTabMetadata): void {
    const view = this.#createView()
    const state: BrowserTab = {
      ...restored,
      loading: false,
      canGoBack: false,
      canGoForward: false,
      error: null,
      crashed: false
    }
    const managed = { view, state }
    this.#tabs.set(state.tabId, managed)
    this.#order.push(state.tabId)
    this.#wireTab(managed)
    void view.webContents.loadURL(state.url).catch(() => undefined)
  }

  #wireTab(tab: ManagedTab): void {
    const contents = tab.view.webContents
    const refresh = () => {
      if (contents.isDestroyed()) return
      const currentUrl = contents.getURL()
      if (isOrdinaryWebUrl(currentUrl)) tab.state.url = currentUrl
      tab.state.canGoBack = contents.navigationHistory.canGoBack()
      tab.state.canGoForward = contents.navigationHistory.canGoForward()
      this.#emit()
    }
    contents.on('did-start-loading', () => { tab.state.loading = true; tab.state.error = null; this.#notice = null; refresh() })
    contents.on('did-stop-loading', () => { tab.state.loading = false; refresh() })
    contents.on('did-navigate', refresh)
    contents.on('did-navigate-in-page', refresh)
    contents.on('page-title-updated', (_event, title) => { tab.state.title = title || 'Untitled'; this.#emit() })
    contents.on('page-favicon-updated', (_event, favicons) => {
      tab.state.faviconUrl = favicons.find(isOrdinaryWebUrl) ?? null
      this.#emit()
    })
    contents.on('did-fail-load', (_event, code, description, validatedUrl, isMainFrame) => {
      if (!isMainFrame || code === -3) return
      tab.state.loading = false
      if (isOrdinaryWebUrl(validatedUrl)) tab.state.url = validatedUrl
      tab.state.error = `Page unavailable: ${description}`
      this.#emit()
    })
    contents.on('render-process-gone', (_event, details) => {
      tab.state.loading = false
      tab.state.crashed = true
      tab.state.error = `This page stopped working (${details.reason}). Reload to recover.`
      this.#emit()
    })
    contents.on('unresponsive', () => { tab.state.error = 'This page is not responding. Reload or close the tab.'; this.#emit() })
    contents.on('responsive', () => { if (!tab.state.crashed) tab.state.error = null; this.#emit() })
    contents.on('will-navigate', (event, url) => {
      if (!isOrdinaryWebUrl(url)) {
        event.preventDefault()
        tab.state.error = 'External protocols are blocked. Copy the link and open it in the appropriate app.'
        this.#emit()
      }
    })
    contents.setWindowOpenHandler(({ url }) => {
      if (isOrdinaryWebUrl(url)) void this.create(url, tab.state.associatedJobId)
      else {
        tab.state.error = 'This site requested an external protocol, which JobOS blocked.'
        this.#emit()
      }
      return { action: 'deny' }
    })
  }

  #syncAttachedView(): void {
    const shouldAttach = this.#bounds.visible && this.#bounds.width > 0 && this.#bounds.height > 0 && this.#activeTabId
    if (this.#attachedTabId && this.#attachedTabId !== (shouldAttach ? this.#activeTabId : null)) {
      const attached = this.#tabs.get(this.#attachedTabId)
      if (attached) this.#window.contentView.removeChildView(attached.view)
      this.#attachedTabId = null
    }
    if (!shouldAttach || !this.#activeTabId) return
    const active = this.#requireTab(this.#activeTabId)
    if (this.#attachedTabId !== this.#activeTabId) {
      this.#window.contentView.addChildView(active.view)
      this.#attachedTabId = this.#activeTabId
    }
    active.view.setBounds({ x: this.#bounds.x, y: this.#bounds.y, width: this.#bounds.width, height: this.#bounds.height })
  }

  #installSessionPolicies(): void {
    this.#session.setPermissionCheckHandler(() => false)
    this.#session.setPermissionRequestHandler((contents, permission, callback) => {
      const tab = [...this.#tabs.values()].find(candidate => candidate.view.webContents === contents)
      this.#notice = `JobOS blocked ${permission} permission${tab ? ` for ${tab.state.title}` : ''}.`
      this.#emit()
      callback(false)
    })
    this.#session.on('will-download', this.#downloadHandler)
  }

  #handleDownload(item: DownloadItem, contents: WebContents): void {
    const id = randomUUID()
    const filename = item.getFilename() || 'download'
    const update = (state: BrowserDownload['state'], message?: string) => {
      this.#download = {
        id,
        filename,
        state,
        receivedBytes: item.getReceivedBytes(),
        totalBytes: item.getTotalBytes(),
        message
      }
      this.#emit()
    }
    item.pause()
    update('starting')
    void this.#dialog.showSaveDialog(this.#window, {
      title: `Save ${filename}`,
      defaultPath: path.join(this.#downloadsPath, filename)
    }).then(result => {
      if (result.canceled || !result.filePath) {
        item.cancel()
        update('cancelled', 'Download cancelled')
        return
      }
      item.setSavePath(result.filePath)
      item.resume()
    }).catch(() => {
      item.cancel()
      update('failed', 'Could not choose a download location')
    })
    item.on('updated', (_event, state) => update(state === 'interrupted' ? 'interrupted' : 'progressing'))
    item.once('done', (_event, state) => update(state === 'completed' ? 'completed' : state === 'cancelled' ? 'cancelled' : 'interrupted'))
    if (contents.isDestroyed()) update('failed', 'The page closed before the download started')
  }

  #requireTab(tabId: string): ManagedTab {
    const tab = this.#tabs.get(tabId)
    if (!tab) throw new Error('Browser tab not found')
    return tab
  }

  #emit(): void {
    if (!this.#window.isDestroyed()) this.#window.webContents.send('jobos:browser:state', this.getState())
  }
}
