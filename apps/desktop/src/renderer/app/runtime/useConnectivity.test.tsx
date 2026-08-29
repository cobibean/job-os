import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import { useConnectivity } from './useConnectivity'

afterEach(cleanup)

const connected = {
  state: 'connected' as const,
  apiVersion: '0.1.0',
  checkedAt: '2026-07-20T00:00:00.000Z',
  message: 'Private API authenticated'
}

test('connectivity can fail after launch and recover on focus', async () => {
  const get = vi.fn()
    .mockResolvedValueOnce(connected)
    .mockResolvedValueOnce({
      state: 'disconnected',
      checkedAt: '2026-07-20T00:00:01.000Z',
      message: 'Remote service unavailable'
    })
    .mockResolvedValueOnce(connected)
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectivity: { get } }
  })

  const { result, unmount } = renderHook(() => useConnectivity(60_000))
  await waitFor(() => expect(result.current.state).toBe('connected'))
  act(() => window.dispatchEvent(new Event('focus')))
  await waitFor(() => expect(result.current.state).toBe('disconnected'))
  act(() => window.dispatchEvent(new Event('focus')))
  await waitFor(() => expect(result.current.state).toBe('connected'))
  unmount()
  expect(get).toHaveBeenCalledTimes(3)
})

test('recovery events do not overlap an active probe and listeners are cleaned up', async () => {
  let resolveFirst: ((value: typeof connected) => void) | undefined
  const get = vi.fn()
    .mockImplementationOnce(() => new Promise<typeof connected>(resolve => { resolveFirst = resolve }))
    .mockResolvedValue(connected)
  Object.defineProperty(window, 'jobos', {
    configurable: true,
    value: { connectivity: { get } }
  })

  const { unmount } = renderHook(() => useConnectivity(60_000))
  act(() => window.dispatchEvent(new Event('focus')))
  expect(get).toHaveBeenCalledOnce()
  await act(async () => resolveFirst?.(connected))
  act(() => window.dispatchEvent(new Event('focus')))
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
  unmount()
  act(() => window.dispatchEvent(new Event('focus')))
  expect(get).toHaveBeenCalledTimes(2)
})
