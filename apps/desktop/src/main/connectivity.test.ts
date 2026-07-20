// @vitest-environment node

import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'

import { afterEach, expect, test } from 'vitest'

import { probeConnectivity } from './connectivity.js'

const servers: Array<ReturnType<typeof createServer>> = []

afterEach(async () => {
  await Promise.all(servers.splice(0).map(server => new Promise<void>(resolve => server.close(() => resolve()))))
})

test('connectivity is healthy only after the Mini API authenticates the device', async () => {
  const server = createServer((request, response) => {
    response.setHeader('content-type', 'application/json')
    if (request.url === '/v1/health') {
      response.end(JSON.stringify({
        status: 'ready',
        service: 'jobos-api',
        version: '0.1.0',
        state_schema: 2
      }))
      return
    }
    if (
      request.url === '/v1/device-session' &&
      request.headers.authorization === 'Bearer integration-device-token'
    ) {
      response.end(JSON.stringify({
        authenticated: true,
        transport: 'private-tailscale',
        api_version: '0.1.0'
      }))
      return
    }
    response.statusCode = 401
    response.end(JSON.stringify({ detail: 'Device authentication required' }))
  })
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo

  const connected = await probeConnectivity({
    baseUrl: `http://127.0.0.1:${address.port}`,
    deviceToken: 'integration-device-token'
  })
  const rejected = await probeConnectivity({
    baseUrl: `http://127.0.0.1:${address.port}`,
    deviceToken: 'wrong-device-token'
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
  const server = createServer((_request, response) => {
    response.setHeader('content-type', 'application/json')
    response.end('{}')
  })
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo

  const result = await probeConnectivity({
    baseUrl: `http://127.0.0.1:${address.port}`,
    deviceToken: 'integration-device-token'
  })

  expect(result).toMatchObject({
    state: 'disconnected',
    message: 'JobOS API returned an invalid health response'
  })
})

test('a malformed authenticated response is distinct from network unavailability', async () => {
  const server = createServer((request, response) => {
    response.setHeader('content-type', 'application/json')
    if (request.url === '/v1/health') {
      response.end(JSON.stringify({
        status: 'ready',
        service: 'jobos-api',
        version: '0.1.0',
        state_schema: 2
      }))
      return
    }
    response.end('{}')
  })
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address() as AddressInfo

  const result = await probeConnectivity({
    baseUrl: `http://127.0.0.1:${address.port}`,
    deviceToken: 'integration-device-token'
  })

  expect(result).toMatchObject({
    state: 'degraded',
    message: 'Device authentication response invalid'
  })
})
