import type { BrowserWindowConstructorOptions, WebContents } from 'electron'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { EditableDocument } from '../../shared/editableDocuments.js'
import { renderEditableDocumentHtml } from './documentHtml.js'

const DEFAULT_PDF_TIMEOUT_MS = 15_000
const MAX_PDF_BYTES = 20 * 1024 * 1024
const PDF_HEADER = '%PDF-'
const PRINT_PAYLOAD_CHANNEL = 'jobos:document-print-payload'
const PRINT_READY_CHANNEL = 'jobos:document-print-ready'
const PRINT_FAILED_CHANNEL = 'jobos:document-print-failed'
const moduleDirectory = path.dirname(fileURLToPath(import.meta.url))

const PRINT_READINESS_SCRIPT = `
(async () => {
  await document.fonts.ready;
  const images = Array.from(document.images);
  await Promise.all(images.map(async image => {
    if (!image.complete) {
      await new Promise((resolve, reject) => {
        image.addEventListener('load', resolve, { once: true });
        image.addEventListener('error', reject, { once: true });
      });
    }
    if (typeof image.decode === 'function') await image.decode();
  }));
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  return true;
})()
`

interface PrintWebContents extends Pick<WebContents, 'executeJavaScript' | 'isDestroyed' | 'on' | 'printToPDF' | 'setWindowOpenHandler'> {
  send?: WebContents['send']
}

interface PrintWindow {
  readonly webContents: PrintWebContents
  destroy(): void
  isDestroyed(): boolean
  loadFile?(filePath: string): Promise<void>
  loadURL(url: string): Promise<void>
}

export interface PdfExporterDependencies {
  createWindow?: (options: BrowserWindowConstructorOptions) => PrintWindow | Promise<PrintWindow>
  timeoutMs?: number
  allowUnresolvedSuggestions?: boolean
}

async function createElectronPrintWindow(options: BrowserWindowConstructorOptions): Promise<PrintWindow> {
  const { BrowserWindow } = await import('electron')
  return new BrowserWindow(options)
}

function printWindowOptions(): BrowserWindowConstructorOptions {
  return {
    show: false,
    width: 816,
    height: 1056,
    webPreferences: {
      allowRunningInsecureContent: false,
      backgroundThrottling: false,
      contextIsolation: true,
      javascript: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
      webSecurity: true,
      preload: path.join(moduleDirectory, '../../preload/printPreload.cjs')
    }
  }
}

function dataUrlForHtml(html: string): string {
  return `data:text/html;base64,${Buffer.from(html, 'utf8').toString('base64')}`
}

function assertPdfBytes(value: Buffer): Uint8Array {
  if (value.byteLength === 0 || value.byteLength > MAX_PDF_BYTES) {
    throw new Error(`Generated PDF must be between 1 and ${MAX_PDF_BYTES} bytes`)
  }
  if (value.subarray(0, PDF_HEADER.length).toString('ascii') !== PDF_HEADER) {
    throw new Error('Electron returned invalid PDF bytes')
  }
  return new Uint8Array(value)
}

/**
 * Generates the authoritative PDF for one saved canonical editable document.
 * The hidden window can load only the self-contained data document produced by
 * renderEditableDocumentHtml; its CSP and navigation guards prevent all remote
 * navigation and resource fetching.
 */
export async function exportEditableDocumentPdf(
  document: EditableDocument,
  dependencies: PdfExporterDependencies = {}
): Promise<Uint8Array> {
  const html = renderEditableDocumentHtml(document, {
    allowUnresolvedSuggestions: dependencies.allowUnresolvedSuggestions
  })
  const createWindow = dependencies.createWindow ?? createElectronPrintWindow
  const timeoutMs = dependencies.timeoutMs ?? DEFAULT_PDF_TIMEOUT_MS
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0) throw new Error('PDF export timeout must be a positive integer')

  let printWindow: PrintWindow | undefined
  let active = true
  let timeout: NodeJS.Timeout | undefined

  const operation = (async () => {
    printWindow = await createWindow(printWindowOptions())
    if (!active) {
      if (!printWindow.isDestroyed()) printWindow.destroy()
      throw new Error('PDF export was cancelled')
    }
    const { webContents } = printWindow

    const preventNavigation = (event: { preventDefault(): void }) => event.preventDefault()
    webContents.on('will-navigate', preventNavigation)
    webContents.on('will-redirect', preventNavigation)
    webContents.setWindowOpenHandler(() => ({ action: 'deny' }))

    const send = webContents.send?.bind(webContents)
    if (printWindow.loadFile && send) {
      const ready = new Promise<number>((resolve, reject) => {
        webContents.on('ipc-message', (_event, channel, ...args) => {
          if (channel === PRINT_READY_CHANNEL) {
            const pageCount = args[0]
            if (!Number.isSafeInteger(pageCount) || Number(pageCount) < 1) reject(new Error('Print renderer returned an invalid page count'))
            else resolve(Number(pageCount))
          } else if (channel === PRINT_FAILED_CHANNEL) {
            reject(new Error(typeof args[0] === 'string' ? args[0] : 'Print rendering failed'))
          }
        })
      })
      await printWindow.loadFile(path.join(moduleDirectory, '../../renderer/print.html'))
      send(PRINT_PAYLOAD_CHANNEL, {
        document,
        allowUnresolvedSuggestions: dependencies.allowUnresolvedSuggestions === true
      })
      await ready
    } else {
      await printWindow.loadURL(dataUrlForHtml(html))
      const ready = await webContents.executeJavaScript(PRINT_READINESS_SCRIPT, true)
      if (ready !== true) throw new Error('Print document did not become ready')
    }
    if (!active || printWindow.isDestroyed() || webContents.isDestroyed()) throw new Error('PDF export was cancelled')

    const bytes = await webContents.printToPDF({
      preferCSSPageSize: true,
      printBackground: true
    })
    if (!active) throw new Error('PDF export was cancelled')
    return assertPdfBytes(bytes)
  })()

  const timedOut = new Promise<never>((_resolve, reject) => {
    timeout = setTimeout(() => {
      active = false
      reject(new Error(`PDF export timed out after ${timeoutMs}ms`))
    }, timeoutMs)
  })

  try {
    return await Promise.race([operation, timedOut])
  } finally {
    active = false
    if (timeout) clearTimeout(timeout)
    if (printWindow && !printWindow.isDestroyed()) printWindow.destroy()
  }
}
