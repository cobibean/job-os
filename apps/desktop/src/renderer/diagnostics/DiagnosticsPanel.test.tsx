import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { DiagnosticsPanel } from './DiagnosticsPanel'

afterEach(cleanup)

test('shows capability states without paths or secrets and confirms demo reset', async () => {
  const initialize = vi.fn().mockResolvedValue({ state: 'succeeded', message: 'Setup complete' })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    diagnostics: {
      get: vi.fn().mockResolvedValue({
        mode: 'local-service', appVersion: '0.1.0',
        capabilities: { localService: 'available', agent: 'not-configured', desktop: 'available' }
      }),
      openData: vi.fn(), openLogs: vi.fn()
    },
    setup: { initialize }
  } })
  render(<DiagnosticsPanel />)
  expect(await screen.findByText('local-service')).not.toBeNull()
  expect(screen.getByText('Agent not configured')).not.toBeNull()
  expect(document.body.textContent).not.toContain('/Users/')
  expect(document.body.textContent).not.toContain('token')
  fireEvent.click(screen.getByRole('button', { name: 'Reset fictional demo' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm demo reset' }))
  expect(await screen.findByText('Setup complete')).not.toBeNull()
  expect(initialize).toHaveBeenCalledWith(true, true)
})
