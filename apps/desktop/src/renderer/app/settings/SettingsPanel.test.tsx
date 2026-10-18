import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { useState } from 'react'

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

function ModalHarness() {
  const [open, setOpen] = useState(false)
  return <><button onClick={() => setOpen(true)}>Open settings</button>{open ? <SettingsPanel activeAgentAvatarId="ninja" activeThemeId="graphite" mode="dark" onClose={() => setOpen(false)} onSelectAgentAvatar={() => {}} onSelectTheme={() => {}} /> : null}</>
}

test('settings confines keyboard focus, cancels with Escape, and restores the opener', async () => {
  render(<ModalHarness />)
  const opener = screen.getByRole('button', { name: 'Open settings' })
  opener.focus()
  fireEvent.click(opener)
  const dialog = screen.getByRole('dialog', { name: 'Settings' })
  const controls = within(dialog).getAllByRole('button')
  const first = controls[0]!
  const last = controls.at(-1)!
  expect(document.activeElement).toBe(first)
  first.focus()
  expect(fireEvent.keyDown(first, { key: 'Tab', shiftKey: true })).toBe(false)
  expect(document.activeElement).toBe(last)
  expect(fireEvent.keyDown(last, { key: 'Tab' })).toBe(false)
  expect(document.activeElement).toBe(first)
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
  await waitFor(() => expect(document.activeElement).toBe(opener))
})

test('settings makes background siblings inert while preserving previously inert content', () => {
  const preexisting = document.createElement('div')
  preexisting.inert = true
  document.body.append(preexisting)
  try {
    render(<ModalHarness />)
    const opener = screen.getByRole('button', { name: 'Open settings' })
    fireEvent.click(opener)
    expect(opener.inert).toBe(true)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(opener.inert).toBe(false)
    expect(preexisting.inert).toBe(true)
  } finally { preexisting.remove() }
})
