import { cleanup, render, screen } from '@testing-library/react'
import { useContext, useLayoutEffect } from 'react'
import { afterEach, expect, test, vi } from 'vitest'

import { CloseSafetyContext, type PrepareClose } from './CloseSafetyContext'
import { App } from './App'

const workbench = vi.hoisted(() => ({ register: true, prepare: vi.fn() }))
vi.mock('./WorkbenchApp', () => ({ WorkbenchApp: () => {
  const register = useContext(CloseSafetyContext)
  useLayoutEffect(() => workbench.register ? register?.(workbench.prepare) : undefined, [register])
  return <main>Safety test workbench</main>
} }))

afterEach(() => { cleanup(); vi.clearAllMocks(); workbench.register = true })

async function ready() {
  let prepare!: PrepareClose
  const subscribe = vi.fn((handler: PrepareClose) => { prepare = handler; return () => undefined })
  Object.defineProperty(window, 'jobos', { configurable: true, value: {
    setup: { get: vi.fn().mockResolvedValue({ state: 'ready', message: 'Synthetic ready' }) },
    lifecycle: { subscribePrepareClose: subscribe }
  } })
  render(<App />)
  await screen.findByText('Safety test workbench')
  return { prepare, subscribe }
}

test('the root registers one responder and preserves document and profile-switch decisions', async () => {
  workbench.prepare.mockResolvedValue(false)
  const { prepare, subscribe } = await ready()
  expect(subscribe).toHaveBeenCalledOnce()
  await expect(prepare('window-close')).resolves.toBe(false)
  expect(workbench.prepare).toHaveBeenLastCalledWith('window-close', undefined)
  workbench.prepare.mockResolvedValue(true)
  const request = { setPromptPending: vi.fn(), onCancelled: vi.fn(() => () => undefined) }
  await expect(prepare('profile-switch', request)).resolves.toBe(true)
  expect(workbench.prepare).toHaveBeenLastCalledWith('profile-switch', request)
  workbench.prepare.mockRejectedValue(new Error('Synthetic renderer error'))
  await expect(prepare('window-close')).resolves.toBe(false)
})

test('a ready workbench without a document safety responder fails closed', async () => {
  workbench.register = false
  const { prepare } = await ready()
  await expect(prepare('window-close')).resolves.toBe(false)
})
