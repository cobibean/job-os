// @vitest-environment node

import { describe, expect, it, vi } from 'vitest'

import { activateVisibleWindow, RendererSafetyCoordinator } from './mainWindowLifecycle.js'

describe('activateVisibleWindow', () => {
  it('creates a visible window even when unrelated hidden windows exist', async () => {
    const created = { isDestroyed: () => false, show: vi.fn() }
    const create = vi.fn(async () => created)

    expect(await activateVisibleWindow(null, create)).toBe(created)
    expect(create).toHaveBeenCalledTimes(1)
  })

  it('shows the existing visible window instead of creating another', async () => {
    const existing = { isDestroyed: () => false, show: vi.fn() }
    const create = vi.fn(async () => existing)

    expect(await activateVisibleWindow(existing, create)).toBe(existing)
    expect(existing.show).toHaveBeenCalledTimes(1)
    expect(create).not.toHaveBeenCalled()
  })
})

describe('RendererSafetyCoordinator', () => {
  it('uses one request/ack path for close and profile switch', async () => {
    const send = vi.fn()
    const coordinator = new RendererSafetyCoordinator(send, 100, () => 'request-1')
    const result = coordinator.request('profile-switch')
    expect(send).toHaveBeenCalledWith('request-1', 'profile-switch')
    expect(coordinator.resolve('request-1', true)).toBe(true)
    await expect(result).resolves.toBe(true)
  })

  it('fails closed on timeout, duplicate requests, renderer failure, and stale ack', async () => {
    vi.useFakeTimers()
    const coordinator = new RendererSafetyCoordinator(vi.fn(), 10, () => 'request-timeout')
    const pending = coordinator.request('window-close')
    await expect(coordinator.request('profile-switch')).resolves.toBe(false)
    await vi.advanceTimersByTimeAsync(10)
    await expect(pending).resolves.toBe(false)
    expect(coordinator.resolve('request-timeout', true)).toBe(false)
    const crashed = coordinator.request('profile-switch')
    coordinator.dispose()
    await expect(crashed).resolves.toBe(false)
    vi.useRealTimers()
  })
})
