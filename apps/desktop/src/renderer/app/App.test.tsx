import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { SetupSnapshot } from '../../shared/contracts'
import { App } from './App'

afterEach(() => { cleanup(); window.localStorage.clear() })

test('checks setup before starting workbench services and mounts the workbench only when ready', async () => {
  let resolveSetup!: (snapshot: SetupSnapshot) => void
  const getConnectivity = vi.fn().mockResolvedValue({
    state: 'connected',
    checkedAt: '2026-08-29T00:00:00.000Z',
    message: 'Connected'
  })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      setup: {
        get: vi.fn(() => new Promise<SetupSnapshot>(resolve => { resolveSetup = resolve })),
        initialize: vi.fn(),
        restart: vi.fn()
      },
      connectivity: { get: getConnectivity }
    }
  })

  render(<App />)

  expect(screen.getByRole('status').textContent).toBe('Checking local setup…')
  expect(screen.queryByLabelText('Job navigation')).toBeNull()
  expect(getConnectivity).not.toHaveBeenCalled()

  await act(async () => resolveSetup({ state: 'ready', message: 'JobOS is configured' }))

  expect(await screen.findByLabelText('Job navigation')).not.toBeNull()
  expect(getConnectivity).toHaveBeenCalledOnce()
})

test('starts the source fallback workbench when no setup bridge is present', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })

  render(<App />)

  expect(screen.getByLabelText('Job navigation')).not.toBeNull()
})

test('missing configuration opens setup without starting workbench services', async () => {
  const getConnectivity = vi.fn()
  const getJobs = vi.fn()
  const getWorkspace = vi.fn()
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      setup: {
        get: vi.fn().mockResolvedValue({ state: 'required', message: 'JobOS setup is required' }),
        initialize: vi.fn(),
        restart: vi.fn()
      },
      connectivity: { get: getConnectivity },
      jobs: { getState: getJobs },
      workspace: { get: getWorkspace }
    }
  })

  render(<App />)

  expect(await screen.findByRole('heading', { name: 'Set up JobOS on this Mac' })).not.toBeNull()
  expect(screen.queryByLabelText('Job navigation')).toBeNull()
  expect(getConnectivity).not.toHaveBeenCalled()
  expect(getJobs).not.toHaveBeenCalled()
  expect(getWorkspace).not.toHaveBeenCalled()
})
