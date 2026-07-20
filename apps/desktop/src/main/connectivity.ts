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

function recordValue(value: unknown, key: string): unknown {
  return value && typeof value === 'object' ? (value as Record<string, unknown>)[key] : undefined
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

    const deviceSession = await deviceSessionV1DeviceSessionGet({ client })
    if (deviceSession.error || deviceSession.response?.status !== 200) {
      return {
        state: 'degraded',
        checkedAt,
        message: 'Device authentication failed'
      }
    }

    const apiVersion = recordValue(deviceSession.data, 'api_version')
    return {
      state: 'connected',
      apiVersion: typeof apiVersion === 'string' ? apiVersion : undefined,
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
