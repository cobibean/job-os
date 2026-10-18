// @vitest-environment node
import { EventEmitter } from 'node:events'
import { afterEach, expect, test, vi } from 'vitest'

const mocks = vi.hoisted(() => ({ spawn: vi.fn(), execFile: vi.fn() }))
vi.mock('node:child_process', () => mocks)
import { createSourceApiProcess } from './sourceApiProcess.js'

afterEach(() => { vi.useRealTimers(); vi.clearAllMocks() })

test.each(['SIGTERM', 'SIGKILL'] as const)('recognizes a child that exits by %s as stopped', async signal => {
  vi.useFakeTimers()
  const child = Object.assign(new EventEmitter(), {
    exitCode: null as number | null,
    signalCode: null as NodeJS.Signals | null,
    kill: vi.fn((requested: NodeJS.Signals = 'SIGTERM') => {
      if (requested === signal) setTimeout(() => { child.signalCode = signal; child.emit('exit', null, signal) }, 1)
      return true
    })
  })
  mocks.spawn.mockImplementation(() => { setTimeout(() => child.emit('spawn'), 0); return child })
  const api = createSourceApiProcess({ sourceRoot: '/tmp/synthetic', isPackaged: () => false,
    getConfigPath: () => '/tmp/synthetic/config.json', environment: {} })
  const start = api.start({ schemaVersion: 1, mode: 'local-service', apiBaseUrl: 'http://127.0.0.1:18766', deviceId: 'synthetic' })
  await vi.advanceTimersByTimeAsync(0)
  await start
  const stopped = api.stop().then(() => ({ stopped: true }), error => ({ error: error.message }))
  await vi.advanceTimersByTimeAsync(5_000)
  expect(await stopped).toEqual({ stopped: true })
  expect(child.kill.mock.calls).toEqual(signal === 'SIGTERM' ? [[]] : [[], ['SIGKILL']])
})

test.each(['normal', 'exit-event-only', 'unresponsive'] as const)('handles %s child shutdown without signaling unrelated processes', async outcome => {
  vi.useFakeTimers()
  const child = Object.assign(new EventEmitter(), {
    exitCode: null as number | null, signalCode: null as NodeJS.Signals | null,
    kill: vi.fn(() => {
      if (outcome === 'normal') child.exitCode = 0
      if (outcome !== 'unresponsive') child.emit('exit', outcome === 'normal' ? 0 : null, null)
      return true
    })
  })
  mocks.spawn.mockImplementation(() => { queueMicrotask(() => child.emit('spawn')); return child })
  const api = createSourceApiProcess({ sourceRoot: '/tmp/synthetic', isPackaged: () => false,
    getConfigPath: () => '/tmp/synthetic/config.json', environment: {} })
  const start = api.start({ schemaVersion: 1, mode: 'local-service', apiBaseUrl: 'http://127.0.0.1:18766', deviceId: 'synthetic' })
  await vi.advanceTimersByTimeAsync(0)
  await start
  const stopped = api.stop().then(() => ({ stopped: true }), error => ({ error: error.message }))
  await vi.advanceTimersByTimeAsync(5_000)
  expect(await stopped).toEqual(outcome === 'unresponsive' ? { error: 'Source JobOS API did not stop' } : { stopped: true })
  expect(child.kill.mock.calls).toEqual(outcome === 'unresponsive' ? [[], ['SIGKILL']] : [[]])
})
