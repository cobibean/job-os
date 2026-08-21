import { randomUUID } from 'node:crypto'
import { execFile, spawn } from 'node:child_process'
import { writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { app, BrowserWindow, clipboard, dialog, ipcMain, session, shell, WebContentsView } from 'electron'
import type { IpcMainEvent, IpcMainInvokeEvent } from 'electron'

import type { BrowserBounds, DocumentKey, JobSortMode, JobStatus, WorkspaceSnapshot } from '../shared/contracts.js'
import { AgentConversationRegistry, createScopedMainAgentClient, startAgentEventStream } from './agent.js'
import { registerAgentIpc } from './agentIpc.js'
import { createApiLifecycle } from './apiLifecycle.js'
import { BROWSER_PARTITION, BrowserManager, remoteBrowserViewOptions } from './browser.js'
import { canonicalListingUrl, safeApplicationUrl, validatedBrowserJobExtraction } from './browserJobExtraction.js'
import { createMainCareerProfileClient } from './careerProfile.js'
import { registerBrowserRestoreHandler } from './browserIpc.js'
import { startDesktopCapabilityClient } from './capabilityClient.js'
import { initializeDesktopRuntime } from './desktopRuntime.js'
import type { DesktopRuntimeState } from './desktopRuntime.js'
import { runtimeConfigPath } from './runtimeConfig.js'
import type { DesktopRuntimeConfig } from './runtimeConfig.js'
import { DocxWorkerManager } from './DocxWorkerManager.js'
import { registerDocxDocumentsIpc } from './docxDocumentsIpc.js'
import { DocxDocumentsService } from './docxDocuments.js'
import { DocxFileStore } from './docxFileStore.js'
import { LocalDocxBindingStore } from './localDocxBindingStore.js'
import { activateVisibleWindow } from './mainWindowLifecycle.js'
import { createMainJobsClient, startJobEventStream } from './jobs.js'
import type { JobsConfig } from './jobs.js'
import { safeExternalUrl } from '../shared/externalLinks.js'
import { createMainDocumentsClient } from './documents.js'
import { createMainEditableDocumentsClient } from './editableDocuments.js'
import { registerEditableDocumentsIpc } from './editableDocumentsIpc.js'
import { isTrustedRendererUrl } from './security.js'
import { createMainWorkspaceClient } from './workspace.js'
import { bindMediaFixture, loadMediaCaptureSpec, runMediaCapture } from './mediaCapture.js'

const currentDirectory = path.dirname(fileURLToPath(import.meta.url))
const rendererRoot = path.resolve(currentDirectory, '../renderer')
const sourceRoot = path.resolve(currentDirectory, '../../../..')
const developmentUrl = process.env.VITE_DEV_SERVER_URL
const developmentOrigin = developmentUrl ? new URL(developmentUrl).origin : undefined
let browserManager: BrowserManager | null = null
let mainWindow: BrowserWindow | null = null
let docxDocumentsService: DocxDocumentsService | null = null
const activeAgentConversationIds = new AgentConversationRegistry()
let docxWorkerManager: DocxWorkerManager | null = null
let mainDocumentsClient: ReturnType<typeof createMainDocumentsClient> | null = null
let appIsQuitting = false
let markBrowserRestored: () => void = () => undefined
let activeConfigPath: string | null = null
let sourceApiProcess: ReturnType<typeof spawn> | null = null
let mediaCaptureSpec: Awaited<ReturnType<typeof loadMediaCaptureSpec>> = null
const apiLifecycle = createApiLifecycle({ startSource: startSourceApi })
let desktopRuntimeState: DesktopRuntimeState = {
  runtime: null,
  deviceToken: null,
  connectivity: {
    state: 'disconnected',
    checkedAt: new Date().toISOString(),
    message: 'JobOS setup is required'
  }
}
let setupState: import('../shared/contracts.js').SetupSnapshot = {
  state: 'required',
  message: 'JobOS setup is required'
}

function assertTrustedRenderer(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url ?? ''
  if (!isTrustedRendererUrl(senderUrl, { developmentOrigin, rendererRoot })) {
    throw new Error('Untrusted renderer')
  }
}

function jobsConfig(): JobsConfig | null {
  const { runtime, deviceToken } = desktopRuntimeState
  if (!runtime || !deviceToken) return null
  return {
    baseUrl: runtime.apiBaseUrl,
    deviceToken
  }
}

function registerShellInterface(): void {
  ipcMain.handle('jobos:shell:open-external', async (event, rawUrl: unknown) => {
    assertTrustedRenderer(event)
    const url = safeExternalUrl(rawUrl)
    if (!url) throw new Error('Invalid external link')
    await shell.openExternal(url)
  })
}

function startSourceApi(runtime: DesktopRuntimeConfig): Promise<void> {
  const configPath = activeConfigPath
  if (app.isPackaged || !configPath) {
    return Promise.reject(new Error('Source API startup is unavailable'))
  }
  if (sourceApiProcess && sourceApiProcess.exitCode === null) return Promise.resolve()
  const address = new URL(runtime.apiBaseUrl)
  const uvExecutable = process.env.JOBOS_UV_EXECUTABLE ?? 'uv'
  const childEnvironment = { ...process.env }
  delete childEnvironment.JOBOS_DEVICE_TOKEN
  delete childEnvironment.JOBOS_MCP_TOKEN
  return new Promise((resolve, reject) => {
    const child = spawn(uvExecutable, [
      'run',
      'uvicorn',
      'jobos_api.main:app',
      '--host',
      address.hostname,
      '--port',
      address.port || '8766'
    ], {
      cwd: sourceRoot,
      env: {
        ...childEnvironment,
        JOBOS_CONFIG_PATH: configPath,
        JOBOS_TRANSPORT: runtime.mode === 'remote-client' ? 'private-remote' : 'local-loopback'
      },
      stdio: 'ignore'
    })
    child.once('spawn', () => {
      sourceApiProcess = child
      resolve()
    })
    child.once('error', error => {
      if (sourceApiProcess === child) sourceApiProcess = null
      reject(error)
    })
    child.once('exit', () => {
      if (sourceApiProcess === child) sourceApiProcess = null
    })
  })
}

function runInitializer(arguments_: string[]): Promise<void> {
  const override = process.env.JOBOS_INIT_EXECUTABLE
  const executable = override ?? 'uv'
  const commandArguments = override ? arguments_ : ['run', 'jobos-init', ...arguments_]
  if (app.isPackaged && !override) {
    return Promise.reject(new Error('Packaged setup executable is unavailable'))
  }
  return new Promise((resolve, reject) => {
    execFile(
      executable,
      commandArguments,
      { cwd: sourceRoot, encoding: 'utf8', maxBuffer: 8192, timeout: 30_000 },
      error => error ? reject(new Error('Local setup command failed')) : resolve()
    )
  })
}

function registerSetupInterface(configPath: string): void {
  setupState = desktopRuntimeState.runtime && desktopRuntimeState.deviceToken
    ? { state: 'ready', message: 'JobOS is configured' }
    : { state: 'required', message: 'JobOS setup or credential repair is required' }
  ipcMain.handle('jobos:setup:get', event => {
    assertTrustedRenderer(event)
    return setupState
  })
  ipcMain.handle('jobos:setup:initialize', async (event, resetDemo: unknown, confirmed: unknown) => {
    assertTrustedRenderer(event)
    if (resetDemo && confirmed !== true) {
      return { state: 'error', message: 'Demo reset requires confirmation' }
    }
    setupState = { state: 'working', message: resetDemo ? 'Resetting demo…' : 'Creating local profile…' }
    try {
      await runInitializer([
        '--data-dir', path.dirname(configPath),
        '--config-path', configPath,
        ...(resetDemo ? ['--reset-demo', '--confirm-reset-demo'] : [])
      ])
      setupState = { state: 'succeeded', message: 'Setup complete. Restart JobOS to continue.' }
    } catch {
      setupState = { state: 'error', message: 'Setup could not finish. Check that the local JobOS tools are installed, then retry.' }
    }
    return setupState
  })
  ipcMain.handle('jobos:setup:restart', event => {
    assertTrustedRenderer(event)
    if (setupState.state !== 'succeeded' && setupState.state !== 'ready') {
      throw new Error('JobOS restart is unavailable until setup succeeds')
    }
    app.relaunch()
    app.quit()
  })
}

function registerDiagnosticsInterface(configPath: string): void {
  ipcMain.handle('jobos:diagnostics:get', event => {
    assertTrustedRenderer(event)
    const runtime = desktopRuntimeState.runtime
    return {
      mode: runtime?.mode ?? 'not-configured',
      appVersion: app.getVersion(),
      ...(desktopRuntimeState.connectivity.apiVersion
        ? { apiVersion: desktopRuntimeState.connectivity.apiVersion }
        : {}),
      capabilities: {
        localService: !runtime || runtime.mode !== 'local-service' ? 'not-configured'
          : desktopRuntimeState.connectivity.state === 'connected' ? 'available' : 'unavailable',
        agent: !runtime || desktopRuntimeState.connectivity.agent === 'not-configured' ? 'not-configured'
          : desktopRuntimeState.connectivity.agent === 'offline' ? 'offline'
            : desktopRuntimeState.connectivity.agent === 'connecting' ? 'connecting' : 'available',
        desktop: desktopRuntimeState.connectivity.desktop === 'connected' ? 'available' : 'disconnected',
        renderer: docxWorkerManager?.isAvailable() ? 'available' : 'unavailable',
        artifactStorage: desktopRuntimeState.connectivity.artifactStorage ?? 'unavailable',
        artifactGateway: desktopRuntimeState.connectivity.artifactGateway ?? 'not-configured',
        transport: desktopRuntimeState.connectivity.transport ?? 'not-configured'
      }
    }
  })
  ipcMain.handle('jobos:diagnostics:open-data', async event => {
    assertTrustedRenderer(event)
    await shell.openPath(path.dirname(configPath))
  })
  ipcMain.handle('jobos:diagnostics:open-logs', async event => {
    assertTrustedRenderer(event)
    const configuredLogs = desktopRuntimeState.runtime?.paths?.logs ?? 'logs'
    const logsPath = path.isAbsolute(configuredLogs)
      ? configuredLogs
      : path.resolve(path.dirname(configPath), configuredLogs)
    await shell.openPath(logsPath)
  })
}

function registerConnectivityInterface(): void {
  ipcMain.handle('jobos:connectivity:get', async event => {
    assertTrustedRenderer(event)

    const { runtime, deviceToken } = desktopRuntimeState
    if (!runtime || !deviceToken) return desktopRuntimeState.connectivity

    const snapshot = await apiLifecycle.ensureApiReady(runtime, deviceToken)
    desktopRuntimeState = { runtime, deviceToken, connectivity: snapshot }
    if (process.env.JOBOS_CAPTURE_PATH) {
      console.info('[JobOS capture] connectivity', JSON.stringify(snapshot))
    }
    return snapshot
  })
}

function registerCareerProfileInterface(): void {
  const config = jobsConfig()
  const careerProfile = config ? createMainCareerProfileClient(config) : null
  const trusted = (event: IpcMainInvokeEvent) => {
    assertTrustedRenderer(event)
    if (!careerProfile) throw new Error('Device credential unavailable')
    return careerProfile
  }

  ipcMain.handle('jobos:career-profile:availability', event => trusted(event).availability())
  ipcMain.handle('jobos:career-profile:cache:validate', (event, candidate) => (
    trusted(event).validateCachedWorkArrangement(candidate)
  ))
  ipcMain.handle('jobos:career-profile:work-arrangement:get', event => trusted(event).getWorkArrangement())
  ipcMain.handle('jobos:career-profile:work-arrangement:save', (event, request) => (
    trusted(event).saveWorkArrangement(request)
  ))
  ipcMain.handle('jobos:career-profile:work-arrangement:history', event => (
    trusted(event).getWorkArrangementHistory()
  ))
  ipcMain.handle('jobos:career-profile:work-arrangement:restore', (event, request) => (
    trusted(event).restoreWorkArrangement(request)
  ))
}

function registerAgentInterface(): void {
  const config = jobsConfig()
  const agent = config ? createScopedMainAgentClient(config, activeAgentConversationIds) : null
  registerAgentIpc(ipcMain, event => {
    assertTrustedRenderer(event)
    if (!agent) throw new Error('Device credential unavailable')
    return agent
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
  ipcMain.handle('jobos:jobs:inspect', (event, jobId: string) => {
    if (typeof jobId !== 'string' || !jobId || jobId.length > 512) throw new Error('Invalid job')
    return trusted(event).inspect(jobId)
  })
  ipcMain.handle('jobos:jobs:select', (event, conversationId: string, jobId: string) => {
    if (typeof conversationId !== 'string' || !/^conv_[A-Za-z0-9_-]{1,128}$/.test(conversationId)) throw new Error('Invalid agent conversation')
    if (typeof jobId !== 'string' || !jobId || jobId.length > 512) throw new Error('Invalid job selection')
    return trusted(event).select(conversationId, jobId)
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
  ipcMain.handle('jobos:jobs:remove-demo', (event, jobId: string) => {
    if (typeof jobId !== 'string' || !jobId) throw new Error('Invalid demo job')
    return trusted(event).removeDemo(jobId)
  })
  ipcMain.handle('jobos:jobs:save-from-browser', async (
    event,
    rawTabId: unknown,
    expectedUrl: unknown,
    rawExtraction: unknown,
    idempotencyKey: unknown
  ) => {
    const client = trusted(event)
    if (!browserManager) throw new Error('Browser surface unavailable')
    if (typeof rawTabId !== 'string' || !rawTabId || rawTabId.length > 128) throw new Error('Invalid browser tab')
    if (typeof expectedUrl !== 'string' || !expectedUrl || expectedUrl.length > 8192) throw new Error('Invalid browser address')
    if (typeof idempotencyKey !== 'string' || !idempotencyKey || idempotencyKey.length > 128) throw new Error('Invalid idempotency key')
    const extraction = validatedBrowserJobExtraction(rawExtraction)
    const before = browserManager.getState()
    const sourceTab = before.tabs.find(tab => tab.tabId === rawTabId)
    const sourceContext = browserManager.contextToken(rawTabId)
    if (before.activeTabId !== rawTabId || sourceTab?.url !== expectedUrl
      || sourceContext.url !== expectedUrl || sourceContext.loading) {
      throw new Error('The browser listing changed before saving finished. Retry on the intended listing.')
    }
    if (sourceTab.associatedJobId) throw new Error('This browser listing is already associated with a job')
    const canonicalUrl = canonicalListingUrl(expectedUrl, extraction.canonicalUrl)
    const result = await client.createFromBrowser({
      ...extraction,
      canonicalUrl,
      applicationUrl: safeApplicationUrl(extraction.applicationUrl)
    }, idempotencyKey)
    const after = browserManager.getState()
    const currentTab = after.tabs.find(tab => tab.tabId === rawTabId)
    const currentContext = browserManager.contextToken(rawTabId)
    if (after.activeTabId !== rawTabId || currentTab?.url !== expectedUrl
      || currentContext.url !== expectedUrl || currentContext.loading
      || currentContext.documentEpoch !== sourceContext.documentEpoch
      || currentTab.associatedJobId !== sourceTab.associatedJobId) {
      return result
    }
    browserManager.associate(rawTabId, result.job.jobId)
    return { ...result, associated: true }
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
  ipcMain.handle('jobos:workspace:save-document-view', (event, conversationId: string, artifactId: string | null, page: number, zoom: number) => {
    if (typeof conversationId !== 'string' || !/^conv_[A-Za-z0-9_-]{1,128}$/.test(conversationId)) throw new Error('Invalid agent conversation')
    if (artifactId !== null && (typeof artifactId !== 'string' || !/^art_[A-Za-z0-9_-]{16,80}$/.test(artifactId))) throw new Error('Invalid artifact')
    if (!Number.isInteger(page) || page < 1 || !Number.isFinite(zoom) || zoom < 0.5 || zoom > 3) throw new Error('Invalid document view')
    return trusted(event).saveDocumentView(conversationId, artifactId, page, zoom)
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
  registerBrowserRestoreHandler(ipcMain, trusted, () => markBrowserRestored())
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
  mainDocumentsClient = documents
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
  ipcMain.handle('jobos:documents:approve', (event, owner: string, id: string) => (
    trusted(event).approve(jobId(owner), artifactId(id))
  ))
  ipcMain.handle('jobos:documents:load-pdf', (event, id: string) => trusted(event).loadPdf(artifactId(id)))
  ipcMain.handle('jobos:documents:load-original-docx', (event, id: string) => (
    trusted(event).loadOriginalDocx(artifactId(id))
  ))
  ipcMain.handle('jobos:documents:export', (event, id: string) => trusted(event).exportArtifact(artifactId(id)))
  ipcMain.handle('jobos:documents:reveal', (event, id: string) => trusted(event).reveal(artifactId(id)))
  ipcMain.handle('jobos:documents:open', (event, id: string) => trusted(event).open(artifactId(id)))
}

async function registerDocxDocumentsInterface(): Promise<void> {
  const userData = app.getPath('userData')
  const recoveryRoot = path.join(userData, 'docx-recovery')
  const artifactRoot = path.join(userData, 'editable-docx-artifacts')
  docxWorkerManager = new DocxWorkerManager(ipcMain)
  docxDocumentsService = new DocxDocumentsService({
    dialog,
    bindings: new LocalDocxBindingStore(path.join(userData, 'docx-bindings.json')),
    files: new DocxFileStore({
      recoveryRoot,
      denyRoots: [recoveryRoot, path.join(app.getPath('temp'), 'jobos-artifacts')]
    }),
    artifactRoot,
    emit: value => {
      for (const window of BrowserWindow.getAllWindows()) {
        if (!window.isDestroyed()) window.webContents.send('jobos:docx:external-change', value)
      }
    },
    worker: docxWorkerManager
  })
  await docxDocumentsService.initialize()
  registerDocxDocumentsIpc(ipcMain, event => {
    assertTrustedRenderer(event)
    if (!docxDocumentsService) throw new Error('DOCX editor unavailable')
    return docxDocumentsService
  })
  ipcMain.handle(
    'jobos:docx:open-artifact',
    async (event, owner: string, key: DocumentKey, id: string) => {
      assertTrustedRenderer(event)
      if (!docxDocumentsService || !mainDocumentsClient) throw new Error('DOCX artifact editor unavailable')
      if (typeof id !== 'string' || !/^art_[A-Za-z0-9_-]{16,80}$/.test(id)) throw new Error('Invalid artifact')
      const artifact = await mainDocumentsClient.loadOriginalDocx(id)
      return docxDocumentsService.openArtifact(owner, key, artifact)
    }
  )
}

function registerEditableDocumentsInterface(): void {
  const config = jobsConfig()
  const editableDocuments = config
    ? createMainEditableDocumentsClient(config, { dialog })
    : null
  registerEditableDocumentsIpc(ipcMain, event => {
    assertTrustedRenderer(event)
    if (!editableDocuments) throw new Error('Device credential unavailable')
    return editableDocuments
  })
}

async function createWindow(): Promise<BrowserWindow> {
  const window = new BrowserWindow({
    width: 1440,
    height: 1024,
    useContentSize: true,
    enableLargerThanScreen: Boolean(mediaCaptureSpec),
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

  let allowClose = false
  let pendingClose: { requestId: string; timer: NodeJS.Timeout } | null = null
  const onPrepareCloseResult = (
    event: IpcMainEvent,
    requestId: unknown,
    safe: unknown
  ) => {
    if (
      event.sender !== window.webContents
      || typeof requestId !== 'string'
      || requestId !== pendingClose?.requestId
    ) return
    clearTimeout(pendingClose.timer)
    pendingClose = null
    if (safe !== true) {
      appIsQuitting = false
      return
    }
    allowClose = true
    if (appIsQuitting) app.quit()
    else window.close()
  }
  ipcMain.on('jobos:window:prepare-close-result', onPrepareCloseResult)
  window.on('close', event => {
    if (allowClose || window.webContents.isLoadingMainFrame()) return
    event.preventDefault()
    if (pendingClose) return
    const requestId = `close_${randomUUID()}`
    const timer = setTimeout(() => {
      if (pendingClose?.requestId === requestId) pendingClose = null
      appIsQuitting = false
    }, 15_000)
    pendingClose = { requestId, timer }
    window.webContents.send('jobos:window:prepare-close', requestId)
  })

  const browserSession = session.fromPartition(BROWSER_PARTITION, { cache: true })
  browserManager = new BrowserManager({
    window,
    browserSession,
    createView: options => new WebContentsView(remoteBrowserViewOptions(options)),
    dialog,
    clipboard,
    downloadsPath: app.getPath('downloads')
  })
  const browserReady = new Promise<void>(resolve => { markBrowserRestored = resolve })
  const capabilityConfig = jobsConfig()
  const stopCapabilities = capabilityConfig
    ? startDesktopCapabilityClient(browserManager, {
        ...capabilityConfig,
        deviceId: desktopRuntimeState.runtime?.deviceId ?? 'primary-device'
      }, { browserReady }, docxDocumentsService ?? undefined)
    : () => undefined

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
  let stopAgentEvents: () => void = () => undefined
  if (config) {
    const streamClient = createScopedMainAgentClient(config, activeAgentConversationIds)
    stopAgentEvents = startAgentEventStream(
      {
        isDestroyed: () => window.isDestroyed(),
        send: (channel, update) => window.webContents.send(channel, update)
      },
      config,
      { connectedState: 'connecting', knownConversationIds: activeAgentConversationIds }
    )
    const hydrateRegistry = async () => {
      let delay = 500
      while (!window.isDestroyed()) {
        try {
          await streamClient.list()
          return
        } catch {
          await new Promise(resolve => setTimeout(resolve, delay))
          delay = Math.min(delay * 2, 8_000)
        }
      }
    }
    void hydrateRegistry()
  }
  window.once('closed', () => {
    if (pendingClose) clearTimeout(pendingClose.timer)
    ipcMain.removeListener('jobos:window:prepare-close-result', onPrepareCloseResult)
    stopJobEvents()
    stopAgentEvents()
    stopCapabilities()
    browserManager?.dispose()
    browserManager = null
  })

  if (mediaCaptureSpec) {
    void runMediaCapture(window, mediaCaptureSpec).then(() => app.quit()).catch(() => {
      console.error('[JobOS media capture] Capture failed')
      app.exit(1)
    })
  } else {
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
  }

  mainWindow = window
  window.once('closed', () => {
    if (mainWindow === window) mainWindow = null
  })

  return window
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false)
  })
  const configPath = process.env.JOBOS_CONFIG_PATH ?? runtimeConfigPath(app.getPath('appData'))
  mediaCaptureSpec = await loadMediaCaptureSpec(process.env.JOBOS_MEDIA_CAPTURE_SPEC)
  activeConfigPath = configPath
  desktopRuntimeState = await initializeDesktopRuntime({
    configPath,
    environment: process.env,
    ensureApiReady: apiLifecycle.ensureApiReady
  })
  registerSetupInterface(configPath)
  registerDiagnosticsInterface(configPath)
  registerShellInterface()
  registerConnectivityInterface()
  registerCareerProfileInterface()
  registerAgentInterface()
  registerJobsInterface()
  registerWorkspaceInterface()
  registerBrowserInterface()
  registerDocumentsInterface()
  await registerDocxDocumentsInterface()
  if (mediaCaptureSpec && docxDocumentsService) await bindMediaFixture(docxDocumentsService, sourceRoot)
  registerEditableDocumentsInterface()
  await createWindow()

  app.on('activate', async () => {
    mainWindow = await activateVisibleWindow(mainWindow, createWindow)
  })
}).catch(error => {
  console.error('[JobOS startup] Failed before the main window opened', error)
  app.exit(1)
})

app.on('before-quit', () => {
  appIsQuitting = true
})

app.on('will-quit', () => {
  sourceApiProcess?.kill()
  sourceApiProcess = null
  mainDocumentsClient = null
  docxDocumentsService?.dispose()
  docxDocumentsService = null
  docxWorkerManager?.dispose()
  docxWorkerManager = null
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
