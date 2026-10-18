import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { OnboardingScreen } from './OnboardingScreen'

afterEach(cleanup)

test('setup failure supports retry, success, and explicit restart', async () => {
  const initialize = vi.fn()
    .mockResolvedValueOnce({ state: 'error', message: 'Local setup command failed' })
    .mockResolvedValueOnce({ state: 'succeeded', message: 'Setup complete. Restart JobOS to continue.' })
  const restart = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { setup: { initialize, restart } }
  })
  render(<OnboardingScreen initial={{ state: 'required', message: 'JobOS setup is required' }} />)

  fireEvent.click(screen.getByRole('button', { name: 'Set up JobOS' }))
  expect(await screen.findByText('Local setup command failed')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry setup' }))
  expect(await screen.findByText('Setup complete. Restart JobOS to continue.')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Restart JobOS' }))
  expect(restart).toHaveBeenCalledOnce()
})

test('existing-install recovery retries the connection without initializing a new profile', async () => {
  const initialize = vi.fn()
  const retryConnection = vi.fn().mockResolvedValue({ state: 'error', recovery: true, message: 'Synthetic host unavailable' })
  const restart = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(window, 'jobos', { configurable: true, value: { setup: { initialize, retryConnection, restart } } })
  render(<OnboardingScreen initial={{ state: 'error', recovery: true, message: 'Synthetic startup outage' }} />)
  expect(screen.getByRole('heading', { name: 'Reconnect to JobOS' })).not.toBeNull()
  expect(screen.queryByRole('button', { name: 'Retry setup' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Retry connection' }))
  await screen.findByText('Synthetic host unavailable')
  expect(retryConnection).toHaveBeenCalledOnce()
  expect(initialize).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Restart JobOS' }))
  expect(restart).toHaveBeenCalledOnce()
})
