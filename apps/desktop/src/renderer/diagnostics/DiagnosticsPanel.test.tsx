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
        installationProfile: {
          id: 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          name: 'Personal',
          switchStatus: 'idle'
        },
        capabilities: {
          localService: 'available', agent: 'connecting', desktop: 'disconnected',
          renderer: 'unavailable', artifactStorage: 'available', artifactGateway: 'not-configured', transport: 'local-loopback'
        }
      }),
      openData: vi.fn(), openLogs: vi.fn()
    },
    setup: { initialize }
  } })
  render(<DiagnosticsPanel />)
  const diagnosticsSection = screen.getByRole('button', { name: 'Diagnostics' })
  expect(diagnosticsSection.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(diagnosticsSection)
  expect(diagnosticsSection.getAttribute('aria-expanded')).toBe('true')
  expect(await screen.findByText('local-service')).not.toBeNull()
  expect(screen.getByText('Agent connecting')).not.toBeNull()
  expect(screen.getByText('jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')).not.toBeNull()
  expect(screen.getByText('Personal')).not.toBeNull()
  expect(screen.getByText('Desktop capability disconnected')).not.toBeNull()
  expect(screen.getByText('Renderer unavailable')).not.toBeNull()
  expect(screen.getByText('Artifact storage available')).not.toBeNull()
  expect(screen.getByText('Artifact gateway not configured')).not.toBeNull()
  expect(document.body.textContent).not.toContain(['/Users', '/'].join(''))
  expect(document.body.textContent).not.toContain('token')
  fireEvent.click(screen.getByRole('button', { name: 'Reset fictional demo' }))
  fireEvent.click(screen.getByRole('button', { name: 'Confirm demo reset' }))
  expect(await screen.findByText('Setup complete')).not.toBeNull()
  expect(initialize).toHaveBeenCalledWith(true, true)
})
