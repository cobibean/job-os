import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { app, BrowserWindow, clipboard, dialog, ipcMain, session, shell, WebContentsView } from 'electron'
import type { IpcMainInvokeEvent } from 'electron'

import type { BrowserBounds, ConnectivitySnapshot, JobSortMode, JobStatus, WorkspaceSnapshot } from '../shared/contracts.js'
import { BROWSER_PARTITION, BrowserManager, remoteBrowserPreferences } from './browser.js'
import { registerBrowserRestoreHandler } from './browserIpc.js'
import { probeConnectivity } from './connectivity.js'
import { createMainJobsClient, startJobEventStream } from './jobs.js'
import type { JobsConfig } from './jobs.js'
import { createMainDocumentsClient } from './documents.js'
import { isTrustedRendererUrl } from './security.js'
import { createMainWorkspaceClient } from './workspace.js'

const currentDirectory = path.dirname(fileURLToPath(import.meta.url))
const rendererRoot = path.resolve(currentDirectory, '../renderer')
const developmentUrl = process.env.VITE_DEV_SERVER_URL
const developmentOrigin = developmentUrl ? new URL(developmentUrl).origin : undefined
let browserManager: BrowserManager | null = null

function disconnectedCredentialSnapshot(): ConnectivitySnapshot {
  return {
    state: 'disconnected',
    checkedAt: new Date().toISOString(),
    message: 'Device credential unavailable'
  }
}

function assertTrustedRenderer(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url ?? ''
  if (!isTrustedRendererUrl(senderUrl, { developmentOrigin, rendererRoot })) {
    throw new Error('Untrusted renderer')
  }
}

function jobsConfig(): JobsConfig | null {
  const deviceToken = process.env.JOBOS_DEVICE_TOKEN
  if (!deviceToken) return null
  return {
    baseUrl: process.env.JOBOS_API_BASE_URL ?? 'http://100.123.109.19:8766',
    deviceToken
  }
}

function registerConnectivityInterface(): void {
  ipcMain.handle('jobos:connectivity:get', async event => {
    assertTrustedRenderer(event)

    const deviceToken = process.env.JOBOS_DEVICE_TOKEN
    if (!deviceToken) return disconnectedCredentialSnapshot()

    const snapshot = await probeConnectivity({
      baseUrl: process.env.JOBOS_API_BASE_URL ?? 'http://100.123.109.19:8766',
      deviceToken
    })
    if (process.env.JOBOS_CAPTURE_PATH) {
      console.info('[JobOS capture] connectivity', JSON.stringify(snapshot))
    }
    return snapshot
  })
}

function registerJobsInterface(): void {
  const config = jobsConfig()
  const jobs = config ? createMainJobsClient(config) : null
  const requireJobs = () => {
    if (!jobs) throw new Error('Device credential unavailable')
    return jobs
  }
  const trusted = (event: IpcMainInvokeEvent) => {
    assertTrustedRenderer(event)
    return requireJobs()
  }
  const sortModes = new Set<JobSortMode>(['manual', 'recent', 'alphabetical', 'status'])
  const statuses = new Set<JobStatus>(['discovered', 'scored', 'reviewed', 'shortlisted', 'apply_now', 'maybe', 'stretch', 'skipped', 'applied', 'interviewing', 'closed', 'archived'])

  ipcMain.handle('jobos:jobs:get-state', event => trusted(event).getState())
  ipcMain.handle('jobos:jobs:list', (event, sort: JobSortMode, query?: string, statusGroup?: string) => {
    if (!sortModes.has(sort)) throw new Error('Invalid job ordering')
    return trusted(event).list(sort, query, statusGroup)
  })
  ipcMain.handle('jobos:jobs:select', (event, jobId: string) => {
    if (typeof jobId !== 'string' || !jobId) throw new Error('Invalid job selection')
    return trusted(event).select(jobId)
  })
  ipcMain.handle('jobos:jobs:reorder', (event, jobIds: string[]) => {
    if (!Array.isArray(jobIds) || jobIds.some(jobId => typeof jobId !== 'string') || new Set(jobIds).size !== jobIds.length) {
      throw new Error('Invalid manual job order')
    }
    return trusted(event).reorder(jobIds)
  })
  ipcMain.handle('jobos:jobs:set-sort', (event, sort: JobSortMode) => {
    if (!sortModes.has(sort)) throw new Error('Invalid job ordering')
    return trusted(event).setSort(sort)
  })
  ipcMain.handle('jobos:jobs:update-status', (event, jobId: string, status: JobStatus) => {
    if (typeof jobId !== 'string' || !jobId || !statuses.has(status)) throw new Error('Invalid job status change')
    return trusted(event).updateStatus(jobId, status)
  })
}

function registerWorkspaceInterface(): void {
  const config = jobsConfig()
  const workspace = config ? createMainWorkspaceClient(config) : null
  const trusted = (event: IpcMainInvokeEvent) => {
    assertTrustedRenderer(event)
    if (!workspace) throw new Error('Device credential unavailable')
    return workspace
  }
  ipcMain.handle('jobos:workspace:get', event => trusted(event).get())
  ipcMain.handle('jobos:workspace:save', (event, snapshot: WorkspaceSnapshot) => {
    if (!snapshot || typeof snapshot !== 'object' || !Number.isInteger(snapshot.revision)) {
      throw new Error('Invalid workspace snapshot')
    }
    return trusted(event).save(snapshot)
  })
}

function registerBrowserInterface(): void {
  const trusted = (event: IpcMainInvokeEvent) => {
    assertTrustedRenderer(event)
    if (!browserManager) throw new Error('Browser surface unavailable')
    return browserManager
  }
  const tabId = (value: unknown) => {
    if (typeof value !== 'string' || !value || value.length > 128) throw new Error('Invalid browser tab')
    return value
  }
  ipcMain.handle('jobos:browser:get-state', event => trusted(event).getState())
  registerBrowserRestoreHandler(ipcMain, trusted)
  ipcMain.handle('jobos:browser:create', (event, url?: string, jobId?: string | null) => {
    if (url !== undefined && (typeof url !== 'string' || url.length > 8192)) throw new Error('Invalid browser address')
    if (jobId !== undefined && jobId !== null && typeof jobId !== 'string') throw new Error('Invalid job association')
    return trusted(event).create(url, jobId)
  })
  ipcMain.handle('jobos:browser:select', (event, id: string) => trusted(event).select(tabId(id)))
  ipcMain.handle('jobos:browser:close', (event, id: string) => trusted(event).close(tabId(id)))
  ipcMain.handle('jobos:browser:reorder', (event, ids: string[]) => {
    if (!Array.isArray(ids) || ids.some(id => typeof id !== 'string') || new Set(ids).size !== ids.length) throw new Error('Invalid tab order')
    return trusted(event).reorder(ids)
  })
  ipcMain.handle('jobos:browser:navigate', (event, id: string, input: string) => {
    if (typeof input !== 'string' || input.length > 8192) throw new Error('Invalid browser address')
    return trusted(event).navigate(tabId(id), input)
  })
  ipcMain.handle('jobos:browser:back', (event, id: string) => trusted(event).back(tabId(id)))
  ipcMain.handle('jobos:browser:forward', (event, id: string) => trusted(event).forward(tabId(id)))
  ipcMain.handle('jobos:browser:reload', (event, id: string) => trusted(event).reload(tabId(id)))
  ipcMain.handle('jobos:browser:stop', (event, id: string) => trusted(event).stop(tabId(id)))
  ipcMain.handle('jobos:browser:associate', (event, id: string, jobId: string | null) => {
    if (jobId !== null && (typeof jobId !== 'string' || jobId.length > 512)) throw new Error('Invalid job association')
    return trusted(event).associate(tabId(id), jobId)
  })
  ipcMain.handle('jobos:browser:copy-blocked-url', (event, id: string) => trusted(event).copyBlockedUrl(tabId(id)))
  ipcMain.handle('jobos:browser:set-bounds', (event, bounds: BrowserBounds) => {
    if (!bounds || ['x', 'y', 'width', 'height'].some(key => !Number.isFinite(bounds[key as keyof BrowserBounds]))) throw new Error('Invalid browser bounds')
    trusted(event).setBounds(bounds)
  })
}

function registerDocumentsInterface(): void {
  const config = jobsConfig()
  const documents = config
    ? createMainDocumentsClient(config, {
        dialog,
        shell,
        cacheRoot: path.join(app.getPath('temp'), 'jobos-artifacts')
      })
    : null
  const trusted = (event: IpcMainInvokeEvent) => {
    assertTrustedRenderer(event)
    if (!documents) throw new Error('Device credential unavailable')
    return documents
  }
  const artifactId = (value: unknown) => {
    if (typeof value !== 'string' || !/^art_[A-Za-z0-9_-]{16,80}$/.test(value)) {
      throw new Error('Invalid artifact')
    }
    return value
  }
  const jobId = (value: unknown) => {
    if (typeof value !== 'string' || !value || value.length > 512 || /[\\/]/.test(value)) {
      throw new Error('Invalid job')
    }
    return value
  }
  ipcMain.handle('jobos:documents:list', (event, id: string) => trusted(event).list(jobId(id)))
  ipcMain.handle('jobos:documents:refresh', (event, id: string) => trusted(event).refresh(jobId(id)))
  ipcMain.handle('jobos:documents:load-pdf', (event, id: string) => trusted(event).loadPdf(artifactId(id)))
  ipcMain.handle('jobos:documents:export', (event, id: string) => trusted(event).exportArtifact(artifactId(id)))
  ipcMain.handle('jobos:documents:reveal', (event, id: string) => trusted(event).reveal(artifactId(id)))
  ipcMain.handle('jobos:documents:open', (event, id: string) => trusted(event).open(artifactId(id)))
}

async function createWindow(): Promise<BrowserWindow> {
  const window = new BrowserWindow({
    width: 1440,
    height: 1024,
    useContentSize: true,
    minWidth: 980,
    minHeight: 640,
    backgroundColor: '#0f1114',
    show: false,
    title: 'JobOS',
    titleBarStyle: 'hiddenInset',
    webPreferences: {
      allowRunningInsecureContent: false,
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.resolve(currentDirectory, '../preload/preload.cjs'),
      sandbox: true,
      webSecurity: true
    }
  })

  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', event => event.preventDefault())

  const browserSession = session.fromPartition(BROWSER_PARTITION, { cache: true })
  browserManager = new BrowserManager({
    window,
    browserSession,
    createView: () => new WebContentsView({ webPreferences: remoteBrowserPreferences() }),
    dialog,
    clipboard,
    downloadsPath: app.getPath('downloads')
  })

  if (developmentUrl) {
    await window.loadURL(developmentUrl)
  } else {
    await window.loadFile(path.join(rendererRoot, 'index.html'))
  }

  window.show()

  const config = jobsConfig()
  const stopJobEvents = config
    ? startJobEventStream(
        {
          isDestroyed: () => window.isDestroyed(),
          send: (channel, event) => window.webContents.send(channel, event)
        },
        config
      )
    : () => undefined
  window.once('closed', () => {
    stopJobEvents()
    browserManager?.dispose()
    browserManager = null
  })

  const capturePath = process.env.JOBOS_CAPTURE_PATH
  if (capturePath) {
    const requestedDelay = Number(process.env.JOBOS_CAPTURE_DELAY_MS ?? 1_200)
    const captureDelay = Number.isFinite(requestedDelay)
      ? Math.max(500, Math.min(requestedDelay, 10_000))
      : 1_200
    setTimeout(async () => {
      const image = await window.webContents.capturePage()
      await writeFile(capturePath, image.toPNG())
      app.quit()
    }, captureDelay)
  }

  return window
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false)
  })
  registerConnectivityInterface()
  registerJobsInterface()
  registerWorkspaceInterface()
  registerBrowserInterface()
  registerDocumentsInterface()
  await createWindow()

  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) await createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
