import { Check, ChevronDown } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { InstallationProfileListSnapshot, InstallationProfileSummary } from '../../shared/contracts'
import { useModalDialog } from '../app/dialogs/useModalDialog'

interface Props {
  activeProfileName: string
  onOverlayClose?: () => void
  prepareOverlay?: () => Promise<boolean>
}

type DialogState =
  | { kind: 'create'; name: string }
  | { kind: 'rename'; name: string }
  | { kind: 'switch'; profile: InstallationProfileSummary }
  | null

function idempotencyKey(prefix: string): string {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`
}

function actionErrorMessage(failure: unknown, fallback: string): string {
  if (!(failure instanceof Error)) return fallback
  const message = failure.message.replace(
    /^Error invoking remote method '[^']+':\s*(?:Error:\s*)?/,
    ''
  ).trim()
  return message || fallback
}

export function InstallationProfileMenu({ activeProfileName, onOverlayClose, prepareOverlay }: Props) {
  const [profiles, setProfiles] = useState<InstallationProfileListSnapshot | null>(null)
  const [open, setOpen] = useState(false)
  const [dialog, setDialog] = useState<DialogState>(null)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [switching, setSwitching] = useState<string | null>(null)
  const [overlayPrepared, setOverlayPrepared] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const dialogElement = useRef<HTMLDivElement>(null)
  const switchDialogElement = useRef<HTMLDivElement>(null)
  const pendingCreation = useRef<{ name: string; key: string; previousIds: string[] } | null>(null)
  const currentActiveProfileName = profiles?.profiles.find(profile => profile.active)?.displayName
    ?? activeProfileName

  const load = async () => {
    if (!window.jobos?.installationProfiles) return null
    try {
      const snapshot = await window.jobos.installationProfiles.list()
      setProfiles(snapshot)
      setListError('')
      return snapshot
    } catch (failure) {
      setListError('JobOS Profiles are unavailable.')
      throw failure
    }
  }
  useEffect(() => { void load().catch(() => undefined) }, [])
  useEffect(() => {
    if (open || dialog || switching || !overlayPrepared) return
    setOverlayPrepared(false)
    onOverlayClose?.()
  }, [dialog, onOverlayClose, open, overlayPrepared, switching])

  const openMenu = async () => {
    if (open) return
    if (prepareOverlay && !await prepareOverlay()) return
    setOverlayPrepared(true)
    setOpen(true)
  }

  const closeMenu = (restoreFocus = true) => {
    setOpen(false)
    if (restoreFocus) requestAnimationFrame(() => trigger.current?.focus())
  }
  const closeDialog = () => {
    setDialog(null)
    setError('')
    requestAnimationFrame(() => trigger.current?.focus())
  }
  useModalDialog(dialogElement, closeDialog, { open: dialog?.kind === 'create' || dialog?.kind === 'rename', returnFocusRef: trigger })
  useModalDialog(switchDialogElement, closeDialog, { open: dialog?.kind === 'switch', returnFocusRef: trigger })
  const nameError = dialog && (dialog.kind === 'create' || dialog.kind === 'rename')
    ? !dialog.name.trim()
      ? 'Enter a profile name.'
      : dialog.name.includes('/')
        || dialog.name.includes('\\')
        || /\p{C}/u.test(dialog.name)
        || dialog.name.trim().length > 64
        ? 'Use 1–64 characters without slashes or line breaks.'
        : profiles?.profiles.some(profile => (
          profile.displayName.localeCompare(dialog.name.trim(), undefined, { sensitivity: 'base' }) === 0
          && (dialog.kind === 'create' || !profile.active)
        )) ? 'A JobOS Profile with this name already exists.' : ''
    : ''

  const create = async () => {
    if (!dialog || dialog.kind !== 'create' || nameError || !profiles || !window.jobos?.installationProfiles) return
    const name = dialog.name.trim()
    if (pendingCreation.current?.name !== name) {
      pendingCreation.current = { name, key: idempotencyKey('create-profile'), previousIds: profiles.profiles.map(profile => profile.profileId) }
    }
    const pending = pendingCreation.current
    setSwitching(name)
    setError('')
    try {
      await window.jobos.installationProfiles.createAndSwitch(pending.name, pending.key)
    } catch (failure) {
      setSwitching(null)
      const refreshed = await load().catch(() => null)
      const created = refreshed?.profiles.find(profile => !pending.previousIds.includes(profile.profileId)
        && profile.displayName.localeCompare(pending.name, undefined, { sensitivity: 'base' }) === 0)
      if (created) {
        pendingCreation.current = null
        setDialog({ kind: 'switch', profile: created })
      }
      const message = actionErrorMessage(failure, 'Couldn’t switch profiles.')
      const normalizedMessage = message.replace(/\.$/, '')
      setError(normalizedMessage === 'JobOS stayed in the previous profile; no workspace data was changed'
        ? `“${name}” was created, but JobOS couldn’t open it. JobOS returned to ${currentActiveProfileName}.`
        : normalizedMessage === 'JobOS did not open the requested profile'
          ? `Couldn’t confirm “${name}” opened. Check the active profile before retrying.`
          : message)
    }
  }
  const rename = async () => {
    if (!dialog || dialog.kind !== 'rename' || nameError || !profiles || !window.jobos?.installationProfiles) return
    try {
      const active = profiles.profiles.find(profile => profile.active)
      if (!active) throw new Error('Active JobOS Profile is unavailable')
      const updated = await window.jobos.installationProfiles.rename(
        active.profileId,
        dialog.name.trim(),
        profiles.registryRevision,
        idempotencyKey('rename-profile')
      )
      setProfiles(updated)
      closeDialog()
    } catch (failure) {
      await load().catch(() => undefined)
      setError(actionErrorMessage(failure, 'Profile could not be renamed.'))
    }
  }
  const activate = async () => {
    if (!dialog || dialog.kind !== 'switch' || !profiles || !window.jobos?.installationProfiles) return
    setSwitching(dialog.profile.displayName)
    setError('')
    try {
      await window.jobos.installationProfiles.activate(
        dialog.profile.profileId,
        profiles.registryRevision,
        idempotencyKey('switch-profile')
      )
    } catch (failure) {
      setSwitching(null)
      await load().catch(() => undefined)
      const message = actionErrorMessage(failure, 'Couldn’t switch profiles.')
      const normalizedMessage = message.replace(/\.$/, '')
      setError(normalizedMessage === 'JobOS stayed in the previous profile; no workspace data was changed'
        ? `Couldn’t open “${dialog.profile.displayName}”. JobOS returned to ${currentActiveProfileName}.`
        : normalizedMessage === 'JobOS did not open the requested profile'
          ? `Couldn’t confirm “${dialog.profile.displayName}” opened. Check the active profile before retrying.`
          : message)
    }
  }

  return (
    <div className="installation-profile-control" onKeyDown={event => {
      if (event.key === 'Escape') {
        if (dialog) closeDialog()
        else if (open) closeMenu()
      }
    }}>
      <button
        aria-expanded={open}
        aria-haspopup="menu"
        className="installation-profile-trigger"
        onClick={() => {
          if (open) closeMenu()
          else void openMenu()
        }}
        onKeyDown={event => {
          if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
          event.preventDefault()
          void openMenu().then(() => requestAnimationFrame(() => {
            const items = menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')
            items?.[event.key === 'ArrowDown' ? 0 : items.length - 1]?.focus()
          }))
        }}
        ref={trigger}
        type="button"
      >
        <span>{currentActiveProfileName}</span>
        <ChevronDown aria-hidden="true" size={13} />
      </button>
      {open ? (
        <div
          aria-label="JobOS Profiles"
          className="installation-profile-menu"
          onKeyDown={event => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
            const items = Array.from(
              menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? []
            )
            if (!items.length) return
            event.preventDefault()
            const current = items.indexOf(document.activeElement as HTMLButtonElement)
            const next = event.key === 'Home' ? 0
              : event.key === 'End' ? items.length - 1
                : event.key === 'ArrowDown' ? (current + 1) % items.length
                  : (current - 1 + items.length) % items.length
            items[next]?.focus()
          }}
          ref={menu}
          role="menu"
        >
          {profiles?.profiles.map(profile => (
            <button
              key={profile.profileId}
              onClick={() => {
                if (!profile.active) setDialog({ kind: 'switch', profile })
                closeMenu(profile.active)
              }}
              role="menuitem"
              type="button"
            >
              <span>{profile.displayName}</span>
              {profile.active ? <Check aria-label="Active" size={14} /> : null}
            </button>
          ))}
          {listError ? <><p role="alert">{listError}</p><button onClick={() => { void load().catch(() => undefined) }} role="menuitem" type="button">Retry profiles</button></> : null}
          <div className="installation-profile-separator" role="separator" />
          <button disabled={!profiles} onClick={() => { setDialog({ kind: 'create', name: 'Fresh setup' }); closeMenu(false) }} role="menuitem" type="button">New profile…</button>
          <button disabled={!profiles} onClick={() => { setDialog({ kind: 'rename', name: currentActiveProfileName }); closeMenu(false) }} role="menuitem" type="button">Rename current profile…</button>
        </div>
      ) : null}
      {dialog?.kind === 'create' || dialog?.kind === 'rename' ? (
        <div aria-labelledby="installation-profile-dialog-title" aria-modal="true" className="profile-dialog-backdrop" ref={dialogElement} role="dialog">
          <div className="profile-dialog">
            <h2 id="installation-profile-dialog-title">{dialog.kind === 'create' ? 'New JobOS Profile' : 'Rename JobOS Profile'}</h2>
            {dialog.kind === 'create' ? <p>This starts a blank JobOS workspace. Your agent connections stay available.<br />Jobs, Career Profile data, documents, chats, browser data, and layout do not carry over.</p> : null}
            <label>Name<input autoFocus value={dialog.name} onChange={event => setDialog({ ...dialog, name: event.target.value })} /></label>
            {nameError ? <p className="profile-dialog-error" role="alert">{nameError}</p> : null}
            {error ? <p className="profile-dialog-error" role="alert">{error}</p> : null}
            <div className="profile-dialog-actions">
              <button onClick={closeDialog} type="button">Cancel</button>
              <button disabled={Boolean(nameError)} onClick={() => { void (dialog.kind === 'create' ? create() : rename()) }} type="button">
                {dialog.kind === 'create' ? 'Create and switch' : 'Rename profile'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {dialog?.kind === 'switch' ? (
        <div aria-labelledby="installation-profile-dialog-title" aria-modal="true" className="profile-dialog-backdrop" ref={switchDialogElement} role="alertdialog">
          <div className="profile-dialog">
            <h2 id="installation-profile-dialog-title">Switch to “{dialog.profile.displayName}”?</h2>
            <p>JobOS will save this workspace, restart, and open the selected profile.<br />Your current profile stays unchanged.</p>
            {error ? <p className="profile-dialog-error" role="alert">{error}</p> : null}
            <div className="profile-dialog-actions">
              <button onClick={closeDialog} type="button">Cancel</button>
              <button onClick={() => { void activate() }} type="button">Switch profile</button>
            </div>
          </div>
        </div>
      ) : null}
      {switching ? <div className="profile-switching-overlay" role="status"><strong>Switching to “{switching}”…</strong><span>JobOS will reopen in this profile.</span></div> : null}
    </div>
  )
}
