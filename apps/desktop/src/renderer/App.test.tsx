import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { App } from './App'

afterEach(cleanup)


test('the shell reports authenticated Mini connectivity without exposing credentials', async () => {
  const getConnectivity = vi.fn().mockResolvedValue({
    state: 'connected',
    apiVersion: '0.1.0',
    checkedAt: '2026-07-20T00:00:00.000Z',
    message: 'Private API authenticated'
  })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: { get: getConnectivity }
    }
  })

  render(<App />)

  expect(screen.getByText('Connecting to Mac Mini…')).not.toBeNull()
  expect(await screen.findByText('Mac Mini connected')).not.toBeNull()
  expect(screen.getByText('API 0.1.0')).not.toBeNull()
  expect(getConnectivity).toHaveBeenCalledOnce()
  expect(JSON.stringify(window.jobos)).not.toContain('test-device-token')
})

test('reset preserves the selected layout preset', async () => {
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: {
      connectivity: {
        get: vi.fn().mockResolvedValue({
          state: 'connected',
          apiVersion: '0.1.0',
          checkedAt: '2026-07-20T00:00:00.000Z',
          message: 'Private API authenticated'
        })
      }
    }
  })

  render(<App />)
  const research = screen.getByRole('button', { name: 'Research' })
  fireEvent.click(research)
  fireEvent.click(screen.getByRole('button', { name: 'Reset layout' }))

  expect(research.getAttribute('aria-pressed')).toBe('true')
})

test('later-phase controls are visibly disabled while layout controls remain interactive', () => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: undefined })
  render(<App />)

  for (const name of [
    'Job ordering: Manual',
    'Reverse job order',
    'Open a new surface',
    'Agent context settings',
    'Send message',
    'Open settings'
  ]) {
    expect((screen.getByRole('button', { name }) as HTMLButtonElement).disabled).toBe(true)
  }
  expect((screen.getByRole('tab', { name: 'Browser' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('button', { name: 'Research' }) as HTMLButtonElement).disabled).toBe(false)
  expect((screen.getByRole('button', { name: 'Reset layout' }) as HTMLButtonElement).disabled).toBe(false)
})

test('auth degradation is distinct from network unavailability', async () => {
  const get = vi.fn()
    .mockResolvedValueOnce({
      state: 'degraded',
      checkedAt: '2026-07-20T00:00:00.000Z',
      message: 'Device authentication failed'
    })
    .mockResolvedValueOnce({
      state: 'disconnected',
      checkedAt: '2026-07-20T00:00:01.000Z',
      message: 'Mac Mini unavailable'
    })
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectivity: { get } }
  })

  render(<App />)
  expect(await screen.findByText('Mac Mini authentication failed')).not.toBeNull()
  fireEvent.focus(window)
  expect(await screen.findByText('Mac Mini unavailable')).not.toBeNull()
})
