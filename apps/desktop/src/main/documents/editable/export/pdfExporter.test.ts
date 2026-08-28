// @vitest-environment node

import { EventEmitter } from 'node:events'

import type { BrowserWindowConstructorOptions, WebContents } from 'electron'
import { describe, expect, it, vi } from 'vitest'

import type { EditableDocument } from '../../../../shared/editableDocuments.js'
import { createBlankDocument, defaultDocumentSettings } from '../../../../shared/editableDocumentSchema.js'
import { exportEditableDocumentPdf } from './pdfExporter.js'

function documentFixture(): EditableDocument {
  const content = createBlankDocument('resume')
  const paragraph = content.content?.[1]?.content?.[0]
  if (!paragraph) throw new Error('Fixture paragraph missing')
  paragraph.content = [{ type: 'text', text: 'Authoritative PDF' }]
  return {
    schemaVersion: 1,
    documentId: 'edoc_ABCDEFGHIJKLMNOPQRSTUVWX',
    jobId: 'job-7',
    documentKey: 'resume',
    documentLabel: 'Resume',
    revision: 4,
    content,
    settings: defaultDocumentSettings(),
    comments: [],
    sourceArtifactId: null,
    sourceFilename: null,
    sourceSha256: null,
    publishedRevision: null,
    importReport: { sourceFilename: null, importedAt: null, issues: [] },
    createdAt: '2026-08-07T00:00:00Z',
    updatedAt: '2026-08-07T00:00:00Z'
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

function fakePrintWindow(overrides: {
  executeJavaScript?: (code: string, userGesture?: boolean) => Promise<unknown>
  loadURL?: (url: string) => Promise<void>
  printToPDF?: (options: Electron.PrintToPDFOptions) => Promise<Buffer>
} = {}) {
  const events = new EventEmitter()
  let destroyed = false
  let windowOpenHandler: ((details: unknown) => { action: 'deny' | 'allow' }) | undefined
  const loadURL = vi.fn(overrides.loadURL ?? (async () => undefined))
  const executeJavaScript = vi.fn(overrides.executeJavaScript ?? (async () => true))
  const printToPDF = vi.fn(overrides.printToPDF ?? (async () => Buffer.from('%PDF-1.7\nfixture')))
  const destroy = vi.fn(() => { destroyed = true })
  const setWindowOpenHandler = vi.fn((handler: typeof windowOpenHandler) => { windowOpenHandler = handler })
  const webContents = Object.assign(events, {
    executeJavaScript,
    isDestroyed: () => destroyed,
    printToPDF,
    setWindowOpenHandler
  }) as unknown as Pick<WebContents, 'executeJavaScript' | 'isDestroyed' | 'on' | 'printToPDF' | 'setWindowOpenHandler'>
  const window = {
    destroy,
    isDestroyed: () => destroyed,
    loadURL,
    webContents
  }
  return { destroy, events, executeJavaScript, loadURL, printToPDF, setWindowOpenHandler, window, windowOpenHandler: () => windowOpenHandler }
}

describe('authoritative Electron PDF export', () => {
  it('loads only self-contained canonical HTML, waits for readiness, prints with exact options, and cleans up', async () => {
    const fake = fakePrintWindow()
    let options: BrowserWindowConstructorOptions | undefined
    const bytes = await exportEditableDocumentPdf(documentFixture(), {
      createWindow: supplied => {
        options = supplied
        return fake.window
      }
    })

    expect(options).toMatchObject({
      show: false,
      webPreferences: {
        allowRunningInsecureContent: false,
        backgroundThrottling: false,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        spellcheck: false,
        webSecurity: true
      }
    })
    expect(fake.loadURL).toHaveBeenCalledOnce()
    const url = fake.loadURL.mock.calls[0]?.[0]
    expect(url).toMatch(/^data:text\/html;base64,/)
    const html = Buffer.from(String(url).split(',')[1] ?? '', 'base64').toString('utf8')
    expect(html).toContain('Authoritative PDF')
    expect(html).toContain("default-src 'none'")
    expect(html).toContain('img-src data:')
    expect(html).not.toMatch(/<script[ >]/i)

    const readinessScript = fake.executeJavaScript.mock.calls[0]?.[0]
    expect(readinessScript).toContain('document.fonts.ready')
    expect(readinessScript).toContain('image.decode')
    expect(readinessScript).toContain('requestAnimationFrame')
    expect(fake.printToPDF).toHaveBeenCalledWith({ preferCSSPageSize: true, printBackground: true })
    expect(Buffer.from(bytes).toString('ascii')).toBe('%PDF-1.7\nfixture')
    expect(fake.destroy).toHaveBeenCalledOnce()
  })

  it('uses the dedicated Paged.js renderer and waits for its bounded IPC readiness handshake', async () => {
    const events = new EventEmitter()
    const printToPDF = vi.fn(async () => Buffer.from('%PDF-1.7\npaged'))
    const loadFile = vi.fn(async (_filePath: string) => undefined)
    const send = vi.fn((channel: string, _payload?: unknown) => {
      expect(channel).toBe('jobos:document-print-payload')
      queueMicrotask(() => events.emit('ipc-message', {}, 'jobos:document-print-ready', 2))
    })
    const webContents = Object.assign(events, {
      executeJavaScript: vi.fn(),
      isDestroyed: () => false,
      printToPDF,
      send,
      setWindowOpenHandler: vi.fn()
    }) as unknown as WebContents
    const destroy = vi.fn()
    const bytes = await exportEditableDocumentPdf(documentFixture(), {
      createWindow: () => ({
        destroy,
        isDestroyed: () => false,
        loadFile,
        loadURL: vi.fn(),
        webContents
      })
    })

    expect(loadFile).toHaveBeenCalledOnce()
    expect(loadFile.mock.calls[0]?.[0]).toMatch(/src\/renderer\/print\.html$/)
    expect(send.mock.calls[0]?.[1]).toMatchObject({ document: { revision: 4 } })
    expect(printToPDF).toHaveBeenCalledWith({ preferCSSPageSize: true, printBackground: true })
    expect(Buffer.from(bytes).toString('ascii')).toBe('%PDF-1.7\npaged')
    expect(destroy).toHaveBeenCalledOnce()
  })

  it('does not print until both load and readiness complete', async () => {
    const loaded = deferred<void>()
    const ready = deferred<boolean>()
    const fake = fakePrintWindow({
      loadURL: vi.fn(() => loaded.promise),
      executeJavaScript: vi.fn(() => ready.promise)
    })
    const result = exportEditableDocumentPdf(documentFixture(), { createWindow: () => fake.window })

    await Promise.resolve()
    expect(fake.executeJavaScript).not.toHaveBeenCalled()
    expect(fake.printToPDF).not.toHaveBeenCalled()
    loaded.resolve()
    await Promise.resolve()
    expect(fake.executeJavaScript).toHaveBeenCalledOnce()
    expect(fake.printToPDF).not.toHaveBeenCalled()
    ready.resolve(true)
    await expect(result).resolves.toBeInstanceOf(Uint8Array)
    expect(fake.printToPDF).toHaveBeenCalledOnce()
  })

  it('denies new windows and prevents navigation and redirects', async () => {
    const fake = fakePrintWindow()
    await exportEditableDocumentPdf(documentFixture(), { createWindow: () => fake.window })

    const openHandler = fake.windowOpenHandler()
    expect(openHandler?.({ url: 'https://example.com' })).toEqual({ action: 'deny' })
    for (const eventName of ['will-navigate', 'will-redirect']) {
      const event = { preventDefault: vi.fn() }
      fake.events.emit(eventName, event, 'https://example.com')
      expect(event.preventDefault).toHaveBeenCalledOnce()
    }
  })

  it('times out the bounded operation and destroys the hidden window', async () => {
    vi.useFakeTimers()
    const neverLoads = deferred<void>()
    const fake = fakePrintWindow({ loadURL: vi.fn(() => neverLoads.promise) })
    const result = exportEditableDocumentPdf(documentFixture(), { createWindow: () => fake.window, timeoutMs: 25 })
    const rejection = expect(result).rejects.toThrow('PDF export timed out after 25ms')
    await vi.advanceTimersByTimeAsync(25)

    await rejection
    expect(fake.printToPDF).not.toHaveBeenCalled()
    expect(fake.destroy).toHaveBeenCalledOnce()
    vi.useRealTimers()
  })

  it('destroys a window that is created only after the timeout fires', async () => {
    vi.useFakeTimers()
    const created = deferred<ReturnType<typeof fakePrintWindow>['window']>()
    const fake = fakePrintWindow()
    const result = exportEditableDocumentPdf(documentFixture(), { createWindow: () => created.promise, timeoutMs: 25 })
    const rejection = expect(result).rejects.toThrow('PDF export timed out after 25ms')
    await vi.advanceTimersByTimeAsync(25)
    await rejection

    created.resolve(fake.window)
    await vi.runAllTimersAsync()
    await Promise.resolve()
    expect(fake.loadURL).not.toHaveBeenCalled()
    expect(fake.destroy).toHaveBeenCalledOnce()
    vi.useRealTimers()
  })

  it.each([
    ['load', { loadURL: vi.fn(async () => { throw new Error('load failed') }) }],
    ['readiness', { executeJavaScript: vi.fn(async () => { throw new Error('readiness failed') }) }],
    ['print', { printToPDF: vi.fn(async () => { throw new Error('print failed') }) }]
  ])('cleans up when %s fails', async (_stage, overrides) => {
    const fake = fakePrintWindow(overrides)
    await expect(exportEditableDocumentPdf(documentFixture(), { createWindow: () => fake.window })).rejects.toThrow('failed')
    expect(fake.destroy).toHaveBeenCalledOnce()
  })

  it.each([
    ['non-PDF bytes', Buffer.from('not a pdf'), 'invalid PDF bytes'],
    ['oversized bytes', Buffer.concat([Buffer.from('%PDF-'), Buffer.alloc(20 * 1024 * 1024)]), 'between 1 and']
  ])('rejects %s and cleans up', async (_case, output, message) => {
    const fake = fakePrintWindow({ printToPDF: vi.fn(async () => output) })
    await expect(exportEditableDocumentPdf(documentFixture(), { createWindow: () => fake.window })).rejects.toThrow(message)
    expect(fake.destroy).toHaveBeenCalledOnce()
  })
})
