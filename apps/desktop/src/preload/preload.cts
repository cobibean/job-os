import { contextBridge, ipcRenderer } from 'electron'
import type { IpcRendererEvent } from 'electron'

import type { AgentStreamUpdate, BrowserBounds, BrowserRestoreState, BrowserState, JobEvent, JobOsRendererBridge, JobSortMode, JobStatus, WorkspaceSnapshot } from '../shared/contracts.js'

const bridge: JobOsRendererBridge = Object.freeze({
  connectivity: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:connectivity:get')
  }),
  agent: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:agent:get'),
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
    select: (jobId: string) => ipcRenderer.invoke('jobos:jobs:select', jobId),
    reorder: (jobIds: string[]) => ipcRenderer.invoke('jobos:jobs:reorder', jobIds),
    setSort: (sort: JobSortMode) => ipcRenderer.invoke('jobos:jobs:set-sort', sort),
    updateStatus: (jobId: string, status: JobStatus) => ipcRenderer.invoke('jobos:jobs:update-status', jobId, status),
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
    loadPdf: (artifactId: string) => ipcRenderer.invoke('jobos:documents:load-pdf', artifactId),
    export: (artifactId: string) => ipcRenderer.invoke('jobos:documents:export', artifactId),
    reveal: (artifactId: string) => ipcRenderer.invoke('jobos:documents:reveal', artifactId),
    open: (artifactId: string) => ipcRenderer.invoke('jobos:documents:open', artifactId)
  })
})

contextBridge.exposeInMainWorld('jobos', bridge)
