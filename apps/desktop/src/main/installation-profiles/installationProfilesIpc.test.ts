import type { IpcMain, IpcMainEvent, IpcMainInvokeEvent } from 'electron'
import { expect, test, vi } from 'vitest'

import { registerInstallationProfilesIpc } from './installationProfilesIpc.js'

test('preserves installation-profile registration kinds and switch forwarding', async () => {
  const handlers = new Map<string, (...arguments_: never[]) => unknown>()
  const listeners = new Map<string, (...arguments_: never[]) => unknown>()
  const ipc = {
    handle: (channel: string, handler: (...arguments_: never[]) => unknown) => handlers.set(channel, handler),
    on: (channel: string, listener: (...arguments_: never[]) => unknown) => { listeners.set(channel, listener); return ipc }
  } as unknown as Pick<IpcMain, 'handle' | 'on'>
  const completeSwitch = vi.fn(async () => undefined)
  const restart = vi.fn()
  const assertTrusted = vi.fn()
  registerInstallationProfilesIpc(ipc, assertTrusted, {
    getExpectedProfileId: () => 'profile-1',
    getClient: () => ({ list: vi.fn(), rename: vi.fn() }) as never,
    completeSwitch,
    restart
  })
  expect([...listeners.keys()]).toEqual(['jobos:installation-profiles:expected-id'])
  expect([...handlers.keys()]).toEqual([
    'jobos:installation-profiles:list', 'jobos:installation-profiles:rename',
    'jobos:installation-profiles:activate', 'jobos:installation-profiles:create-and-switch',
    'jobos:installation-profiles:restart'
  ])
  const syncEvent = { returnValue: undefined } as unknown as IpcMainEvent
  listeners.get('jobos:installation-profiles:expected-id')?.(syncEvent)
  expect(syncEvent.returnValue).toBe('profile-1')
  const event = {} as IpcMainInvokeEvent
  await handlers.get('jobos:installation-profiles:activate')?.(event, 'profile-2', 4, 'activate-key')
  expect(completeSwitch).toHaveBeenCalledWith({ profileId: 'profile-2', expectedRegistryRevision: 4, activationIdempotencyKey: 'activate-key' })
  handlers.get('jobos:installation-profiles:restart')?.(event)
  expect(restart).toHaveBeenCalledOnce()
})
