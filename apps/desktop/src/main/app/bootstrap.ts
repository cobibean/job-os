import { writeFile } from 'node:fs/promises'
import path from 'node:path'

import { app, BrowserWindow, clipboard, dialog, ipcMain, session, shell, WebContentsView } from 'electron'
import type { IpcMainEvent, IpcMainInvokeEvent } from 'electron'

import { AgentConversationRegistry, createScopedMainAgentClient, startAgentEventStream } from '../agents/agent.js'
import { registerAgentIpc } from '../agents/agentIpc.js'
import { createMainConnectedAgentsClient } from '../agents/connectedAgents.js'
import { registerConnectedAgentsIpc } from '../agents/connectedAgentsIpc.js'
import { BrowserManager, remoteBrowserViewOptions } from '../browser/browser.js'
import { registerBrowserIpc } from '../browser/browserIpc.js'
import { createMainCareerProfileClient } from '../career-profile/careerProfile.js'
import { careerProfileAcceptanceDialogPaths } from '../career-profile/careerProfileAcceptanceDialogs.js'
import { registerCareerProfileIpc } from '../career-profile/careerProfileIpc.js'
import { createMainDocumentsClient } from '../documents/artifacts/documents.js'
import { registerDocumentsIpc } from '../documents/artifacts/documentsIpc.js'
import { DocxWorkerManager } from '../documents/docx/DocxWorkerManager.js'
import { DocxDocumentsService } from '../documents/docx/docxDocuments.js'
import { registerDocxDocumentsIpc } from '../documents/docx/docxDocumentsIpc.js'
import { DocxFileStore } from '../documents/docx/docxFileStore.js'
import { LocalDocxBindingStore } from '../documents/docx/localDocxBindingStore.js'
import { createMainEditableDocumentsClient } from '../documents/editable/editableDocuments.js'
import { registerEditableDocumentsIpc } from '../documents/editable/editableDocumentsIpc.js'
import { createInstallationProfilesClient, resolveProfileStorageIdentity } from '../installation-profiles/installationProfiles.js'
import { registerInstallationProfilesIpc } from '../installation-profiles/installationProfilesIpc.js'
import { createProfileSwitchCoordinator } from '../installation-profiles/profileSwitchCoordinator.js'
import { browserPartition, prepareProfileClientPaths, rendererPartition, type DesktopProfileStorageIdentity } from '../installation-profiles/profileStorage.js'
import { createMainJobsClient, startJobEventStream } from '../jobs/jobs.js'
import { registerJobsIpc } from '../jobs/jobsIpc.js'
import { createMainWorkspaceClient } from '../workspace/workspace.js'
import { registerWorkspaceIpc } from '../workspace/workspaceIpc.js'
import { bindMediaFixture, loadMediaCaptureSpec, runMediaCapture } from './automation/mediaCapture.js'
import { startDesktopCapabilityClient } from './capabilities/capabilityClient.js'
import { registerConnectivityIpc } from './ipc/connectivityIpc.js'
import { registerDiagnosticsIpc } from './ipc/diagnosticsIpc.js'
import { registerSetupIpc } from './ipc/setupIpc.js'
import { registerShellIpc } from './ipc/shellIpc.js'
import { createApiLifecycle } from './runtime/apiLifecycle.js'
import type { DesktopApiConfig } from './runtime/desktopApiConfig.js'
import { resolveDesktopPaths } from './runtime/desktopPaths.js'
import { initializeDesktopRuntime, type DesktopRuntimeState } from './runtime/desktopRuntime.js'
import { runtimeConfigPath } from './runtime/runtimeConfig.js'
import { createSourceApiProcess } from './runtime/sourceApiProcess.js'
import { applyDenyAllPermissionPolicy, assertTrustedRendererEvent } from './security/security.js'
import { createMainWindow } from './window/mainWindow.js'
import { activateVisibleWindow } from './window/mainWindowLifecycle.js'

export function createDesktopApplication() {
  const paths = resolveDesktopPaths()
  const developmentUrl = process.env.VITE_DEV_SERVER_URL
  const developmentOrigin = developmentUrl ? new URL(developmentUrl).origin : undefined
  let browserManager: BrowserManager | null = null
  let mainWindow: BrowserWindow | null = null
  let docxDocumentsService: DocxDocumentsService | null = null
  let docxWorkerManager: DocxWorkerManager | null = null
  let mainDocumentsClient: ReturnType<typeof createMainDocumentsClient> | null = null
  let appIsQuitting = false
  let markBrowserRestored: () => void = () => undefined
  let activeConfigPath: string | null = null
  let mediaCaptureSpec: Awaited<ReturnType<typeof loadMediaCaptureSpec>> = null
  let activeProfileStorageIdentity: DesktopProfileStorageIdentity = { kind: 'recovery' }
  let requestMainWindowSafety: (reason: 'window-close' | 'profile-switch') => Promise<boolean> = async () => false
  const activeAgentConversationIds = new AgentConversationRegistry()
  let desktopRuntimeState: DesktopRuntimeState = {
    runtime: null,
    deviceToken: null,
    connectivity: { state: 'disconnected', checkedAt: new Date().toISOString(), message: 'JobOS setup is required' }
  }
  const sourceApi = createSourceApiProcess({
    sourceRoot: paths.sourceRoot,
    isPackaged: () => app.isPackaged,
    getConfigPath: () => activeConfigPath,
    environment: process.env
  })
  const apiLifecycle = createApiLifecycle({ startSource: sourceApi.start })

  const desktopApiConfig = (): DesktopApiConfig | null => {
    const { runtime, deviceToken } = desktopRuntimeState
    if (!runtime || !deviceToken) return null
    return { baseUrl: runtime.apiBaseUrl, deviceToken, installationProfileId: desktopRuntimeState.connectivity.installationProfileId }
  }
  const profileClient = () => {
    const config = desktopApiConfig()
    if (!config) throw new Error('JobOS device credential unavailable')
    return createInstallationProfilesClient(config)
  }
  const assertTrustedRenderer = (event: IpcMainInvokeEvent | IpcMainEvent) => {
    assertTrustedRendererEvent(event, { developmentOrigin, rendererRoot: paths.rendererRoot })
  }
  const requireClient = <T>(client: T | null): T => {
    if (!client) throw new Error('Device credential unavailable')
    return client
  }
  const profileSwitch = createProfileSwitchCoordinator({
    getBrowserManager: () => browserManager,
    requestWorkspaceSafety: () => requestMainWindowSafety('profile-switch'),
    getClient: profileClient,
    getRuntimeState: () => desktopRuntimeState,
    apiLifecycle,
    sourceApi,
    relaunchAndQuit: () => { app.relaunch(); app.quit() }
  })

  const registerApplicationIpc = async (configPath: string): Promise<void> => {
    registerSetupIpc(ipcMain, assertTrustedRenderer, {
      configPath,
      configured: activeProfileStorageIdentity.kind !== 'recovery',
      runtimeCredentialAvailable: Boolean(desktopRuntimeState.runtime && desktopRuntimeState.deviceToken),
      runInitializer: sourceApi.runInitializer,
      restart: () => { app.relaunch(); app.quit() }
    })
    registerDiagnosticsIpc(ipcMain, assertTrustedRenderer, {
      configPath,
      getRuntimeState: () => desktopRuntimeState,
      getAppVersion: () => app.getVersion(),
      isProfileSwitching: profileSwitch.isInProgress,
      isRendererAvailable: () => Boolean(docxWorkerManager?.isAvailable()),
      openPath: value => shell.openPath(value)
    })
    registerShellIpc(ipcMain, assertTrustedRenderer, value => shell.openExternal(value))
    registerConnectivityIpc(ipcMain, assertTrustedRenderer, {
      getState: () => desktopRuntimeState,
      setState: state => { desktopRuntimeState = state },
      ensureApiReady: apiLifecycle.ensureApiReady,
      captureEnabled: Boolean(process.env.JOBOS_CAPTURE_PATH)
    })
    registerInstallationProfilesIpc(ipcMain, assertTrustedRenderer, {
      getExpectedProfileId: () => activeProfileStorageIdentity.kind === 'recovery' ? null : activeProfileStorageIdentity.profileId,
      getClient: profileClient,
      completeSwitch: profileSwitch.complete,
      restart: () => { app.relaunch(); app.quit() }
    })

    const config = desktopApiConfig()
    const acceptanceDialogs = careerProfileAcceptanceDialogPaths()
    const careerProfile = config ? createMainCareerProfileClient(config, {
      chooseArchivePath: async () => {
        if (acceptanceDialogs) return acceptanceDialogs.chooseArchivePath()
        const selection = await dialog.showOpenDialog({
          title: 'Restore a Career Profile baseline', properties: ['openFile'], filters: [{ name: 'JobOS Career Profile archive', extensions: ['zip'] }]
        })
        return selection.canceled ? null : selection.filePaths[0] ?? null
      },
      chooseExportPath: async filename => {
        if (acceptanceDialogs) return acceptanceDialogs.chooseExportPath()
        const selection = await dialog.showSaveDialog({
          title: 'Export Career Profile', defaultPath: filename, filters: [{ name: 'JobOS Career Profile archive', extensions: ['zip'] }]
        })
        return selection.canceled ? null : selection.filePath ?? null
      }
    }) : null
    registerCareerProfileIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(careerProfile) })

    const agent = config ? createScopedMainAgentClient(config, activeAgentConversationIds) : null
    const connectedAgents = config ? createMainConnectedAgentsClient(config) : null
    registerAgentIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(agent) })
    registerConnectedAgentsIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(connectedAgents) })

    const jobs = config ? createMainJobsClient(config) : null
    registerJobsIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(jobs) }, () => browserManager)
    const workspace = config ? createMainWorkspaceClient(config) : null
    registerWorkspaceIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(workspace) })
    registerBrowserIpc(ipcMain, assertTrustedRenderer, () => browserManager, () => markBrowserRestored())

    mainDocumentsClient = config ? createMainDocumentsClient(config, {
      dialog, shell, cacheRoot: path.join(app.getPath('temp'), 'jobos-artifacts')
    }) : null
    registerDocumentsIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(mainDocumentsClient) })

    if (activeProfileStorageIdentity.kind !== 'recovery') {
      const clientPaths = prepareProfileClientPaths(app.getPath('userData'), activeProfileStorageIdentity)
      docxWorkerManager = new DocxWorkerManager(ipcMain)
      docxDocumentsService = new DocxDocumentsService({
        dialog,
        bindings: new LocalDocxBindingStore(clientPaths.bindingsPath),
        files: new DocxFileStore({
          recoveryRoot: clientPaths.recoveryRoot,
          denyRoots: [clientPaths.recoveryRoot, path.join(app.getPath('temp'), 'jobos-artifacts')]
        }),
        artifactRoot: clientPaths.artifactRoot,
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
      }, {
        assertAvailable: () => { if (!mainDocumentsClient) throw new Error('DOCX artifact editor unavailable') },
        loadOriginalDocx: id => mainDocumentsClient!.loadOriginalDocx(id)
      })
    }
    if (mediaCaptureSpec && docxDocumentsService) await bindMediaFixture(docxDocumentsService, paths.sourceRoot)
    const editableDocuments = config ? createMainEditableDocumentsClient(config, { dialog }) : null
    registerEditableDocumentsIpc(ipcMain, event => { assertTrustedRenderer(event); return requireClient(editableDocuments) })
  }

  const attachWindowFeatures = (window: BrowserWindow) => {
    const activeBrowserPartition = browserPartition(activeProfileStorageIdentity)
    const browserSession = session.fromPartition(activeBrowserPartition, { cache: true })
    applyDenyAllPermissionPolicy(browserSession)
    const manager = new BrowserManager({
      window,
      browserSession,
      createView: options => new WebContentsView(remoteBrowserViewOptions(options, activeBrowserPartition)),
      dialog,
      clipboard,
      downloadsPath: app.getPath('downloads')
    })
    browserManager = manager
    const browserReady = new Promise<void>(resolve => { markBrowserRestored = resolve })
    const capabilityConfig = desktopApiConfig()
    const stopCapabilities = capabilityConfig ? startDesktopCapabilityClient(manager, {
      ...capabilityConfig, deviceId: desktopRuntimeState.runtime?.deviceId ?? 'primary-device'
    }, { browserReady }, docxDocumentsService ?? undefined) : () => undefined
    let stopJobEvents: () => void = () => undefined
    let stopAgentEvents: () => void = () => undefined
    const startWindowStreams = () => {
      const config = desktopApiConfig()
      if (config) {
        stopJobEvents = startJobEventStream({
          isDestroyed: () => window.isDestroyed(),
          send: (channel, event) => window.webContents.send(channel, event)
        }, config)
        const streamClient = createScopedMainAgentClient(config, activeAgentConversationIds)
        stopAgentEvents = startAgentEventStream({
          isDestroyed: () => window.isDestroyed(),
          send: (channel, update) => window.webContents.send(channel, update)
        }, config, { connectedState: 'connecting', knownConversationIds: activeAgentConversationIds })
        const hydrateRegistry = async () => {
          let delay = 500
          while (!window.isDestroyed()) {
            try { await streamClient.list(); return } catch {
              await new Promise(resolve => setTimeout(resolve, delay))
              delay = Math.min(delay * 2, 8_000)
            }
          }
        }
        void hydrateRegistry()
      }
    }
    return {
      afterShow: startWindowStreams,
      cleanup: () => {
        stopJobEvents()
        stopAgentEvents()
        stopCapabilities()
        manager.dispose()
        if (browserManager === manager) browserManager = null
      }
    }
  }

  const afterShow = (window: BrowserWindow) => {
    if (mediaCaptureSpec) {
      void runMediaCapture(window, mediaCaptureSpec).then(() => app.quit()).catch(() => {
        console.error('[JobOS media capture] Capture failed')
        app.exit(1)
      })
      return
    }
    const capturePath = process.env.JOBOS_CAPTURE_PATH
    if (!capturePath) return
    const requestedDelay = Number(process.env.JOBOS_CAPTURE_DELAY_MS ?? 1_200)
    const captureDelay = Number.isFinite(requestedDelay) ? Math.max(500, Math.min(requestedDelay, 10_000)) : 1_200
    setTimeout(async () => {
      const image = await window.webContents.capturePage()
      await writeFile(capturePath, image.toPNG())
      app.quit()
    }, captureDelay)
  }

  const createWindow = async (): Promise<BrowserWindow> => {
    const window = await createMainWindow({
      rendererPartition: rendererPartition(activeProfileStorageIdentity),
      preloadPath: paths.preloadPath,
      rendererRoot: paths.rendererRoot,
      developmentUrl,
      enableLargerThanScreen: Boolean(mediaCaptureSpec),
      isAppQuitting: () => appIsQuitting,
      cancelAppQuit: () => { appIsQuitting = false },
      quitApp: () => app.quit(),
      setSafetyRequester: request => { requestMainWindowSafety = request },
      attachWindowFeatures,
      afterShow
    })
    mainWindow = window
    window.once('closed', () => { if (mainWindow === window) mainWindow = null })
    return window
  }

  return {
    async start(): Promise<void> {
      applyDenyAllPermissionPolicy(session.defaultSession)
      const configPath = process.env.JOBOS_CONFIG_PATH ?? runtimeConfigPath(app.getPath('appData'))
      mediaCaptureSpec = await loadMediaCaptureSpec(process.env.JOBOS_MEDIA_CAPTURE_SPEC)
      activeConfigPath = configPath
      desktopRuntimeState = await initializeDesktopRuntime({ configPath, environment: process.env, ensureApiReady: apiLifecycle.ensureApiReady })
      const activeProfileId = desktopRuntimeState.connectivity.installationProfileId
      if (desktopRuntimeState.runtime && desktopRuntimeState.deviceToken && activeProfileId) {
        const profiles = await createInstallationProfilesClient({
          baseUrl: desktopRuntimeState.runtime.apiBaseUrl,
          deviceToken: desktopRuntimeState.deviceToken,
          installationProfileId: activeProfileId
        }).list()
        activeProfileStorageIdentity = resolveProfileStorageIdentity(profiles, activeProfileId)
      } else activeProfileStorageIdentity = { kind: 'recovery' }
      await registerApplicationIpc(configPath)
      await createWindow()
    },
    async activate(): Promise<void> {
      mainWindow = await activateVisibleWindow(mainWindow, createWindow)
    },
    beforeQuit(): void { appIsQuitting = true },
    willQuit(): void {
      sourceApi.dispose()
      mainDocumentsClient = null
      docxDocumentsService?.dispose()
      docxDocumentsService = null
      docxWorkerManager?.dispose()
      docxWorkerManager = null
    }
  }
}
