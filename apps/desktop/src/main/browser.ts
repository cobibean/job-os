import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { setTimeout as wait } from 'node:timers/promises'

import type {
  BrowserWindow,
  Clipboard,
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
  BrowserJobListing,
  BrowserRestoreState,
  BrowserSemanticSnapshot,
  BrowserState,
  BrowserTab,
  BrowserTabMetadata
} from '../shared/contracts.js'
import {
  BROWSER_PERSISTENCE_LIMITS,
  BROWSER_SAFE_TITLE_FALLBACK,
  recoverBrowserRestoreState,
  recoverBrowserTabMetadata,
  sanitizeBrowserMetadata,
  sanitizeBrowserTitleForPersistence,
  sanitizeBrowserUrlForPersistence
} from '../shared/browserPersistence.js'

export const BROWSER_PARTITION = 'persist:jobos-browser-v1'
export const DEFAULT_BROWSER_URL = 'https://www.google.com/'
const JOB_EXTRACTION_ATTEMPTS = 21
const JOB_EXTRACTION_RETRY_MS = 250

const JOB_EXTRACTION_SCRIPT = `(() => {
  const normalize = (value) => String(value ?? '').replace(/\\s+/gu, ' ').trim();
  const decode = (value) => {
    const textarea = document.createElement('textarea');
    textarea.innerHTML = String(value ?? '');
    return normalize(textarea.textContent || '');
  };
  const htmlText = (value) => {
    const source = String(value ?? '')
      .replace(/<br\\s*\\/?\\s*>/giu, ' ')
      .replace(/<\\/(?:address|article|aside|blockquote|div|h[1-6]|li|main|p|pre|section|td|tr)>/giu, ' ');
    const parsed = new DOMParser().parseFromString(source, 'text/html');
    parsed.querySelectorAll('script,style,noscript,template,[hidden],[aria-hidden="true"]').forEach(element => element.remove());
    parsed.querySelectorAll('[style]').forEach(element => {
      if (element.style.display === 'none' || element.style.visibility === 'hidden') element.remove();
    });
    return normalize(parsed.body?.textContent || '');
  };
  const text = (selector) => normalize(document.querySelector(selector)?.textContent || '');
  const elementHtml = (selector) => htmlText(document.querySelector(selector)?.innerHTML || '');
  const textWithin = (root, selector) => {
    for (const element of root?.querySelectorAll(selector) || []) {
      const value = normalize(element.textContent || '');
      if (value) return value;
    }
    return '';
  };
  const elementHtmlWithin = (root, selector) => htmlText(root?.querySelector(selector)?.innerHTML || '');
  const meta = (selector) => normalize(document.querySelector(selector)?.getAttribute('content') || '');
  const visible = (element) => {
    if (!element) return false;
    const style = getComputedStyle(element);
    return !element.hidden && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const commonAncestor = (first, second) => {
    if (!first || !second) return null;
    const firstAncestors = new Set();
    for (let node = first; node; node = node.parentElement) firstAncestors.add(node);
    for (let node = second; node; node = node.parentElement) {
      if (firstAncestors.has(node)) return node === document.body ? null : node;
    }
    return null;
  };
  const typeIsJobPosting = (value) => {
    const types = Array.isArray(value) ? value : [value];
    return types.some(type => String(type).toLowerCase().replace(/^.*[/#]/u, '') === 'jobposting');
  };
  const findPosting = (value) => {
    if (Array.isArray(value)) {
      for (const item of value) { const found = findPosting(item); if (found) return found; }
      return null;
    }
    if (!value || typeof value !== 'object') return null;
    if (typeIsJobPosting(value['@type'])) return value;
    if (Array.isArray(value['@graph'])) {
      const found = findPosting(value['@graph']);
      if (found) return found;
    }
    return null;
  };
  let posting = null;
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      posting = findPosting(JSON.parse(script.textContent || ''));
      if (posting) break;
    } catch { /* Ignore malformed publisher metadata. */ }
  }
  const headings = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6, [role="heading"]')]
    .filter(visible);
  const aboutJobHeading = headings
    .filter(element => /^about the job$/iu.test(normalize(element.textContent || '')))
    .at(-1) || null;
  const detailTitleHeading = aboutJobHeading
    ? headings.filter(element => element.tagName === 'H1'
      && Boolean(element.compareDocumentPosition(aboutJobHeading) & Node.DOCUMENT_POSITION_FOLLOWING)).at(-1) || null
    : null;
  const detailRoot = commonAncestor(detailTitleHeading, aboutJobHeading);
  const organization = posting?.hiringOrganization;
  const greenhouseLogoCompany = normalize(document.querySelector('.job-post-container img.logo[alt]')?.getAttribute('alt') || '')
    .replace(/\\s+logo$/iu, '');
  const greenhouseTitleCompany = normalize(document.title).match(/\\sat\\s+(.+)$/iu)?.[1] || '';
  const companySelector = '[data-testid*="company" i], [itemprop="hiringOrganization"], .company-name, .job-company';
  const scopedCompanySelector = 'a[href^="/company/"], a[href*="wellfound.com/company/"], [data-testid="startup-header"] h2, [data-testid="startup-header"] h3, ' + companySelector;
  const companyName = decode(typeof organization === 'string' ? organization : organization?.name)
    || (detailRoot
      ? textWithin(detailRoot, scopedCompanySelector)
      : text(companySelector))
    || greenhouseLogoCompany
    || greenhouseTitleCompany
    || meta('meta[property="og:site_name"], meta[name="application-name"]');
  const title = decode(posting?.title)
    || normalize(detailTitleHeading?.textContent || '')
    || text('[data-testid*="job-title" i], [itemprop="title"], .job-title, h1')
    || meta('meta[property="og:title"], meta[name="twitter:title"]');
  const nearbyRenderedLocation = () => {
    if (!title) return '';
    const anchor = detailTitleHeading || headings
      .filter(element => normalize(element.textContent || '') === title)
      .at(-1);
    if (!anchor) return '';
    let scope = anchor.parentElement;
    for (let depth = 0; scope && depth < 5; depth += 1, scope = scope.parentElement) {
      if (detailRoot && !detailRoot.contains(scope)) break;
      const rendered = normalize(scope.innerText || scope.textContent || '');
      const titleIndex = rendered.lastIndexOf(title);
      if (titleIndex < 0) continue;
      const nearby = rendered.slice(titleIndex + title.length, titleIndex + title.length + 300).trim();
      const match = nearby.match(/^(Remote\\s*\\(\\s*[^)]{2,80}\\s*\\))/iu);
      if (match) return normalize(match[1]).replace(/\\(\\s+/u, '(').replace(/\\s+\\)/u, ')');
    }
    return '';
  };
  const addressText = (location) => {
    if (typeof location === 'string') return decode(location);
    if (!location || typeof location !== 'object') return '';
    const address = location.address && typeof location.address === 'object' ? location.address : location;
    const country = typeof address.addressCountry === 'object' ? address.addressCountry?.name : address.addressCountry;
    const parts = [address.streetAddress, address.addressLocality, address.addressRegion, address.postalCode, country]
      .map(decode).filter(Boolean);
    return parts.length ? parts.join(', ') : decode(location.name);
  };
  const locations = Array.isArray(posting?.jobLocation) ? posting.jobLocation : [posting?.jobLocation];
  let locationText = locations.map(addressText).filter(Boolean).join('; ');
  const structuredLocationLength = locationText.length;
  if (!locationText && posting?.jobLocationType) locationText = decode(posting.jobLocationType);
  const locationSelector = '[data-testid*="job-location" i], [itemprop="jobLocation"], .job-location, .job__location, .location';
  const locationSelectorText = detailRoot
    ? textWithin(detailRoot, locationSelector)
    : text(locationSelector);
  if (!locationText) locationText = locationSelectorText;
  const nearbyLocationText = nearbyRenderedLocation();
  if (!locationText) locationText = nearbyLocationText;
  const semanticDescription = htmlText(
    aboutJobHeading?.nextElementSibling?.innerHTML
      || aboutJobHeading?.nextElementSibling?.textContent
      || ''
  );
  const descriptionSelector = '[data-testid*="job-description" i], [itemprop="description"], #job-description, .job-description, .job__description';
  const descriptionText = htmlText(posting?.description)
    || semanticDescription
    || (detailRoot
      ? elementHtmlWithin(detailRoot, descriptionSelector)
      : elementHtml(descriptionSelector));
  const ordinaryUrl = (value) => {
    if (typeof value !== 'string' || !value.trim()) return '';
    try {
      const url = new URL(value, document.baseURI);
      return (url.protocol === 'http:' || url.protocol === 'https:') ? url.href : '';
    } catch { return ''; }
  };
  let applicationUrl = ordinaryUrl(posting?.applicationUrl || posting?.applyUrl);
  if (!applicationUrl) {
    const links = [...(detailRoot || document).querySelectorAll('a[href]')];
    const apply = links.find(link => /\\bapply(?: now| for| here)?\\b/iu.test(normalize(link.textContent || '')))
      || links.find(link => /(?:^|[/_-])apply(?:[/?#_-]|$)/iu.test(link.getAttribute('href') || ''));
    applicationUrl = ordinaryUrl(apply?.getAttribute('href'));
  }
  return {
    pageUrl: location.href,
    companyName,
    title,
    locationText,
    descriptionText,
    applicationUrl,
    diagnostics: {
      readyState: document.readyState,
      documentTitle: normalize(document.title).slice(0, 300),
      bodyTextLength: normalize(document.body?.innerText || '').length,
      h1Text: text('h1').slice(0, 300),
      jobPostingFound: Boolean(posting),
      headingCount: document.querySelectorAll('h1, h2, h3, [role="heading"]').length,
      exactTitleHeadingCount: [...document.querySelectorAll('h1, h2, h3, [role="heading"]')]
        .filter(element => normalize(element.textContent || '') === title).length,
      structuredLocationLength,
      jobLocationTypeLength: decode(posting?.jobLocationType).length,
      locationSelectorTextLength: locationSelectorText.length,
      nearbyLocationTextLength: nearbyLocationText.length,
      companyLength: companyName.length,
      titleLength: title.length,
      locationLength: locationText.length,
      descriptionLength: descriptionText.length,
      applicationUrlLength: applicationUrl.length
    }
  };
})()`

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
  return recoverBrowserTabMetadata(value) !== null
}

interface ManagedTab {
  state: BrowserTab
  view: WebContentsView
  targetEpoch: number
  targets: Map<string, SemanticTarget>
}

interface SemanticTargetFingerprint {
  role: string
  name: string
  disabled: boolean
  type: string
}

interface SemanticTarget {
  index: number
  fingerprint: SemanticTargetFingerprint
}

interface BrowserManagerOptions {
  window: BrowserWindow
  browserSession: Session
  createView: () => WebContentsView
  dialog: Pick<Dialog, 'showSaveDialog'>
  downloadsPath: string
  clipboard: Pick<Clipboard, 'writeText'>
}

export class BrowserManager {
  readonly #window: BrowserWindow
  readonly #session: Session
  readonly #createView: () => WebContentsView
  readonly #dialog: Pick<Dialog, 'showSaveDialog'>
  readonly #downloadsPath: string
  readonly #clipboard: Pick<Clipboard, 'writeText'>
  readonly #tabs = new Map<string, ManagedTab>()
  #order: string[] = []
  #activeTabId: string | null = null
  #attachedTabId: string | null = null
  #bounds: BrowserBounds = { x: 0, y: 0, width: 0, height: 0, visible: false }
  #download: BrowserDownload | null = null
  #notice: string | null = null
  #synthesizedDefault = false
  #hasExplicitAction = false
  readonly #downloadHandler = (_event: Electron.Event, item: DownloadItem, contents: WebContents) => this.#handleDownload(item, contents)

  constructor(options: BrowserManagerOptions) {
    this.#window = options.window
    this.#session = options.browserSession
    this.#createView = options.createView
    this.#dialog = options.dialog
    this.#downloadsPath = options.downloadsPath
    this.#clipboard = options.clipboard
    this.#installSessionPolicies()
  }

  getState(): BrowserState {
    return {
      tabs: this.#order.flatMap(tabId => {
        const tab = this.#tabs.get(tabId)
        if (!tab) return []
        const metadata = sanitizeBrowserMetadata(tab.state)
        if (
          metadata.url !== tab.state.url
          || metadata.title !== tab.state.title
          || metadata.faviconUrl !== tab.state.faviconUrl
          || metadata.associatedJobId !== tab.state.associatedJobId
        ) this.#notice = 'Browser metadata was adjusted to fit Workspace security and size limits.'
        return [{ ...tab.state, ...metadata }]
      }),
      activeTabId: this.#activeTabId,
      download: this.#download ? { ...this.#download } : null,
      notice: this.#notice
    }
  }

  async restore(restored: BrowserRestoreState): Promise<BrowserState> {
    if (this.#order.length) {
      if (!this.#synthesizedDefault || this.#hasExplicitAction || !restored.tabs.length) return this.getState()
      for (const tab of this.#tabs.values()) {
        if (this.#attachedTabId === tab.state.tabId) this.#window.contentView.removeChildView(tab.view)
        tab.view.webContents.close({ waitForBeforeUnload: false })
      }
      this.#tabs.clear()
      this.#order = []
      this.#activeTabId = null
      this.#attachedTabId = null
      this.#synthesizedDefault = false
    }
    const recovered = recoverBrowserRestoreState(restored)
    const initialLoads: Promise<void>[] = []
    for (const tab of recovered.tabs) {
      initialLoads.push(this.#createTab(tab))
    }
    if (!this.#order.length) {
      this.#synthesizedDefault = true
      initialLoads.push(this.#createTab({
        tabId: randomUUID(),
        url: DEFAULT_BROWSER_URL,
        title: 'Google',
        faviconUrl: null,
        associatedJobId: null
      }))
    }
    this.#activeTabId = recovered.activeTabId && this.#tabs.has(recovered.activeTabId)
      ? recovered.activeTabId
      : this.#order[0] ?? null
    this.#syncAttachedView()
    this.#emit()
    await Promise.all(initialLoads)
    return this.getState()
  }

  async create(url = DEFAULT_BROWSER_URL, associatedJobId: string | null = null): Promise<BrowserState> {
    this.#hasExplicitAction = true
    if (this.#order.length >= BROWSER_PERSISTENCE_LIMITS.tabs) {
      this.#notice = `Tab limit reached (${BROWSER_PERSISTENCE_LIMITS.tabs}). Close a tab before opening another.`
      this.#emit()
      return this.getState()
    }
    const tabId = randomUUID()
    const initialLoad = this.#createTab({
      tabId,
      url: normalizeBrowserInput(url),
      title: 'New tab',
      faviconUrl: null,
      associatedJobId
    })
    this.#activeTabId = tabId
    this.#syncAttachedView()
    this.#emit()
    await initialLoad
    return this.getState()
  }

  select(tabId: string): BrowserState {
    this.#hasExplicitAction = true
    this.#requireTab(tabId)
    this.#activeTabId = tabId
    this.#syncAttachedView()
    this.#emit()
    return this.getState()
  }

  async close(tabId: string): Promise<BrowserState> {
    this.#hasExplicitAction = true
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
    this.#hasExplicitAction = true
    if (tabIds.length !== this.#order.length || new Set(tabIds).size !== tabIds.length || tabIds.some(id => !this.#tabs.has(id))) {
      throw new Error('Tab order must contain every open tab exactly once')
    }
    this.#order = [...tabIds]
    this.#emit()
    return this.getState()
  }

  async navigate(tabId: string, input: string): Promise<BrowserState> {
    this.#hasExplicitAction = true
    const tab = this.#requireTab(tabId)
    this.#invalidateTargets(tab)
    const url = normalizeBrowserInput(input)
    tab.state.error = null
    tab.state.crashed = false
    await tab.view.webContents.loadURL(url).catch(() => undefined)
    return this.getState()
  }

  back(tabId: string): BrowserState {
    this.#hasExplicitAction = true
    const tab = this.#requireTab(tabId)
    this.#invalidateTargets(tab)
    const contents = tab.view.webContents
    if (contents.navigationHistory.canGoBack()) contents.navigationHistory.goBack()
    return this.getState()
  }

  forward(tabId: string): BrowserState {
    this.#hasExplicitAction = true
    const tab = this.#requireTab(tabId)
    this.#invalidateTargets(tab)
    const contents = tab.view.webContents
    if (contents.navigationHistory.canGoForward()) contents.navigationHistory.goForward()
    return this.getState()
  }

  reload(tabId: string): BrowserState {
    this.#hasExplicitAction = true
    const tab = this.#requireTab(tabId)
    this.#invalidateTargets(tab)
    tab.state.error = null
    tab.state.crashed = false
    tab.view.webContents.reload()
    this.#emit()
    return this.getState()
  }

  stop(tabId: string): BrowserState {
    this.#hasExplicitAction = true
    this.#requireTab(tabId).view.webContents.stop()
    return this.getState()
  }

  inspect(): BrowserState {
    return this.getState()
  }

  async extractJob(tabId: string): Promise<BrowserJobListing> {
    const tab = this.#requireTab(tabId)
    const targetEpoch = tab.targetEpoch
    const startedAt = Date.now()
    const before = {
      tabId,
      stateUrl: tab.state.url,
      stateLoading: tab.state.loading,
      webContentsUrl: tab.view.webContents.getURL(),
      webContentsLoading: typeof tab.view.webContents.isLoading === 'function'
        ? tab.view.webContents.isLoading()
        : null
    }
    const expectedRawUrl = before.webContentsUrl
    const expectedUrl = ordinaryHttpUrl(expectedRawUrl)
    let missing: string[] = []
    for (let attempt = 1; attempt <= JOB_EXTRACTION_ATTEMPTS; attempt += 1) {
      const rawUrlBeforeAttempt = tab.view.webContents.getURL()
      if (
        tab.targetEpoch !== targetEpoch
        || rawUrlBeforeAttempt !== expectedRawUrl
        || ordinaryHttpUrl(rawUrlBeforeAttempt) !== expectedUrl
      ) {
        throw new Error('The page changed while JobOS was reading it. Try saving again.')
      }
      let raw: unknown
      try {
        raw = await tab.view.webContents.executeJavaScript(JOB_EXTRACTION_SCRIPT, true) as unknown
      } catch (error) {
        const rawUrlAfterError = tab.view.webContents.getURL()
        if (
          tab.targetEpoch !== targetEpoch
          || rawUrlAfterError !== expectedRawUrl
          || ordinaryHttpUrl(rawUrlAfterError) !== expectedUrl
        ) {
          throw new Error('The page changed while JobOS was reading it. Try saving again.')
        }
        throw error
      }
      const value = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
      const activeUrl = ordinaryHttpUrl(value.pageUrl)
      const currentRawUrl = tab.view.webContents.getURL()
      const currentUrl = ordinaryHttpUrl(currentRawUrl)
      if (process.env.JOBOS_EXTRACTION_DIAGNOSTICS === '1') {
        console.info('[JobOS extraction diagnostic]', JSON.stringify({
          attempt,
          elapsedMs: Date.now() - startedAt,
          before,
          after: {
            stateUrl: tab.state.url,
            stateLoading: tab.state.loading,
            webContentsUrl: tab.view.webContents.getURL(),
            webContentsLoading: typeof tab.view.webContents.isLoading === 'function'
              ? tab.view.webContents.isLoading()
              : null
          },
          pageUrl: activeUrl,
          diagnostics: value.diagnostics ?? null
        }))
      }
      if (
        tab.targetEpoch !== targetEpoch
        || currentRawUrl !== expectedRawUrl
        || currentUrl !== expectedUrl
        || (activeUrl && currentUrl && activeUrl !== currentUrl)
        || (expectedUrl && currentUrl && expectedUrl !== currentUrl)
        || (expectedUrl && activeUrl && expectedUrl !== activeUrl)
      ) {
        throw new Error('The page changed while JobOS was reading it. Try saving again.')
      }
      const companyName = boundedText(value.companyName, 300)
      const title = boundedText(value.title, 500)
      const locationText = boundedText(value.locationText, 1_000)
      const descriptionText = boundedText(value.descriptionText, 100_000)
      const explicitApplicationUrl = ordinaryHttpUrl(value.applicationUrl)
      missing = [
        companyName ? null : 'company',
        title ? null : 'role',
        locationText ? null : 'location',
        descriptionText ? null : 'description',
        activeUrl ? null : 'URL'
      ].filter((field): field is string => field !== null)
      if (!missing.length) {
        return {
          companyName,
          title,
          canonicalUrl: activeUrl,
          locationText,
          descriptionText,
          applicationUrl: explicitApplicationUrl || activeUrl
        }
      }
      if (!expectedUrl || attempt === JOB_EXTRACTION_ATTEMPTS) break
      await wait(JOB_EXTRACTION_RETRY_MS)
    }
    const fields = missing.length === 1
      ? missing[0]
      : `${missing.slice(0, -1).join(', ')}${missing.length > 2 ? ',' : ''} and ${missing.at(-1)}`
    throw new Error(`Could not extract a complete job listing; missing ${fields}.`)
  }

  async snapshot(tabId: string): Promise<BrowserSemanticSnapshot> {
    const tab = this.#requireTab(tabId)
    this.#invalidateTargets(tab)
    const targetEpoch = tab.targetEpoch
    const targetPrefix = randomUUID().replaceAll('-', '').slice(0, 20)
    const raw = await tab.view.webContents.executeJavaScript(`(() => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        return !element.hidden && style.visibility !== 'hidden'
          && style.display !== 'none' && element.getClientRects().length > 0;
      };
      const candidates = [...document.querySelectorAll(
        'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"],[tabindex]'
      )].slice(0, 100);
      const elements = candidates.map((element, index) => {
        if (!visible(element)) return null;
        const name = (element.getAttribute('aria-label') || element.getAttribute('title')
          || element.innerText || element.placeholder || '').trim().slice(0, 200);
        const type = (element.getAttribute('type') || ('type' in element ? element.type : ''))
          .toLowerCase().slice(0, 40);
        return { index, role: (element.getAttribute('role') || element.tagName).toLowerCase().slice(0, 40),
          name, disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'), type };
      }).filter(Boolean);
      return { text: (document.body?.innerText || '').trim().slice(0, 5000), elements };
    })()`, true) as unknown
    const value = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
    const targets = Array.isArray(value.elements)
      ? value.elements.slice(0, 100).flatMap(item => {
          if (!item || typeof item !== 'object') return []
          const candidate = item as Record<string, unknown>
          if (!Number.isInteger(candidate.index) || Number(candidate.index) < 0 || Number(candidate.index) >= 100) return []
          const fingerprint = {
            role: String(candidate.role ?? 'control').slice(0, 40),
            name: String(candidate.name ?? '').slice(0, 200),
            disabled: Boolean(candidate.disabled),
            type: String(candidate.type ?? '').slice(0, 40)
          }
          return [{
            targetId: `t_${targetPrefix}_${Number(candidate.index) + 1}`,
            index: Number(candidate.index),
            fingerprint
          }]
        })
      : []
    if (tab.targetEpoch === targetEpoch) {
      tab.targets = new Map(targets.map(target => [target.targetId, {
        index: target.index,
        fingerprint: target.fingerprint
      }]))
    }
    const elements = targets.map(target => ({
      targetId: target.targetId,
      role: target.fingerprint.role,
      name: target.fingerprint.name,
      disabled: target.fingerprint.disabled
    }))
    const state = this.getState().tabs.find(candidate => candidate.tabId === tabId)
    return {
      tabId,
      url: state?.url ?? 'about:blank',
      title: state?.title ?? 'Untitled',
      text: typeof value.text === 'string' ? value.text.slice(0, 5000) : '',
      elements
    }
  }

  async click(tabId: string, targetId: string): Promise<BrowserState> {
    this.#assertTargetId(targetId)
    const tab = this.#requireTab(tabId)
    const target = tab.targets.get(targetId)
    if (!target) throw new Error('Browser target is stale; capture a new snapshot')
    const fingerprint = JSON.stringify(target.fingerprint)
    const result = await tab.view.webContents.executeJavaScript(`(() => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        return !element.hidden && style.visibility !== 'hidden'
          && style.display !== 'none' && element.getClientRects().length > 0;
      };
      const candidates = [...document.querySelectorAll(
        'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"],[tabindex]'
      )].slice(0, 100);
      const element = candidates[${target.index}];
      if (!element || !visible(element)) return { ok: false, code: 'target_not_found' };
      const name = (element.getAttribute('aria-label') || element.getAttribute('title')
        || element.innerText || element.placeholder || '').trim().slice(0, 200);
      const fingerprint = { role: (element.getAttribute('role') || element.tagName).toLowerCase().slice(0, 40),
        name, disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
        type: (element.getAttribute('type') || ('type' in element ? element.type : '')).toLowerCase().slice(0, 40) };
      if (JSON.stringify(fingerprint) !== JSON.stringify(${fingerprint})) return { ok: false, code: 'target_changed' };
      element.scrollIntoView({ block: 'center', inline: 'center' }); element.click();
      return { ok: true };
    })()`, true) as { ok?: boolean }
    if (!result?.ok) throw new Error('Browser target not found; capture a new snapshot')
    return this.getState()
  }

  async type(tabId: string, targetId: string, text: string, clear = true): Promise<BrowserState> {
    this.#assertTargetId(targetId)
    if (typeof text !== 'string' || text.length > 4000) throw new Error('Invalid browser text')
    const tab = this.#requireTab(tabId)
    const target = tab.targets.get(targetId)
    if (!target) throw new Error('Browser target is stale; capture a new snapshot')
    const encodedText = JSON.stringify(text)
    const fingerprint = JSON.stringify(target.fingerprint)
    const result = await tab.view.webContents.executeJavaScript(`(() => {
      const visible = (element) => {
        const style = getComputedStyle(element);
        return !element.hidden && style.visibility !== 'hidden'
          && style.display !== 'none' && element.getClientRects().length > 0;
      };
      const candidates = [...document.querySelectorAll(
        'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"],[tabindex]'
      )].slice(0, 100);
      const element = candidates[${target.index}];
      if (!element || !visible(element) || (!('value' in element) && !element.isContentEditable))
        return { ok: false, code: 'target_not_found' };
      const name = (element.getAttribute('aria-label') || element.getAttribute('title')
        || element.innerText || element.placeholder || '').trim().slice(0, 200);
      const fingerprint = { role: (element.getAttribute('role') || element.tagName).toLowerCase().slice(0, 40),
        name, disabled: Boolean(element.disabled || element.getAttribute('aria-disabled') === 'true'),
        type: (element.getAttribute('type') || ('type' in element ? element.type : '')).toLowerCase().slice(0, 40) };
      if (JSON.stringify(fingerprint) !== JSON.stringify(${fingerprint})) return { ok: false, code: 'target_changed' };
      element.focus();
      if (element.isContentEditable) element.textContent = ${clear ? "''" : "element.textContent || ''"} + ${encodedText};
      else element.value = ${clear ? "''" : "element.value || ''"} + ${encodedText};
      element.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: ${encodedText} }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return { ok: true };
    })()`, true) as { ok?: boolean }
    if (!result?.ok) throw new Error('Browser target not found; capture a new snapshot')
    return this.getState()
  }

  async scroll(tabId: string, direction: 'up' | 'down', amount = 600): Promise<BrowserState> {
    const tab = this.#requireTab(tabId)
    if (!Number.isInteger(amount) || amount < 1 || amount > 2000) throw new Error('Invalid scroll amount')
    const delta = direction === 'up' ? -amount : amount
    await tab.view.webContents.executeJavaScript(
      `(() => { window.scrollBy({ top: ${delta}, behavior: 'auto' }); return { ok: true }; })()`,
      true
    )
    return this.getState()
  }

  associate(tabId: string, jobId: string | null): BrowserState {
    this.#hasExplicitAction = true
    const tab = this.#requireTab(tabId)
    tab.state.associatedJobId = jobId
    this.#emit()
    return this.getState()
  }

  copyBlockedUrl(tabId: string): BrowserState {
    const tab = this.#requireTab(tabId)
    if (!tab.state.blockedUrl) throw new Error('No blocked link is available to copy')
    this.#clipboard.writeText(tab.state.blockedUrl)
    this.#notice = 'Blocked link copied. JobOS did not open it.'
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

  #createTab(restored: BrowserTabMetadata): Promise<void> {
    const view = this.#createView()
    const state: BrowserTab = {
      ...restored,
      loading: false,
      canGoBack: false,
      canGoForward: false,
      error: null,
      crashed: false,
      blockedUrl: null
    }
    const managed = { view, state, targetEpoch: 0, targets: new Map<string, SemanticTarget>() }
    this.#tabs.set(state.tabId, managed)
    this.#order.push(state.tabId)
    this.#wireTab(managed)
    return view.webContents.loadURL(state.url).then(() => undefined, () => undefined)
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
    contents.on('did-start-loading', () => { this.#invalidateTargets(tab); tab.state.loading = true; tab.state.error = null; tab.state.blockedUrl = null; this.#notice = null; refresh() })
    contents.on('did-stop-loading', () => { tab.state.loading = false; refresh() })
    contents.on('did-navigate', () => { this.#invalidateTargets(tab); refresh() })
    contents.on('did-navigate-in-page', () => { this.#invalidateTargets(tab); refresh() })
    contents.on('page-title-updated', (_event, title) => {
      const nextTitle = title || 'Untitled'
      const safeTitle = sanitizeBrowserTitleForPersistence(nextTitle)
      if (safeTitle !== nextTitle && safeTitle === BROWSER_SAFE_TITLE_FALLBACK) {
        this.#notice = 'A page title containing credential-like metadata was hidden.'
      } else if (nextTitle.length > BROWSER_PERSISTENCE_LIMITS.title) {
        this.#notice = 'A page title was shortened to keep Workspace saves reliable.'
      }
      tab.state.title = safeTitle
      this.#emit()
    })
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
    const blockExternalProtocol = (url: string) => {
      tab.state.blockedUrl = safeBlockedExternalUrl(url)
      tab.state.error = tab.state.blockedUrl
        ? 'JobOS blocked an external protocol. Copy the link if you want to open it yourself.'
        : 'JobOS blocked an unsafe external protocol.'
      this.#emit()
    }
    const handleCancellableNavigation = (event: Electron.Event, url: string) => {
      if (!isOrdinaryWebUrl(url)) {
        event.preventDefault()
        blockExternalProtocol(url)
      }
    }
    contents.on('will-navigate', handleCancellableNavigation)
    contents.on('will-redirect', handleCancellableNavigation)
    contents.setWindowOpenHandler(({ url }) => {
      if (isOrdinaryWebUrl(url)) void this.create(url, tab.state.associatedJobId)
      else blockExternalProtocol(url)
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

  #assertTargetId(targetId: string): void {
    if (!/^t_[A-Za-z0-9_-]{1,64}$/.test(targetId)) throw new Error('Invalid browser target')
  }

  #emit(): void {
    if (!this.#window.isDestroyed()) this.#window.webContents.send('jobos:browser:state', this.getState())
  }

  #invalidateTargets(tab: ManagedTab): void {
    tab.targetEpoch += 1
    tab.targets.clear()
  }
}

export function safeBlockedExternalUrl(value: string): string | null {
  try {
    const parsed = new URL(value)
    if (['http:', 'https:', 'about:', 'javascript:', 'data:', 'file:', 'blob:'].includes(parsed.protocol)) return null
    const sanitized = sanitizeBrowserUrlForPersistence(value)
    return sanitized.length <= 2048 ? sanitized : null
  } catch {
    return null
  }
}

function boundedText(value: unknown, limit: number): string {
  return (typeof value === 'string' ? value : '').replace(/\s+/gu, ' ').trim().slice(0, limit)
}

function ordinaryHttpUrl(value: unknown): string {
  if (typeof value !== 'string' || value.length > 2_083) return ''
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.toString() : ''
  } catch {
    return ''
  }
}
