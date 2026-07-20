import {
  createJobOsApiClient,
  deviceSessionV1DeviceSessionGet,
  healthV1HealthGet
} from '@jobos/contracts'

import type { ConnectivitySnapshot } from '../shared/contracts.js'

export interface ConnectivityConfig {
  baseUrl: string
  deviceToken: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}

function isHealthResponse(value: unknown): value is {
  status: 'ready'
  service: 'jobos-api'
  version: string
  state_schema: number
} {
  return isRecord(value)
    && value.status === 'ready'
    && value.service === 'jobos-api'
    && typeof value.version === 'string'
    && value.version.length > 0
    && Number.isInteger(value.state_schema)
    && Number(value.state_schema) >= 1
}

function isDeviceSessionResponse(value: unknown): value is {
  authenticated: true
  transport: 'private-tailscale'
  api_version: string
} {
  return isRecord(value)
    && value.authenticated === true
    && value.transport === 'private-tailscale'
    && typeof value.api_version === 'string'
    && value.api_version.length > 0
}

export async function probeConnectivity(config: ConnectivityConfig): Promise<ConnectivitySnapshot> {
  const checkedAt = new Date().toISOString()
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)

  try {
    const health = await healthV1HealthGet({ client })
    if (health.error || health.response?.status !== 200) {
      return {
        state: 'disconnected',
        checkedAt,
        message: 'JobOS API unavailable'
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
      checkedAt,
      message: 'Private API authenticated'
    }
  } catch (error) {
    return {
      state: 'disconnected',
      checkedAt,
      message: error instanceof Error ? error.message : 'JobOS API unavailable'
    }
  }
}
