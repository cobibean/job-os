import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, expect, test, vi } from 'vitest'

import type { AgentConversationSnapshot, AgentSessionStreamUpdate } from '../../shared/contracts'
import { readFileSync } from 'node:fs'
import { App } from './App'
import type { PrepareClose } from './CloseSafetyContext'

const docx = vi.hoisted(() => ({ prepare: vi.fn(async () => true) }))
vi.mock('../documents/artifacts/DocumentWorkspace', () => ({
  DocumentWorkspace: ({ onOpenEditor }: { onOpenEditor: (document: unknown) => void }) => (
    <button onClick={() => onOpenEditor({})} type="button">Edit synthetic document</button>
  )
}))
vi.mock('../documents/docx/editor/DocxDocumentEditorShell', () => ({
  DocxDocumentEditorShell: ({ onPrepareClose }: { onPrepareClose: (handler: () => Promise<boolean>) => void }) => {
    useEffect(() => { onPrepareClose(docx.prepare) }, [onPrepareClose])
    return <p>Synthetic DOCX editor</p>
  }
}))

afterEach(() => { cleanup(); window.localStorage.clear(); docx.prepare.mockReset().mockResolvedValue(true); vi.restoreAllMocks() })

function install(count = 1) {
  let prepare!: PrepareClose
  let emit: (update: AgentSessionStreamUpdate) => void = () => undefined
  const snapshots: AgentConversationSnapshot[] = Array.from({ length: count }, (_, index) => ({
    conversationId: `conv-${index + 1}`, position: index + 1, title: `Session ${index + 1}`, createdAt: '',
    entries: [], activeTurn: null, connection: 'online', recoveryState: 'ready', latestEventId: 0,
    jobContext: { selectedJobId: null, activeArtifactId: null, activeArtifactPage: 1, activeArtifactZoom: 1 },
    binding: { connectedAgentId: 'agent-codex', provider: 'codex', modelId: 'synthetic', reasoningEffort: 'medium' },
    availability: { state: 'ready', reason: null }
  }))
  const subscribePrepareClose = vi.fn((handler: PrepareClose) => { prepare = handler; return () => undefined })
  const agent = {
    list: vi.fn().mockResolvedValue(snapshots), get: vi.fn((id: string) => Promise.resolve(snapshots.find(item => item.conversationId === id))),
    subscribe: vi.fn((listener: typeof emit) => { emit = listener; return () => undefined }),
    send: vi.fn().mockResolvedValue({ turnId: 'turn-1', status: 'running' }), archive: vi.fn(), cancel: vi.fn(), retry: vi.fn()
  }
  const setBounds = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    agent, lifecycle: { subscribePrepareClose },
    connectivity: { get: vi.fn().mockResolvedValue({ state: 'connected', checkedAt: '', message: 'Connected' }) }
  } })
  return { agent, setBounds, subscribePrepareClose, prepare: (...args: Parameters<PrepareClose>) => prepare(...args), emit: (update: AgentSessionStreamUpdate) => emit(update) }
}

test.each(['window-close', 'profile-switch'] as const)('%s cancellation preserves drafts in every chat and keeps the single close responder', async reason => {
  const actions = install(2)
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'First draft' } })
  fireEvent.click(screen.getByRole('tab', { name: 'Session 2, Idle' }))
  fireEvent.change(composer, { target: { value: 'Second draft' } })
  let closing!: Promise<boolean>
  act(() => { closing = actions.prepare(reason) })
  await screen.findByRole('dialog', { name: 'Discard unsent chat messages?' })
  expect(screen.getByText('2 chats have unsent messages. They are not saved when JobOS closes or switches profiles.')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Keep working' }))
  await expect(closing).resolves.toBe(false)
  expect(composer.value).toBe('Second draft')
  fireEvent.click(screen.getByRole('tab', { name: 'Session 1, Idle' }))
  expect(composer.value).toBe('First draft')
  expect(actions.subscribePrepareClose).toHaveBeenCalledOnce()
  expect(actions.agent.archive).not.toHaveBeenCalled()
})

test('confirmed close approves exit without clearing drafts if the caller cannot finish closing', async () => {
  const actions = install()
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'Keep unless the app actually exits' } })
  let closing!: Promise<boolean>
  act(() => { closing = actions.prepare('window-close') })
  fireEvent.click(await screen.findByRole('button', { name: 'Discard messages and continue' }))
  await expect(closing).resolves.toBe(true)
  expect(composer.value).toBe('Keep unless the app actually exits')
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Discard unsent chat messages?' })).toBeNull())
})

test('DOCX close safety blocks before any draft confirmation and is checked again after consent', async () => {
  const actions = install()
  render(<App />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'Message the agent' }), { target: { value: 'Chat draft while editing' } })
  fireEvent.click(screen.getByRole('button', { name: 'Edit synthetic document' }))
  await screen.findByText('Synthetic DOCX editor')
  docx.prepare.mockResolvedValue(false)
  await act(async () => { expect(await actions.prepare('window-close')).toBe(false) })
  expect(screen.queryByRole('dialog', { name: 'Discard unsent chat messages?' })).toBeNull()
  docx.prepare.mockResolvedValueOnce(true).mockResolvedValue(false)
  let closing!: Promise<boolean>
  act(() => { closing = actions.prepare('window-close') })
  fireEvent.click(await screen.findByRole('button', { name: 'Discard messages and continue' }))
  await expect(closing).resolves.toBe(false)
  expect(docx.prepare).toHaveBeenCalledTimes(3)
})

test('a pending send disables draft discard, then cancellation retains a newer draft', async () => {
  const actions = install()
  let resolveSend!: (value: unknown) => void
  actions.agent.send.mockReturnValue(new Promise(resolve => { resolveSend = resolve }))
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'Submitted message' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(actions.agent.send).toHaveBeenCalledOnce())
  fireEvent.change(composer, { target: { value: 'Newer unsent draft' } })
  let closing!: Promise<boolean>
  act(() => { closing = actions.prepare('window-close') })
  const discard = await screen.findByRole('button', { name: 'Discard messages and continue' }) as HTMLButtonElement
  expect(discard.disabled).toBe(true)
  await act(async () => resolveSend({ turnId: 'turn-1', status: 'running' }))
  expect(discard.disabled).toBe(false)
  fireEvent.keyDown(document, { key: 'Escape' })
  await expect(closing).resolves.toBe(false)
  expect(composer.value).toBe('Newer unsent draft')
})

test('profile switching rejects active work before offering draft discard', async () => {
  const actions = install()
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'Submit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
  await waitFor(() => expect(screen.getByLabelText('Agent turn status')).not.toBeNull())
  fireEvent.change(composer, { target: { value: 'Unsent next instruction' } })
  await act(async () => { expect(await actions.prepare('profile-switch')).toBe(false) })
  expect(screen.queryByRole('dialog', { name: 'Discard unsent chat messages?' })).toBeNull()
  expect(composer.value).toBe('Unsent next instruction')
})

test('a pending archive confirmation cancels app close without stacking another discard prompt', async () => {
  const actions = install()
  render(<App />)
  fireEvent.change(await screen.findByRole('textbox', { name: 'Message the agent' }), { target: { value: 'Archive draft' } })
  fireEvent.click(screen.getByRole('button', { name: 'Close Session 1' }))
  await screen.findByRole('dialog', { name: 'Discard unsent message?' })
  await act(async () => { expect(await actions.prepare('window-close')).toBe(false) })
  expect(screen.getAllByRole('dialog')).toHaveLength(1)
})

function requestContext() {
  let cancel: () => void = () => undefined
  const request = {
    setPromptPending: vi.fn(),
    onCancelled: vi.fn((callback: () => void) => { cancel = callback; return () => { cancel = () => undefined } })
  }
  return { request, cancel: () => cancel() }
}

function inertAncestor(element: Element | null): boolean {
  for (let current = element; current; current = current.parentElement) {
    if ((current as HTMLElement).inert) return true
  }
  return false
}

test('request cancellation removes a waiting prompt and rejects a late DOCX result', async () => {
  const actions = install()
  const context = requestContext()
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  fireEvent.change(composer, { target: { value: 'Never discard on timeout' } })
  let closing!: Promise<boolean>
  act(() => { closing = actions.prepare('window-close', context.request) })
  await screen.findByRole('dialog', { name: 'Discard unsent chat messages?' })
  expect(context.request.setPromptPending).toHaveBeenCalledWith(true)
  act(() => context.cancel())
  await expect(closing).resolves.toBe(false)
  expect(composer.value).toBe('Never discard on timeout')
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Edit synthetic document' }))
  let finishDocumentCheck!: (safe: boolean) => void
  docx.prepare.mockReturnValueOnce(new Promise(resolve => { finishDocumentCheck = resolve }))
  act(() => { closing = actions.prepare('window-close', context.request) })
  act(() => context.cancel())
  await act(async () => finishDocumentCheck(true))
  await expect(closing).resolves.toBe(false)
  expect(screen.queryByRole('dialog')).toBeNull()
})

test.each([true, false])('the actual profile switch overlay keeps draft consent visible and unlocks on failure, initial draft %s', async withDraft => {
  const actions = install()
  const context = requestContext()
  let releaseSwitch!: () => void
  const activate = vi.fn(async () => {
    if (!await actions.prepare('profile-switch', context.request)) throw new Error('Switch cancelled')
    await new Promise<void>(resolve => { releaseSwitch = resolve })
    context.cancel()
    throw new Error('Synthetic profile activation failed')
  })
  Object.assign(window.jobos, { installationProfiles: {
    list: vi.fn().mockResolvedValue({ activeProfileId: 'old', registryRevision: 1, profiles: [
      { profileId: 'old', displayName: 'Personal', active: true, createdAt: '', updatedAt: '' },
      { profileId: 'new', displayName: 'Synthetic alternate', active: false, createdAt: '', updatedAt: '' }
    ] }), activate
  } })
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' }) as HTMLTextAreaElement
  if (withDraft) fireEvent.change(composer, { target: { value: 'Retained if switch fails' } })
  fireEvent.click(screen.getByRole('button', { name: 'Personal' }))
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Synthetic alternate' }))
  fireEvent.click(screen.getByRole('button', { name: 'Switch profile' }))
  const dialog = await screen.findByRole('dialog', { name: withDraft ? 'Discard unsent chat messages?' : 'Switching profiles…' })
  expect(dialog.closest('.app-close-safety-layer')).not.toBeNull()
  expect(inertAncestor(document.querySelector('.app-shell'))).toBe(true)
  const appCss = readFileSync(`${process.cwd()}/src/renderer/app/styles/app-shell.css`, 'utf8')
  const profileCss = readFileSync(`${process.cwd()}/src/renderer/installation-profiles/installation-profiles.css`, 'utf8')
  const guardZ = Number(appCss.match(/\.app-close-safety-layer\s*\{\s*z-index:\s*(\d+)/)?.[1])
  const profileZ = Number(profileCss.match(/\.profile-switching-overlay\s*\{[^}]*z-index:\s*(\d+)/)?.[1])
  expect(guardZ).toBeGreaterThan(profileZ)
  if (withDraft) fireEvent.click(screen.getByRole('button', { name: 'Discard messages and continue' }))
  await waitFor(() => expect(releaseSwitch).toBeTypeOf('function'))
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(dialog.isConnected).toBe(true)
  expect(inertAncestor(document.querySelector('.app-shell'))).toBe(true)
  await act(async () => releaseSwitch())
  await screen.findByText('Synthetic profile activation failed')
  expect(screen.queryByRole('dialog', { name: 'Discard unsent chat messages?' })).toBeNull()
  expect(screen.queryByRole('dialog', { name: 'Switching profiles…' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
  expect(composer.value).toBe(withDraft ? 'Retained if switch fails' : '')
  expect(inertAncestor(document.querySelector('.app-shell'))).toBe(false)
  fireEvent.change(composer, { target: { value: 'Continue after failed switch' } })
  expect(composer.value).toBe('Continue after failed switch')
})

test.each(['cancel', 'detach-error'] as const)('native browser detach must finish before confirmation and restores on %s', async outcome => {
  const actions = install()
  const setBounds = vi.fn().mockResolvedValue(undefined)
  Object.assign(window.jobos, { browser: {
    setBounds, subscribe: vi.fn(() => () => undefined),
    restore: vi.fn().mockResolvedValue({ tabs: [], activeTabId: null, download: null, notice: null })
  } })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 500, height: 400, x: 1, y: 2, top: 2, left: 1, right: 501, bottom: 402, toJSON: () => ({}) })
  render(<App />)
  const composer = await screen.findByRole('textbox', { name: 'Message the agent' })
  fireEvent.change(composer, { target: { value: 'Keep with browser open' } })
  fireEvent.click(screen.getByRole('button', { name: 'Research' }))
  await waitFor(() => expect(setBounds.mock.calls.some(([bounds]) => bounds.visible)).toBe(true))
  let finishDetach!: () => void
  if (outcome === 'cancel') setBounds.mockReturnValueOnce(new Promise<void>(resolve => { finishDetach = resolve }))
  else setBounds.mockRejectedValueOnce(new Error('Native view detach failed'))
  let closing!: Promise<boolean>
  act(() => { closing = actions.prepare('window-close') })
  if (outcome === 'cancel') {
    expect(screen.queryByRole('dialog', { name: 'Discard unsent chat messages?' })).toBeNull()
    await act(async () => finishDetach())
    await screen.findByRole('dialog', { name: 'Discard unsent chat messages?' })
    fireEvent.click(document.querySelector<HTMLButtonElement>('.career-product-backdrop')!)
  } else await screen.findByText('JobOS could not prepare the close confirmation. Your chat drafts are still here.')
  await expect(closing).resolves.toBe(false)
  await waitFor(() => expect(setBounds.mock.calls.at(-1)?.[0].visible).toBe(true))
  expect((composer as HTMLTextAreaElement).value).toBe('Keep with browser open')
})
