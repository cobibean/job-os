import { randomUUID } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import type { BrowserWindow as BrowserWindowType, IpcMain, IpcMainEvent } from 'electron'
import { BrowserWindow } from 'electron'

import type { DocxWorkerRequest, DocxWorkerResponse, DocxWorkerResult } from '../shared/docxWorker.js'

const dirname = path.dirname(fileURLToPath(import.meta.url))

export class DocxWorkerManager {
  private window: BrowserWindowType | null = null
  private ready: Promise<void> | null = null
  private available = false
  private readonly pending = new Map<string, {
    resolve: (value: DocxWorkerResult) => void
    reject: (error: Error) => void
    timer: ReturnType<typeof setTimeout>
    worker: BrowserWindowType
  }>()

  constructor(private readonly ipcMain: IpcMain) {
    this.ipcMain.on('jobos:docx-worker:response', this.onResponse)
  }

  async run(request: DocxWorkerRequest): Promise<DocxWorkerResult> {
    await this.ensureReady()
    const worker = this.window
    if (!worker || worker.isDestroyed()) throw new Error('DOCX worker unavailable')
    const requestId = `docxw_${randomUUID()}`
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        reject(new Error('DOCX worker timed out'))
      }, 15_000)
      this.pending.set(requestId, { resolve, reject, timer, worker })
      worker.webContents.send('jobos:docx-worker:request', { requestId, request })
    })
  }

  isAvailable(): boolean {
    return this.available && this.window !== null && !this.window.isDestroyed()
  }

  dispose(): void {
    this.ipcMain.removeListener('jobos:docx-worker:response', this.onResponse)
    for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error('DOCX worker closed')) }
    this.pending.clear()
    this.window?.destroy()
    this.window = null
    this.ready = null
    this.available = false
  }

  private ensureReady(): Promise<void> {
    if (this.ready) return this.ready
    this.ready = new Promise((resolve, reject) => {
      let loadSettled = false
      const failLoad = (error: Error) => {
        if (loadSettled) return
        loadSettled = true
        this.available = false
        if (this.window === worker) this.ready = null
        reject(error)
      }
      const worker = new BrowserWindow({
        show: false,
        width: 900,
        height: 1100,
        webPreferences: {
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          preload: path.join(dirname, '../preload/docxWorker.cjs')
        }
      })
      this.window = worker
      worker.webContents.once('did-finish-load', () => {
        if (loadSettled) return
        loadSettled = true
        this.available = true
        resolve()
      })
      worker.webContents.once('did-fail-load', (_event, code, description) => {
        failLoad(new Error(`DOCX worker failed to load (${code}): ${description}`))
        if (!worker.isDestroyed()) worker.destroy()
      })
      worker.webContents.once('render-process-gone', (_event, details) => {
        if (this.window === worker) this.available = false
        failLoad(new Error(`DOCX worker renderer stopped: ${details.reason}`))
        if (!worker.isDestroyed()) worker.destroy()
      })
      worker.once('closed', () => {
        if (this.window === worker) this.available = false
        failLoad(new Error('DOCX worker closed before it finished loading'))
        if (this.window === worker) {
          this.window = null
          this.ready = null
        }
        for (const [requestId, item] of this.pending) {
          if (item.worker !== worker) continue
          clearTimeout(item.timer)
          item.reject(new Error('DOCX worker closed'))
          this.pending.delete(requestId)
        }
      })
      const devUrl = process.env.VITE_DEV_SERVER_URL
      const loading = devUrl
        ? worker.loadURL(`${devUrl}/docx-worker.html`)
        : worker.loadFile(path.join(dirname, '../renderer/docx-worker.html'))
      void loading.catch(error => {
        failLoad(error instanceof Error ? error : new Error('DOCX worker failed to load'))
        if (!worker.isDestroyed()) worker.destroy()
      })
    })
    return this.ready
  }

  private readonly onResponse = (event: IpcMainEvent, response: DocxWorkerResponse): void => {
    if (!this.window || event.sender !== this.window.webContents) return
    const pending = this.pending.get(response.requestId)
    if (!pending) return
    clearTimeout(pending.timer)
    this.pending.delete(response.requestId)
    if (response.error || !response.result) pending.reject(new Error(response.error ?? 'DOCX worker returned no result'))
    else pending.resolve(response.result)
  }
}
