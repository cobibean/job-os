import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

import { InstallationProfileMenu } from './InstallationProfileMenu'

const profiles = {
  registryRevision: 2,
  activeProfileId: 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  profiles: [
    { profileId: 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', displayName: 'Personal', active: true, createdAt: '2026-08-23T10:00:00Z', updatedAt: '2026-08-23T10:00:00Z' },
    { profileId: 'jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', displayName: 'Fresh setup', active: false, createdAt: '2026-08-23T11:00:00Z', updatedAt: '2026-08-23T11:00:00Z' }
  ]
}

beforeEach(() => {
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    installationProfiles: {
      list: vi.fn(async () => profiles),
      activate: vi.fn(async () => undefined),
      createAndSwitch: vi.fn(async () => new Promise<void>(() => undefined)),
      rename: vi.fn(async () => profiles),
      restart: vi.fn()
    }
  } })
})
afterEach(cleanup)

test('shows the active check, keyboard escape, and switch confirmation', async () => {
  render(<InstallationProfileMenu activeProfileName="Personal" />)
  const trigger = await screen.findByRole('button', { name: /Personal/ })
  fireEvent.keyDown(trigger, { key: 'ArrowDown' })
  await waitFor(() => expect(document.activeElement?.textContent).toContain('Personal'))
  expect(screen.getByLabelText('Active')).toBeTruthy()
  fireEvent.keyDown(document.activeElement as Element, { key: 'ArrowDown' })
  expect(document.activeElement?.textContent).toContain('Fresh setup')
  fireEvent.click(screen.getByRole('menuitem', { name: 'Fresh setup' }))
  expect(screen.getByRole('alertdialog').textContent).toContain('Switch to “Fresh setup”?')
  fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' })
  expect(screen.queryByRole('alertdialog')).toBeNull()
})

test('validates duplicate names and presents one create-and-switch action', async () => {
  render(<InstallationProfileMenu activeProfileName="Personal" />)
  fireEvent.click(await screen.findByRole('button', { name: /Personal/ }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'New profile…' }))
  const input = screen.getByRole('textbox', { name: 'Name' })
  fireEvent.change(input, { target: { value: 'personal' } })
  expect(screen.getByRole('alert').textContent).toContain('already exists')
  fireEvent.change(input, { target: { value: 'Clean audit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create and switch' }))
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Switching to “Clean audit”…'))
})
