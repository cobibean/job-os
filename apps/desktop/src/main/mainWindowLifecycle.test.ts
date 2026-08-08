// @vitest-environment node

import { describe, expect, it, vi } from 'vitest'

import { activateVisibleWindow } from './mainWindowLifecycle.js'

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
