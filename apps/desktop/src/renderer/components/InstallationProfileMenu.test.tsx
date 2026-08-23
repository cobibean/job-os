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

test('waits for the native browser to detach and restores it after the menu closes', async () => {
  let finishPreparing: ((ready: boolean) => void) | undefined
  const prepareOverlay = vi.fn(() => new Promise<boolean>(resolve => { finishPreparing = resolve }))
  const onOverlayClose = vi.fn()
  render(
    <InstallationProfileMenu
      activeProfileName="Personal"
      onOverlayClose={onOverlayClose}
      prepareOverlay={prepareOverlay}
    />
  )

  fireEvent.click(await screen.findByRole('button', { name: /Personal/ }))
  expect(prepareOverlay).toHaveBeenCalledOnce()
  expect(screen.queryByRole('menu')).toBeNull()

  finishPreparing?.(true)
  expect(await screen.findByRole('menu')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Personal/ }))
  await waitFor(() => expect(onOverlayClose).toHaveBeenCalledOnce())
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

test('presents failed switch rollback without Electron IPC internals', async () => {
  window.jobos!.installationProfiles.activate = vi.fn(async () => {
    throw new Error(
      "Error invoking remote method 'jobos:installation-profiles:activate': Error: JobOS stayed in the previous profile; no workspace data was changed."
    )
  })
  render(<InstallationProfileMenu activeProfileName="Personal" />)
  fireEvent.click(await screen.findByRole('button', { name: /Personal/ }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Fresh setup' }))
  fireEvent.click(screen.getByRole('button', { name: 'Switch profile' }))

  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toBe('Couldn’t open “Fresh setup”. JobOS returned to Personal.')
  expect(alert.textContent).not.toContain('remote method')
})

test('does not claim rollback when the active profile is uncertain', async () => {
  window.jobos!.installationProfiles.activate = vi.fn(async () => {
    throw new Error(
      "Error invoking remote method 'jobos:installation-profiles:activate': Error: JobOS did not open the requested profile."
    )
  })
  render(<InstallationProfileMenu activeProfileName="Personal" />)
  fireEvent.click(await screen.findByRole('button', { name: /Personal/ }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Fresh setup' }))
  fireEvent.click(screen.getByRole('button', { name: 'Switch profile' }))

  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toBe('Couldn’t confirm “Fresh setup” opened. Check the active profile before retrying.')
  expect(alert.textContent).not.toContain('returned to Personal')
})

test('uses the latest renamed active profile in failed-switch rollback copy', async () => {
  const renamedProfiles = {
    ...profiles,
    registryRevision: 3,
    profiles: profiles.profiles.map(profile => profile.active
      ? { ...profile, displayName: 'Primary search' }
      : profile)
  }
  window.jobos!.installationProfiles.rename = vi.fn(async () => renamedProfiles)
  window.jobos!.installationProfiles.activate = vi.fn(async () => {
    throw new Error('JobOS stayed in the previous profile; no workspace data was changed.')
  })
  render(<InstallationProfileMenu activeProfileName="Personal" />)

  fireEvent.click(await screen.findByRole('button', { name: /Personal/ }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Rename current profile…' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'Name' }), {
    target: { value: 'Primary search' }
  })
  fireEvent.click(screen.getByRole('button', { name: 'Rename profile' }))
  await screen.findByRole('button', { name: /Primary search/ })

  fireEvent.click(screen.getByRole('button', { name: /Primary search/ }))
  fireEvent.click(screen.getByRole('menuitem', { name: 'Fresh setup' }))
  fireEvent.click(screen.getByRole('button', { name: 'Switch profile' }))

  expect((await screen.findByRole('alert')).textContent).toBe(
    'Couldn’t open “Fresh setup”. JobOS returned to Primary search.'
  )
})
