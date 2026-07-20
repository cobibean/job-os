export type ConnectivityState = 'connecting' | 'connected' | 'degraded' | 'disconnected'

export interface ConnectivitySnapshot {
  state: Exclude<ConnectivityState, 'connecting'>
  apiVersion?: string
  checkedAt: string
  message: string
}

export interface JobOsRendererBridge {
  connectivity: {
    get: () => Promise<ConnectivitySnapshot>
  }
}
