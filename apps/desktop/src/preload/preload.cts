import { contextBridge, ipcRenderer } from 'electron'
import type { IpcRendererEvent } from 'electron'

import type { DocxExternalChangeEvent, SaveDocxRequest } from '../shared/docxDocuments.js'

import type { AgentStreamUpdate, BrowserBounds, BrowserJobExtraction, BrowserRestoreState, BrowserState, JobEvent, JobOsRendererBridge, JobSortMode, JobStatus, WorkspaceSnapshot } from '../shared/contracts.js'
import type {
  ApplyEditableDocumentOperationsRequest,
  CreateEditableDocumentSnapshotRequest,
  DocumentKey,
  RestoreEditableDocumentSnapshotRequest,
  SaveEditableDocumentRequest
} from '../shared/editableDocuments.js'

const bridge: JobOsRendererBridge = Object.freeze({
  lifecycle: Object.freeze({
    subscribePrepareClose: (handler: () => Promise<boolean>) => {
      const wrapped = (_event: IpcRendererEvent, requestId: string) => {
        void handler()
          .then(safe => ipcRenderer.send('jobos:window:prepare-close-result', requestId, safe))
          .catch(() => ipcRenderer.send('jobos:window:prepare-close-result', requestId, false))
      }
      ipcRenderer.on('jobos:window:prepare-close', wrapped)
      return () => ipcRenderer.removeListener('jobos:window:prepare-close', wrapped)
    }
  }),
  shell: Object.freeze({
    openExternal: (url: string) => ipcRenderer.invoke('jobos:shell:open-external', url)
  }),
  connectivity: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:connectivity:get')
  }),
  agent: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:agent:get'),
    reset: () => ipcRenderer.invoke('jobos:agent:reset'),
    send: (text: string, idempotencyKey: string) => ipcRenderer.invoke('jobos:agent:send', text, idempotencyKey),
    cancel: (turnId: string) => ipcRenderer.invoke('jobos:agent:cancel', turnId),
    retry: (turnId: string, idempotencyKey: string) => ipcRenderer.invoke('jobos:agent:retry', turnId, idempotencyKey),
    subscribe: (listener: (update: AgentStreamUpdate) => void) => {
      const wrapped = (_event: IpcRendererEvent, update: AgentStreamUpdate) => listener(update)
      ipcRenderer.on('jobos:agent:event', wrapped)
      return () => ipcRenderer.removeListener('jobos:agent:event', wrapped)
    }
  }),
  jobs: Object.freeze({
    getState: () => ipcRenderer.invoke('jobos:jobs:get-state'),

    list: (sort: JobSortMode, query?: string, statusGroup?: string) => ipcRenderer.invoke('jobos:jobs:list', sort, query, statusGroup),
    inspect: (jobId: string) => ipcRenderer.invoke('jobos:jobs:inspect', jobId),
    select: (jobId: string) => ipcRenderer.invoke('jobos:jobs:select', jobId),
    reorder: (jobIds: string[]) => ipcRenderer.invoke('jobos:jobs:reorder', jobIds),
    setSort: (sort: JobSortMode) => ipcRenderer.invoke('jobos:jobs:set-sort', sort),
    updateStatus: (jobId: string, status: JobStatus) => ipcRenderer.invoke('jobos:jobs:update-status', jobId, status),
    saveFromBrowser: (tabId: string, expectedUrl: string, extraction: BrowserJobExtraction, idempotencyKey: string) => (
      ipcRenderer.invoke('jobos:jobs:save-from-browser', tabId, expectedUrl, extraction, idempotencyKey)
    ),
    subscribe: (listener: (event: JobEvent) => void) => {
      const wrapped = (_event: IpcRendererEvent, jobEvent: Parameters<typeof listener>[0]) => listener(jobEvent)
      ipcRenderer.on('jobos:jobs:event', wrapped)
      return () => ipcRenderer.removeListener('jobos:jobs:event', wrapped)
    }
  }),
  workspace: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:workspace:get'),
    save: (snapshot: WorkspaceSnapshot) => ipcRenderer.invoke('jobos:workspace:save', snapshot)
  }),
  browser: Object.freeze({
    getState: () => ipcRenderer.invoke('jobos:browser:get-state'),
    restore: (state: BrowserRestoreState) => ipcRenderer.invoke('jobos:browser:restore', state),
    create: (url?: string, associatedJobId?: string | null) => ipcRenderer.invoke('jobos:browser:create', url, associatedJobId),
    select: (tabId: string) => ipcRenderer.invoke('jobos:browser:select', tabId),
    close: (tabId: string) => ipcRenderer.invoke('jobos:browser:close', tabId),
    reorder: (tabIds: string[]) => ipcRenderer.invoke('jobos:browser:reorder', tabIds),
    navigate: (tabId: string, input: string) => ipcRenderer.invoke('jobos:browser:navigate', tabId, input),
    back: (tabId: string) => ipcRenderer.invoke('jobos:browser:back', tabId),
    forward: (tabId: string) => ipcRenderer.invoke('jobos:browser:forward', tabId),
    reload: (tabId: string) => ipcRenderer.invoke('jobos:browser:reload', tabId),
    stop: (tabId: string) => ipcRenderer.invoke('jobos:browser:stop', tabId),

    associate: (tabId: string, jobId: string | null) => ipcRenderer.invoke('jobos:browser:associate', tabId, jobId),
    copyBlockedUrl: (tabId: string) => ipcRenderer.invoke('jobos:browser:copy-blocked-url', tabId),
    setBounds: (bounds: BrowserBounds) => ipcRenderer.invoke('jobos:browser:set-bounds', bounds),
    subscribe: (listener: (state: BrowserState) => void) => {
      const wrapped = (_event: IpcRendererEvent, state: BrowserState) => listener(state)
      ipcRenderer.on('jobos:browser:state', wrapped)
      return () => ipcRenderer.removeListener('jobos:browser:state', wrapped)
    }
  }),
  documents: Object.freeze({
    list: (jobId: string) => ipcRenderer.invoke('jobos:documents:list', jobId),
    refresh: (jobId: string) => ipcRenderer.invoke('jobos:documents:refresh', jobId),
    approve: (jobId: string, artifactId: string) => (
      ipcRenderer.invoke('jobos:documents:approve', jobId, artifactId)
    ),
    loadPdf: (artifactId: string) => ipcRenderer.invoke('jobos:documents:load-pdf', artifactId),
    loadOriginalDocx: (artifactId: string) => (
      ipcRenderer.invoke('jobos:documents:load-original-docx', artifactId)
    ),
    export: (artifactId: string) => ipcRenderer.invoke('jobos:documents:export', artifactId),
    reveal: (artifactId: string) => ipcRenderer.invoke('jobos:documents:reveal', artifactId),
    open: (artifactId: string) => ipcRenderer.invoke('jobos:documents:open', artifactId)
  }),
  docxDocuments: Object.freeze({
    listBindings: (jobId: string) => ipcRenderer.invoke('jobos:docx:list-bindings', jobId),
    openBound: (jobId: string, documentKey: DocumentKey) => ipcRenderer.invoke('jobos:docx:open-bound', jobId, documentKey),
    openArtifact: (jobId: string, documentKey: DocumentKey, artifactId: string) => ipcRenderer.invoke('jobos:docx:open-artifact', jobId, documentKey, artifactId),
    chooseFile: (jobId: string, documentKey: DocumentKey) => ipcRenderer.invoke('jobos:docx:choose-file', jobId, documentKey),
    createBlank: (jobId: string, documentKey: DocumentKey) => ipcRenderer.invoke('jobos:docx:create-blank', jobId, documentKey),
    reload: (bindingId: string) => ipcRenderer.invoke('jobos:docx:reload', bindingId),
    save: (request: SaveDocxRequest) => ipcRenderer.invoke('jobos:docx:save', request),
    saveAs: (bindingId: string, bytes: ArrayBuffer) => ipcRenderer.invoke('jobos:docx:save-as', bindingId, bytes),
    createRecovery: (bindingId: string, reason: 'baseline' | 'autosave' | 'manual' | 'conflict' | 'agent') => ipcRenderer.invoke('jobos:docx:create-recovery', bindingId, reason),
    listRecoveries: (bindingId: string) => ipcRenderer.invoke('jobos:docx:list-recoveries', bindingId),
    restoreRecovery: (bindingId: string, recoveryId: string) => ipcRenderer.invoke('jobos:docx:restore-recovery', bindingId, recoveryId),
    unbind: (bindingId: string) => ipcRenderer.invoke('jobos:docx:unbind', bindingId),
    subscribe: (listener: (event: DocxExternalChangeEvent) => void) => {
      const wrapped = (_event: IpcRendererEvent, value: DocxExternalChangeEvent) => listener(value)
      ipcRenderer.on('jobos:docx:external-change', wrapped)
      return () => ipcRenderer.removeListener('jobos:docx:external-change', wrapped)
    }
  }),
  editableDocuments: Object.freeze({
    list: (jobId: string) => ipcRenderer.invoke('jobos:editable-documents:list', jobId),
    getForJob: (jobId: string, documentKey: DocumentKey) => (
      ipcRenderer.invoke('jobos:editable-documents:get-for-job', jobId, documentKey)
    ),
    get: (documentId: string) => ipcRenderer.invoke('jobos:editable-documents:get', documentId),
    createBlank: (jobId: string, documentKey: DocumentKey, idempotencyKey: string) => (
      ipcRenderer.invoke('jobos:editable-documents:create-blank', jobId, documentKey, idempotencyKey)
    ),
    save: (documentId: string, request: SaveEditableDocumentRequest) => (
      ipcRenderer.invoke('jobos:editable-documents:save', documentId, request)
    ),
    listSnapshots: (documentId: string) => (
      ipcRenderer.invoke('jobos:editable-documents:list-snapshots', documentId)
    ),
    createSnapshot: (documentId: string, request: CreateEditableDocumentSnapshotRequest) => (
      ipcRenderer.invoke('jobos:editable-documents:create-snapshot', documentId, request)
    ),
    restoreSnapshot: (
      documentId: string,
      snapshotId: string,
      request: RestoreEditableDocumentSnapshotRequest
    ) => ipcRenderer.invoke('jobos:editable-documents:restore-snapshot', documentId, snapshotId, request),
    applyOperations: (documentId: string, request: ApplyEditableDocumentOperationsRequest) => (
      ipcRenderer.invoke('jobos:editable-documents:apply-operations', documentId, request)
    ),
    importRegisteredArtifact: (jobId: string, documentKey: DocumentKey, artifactId: string) => (
      ipcRenderer.invoke('jobos:editable-documents:import-registered', jobId, documentKey, artifactId)
    ),
    importFile: (jobId: string, documentKey: DocumentKey) => (
      ipcRenderer.invoke('jobos:editable-documents:import-file', jobId, documentKey)
    ),
    preview: (documentId: string) => ipcRenderer.invoke('jobos:editable-documents:preview', documentId),
    export: (documentId: string, format: 'docx' | 'pdf') => (
      ipcRenderer.invoke('jobos:editable-documents:export', documentId, format)
    ),
    publish: (documentId: string) => ipcRenderer.invoke('jobos:editable-documents:publish', documentId)
  })
})

contextBridge.exposeInMainWorld('jobos', bridge)
