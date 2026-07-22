// @vitest-environment node

import { expect, test } from 'vitest'

import { EventEmitter } from 'node:events'
import { readFileSync } from 'node:fs'
// @ts-expect-error jsdom does not publish TypeScript declarations.
import { JSDOM } from 'jsdom'

import type { BrowserWindow, Dialog, Session, WebContents, WebContentsView } from 'electron'
import { vi } from 'vitest'

import { BrowserManager, DEFAULT_BROWSER_URL, isOrdinaryWebUrl, normalizeBrowserInput, remoteBrowserPreferences, safeBlockedExternalUrl } from './browser.js'
import {
  BROWSER_PERSISTENCE_LIMITS,
  BROWSER_SAFE_TITLE_FALLBACK,
  normalizeBrowserUrlForPersistence,
  sanitizeBrowserTitleForPersistence,
  sanitizeBrowserUrlForPersistence
} from '../shared/browserPersistence.js'

const browserUrlPolicyFixtures = JSON.parse(readFileSync(
  new URL('../../../../tests/fixtures/browser-url-policy.json', import.meta.url),
  'utf8'
)) as Array<{ url: string, desktop: string, api_safe: boolean }>
const browserTitlePolicyFixtures = JSON.parse(readFileSync(
  new URL('../../../../tests/fixtures/browser-title-policy.json', import.meta.url),
  'utf8'
)) as Array<{ title: string, expected: string, unsafe: boolean }>

test('browser input keeps ordinary sites free and turns plain text into a search', () => {
  expect(normalizeBrowserInput('https://mail.google.com/mail/u/0/')).toBe('https://mail.google.com/mail/u/0/')
  expect(normalizeBrowserInput('jobs.example.com/opening/7')).toBe('https://jobs.example.com/opening/7')
  expect(normalizeBrowserInput('staff product manager jobs')).toBe('https://www.google.com/search?q=staff%20product%20manager%20jobs')
  expect(normalizeBrowserInput('')).toBe(DEFAULT_BROWSER_URL)
})

test('remote navigation policy admits only ordinary web pages', () => {
  expect(isOrdinaryWebUrl('https://example.com')).toBe(true)
  expect(isOrdinaryWebUrl('http://127.0.0.1:8765/listing')).toBe(true)
  expect(isOrdinaryWebUrl('mailto:person@example.com')).toBe(false)
  expect(isOrdinaryWebUrl('file:///etc/passwd')).toBe(false)
  expect(isOrdinaryWebUrl('javascript:alert(1)')).toBe(false)
})

test('remote browser content has no Node, preload, webview, or privileged renderer bridge', () => {
  const preferences = remoteBrowserPreferences()
  expect(preferences).toMatchObject({
    nodeIntegration: false,
    contextIsolation: true,
    sandbox: true,
    webSecurity: true,
    webviewTag: false
  })
  expect(preferences).not.toHaveProperty('preload')
})

test('persisted browser URLs remove credentials, auth parameters, and fragments', () => {
  expect(sanitizeBrowserUrlForPersistence('https://person:password@example.com/callback?code=secret&view=inbox#token')).toBe(
    'https://example.com/callback?view=inbox'
  )
  expect(sanitizeBrowserUrlForPersistence(
    'https://example.com/download?view=compact&ticket=a&assertion=b&sig=c&sessionid=d&oauth_verifier=e&X-Amz-Credential=f&X-Amz-Signature=g&X-Goog-Signature=h#fragment'
  )).toBe('https://example.com/download?view=compact')
  expect(safeBlockedExternalUrl('mailto:person@example.com?subject=Hello&ticket=secret#fragment')).toBe(
    'mailto:person@example.com?subject=Hello'
  )
  expect(safeBlockedExternalUrl('javascript:alert(1)')).toBeNull()
})

test('desktop URL persistence matches the shared credential, host, and port fixtures', () => {
  for (const fixture of browserUrlPolicyFixtures) {
    expect(normalizeBrowserUrlForPersistence(fixture.url), fixture.url).toBe(fixture.desktop)
  }
})

test('desktop title persistence matches the shared conservative redaction fixtures', () => {
  for (const fixture of browserTitlePolicyFixtures) {
    expect(sanitizeBrowserTitleForPersistence(fixture.title), fixture.title).toBe(fixture.expected)
  }
})

test('new tab commands do not complete before the initial page load settles', async () => {
  let finishLoad!: () => void
  const loadSettled = new Promise<void>(resolve => { finishLoad = resolve })
  const events = new EventEmitter()
  let url = ''
  const contents = Object.assign(events, {
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
    loadURL: vi.fn(async (next: string) => { url = next; await loadSettled }), getURL: () => url,
    setWindowOpenHandler: vi.fn(), isDestroyed: () => false, close: vi.fn(), reload: vi.fn(), stop: vi.fn()
  })
  const manager = new BrowserManager({
    window: {
      contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false
    } as unknown as BrowserWindow,
    browserSession: Object.assign(new EventEmitter(), {
      setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
    }) as unknown as Session,
    createView: () => ({ webContents: contents, setBounds: vi.fn() }) as unknown as WebContentsView,
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    clipboard: { writeText: vi.fn() }, downloadsPath: '/tmp'
  })

  let completed = false
  const creating = manager.create('https://example.com/jobs/1').then(state => {
    completed = true
    return state
  })
  await Promise.resolve()
  expect(completed).toBe(false)
  finishLoad()
  expect((await creating).tabs[0]?.url).toBe('https://example.com/jobs/1')
})

test('restore does not complete until every initial tab load settles', async () => {
  const finishLoads: Array<() => void> = []
  const createView = () => {
    const events = new EventEmitter()
    let url = ''
    const contents = Object.assign(events, {
      navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
      loadURL: vi.fn((next: string) => {
        url = next
        return new Promise<void>(resolve => { finishLoads.push(resolve) })
      }),
      getURL: () => url,
      setWindowOpenHandler: vi.fn(), isDestroyed: () => false, close: vi.fn(), reload: vi.fn(), stop: vi.fn()
    })
    return { webContents: contents, setBounds: vi.fn() } as unknown as WebContentsView
  }
  const manager = new BrowserManager({
    window: { contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false } as unknown as BrowserWindow,
    browserSession: Object.assign(new EventEmitter(), {
      setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
    }) as unknown as Session,
    createView,
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    clipboard: { writeText: vi.fn() }, downloadsPath: '/tmp'
  })

  let completed = false
  const restoring = manager.restore({ tabs: [
    { tabId: 'one', url: 'https://one.example/', title: 'One', faviconUrl: null, associatedJobId: null },
    { tabId: 'two', url: 'https://two.example/', title: 'Two', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'two' }).then(state => { completed = true; return state })
  await Promise.resolve()
  expect(completed).toBe(false)
  finishLoads[0]!()
  await Promise.resolve()
  expect(completed).toBe(false)
  finishLoads[1]!()
  expect((await restoring).activeTabId).toBe('two')
})

test('tabs reorder and bounds changes reuse the same live WebContentsView instances', async () => {
  const views: WebContentsView[] = []
  const attached: WebContentsView[] = []
  const createView = () => {
    const events = new EventEmitter()
    let url = ''
    const contents = Object.assign(events, {
      navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
      loadURL: vi.fn(async (next: string) => { url = next }),
      getURL: () => url,
      setWindowOpenHandler: vi.fn(),
      isDestroyed: () => false,
      close: vi.fn(),
      reload: vi.fn(),
      stop: vi.fn()
    })
    const view = { webContents: contents, setBounds: vi.fn() } as unknown as WebContentsView
    views.push(view)
    return view
  }
  const permissionRequest = vi.fn()
  const browserSession = Object.assign(new EventEmitter(), {
    setPermissionCheckHandler: vi.fn(),
    setPermissionRequestHandler: permissionRequest
  }) as unknown as Session
  const window = {
    contentView: {
      addChildView: (view: WebContentsView) => attached.push(view),
      removeChildView: (view: WebContentsView) => attached.splice(attached.indexOf(view), 1)
    },
    webContents: { send: vi.fn() },
    isDestroyed: () => false
  } as unknown as BrowserWindow
  const manager = new BrowserManager({
    window,
    browserSession,
    createView,
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    clipboard: { writeText: vi.fn() },
    downloadsPath: '/tmp'
  })
  const callback = vi.fn()
  const requestHandler = permissionRequest.mock.calls[0]?.[0]
  requestHandler(null as unknown as WebContents, 'geolocation', callback)
  expect(callback).toHaveBeenCalledWith(false)
  expect(manager.getState().notice).toContain('blocked geolocation permission')

  await manager.restore({
    tabs: [
      { tabId: 'google', url: 'https://www.google.com/', title: 'Google', faviconUrl: null, associatedJobId: null },
      { tabId: 'gmail', url: 'https://mail.google.com/', title: 'Gmail', faviconUrl: null, associatedJobId: null }
    ],
    activeTabId: 'google'
  })
  const originalViews = [...views]
  manager.setBounds({ x: 240, y: 130, width: 720, height: 640, visible: true })
  manager.reorder(['gmail', 'google'])
  manager.select('gmail')
  manager.setBounds({ x: 310, y: 180, width: 540, height: 510, visible: true })
  manager.select('google')
  manager.associate('google', 'job-7')

  expect(views).toEqual(originalViews)
  expect(views).toHaveLength(2)
  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(['gmail', 'google'])
  expect(manager.getState().tabs.find(tab => tab.tabId === 'google')).toMatchObject({
    url: 'https://www.google.com/',
    associatedJobId: 'job-7'
  })
  expect(attached).toEqual([views[0]])
  expect(views[0]?.setBounds).toHaveBeenLastCalledWith({ x: 310, y: 180, width: 540, height: 510 })

  await manager.close('gmail')
  await manager.create('https://jobs.example.com/roles/8', 'job-8')
  expect(manager.getState().tabs).toHaveLength(2)
  expect(manager.getState().tabs.at(-1)).toMatchObject({
    url: 'https://jobs.example.com/roles/8',
    associatedJobId: 'job-8'
  })
  expect(views).toHaveLength(3)
  expect(browserSession.listenerCount('will-download')).toBe(1)
  manager.dispose()
  expect(browserSession.listenerCount('will-download')).toBe(0)
})

test('main-process emission enforces Workspace bounds and keeps later saves viable', async () => {
  const views: WebContentsView[] = []
  const createView = () => {
    const events = new EventEmitter()
    let url = ''
    const contents = Object.assign(events, {
      navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
      loadURL: vi.fn(async (next: string) => { url = next }), getURL: () => url,
      setWindowOpenHandler: vi.fn(), isDestroyed: () => false, close: vi.fn(), reload: vi.fn(), stop: vi.fn()
    })
    const view = { webContents: contents, setBounds: vi.fn() } as unknown as WebContentsView
    views.push(view)
    return view
  }
  const browserSession = Object.assign(new EventEmitter(), {
    setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
  }) as unknown as Session
  const clipboard = { writeText: vi.fn() }
  const manager = new BrowserManager({
    window: {
      contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false
    } as unknown as BrowserWindow,
    browserSession, createView, clipboard,
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    downloadsPath: '/tmp'
  })

  await manager.restore({ tabs: [], activeTabId: null })
  expect(manager.getState().tabs[0]?.url).toBe(DEFAULT_BROWSER_URL)
  await manager.restore({
    tabs: [{ tabId: 'authoritative', url: 'https://mail.google.com/', title: 'Gmail', faviconUrl: null, associatedJobId: null }],
    activeTabId: 'authoritative'
  })
  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(['authoritative'])
  for (let index = 1; index < BROWSER_PERSISTENCE_LIMITS.tabs; index += 1) await manager.create(`https://example.com/${index}`)
  const beforeLimit = manager.getState().tabs.map(tab => tab.tabId)
  await manager.create('https://example.com/blocked-51')
  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(beforeLimit)
  expect(manager.getState().notice).toContain('Tab limit reached')

  const firstContents = views[1]?.webContents as unknown as EventEmitter
  await manager.navigate('authoritative', `https://example.com/${'u'.repeat(9000)}`)
  firstContents.emit('did-navigate')
  firstContents.emit('page-title-updated', {}, 'T'.repeat(700))
  firstContents.emit('page-favicon-updated', {}, [`https://example.com/${'f'.repeat(9000)}`])
  const durable = manager.getState()
  expect(durable.tabs).toHaveLength(BROWSER_PERSISTENCE_LIMITS.tabs)
  expect(durable.tabs[0]?.url).toBe('https://example.com/')
  expect(durable.tabs[0]?.title).toHaveLength(BROWSER_PERSISTENCE_LIMITS.title)
  expect(durable.tabs[0]?.faviconUrl).toBe('https://example.com/')
  expect(JSON.stringify({ tabs: durable.tabs, activeTabId: durable.activeTabId }).length).toBeGreaterThan(0)

  await manager.navigate('authoritative', 'https://example.com/safe-title-test')
  firstContents.emit('did-start-loading')
  firstContents.emit('did-navigate')
  firstContents.emit('page-favicon-updated', {}, ['https://example.com/favicon.ico'])
  firstContents.emit(
    'page-title-updated',
    {},
    'authorization_code=title-secret PHPSESSID=session-secret SAMLart=saml-secret'
  )
  expect(manager.getState().tabs[0]?.title).toBe(BROWSER_SAFE_TITLE_FALLBACK)
  expect(manager.getState().notice).toContain('credential-like metadata was hidden')
  firstContents.emit(
    'page-title-updated',
    {},
    '%ZZAWS%5FSECRET%5FACCESS%5FKEY%3Dexample-value'
  )
  expect(manager.getState().tabs[0]?.title).toBe(BROWSER_SAFE_TITLE_FALLBACK)
  firstContents.emit('page-title-updated', {}, 'Planning Session: Q3')
  expect(manager.getState().tabs[0]?.title).toBe('Planning Session: Q3')

  await manager.navigate(
    'authoritative',
    'https://example.com/jobs;jsessionid=path-secret/opening?api_key=query-secret&view=safe'
  )
  firstContents.emit('did-navigate')
  firstContents.emit('page-favicon-updated', {}, [
    'https://example.com/icon.png;PHPSESSID=path-secret?SAMLart=query-secret&theme=dark'
  ])
  expect(manager.getState().tabs[0]).toMatchObject({
    url: 'https://example.com/jobs/opening?view=safe',
    faviconUrl: 'https://example.com/icon.png?theme=dark'
  })

  await manager.navigate('authoritative', 'https://-foo.example/unsafe')
  firstContents.emit('did-navigate')
  expect(manager.getState().tabs[0]?.url).toBe('about:blank')
  await manager.navigate('authoritative', 'https://example.com/safe-after-invalid?view=compact')
  firstContents.emit('did-navigate')
  expect(manager.getState().tabs[0]?.url).toBe('https://example.com/safe-after-invalid?view=compact')

  const navigationEvent = { preventDefault: vi.fn() }
  firstContents.emit('will-navigate', navigationEvent, 'mailto:person@example.com?subject=Hello&ticket=secret#fragment')
  expect(navigationEvent.preventDefault).toHaveBeenCalled()
  expect(manager.getState().tabs[0]?.blockedUrl).toBe('mailto:person@example.com?subject=Hello')
  manager.copyBlockedUrl(manager.getState().tabs[0]!.tabId)
  expect(clipboard.writeText).toHaveBeenCalledWith('mailto:person@example.com?subject=Hello')

  const authoritativeView = views[1]
  if (!authoritativeView) throw new Error('Authoritative browser view was not created')
  const openHandler = (authoritativeView.webContents.setWindowOpenHandler as ReturnType<typeof vi.fn>).mock.calls[0]?.[0]
  if (!openHandler) throw new Error('Window-open policy was not installed')
  openHandler({ url: 'slack://channel/open?ticket=secret&team=safe' })
  expect(manager.getState().tabs[0]?.blockedUrl).toBe('slack://channel/open?team=safe')

  const viewsBeforeRedirects = views.length
  const customRedirect = { preventDefault: vi.fn() }
  firstContents.emit('will-redirect', customRedirect, 'slack://channel/open?ticket=secret&team=safe')
  expect(customRedirect.preventDefault).toHaveBeenCalledTimes(1)
  expect(manager.getState().tabs[0]?.blockedUrl).toBe('slack://channel/open?team=safe')
  manager.copyBlockedUrl('authoritative')
  expect(clipboard.writeText).toHaveBeenLastCalledWith('slack://channel/open?team=safe')

  for (const unsafeRedirect of ['file:///etc/passwd', 'data:text/html,unsafe']) {
    const redirectEvent = { preventDefault: vi.fn() }
    firstContents.emit('will-redirect', redirectEvent, unsafeRedirect)
    expect(redirectEvent.preventDefault).toHaveBeenCalledTimes(1)
    expect(manager.getState().tabs[0]?.blockedUrl).toBeNull()
    expect(manager.getState().tabs[0]?.error).toContain('unsafe external protocol')
  }
  const ordinaryRedirect = { preventDefault: vi.fn() }
  firstContents.emit('will-redirect', ordinaryRedirect, 'https://example.com/redirected?view=safe')
  expect(ordinaryRedirect.preventDefault).not.toHaveBeenCalled()
  expect(views).toHaveLength(viewsBeforeRedirects)
  manager.dispose()
})

test('agent browser operations use bounded semantic targets and fixed internal scripts', async () => {
  const executeJavaScript = vi.fn(async (script: string) => {
    if (script.includes('candidates.map')) {
      return {
        text: 'Apply now',
        elements: [{ index: 0, role: 'button', name: 'Apply', disabled: false, type: 'button' }]
      }
    }
    return { ok: true }
  })
  const contents = Object.assign(new EventEmitter(), {
    navigationHistory: {
      canGoBack: () => false, canGoForward: () => false,
      goBack: vi.fn(), goForward: vi.fn()
    },
    loadURL: vi.fn(async () => undefined), getURL: () => 'https://example.com/jobs/1',
    getTitle: () => 'Example job', executeJavaScript,
    setWindowOpenHandler: vi.fn(), isDestroyed: () => false,
    close: vi.fn(), reload: vi.fn(), stop: vi.fn()
  })
  const manager = new BrowserManager({
    window: {
      contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false
    } as unknown as BrowserWindow,
    browserSession: Object.assign(new EventEmitter(), {
      setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
    }) as unknown as Session,
    createView: () => ({ webContents: contents, setBounds: vi.fn() }) as unknown as WebContentsView,
    clipboard: { writeText: vi.fn() },
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    downloadsPath: '/tmp'
  })

  await manager.restore({
    tabs: [{ tabId: 'tab-1', url: 'https://example.com/jobs/1', title: 'Example job', faviconUrl: null, associatedJobId: null }],
    activeTabId: 'tab-1'
  })
  const snapshot = await manager.snapshot('tab-1')
  const targetId = snapshot.elements[0]!.targetId
  await manager.click('tab-1', targetId)
  await manager.type('tab-1', targetId, 'hello', true)
  await manager.scroll('tab-1', 'down', 600)

  expect(snapshot.elements).toEqual([
    { targetId, role: 'button', name: 'Apply', disabled: false }
  ])
  expect(snapshot.text).toBe('Apply now')
  expect(executeJavaScript).toHaveBeenCalledTimes(4)
  const snapshotScript = String(executeJavaScript.mock.calls[0]?.[0])
  expect(snapshotScript).toContain('getClientRects().length')
  expect(snapshotScript).not.toContain('innerHeight')
  const allScripts = executeJavaScript.mock.calls.flat().join('\n')
  expect(allScripts).not.toContain('querySelector(\'button\')')
  expect(allScripts).not.toContain('data-jobos-target-id')
  expect(allScripts).not.toContain('__jobosTargetId')
  expect(JSON.stringify(snapshot).length).toBeLessThan(20_000)
})

test('semantic targets are main-process owned, ignore spoofed page identity, and expire on snapshot or navigation', async () => {
  const executeJavaScript = vi.fn(async (script: string) => {
    if (script.includes('candidates.map')) {
      return { text: 'Apply', elements: [
        {
          index: 0, targetId: 't_page_spoofed_1', role: 'button', name: 'Apply', disabled: false,
          type: 'button', pageAttribute: 't_transferred_from_another_element'
        }
      ] }
    }
    return { ok: true }
  })
  const contents = Object.assign(new EventEmitter(), {
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
    loadURL: vi.fn(async () => undefined), getURL: () => 'https://example.com/jobs/1',
    executeJavaScript, setWindowOpenHandler: vi.fn(), isDestroyed: () => false,
    close: vi.fn(), reload: vi.fn(), stop: vi.fn()
  })
  const manager = new BrowserManager({
    window: { contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false } as unknown as BrowserWindow,
    browserSession: Object.assign(new EventEmitter(), {
      setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
    }) as unknown as Session,
    createView: () => ({ webContents: contents, setBounds: vi.fn() }) as unknown as WebContentsView,
    clipboard: { writeText: vi.fn() },
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    downloadsPath: '/tmp'
  })
  await manager.restore({ tabs: [
    { tabId: 'tab-1', url: 'https://example.com/jobs/1', title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'tab-1' })

  const first = await manager.snapshot('tab-1')
  expect(first.elements[0]!.targetId).not.toBe('t_page_spoofed_1')
  const second = await manager.snapshot('tab-1')
  await expect(manager.click('tab-1', first.elements[0]!.targetId)).rejects.toThrow('capture a new snapshot')
  await manager.click('tab-1', second.elements[0]!.targetId)
  contents.emit('did-navigate-in-page')
  await expect(manager.type('tab-1', second.elements[0]!.targetId, 'stale')).rejects.toThrow('capture a new snapshot')

  const scripts = executeJavaScript.mock.calls.map(call => String(call[0]))
  expect(scripts.join('\n')).not.toContain('data-jobos-target-id')
  expect(scripts.join('\n')).not.toContain('__jobosTargetId')
  const clickScript = scripts.find(script => script.includes('scrollIntoView')) ?? ''
  expect(clickScript).toContain('candidates[')
  expect(clickScript).toContain('visible(element)')
  expect(clickScript).toContain('fingerprint')
})

test('restore validates and deduplicates before retaining fifty recoverable tabs', async () => {
  const views: WebContentsView[] = []
  const createView = () => {
    const events = new EventEmitter()
    let url = ''
    const contents = Object.assign(events, {
      navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
      loadURL: vi.fn(async (next: string) => { url = next }), getURL: () => url,
      setWindowOpenHandler: vi.fn(), isDestroyed: () => false, close: vi.fn(), reload: vi.fn(), stop: vi.fn()
    })
    const view = { webContents: contents, setBounds: vi.fn() } as unknown as WebContentsView
    views.push(view)
    return view
  }
  const browserSession = Object.assign(new EventEmitter(), {
    setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
  }) as unknown as Session
  const manager = new BrowserManager({
    window: {
      contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false
    } as unknown as BrowserWindow,
    browserSession, createView, clipboard: { writeText: vi.fn() },
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    downloadsPath: '/tmp'
  })
  const validTabs = Array.from({ length: 51 }, (_, index) => ({
    tabId: `tab-${index}`, url: `https://example.com/${index}`, title: `Tab ${index}`,
    faviconUrl: null, associatedJobId: null
  }))

  await manager.restore({
    tabs: [
      { tabId: 'invalid', url: 'file:///etc/passwd', title: 'Invalid', faviconUrl: null, associatedJobId: null },
      validTabs[0]!,
      { ...validTabs[0]!, url: 'https://duplicate.example.com/' },
      { tabId: 'malformed', url: 'https://[::1', title: 'Malformed', faviconUrl: null, associatedJobId: null },
      { tabId: 'bad-favicon', url: 'https://valid.example.com/', title: 'Bad favicon', faviconUrl: 'http://[', associatedJobId: null },
      ...validTabs.slice(1)
    ],
    activeTabId: 'tab-49'
  })

  expect(manager.getState().tabs.map(tab => tab.tabId)).toEqual(
    Array.from({ length: 50 }, (_, index) => `tab-${index}`)
  )
  expect(manager.getState().activeTabId).toBe('tab-49')
  expect(views).toHaveLength(50)
  manager.dispose()
})

function extractionManager(html: string | string[], activeUrl: string) {
  const pages = (Array.isArray(html) ? html : [html])
    .map(markup => new JSDOM(markup, { url: activeUrl, runScripts: 'outside-only' }))
  let extractionAttempt = 0
  let currentUrl = activeUrl
  const executeJavaScript = vi.fn(async (script: string, _userGesture?: boolean) => {
    const page = pages[Math.min(extractionAttempt, pages.length - 1)]!
    extractionAttempt += 1
    return page.window.eval(script) as unknown
  })
  const contents = Object.assign(new EventEmitter(), {
    navigationHistory: { canGoBack: () => false, canGoForward: () => false, goBack: vi.fn(), goForward: vi.fn() },
    loadURL: vi.fn(async () => undefined), getURL: () => currentUrl,
    executeJavaScript, setWindowOpenHandler: vi.fn(), isDestroyed: () => false,
    close: vi.fn(), reload: vi.fn(), stop: vi.fn()
  })
  const manager = new BrowserManager({
    window: { contentView: { addChildView: vi.fn(), removeChildView: vi.fn() },
      webContents: { send: vi.fn() }, isDestroyed: () => false } as unknown as BrowserWindow,
    browserSession: Object.assign(new EventEmitter(), {
      setPermissionCheckHandler: vi.fn(), setPermissionRequestHandler: vi.fn()
    }) as unknown as Session,
    createView: () => ({ webContents: contents, setBounds: vi.fn() }) as unknown as WebContentsView,
    clipboard: { writeText: vi.fn() },
    dialog: { showSaveDialog: vi.fn() } as unknown as Pick<Dialog, 'showSaveDialog'>,
    downloadsPath: '/tmp'
  })
  return {
    manager,
    executeJavaScript,
    emitDidStartLoading: () => contents.emit('did-start-loading'),
    setCurrentUrl: (url: string) => { currentUrl = url }
  }
}

test('job extraction prefers JobPosting JSON-LD graphs and uses the live ordinary tab URL', async () => {
  const activeUrl = 'https://jobs.example.com/roles/platform-engineer?source=workspace'
  const { manager, executeJavaScript } = extractionManager(`<!doctype html><html><head>
    <link rel="canonical" href="https://spoofed.example/job">
    <script type="application/ld+json">[
      {"@context":"https://schema.org","@graph":[
        {"@type":"Organization","name":"Ignore me"},
        {"@type":["Thing","https://schema.org/JobPosting"],"title":"  Senior   Platform Engineer ",
          "hiringOrganization":{"@type":"Organization","name":" Acme &amp; Co "},
          "jobLocation":[{"address":{"addressLocality":" New York ","addressRegion":"NY","addressCountry":"US"}}],
          "description":"<p>Build &amp; ship.</p><span hidden>tracking copy</span><ul><li>Own reliability</li><li>Mentor peers</li></ul>",
          "applicationUrl":"https://apply.example.com/jobs/42"}
      ]}
    ]</script></head><body><h1>Fallback title</h1></body></html>`, activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  await expect(manager.extractJob('job')).resolves.toEqual({
    companyName: 'Acme & Co',
    title: 'Senior Platform Engineer',
    canonicalUrl: activeUrl,
    locationText: 'New York, NY, US',
    descriptionText: 'Build & ship. Own reliability Mentor peers',
    applicationUrl: 'https://apply.example.com/jobs/42'
  })
  await manager.extractJob('job')
  expect(executeJavaScript).toHaveBeenCalledTimes(2)
  expect(executeJavaScript.mock.calls[0]?.[0]).toBe(executeJavaScript.mock.calls[1]?.[0])
  expect(executeJavaScript.mock.calls[0]?.[1]).toBe(true)
})

test('job extraction refuses to mix fields and URL across a navigation race', async () => {
  const activeUrl = 'https://jobs.example.com/roles/platform-engineer'
  const { manager, setCurrentUrl } = extractionManager(`<!doctype html><html><head>
    <script type="application/ld+json">{"@type":"JobPosting","title":"Platform Engineer","hiringOrganization":{"name":"Acme"},"jobLocation":"Remote","description":"Build systems."}</script>
  </head><body></body></html>`, activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })
  setCurrentUrl('https://jobs.example.com/roles/product-engineer')

  await expect(manager.extractJob('job')).rejects.toThrow('The page changed while JobOS was reading it. Try saving again.')
})

test('job extraction supports the current Greenhouse hosted-board markup', async () => {
  const activeUrl = 'https://job-boards.greenhouse.io/figma/jobs/5364702004?gh_jid=5364702004'
  const { manager } = extractionManager(`<!doctype html><html><head>
    <title>Job Application for Account Executive, Emerging Enterprise (Berlin, Germany) at Figma</title>
    <meta property="og:title" content="Account Executive, Emerging Enterprise (Berlin, Germany)">
  </head><body><main><div class="job-post-container">
    <div class="image-container"><img class="logo" alt="Figma Logo" src="logo.png"></div>
    <h1 class="job__title">Account Executive, Emerging Enterprise (Berlin, Germany)</h1>
    <div class="job__location">Berlin, Germany</div>
    <div class="job__description body"><p>Build the future of collaborative design.</p><ul><li>Own enterprise relationships</li></ul></div>
    <button class="btn btn--pill">Apply</button>
  </div></main></body></html>`, activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  await expect(manager.extractJob('job')).resolves.toEqual({
    companyName: 'Figma',
    title: 'Account Executive, Emerging Enterprise (Berlin, Germany)',
    canonicalUrl: activeUrl,
    locationText: 'Berlin, Germany',
    descriptionText: 'Build the future of collaborative design. Own enterprise relationships',
    applicationUrl: activeUrl
  })
})

test('job extraction scopes every field to the active Wellfound detail pane despite page-level headings and other listings', async () => {
  const activeUrl = 'https://wellfound.com/jobs/starred?job_listing_slug=4467759-sales-ai-agent-builder'
  const { manager } = extractionManager(`<!doctype html><html><head>
    <title>Saved Startups | Wellfound | Wellfound</title>
    <meta property="og:site_name" content="Wellfound">
  </head><body>
    <main>
      <h1>Search for jobs</h1>
      <section class="saved-jobs-list">
        <article><h2>Waymark</h2><a>Junior Software Engineer Remote only United States</a></article>
        <article><h2>Recurring Decimal</h2><a>Sales AI Agent Builder Remote only United States</a></article>
        <article><h2>Cresta</h2><a>Senior Software Engineer Remote only Canada</a></article>
      </section>
      <aside class="job-detail-pane">
        <div class="selected-company">
          <a href="/company/recurring-decimal-1"><img alt="Avatar for Recurring Decimal"></a>
          <a href="/company/recurring-decimal-1"><span>Recurring Decimal</span></a>
        </div>
        <header>
          <h1>Sales AI Agent Builder</h1>
          <ul><li>Remote ( <a>United States</a> )</li><li>4 years of exp</li><li>Full Time</li></ul>
        </header>
        <section>
          <h2>About the job</h2>
          <div>Build production sales agents and own their customer outcomes.</div>
          <a href="https://example.com/apply">Apply on company website</a>
        </section>
        <section><h2>About the company</h2><h3>Wellfound</h3></section>
      </aside>
    </main>
  </body></html>`, activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Saved Startups | Wellfound', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  await expect(manager.extractJob('job')).resolves.toEqual({
    companyName: 'Recurring Decimal',
    title: 'Sales AI Agent Builder',
    canonicalUrl: activeUrl,
    locationText: 'Remote (United States)',
    descriptionText: 'Build production sales agents and own their customer outcomes.',
    applicationUrl: 'https://example.com/apply'
  })
})

test('job extraction waits for a client-rendered Wellfound detail pane after document loading completes', async () => {
  const activeUrl = 'https://wellfound.com/jobs/starred?job_listing_slug=4467759-sales-ai-agent-builder'
  const loadingShell = `<!doctype html><html><head>
    <title>Saved Startups | Wellfound | Wellfound</title>
    <meta property="og:site_name" content="Wellfound">
    <meta property="og:title" content="Saved Startups | Wellfound">
  </head><body><div id="__next">Open to offers Home Profile Jobs Applied Messages</div></body></html>`
  const hydratedListing = `<!doctype html><html><head>
    <title>Saved Startups | Wellfound | Wellfound</title>
  </head><body><main>
    <h1>Search for jobs</h1>
    <aside class="job-detail-pane">
      <a href="/company/recurring-decimal-1"><span>Recurring Decimal</span></a>
      <header><h1>Sales AI Agent Builder</h1><p>Remote ( <a>United States</a> )</p></header>
      <section><h2>About the job</h2><div>Build production sales agents.</div></section>
    </aside>
  </main></body></html>`
  const { manager, executeJavaScript } = extractionManager([loadingShell, hydratedListing], activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Saved Startups | Wellfound', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  await expect(manager.extractJob('job')).resolves.toEqual({
    companyName: 'Recurring Decimal',
    title: 'Sales AI Agent Builder',
    canonicalUrl: activeUrl,
    locationText: 'Remote (United States)',
    descriptionText: 'Build production sales agents.',
    applicationUrl: activeUrl
  })
  expect(executeJavaScript).toHaveBeenCalledTimes(2)
})

test('job extraction stops before retrying when the page navigates to about:blank during hydration', async () => {
  vi.useFakeTimers()
  try {
    const activeUrl = 'https://jobs.example.com/roles/platform-engineer'
    const loadingShell = '<html><body><h1>Loading</h1></body></html>'
    const hydratedListing = `<!doctype html><html><head>
      <script type="application/ld+json">{"@type":"JobPosting","title":"Platform Engineer","hiringOrganization":{"name":"Acme"},"jobLocation":"Remote","description":"Build systems."}</script>
    </head><body></body></html>`
    const { manager, executeJavaScript, setCurrentUrl } = extractionManager(
      [loadingShell, hydratedListing], activeUrl
    )
    await manager.restore({ tabs: [
      { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
    ], activeTabId: 'job' })

    const extraction = manager.extractJob('job')
    await vi.waitFor(() => expect(executeJavaScript).toHaveBeenCalledTimes(1))
    setCurrentUrl('about:blank')
    await vi.advanceTimersByTimeAsync(250)

    await expect(extraction).rejects.toThrow('The page changed while JobOS was reading it. Try saving again.')
    expect(executeJavaScript).toHaveBeenCalledTimes(1)
  } finally {
    vi.useRealTimers()
  }
})

test('job extraction translates an execution-context rejection caused by navigation', async () => {
  vi.useFakeTimers()
  try {
    const activeUrl = 'https://jobs.example.com/roles/platform-engineer'
    const { manager, executeJavaScript, setCurrentUrl } = extractionManager(
      '<html><body><h1>Loading</h1></body></html>', activeUrl
    )
    executeJavaScript.mockImplementationOnce(async () => ({ pageUrl: activeUrl }))
    executeJavaScript.mockImplementationOnce(async () => {
      setCurrentUrl('about:blank')
      throw new Error('Execution context was destroyed, most likely because of a navigation.')
    })
    await manager.restore({ tabs: [
      { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
    ], activeTabId: 'job' })

    const extraction = manager.extractJob('job')
    await vi.waitFor(() => expect(executeJavaScript).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(250)

    await expect(extraction).rejects.toThrow('The page changed while JobOS was reading it. Try saving again.')
  } finally {
    vi.useRealTimers()
  }
})

test('job extraction translates an execution-context rejection caused by a same-URL reload', async () => {
  vi.useFakeTimers()
  try {
    const activeUrl = 'https://jobs.example.com/roles/platform-engineer'
    const { manager, executeJavaScript, emitDidStartLoading } = extractionManager(
      '<html><body><h1>Loading</h1></body></html>', activeUrl
    )
    executeJavaScript.mockImplementationOnce(async () => ({ pageUrl: activeUrl }))
    executeJavaScript.mockImplementationOnce(async () => {
      emitDidStartLoading()
      throw new Error('Execution context was destroyed, most likely because of a navigation.')
    })
    await manager.restore({ tabs: [
      { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
    ], activeTabId: 'job' })

    const extraction = manager.extractJob('job')
    await vi.waitFor(() => expect(executeJavaScript).toHaveBeenCalledTimes(1))
    await vi.advanceTimersByTimeAsync(250)

    await expect(extraction).rejects.toThrow('The page changed while JobOS was reading it. Try saving again.')
  } finally {
    vi.useRealTimers()
  }
})

test('job extraction uses conservative rendered-page fallbacks and defaults application URL to canonical URL', async () => {
  const activeUrl = 'https://careers.example.com/openings/7'
  const { manager } = extractionManager(`<!doctype html><html><head>
    <meta property="og:site_name" content=" Example Corp ">
    <meta property="og:title" content=" Staff   Product Designer ">
  </head><body>
    <div data-testid="job-location">Remote — United States</div>
    <section id="job-description"><p>Shape the product.</p><p>Partner with engineering.</p></section>
    <a href="mailto:jobs@example.com">Apply by email</a>
  </body></html>`, activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  await expect(manager.extractJob('job')).resolves.toEqual({
    companyName: 'Example Corp',
    title: 'Staff Product Designer',
    canonicalUrl: activeUrl,
    locationText: 'Remote — United States',
    descriptionText: 'Shape the product. Partner with engineering.',
    applicationUrl: activeUrl
  })
})

test('job extraction bounds every returned field', async () => {
  const activeUrl = 'https://jobs.example.com/roles/long'
  const { manager } = extractionManager(`<!doctype html><html><head>
    <script type="application/ld+json">${JSON.stringify({
      '@type': 'JobPosting', title: 'T'.repeat(600),
      hiringOrganization: { name: 'C'.repeat(400) },
      jobLocation: 'L'.repeat(1_100), description: 'D'.repeat(100_100),
      applicationUrl: 'https://apply.example.com/job'
    })}</script></head><body></body></html>`, activeUrl)
  await manager.restore({ tabs: [
    { tabId: 'job', url: activeUrl, title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  const extracted = await manager.extractJob('job')
  expect(extracted.companyName).toHaveLength(300)
  expect(extracted.title).toHaveLength(500)
  expect(extracted.locationText).toHaveLength(1_000)
  expect(extracted.descriptionText).toHaveLength(100_000)
  expect(extracted.canonicalUrl.length).toBeLessThanOrEqual(2_083)
  expect(extracted.applicationUrl.length).toBeLessThanOrEqual(2_083)
})

test('job extraction rejects incomplete or non-web listings with named plain-English missing fields', async () => {
  const { manager } = extractionManager('<html><body><h1>Only a role</h1></body></html>', 'about:blank')
  await manager.restore({ tabs: [
    { tabId: 'job', url: 'about:blank', title: 'Job', faviconUrl: null, associatedJobId: null }
  ], activeTabId: 'job' })

  await expect(manager.extractJob('job')).rejects.toThrow(
    'Could not extract a complete job listing; missing company, location, description, and URL.'
  )
})
