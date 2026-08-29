import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import type { CareerProfileBridge } from '../../../shared/contracts'
import { SettingsPanel } from './SettingsPanel'

afterEach(cleanup)

test('shows the selected agent avatar and reports selection through the settings interface', () => {
  const onSelectAgentAvatar = vi.fn()
  render(
    <SettingsPanel
      activeAgentAvatarId="ninja"
      activeThemeId="graphite"
      mode="dark"
      onClose={() => {}}
      onSelectAgentAvatar={onSelectAgentAvatar}
      onSelectTheme={() => {}}
    />
  )

  const themeSection = screen.getByRole('button', { name: 'Color theme' })
  const avatarSection = screen.getByRole('button', { name: 'Agent icon' })
  const diagnosticsSection = screen.getByRole('button', { name: 'Diagnostics' })
  expect(themeSection.getAttribute('aria-expanded')).toBe('false')
  expect(avatarSection.getAttribute('aria-expanded')).toBe('false')
  expect(diagnosticsSection.getAttribute('aria-expanded')).toBe('false')

  fireEvent.click(avatarSection)
  expect(avatarSection.getAttribute('aria-expanded')).toBe('true')

  const ninja = screen.getByRole('radio', { name: /Ninja/ })
  expect(ninja.getAttribute('aria-checked')).toBe('true')
  expect(ninja.querySelector('[data-agent-avatar-id="ninja"]')).not.toBeNull()

  const avatarPicker = screen.getByRole('radiogroup', { name: /Agent icon/ })
  expect(within(avatarPicker).getAllByRole('radio')).toHaveLength(11)
  expect(within(avatarPicker).getByRole('radio', { name: /Starlight/ })).not.toBeNull()

  fireEvent.click(ninja)
  expect(onSelectAgentAvatar).toHaveBeenCalledWith('ninja')
})

test('explains agent edit modes, changes trust as the user, and disconnects without deleting profile data', async () => {
  const agent = {
    active: true,
    agentId: 'job-hunter',
    connectedAt: '2026-08-21T15:00:00Z',
    disconnectedAt: null,
    displayName: 'Job Hunter',
    principal: 'agent:job-hunter',
    trustMode: 'review' as const,
    updatedAt: '2026-08-21T15:00:00Z'
  }
  const updateConnectedAgentTrustMode = vi.fn().mockResolvedValue({ ...agent, trustMode: 'direct' })
  const disconnectConnectedAgent = vi.fn().mockResolvedValue({
    ...agent,
    active: false,
    disconnectedAt: '2026-08-21T16:00:00Z'
  })
  const careerProfileBridge = {
    listConnectedAgents: vi.fn().mockResolvedValue([agent]),
    updateConnectedAgentTrustMode,
    disconnectConnectedAgent
  } as unknown as CareerProfileBridge

  render(
    <SettingsPanel
      activeAgentAvatarId="ninja"
      activeThemeId="graphite"
      careerProfileBridge={careerProfileBridge}
      mode="dark"
      onClose={() => {}}
      onSelectAgentAvatar={() => {}}
      onSelectTheme={() => {}}
    />
  )

  fireEvent.click(screen.getByRole('button', { name: 'Agent editing' }))
  const review = await screen.findByRole('radio', { name: /Review every change/i })
  expect(review.getAttribute('aria-checked')).toBe('true')
  expect(screen.getByText(/ordinary edits happen right away/i)).not.toBeNull()

  fireEvent.click(screen.getByRole('radio', { name: /Allow direct edits/i }))
  await waitFor(() => expect(updateConnectedAgentTrustMode).toHaveBeenCalledWith('job-hunter', 'direct'))

  fireEvent.click(screen.getByRole('button', { name: 'Disconnect Job Hunter' }))
  expect(screen.getByText(/Your Career Profile stays exactly as it is/i)).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm remove access Job Hunter' }))
  await waitFor(() => expect(disconnectConnectedAgent).toHaveBeenCalledWith('job-hunter'))
  await waitFor(() => expect(screen.queryByText('Job Hunter')).toBeNull())
})
