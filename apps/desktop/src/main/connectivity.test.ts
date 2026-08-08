// @vitest-environment node

import { expect, test } from 'vitest'

import { probeConnectivity } from './connectivity.js'

test('connectivity is healthy only after the Mini API authenticates the device', async () => {
  const fetcher = async (input: string | URL | Request) => {
    const request = input instanceof Request ? input : new Request(input)
    const url = new URL(request.url)
    if (url.pathname === '/v1/health') {
      return Response.json({
        status: 'ready',
        service: 'jobos-api',
        version: '0.1.0',
        state_schema: 2
      })
    }
    if (
      url.pathname === '/v1/device-session' &&
      request.headers.get('authorization') === 'Bearer integration-device-token'
    ) {
      return Response.json({
        authenticated: true,
        transport: 'private-tailscale',
        api_version: '0.1.0'
      })
    }
    return Response.json({ detail: 'Device authentication required' }, { status: 401 })
  }

  const connected = await probeConnectivity({
    baseUrl: 'http://jobos.test',
    deviceToken: 'integration-device-token',
    fetch: fetcher
  })
  const rejected = await probeConnectivity({
    baseUrl: 'http://jobos.test',
    deviceToken: 'wrong-device-token',
    fetch: fetcher
  })

  expect(connected).toMatchObject({
    state: 'connected',
    apiVersion: '0.1.0',
    message: 'Private API authenticated'
  })
  expect(JSON.stringify(connected)).not.toContain('integration-device-token')
  expect(rejected).toMatchObject({
    state: 'degraded',
    message: 'Device authentication failed'
  })
})

test('a malformed 200 response never reports the Mini as connected', async () => {
  const result = await probeConnectivity({
    baseUrl: 'http://jobos.test',
    deviceToken: 'integration-device-token',
    fetch: async () => Response.json({})
  })

  expect(result).toMatchObject({
    state: 'disconnected',
    message: 'JobOS API returned an invalid health response'
  })
})

test('a malformed authenticated response is distinct from network unavailability', async () => {
  const fetcher = async (input: string | URL | Request) => {
    const request = input instanceof Request ? input : new Request(input)
    if (new URL(request.url).pathname === '/v1/health') {
      return Response.json({
        status: 'ready',
        service: 'jobos-api',
        version: '0.1.0',
        state_schema: 2
      })
    }
    return Response.json({})
  }

  const result = await probeConnectivity({
    baseUrl: 'http://jobos.test',
    deviceToken: 'integration-device-token',
    fetch: fetcher
  })

  expect(result).toMatchObject({
    state: 'degraded',
    message: 'Device authentication response invalid'
  })
})

test('network exceptions never expose credential material', async () => {
  const token = 'credential-that-must-not-escape'
  const result = await probeConnectivity({
    baseUrl: 'http://jobos.test',
    deviceToken: token,
    fetch: async () => {
      throw new Error(`request failed with ${token}`)
    }
  })

  expect(result).toMatchObject({
    state: 'disconnected',
    message: 'JobOS API unavailable'
  })
  expect(JSON.stringify(result)).not.toContain(token)
})

test('an unresponsive API request is aborted instead of blocking desktop startup', async () => {
  const result = await probeConnectivity({
    baseUrl: 'http://jobos.test',
    deviceToken: 'integration-device-token',
    requestTimeoutMs: 10,
    fetch: (_input, init) => new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (!signal) {
        reject(new Error('Expected a request timeout signal'))
        return
      }
      signal.addEventListener('abort', () => reject(signal.reason), { once: true })
    })
  })

  expect(result).toMatchObject({
    state: 'disconnected',
    message: 'JobOS API unavailable'
  })
})
