import type { JobOsRendererBridge } from '../shared/contracts'

declare global {
  interface Window {
    jobos: JobOsRendererBridge
  }
}

export {}
