// @vitest-environment node

import { afterEach, describe, expect, it, vi } from 'vitest'

import type { IpcMain } from 'electron'

const electron = vi.hoisted(() => {
  type Listener = { callback: (...arguments_: unknown[]) => void; once: boolean }

  class TinyEmitter {
    private listeners = new Map<string, Listener[]>()

    on(event: string, callback: (...arguments_: unknown[]) => void): this {
      const listeners = this.listeners.get(event) ?? []
      listeners.push({ callback, once: false })
      this.listeners.set(event, listeners)
      return this
    }

    once(event: string, callback: (...arguments_: unknown[]) => void): this {
      const listeners = this.listeners.get(event) ?? []
      listeners.push({ callback, once: true })
      this.listeners.set(event, listeners)
      return this
    }

    removeListener(event: string, callback: (...arguments_: unknown[]) => void): this {
      this.listeners.set(event, (this.listeners.get(event) ?? []).filter(item => item.callback !== callback))
      return this
    }

    emit(event: string, ...arguments_: unknown[]): void {
      const listeners = this.listeners.get(event) ?? []
      this.listeners.set(event, listeners.filter(item => !item.once))
      for (const item of listeners) item.callback(...arguments_)
    }
  }

  class FakeWebContents extends TinyEmitter {
    send = vi.fn()
  }

  class FakeBrowserWindow extends TinyEmitter {
    static instances: FakeBrowserWindow[] = []
    readonly webContents = new FakeWebContents()
    private destroyed = false
    loadFile = vi.fn(async () => undefined)
    loadURL = vi.fn(async () => undefined)

    constructor() {
      super()
      FakeBrowserWindow.instances.push(this)
    }

    destroy(): void {
      this.destroyed = true
    }

    close(): void {
      this.emit('closed')
    }

    isDestroyed(): boolean { return this.destroyed }
  }

  return { FakeBrowserWindow, TinyEmitter }
})

vi.mock('electron', () => ({ BrowserWindow: electron.FakeBrowserWindow }))

import { DocxWorkerManager } from './DocxWorkerManager.js'

function ipcMain(): IpcMain {
  return new electron.TinyEmitter() as unknown as IpcMain
}

afterEach(() => {
  electron.FakeBrowserWindow.instances.length = 0
  vi.restoreAllMocks()
})

describe('DocxWorkerManager', () => {
  it('rejects an early close and recreates the worker on the next request', async () => {
    const manager = new DocxWorkerManager(ipcMain())
    const firstRun = manager.run({ kind: 'inspect', bytes: new ArrayBuffer(0) })
    electron.FakeBrowserWindow.instances[0]?.emit('closed')
    await expect(firstRun).rejects.toThrow('closed before it finished loading')

    const secondRun = manager.run({ kind: 'inspect', bytes: new ArrayBuffer(0) })
    expect(electron.FakeBrowserWindow.instances).toHaveLength(2)
    electron.FakeBrowserWindow.instances[1]?.webContents.emit('did-fail-load', {}, -2, 'failed')
    await expect(secondRun).rejects.toThrow('failed to load')
    manager.dispose()
  })

  it('does not let a stale worker close tear down its replacement', async () => {
    const manager = new DocxWorkerManager(ipcMain())
    const firstRun = manager.run({ kind: 'inspect', bytes: new ArrayBuffer(0) })
    const firstWorker = electron.FakeBrowserWindow.instances[0]
    firstWorker?.webContents.emit('did-fail-load', {}, -2, 'failed')
    await expect(firstRun).rejects.toThrow('failed to load')

    const replacementRun = manager.run({ kind: 'inspect', bytes: new ArrayBuffer(0) })
    let replacementOutcome = 'pending'
    void replacementRun.then(
      () => { replacementOutcome = 'resolved' },
      () => { replacementOutcome = 'rejected' }
    )
    const replacement = electron.FakeBrowserWindow.instances[1]
    replacement?.webContents.emit('did-finish-load')
    await Promise.resolve()

    firstWorker?.close()
    await Promise.resolve()
    expect(replacementOutcome).toBe('pending')
    expect(replacement?.webContents.send).toHaveBeenCalledTimes(1)

    replacement?.close()
    await expect(replacementRun).rejects.toThrow('DOCX worker closed')
    manager.dispose()
  })

  it('rejects pending work when the ready renderer crashes', async () => {
    const manager = new DocxWorkerManager(ipcMain())
    const run = manager.run({ kind: 'inspect', bytes: new ArrayBuffer(0) })
    const worker = electron.FakeBrowserWindow.instances[0]
    worker?.webContents.emit('did-finish-load')
    await Promise.resolve()
    expect(worker?.webContents.send).toHaveBeenCalledTimes(1)

    worker?.webContents.emit('render-process-gone', {}, { reason: 'crashed' })
    worker?.close()
    await expect(run).rejects.toThrow('DOCX worker closed')
    manager.dispose()
  })
})
