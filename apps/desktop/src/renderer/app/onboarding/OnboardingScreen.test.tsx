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
