import {
  createJobOsApiClient,
  deviceSessionV1DeviceSessionGet,
  healthV1HealthGet
} from '@jobos/contracts'

import type { ConnectivitySnapshot } from '../../../shared/contracts.js'

export interface ConnectivityConfig {
  baseUrl: string
  deviceToken: string
  fetch?: typeof fetch
  requestTimeoutMs?: number
}

const DEFAULT_REQUEST_TIMEOUT_MS = 5_000

function fetchWithTimeout(config: ConnectivityConfig): typeof fetch {
  const sourceFetch = config.fetch ?? globalThis.fetch
  const timeoutMs = config.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
  return (input, init) => {
    const timeoutSignal = AbortSignal.timeout(timeoutMs)
    const signal = init?.signal
      ? AbortSignal.any([init.signal, timeoutSignal])
      : timeoutSignal
    return sourceFetch(input, { ...init, signal })
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}

function isHealthResponse(value: unknown): value is {
  status: 'ready'
  service: 'jobos-api'
  version: string
  state_schema: number
  transport: 'local-loopback' | 'private-remote'
  agent: 'not-configured' | 'online' | 'connecting' | 'offline'
  artifact_storage: 'available' | 'unavailable'
  artifact_gateway: 'not-configured' | 'available' | 'unavailable'
} {
  return isRecord(value)
    && value.status === 'ready'
    && value.service === 'jobos-api'
    && typeof value.version === 'string'
    && value.version.length > 0
    && Number.isInteger(value.state_schema)
    && Number(value.state_schema) >= 1
    && (value.transport === 'local-loopback' || value.transport === 'private-remote')
    && ['not-configured', 'online', 'connecting', 'offline'].includes(String(value.agent))
    && (value.artifact_storage === 'available' || value.artifact_storage === 'unavailable')
    && ['not-configured', 'available', 'unavailable'].includes(String(value.artifact_gateway))
}

function isDeviceSessionResponse(value: unknown): value is {
  authenticated: true
  transport: 'local-loopback' | 'private-remote'
  desktop: 'connected' | 'disconnected'
  api_version: string
  installation_profile_id: string
  installation_profile_name: string
  profile_registry_revision: number
} {
  return isRecord(value)
    && value.authenticated === true
    && (value.transport === 'local-loopback' || value.transport === 'private-remote')
    && (value.desktop === 'connected' || value.desktop === 'disconnected')
    && typeof value.api_version === 'string'
    && value.api_version.length > 0
    && typeof value.installation_profile_id === 'string'
    && /^jprof_[a-f0-9]{32}$/.test(value.installation_profile_id)
    && typeof value.installation_profile_name === 'string'
    && value.installation_profile_name.trim().length > 0
    && value.installation_profile_name.length <= 64
    && Number.isInteger(value.profile_registry_revision)
    && Number(value.profile_registry_revision) >= 1
}

export async function probeConnectivity(config: ConnectivityConfig): Promise<ConnectivitySnapshot> {
  const checkedAt = new Date().toISOString()
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)
  client.setConfig({ fetch: fetchWithTimeout(config) })

  try {
    const health = await healthV1HealthGet({ client })
    if (health.error || health.response?.status !== 200) {
      return {
        state: 'disconnected',
        checkedAt,
        message: 'JobOS host unavailable'
      }
    }
    if (!isHealthResponse(health.data)) {
      return {
        state: 'disconnected',
        checkedAt,
        message: 'JobOS API returned an invalid health response'
      }
    }

    const deviceSession = await deviceSessionV1DeviceSessionGet({ client })
    if (deviceSession.error || deviceSession.response?.status !== 200) {
      return {
        state: 'degraded',
        checkedAt,
        message: 'Device authentication failed'
      }
    }
    if (!isDeviceSessionResponse(deviceSession.data)) {
      return {
        state: 'degraded',
        checkedAt,
        message: 'Device authentication response invalid'
      }
    }

    return {
      state: 'connected',
      apiVersion: deviceSession.data.api_version,
      transport: deviceSession.data.transport,
      agent: health.data.agent,
      desktop: deviceSession.data.desktop,
      artifactStorage: health.data.artifact_storage,
      artifactGateway: health.data.artifact_gateway,
      installationProfileId: deviceSession.data.installation_profile_id,
      installationProfileName: deviceSession.data.installation_profile_name,
      profileRegistryRevision: deviceSession.data.profile_registry_revision,
      checkedAt,
      message: deviceSession.data.transport === 'local-loopback'
        ? 'Local loopback API authenticated'
        : 'Private remote API authenticated'
    }
  } catch {
    return {
      state: 'disconnected',
      checkedAt,
      message: 'JobOS host unavailable'
    }
  }
}
