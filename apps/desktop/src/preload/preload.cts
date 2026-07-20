import { contextBridge, ipcRenderer } from 'electron'
import type { IpcRendererEvent } from 'electron'

import type { JobEvent, JobOsRendererBridge, JobSortMode, JobStatus, WorkspaceSnapshot } from '../shared/contracts.js'

const bridge: JobOsRendererBridge = Object.freeze({
  connectivity: Object.freeze({
    get: () => ipcRenderer.invoke('jobos:connectivity:get')
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
  })
})

contextBridge.exposeInMainWorld('jobos', bridge)
