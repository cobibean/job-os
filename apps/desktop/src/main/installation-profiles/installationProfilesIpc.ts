import type { IpcMain, IpcMainEvent, IpcMainInvokeEvent } from 'electron'

import type { InstallationProfileListSnapshot } from '../../shared/contracts.js'
import type { createInstallationProfilesClient } from './installationProfiles.js'

type InstallationProfilesClient = ReturnType<typeof createInstallationProfilesClient>
type ProfileSwitchTarget = {
  profileId: string
  expectedRegistryRevision: number
  activationIdempotencyKey: string
} | {
  displayName: string
  creationIdempotencyKey: string
}

export function installationProfileSnapshot(value: Awaited<ReturnType<InstallationProfilesClient['list']>>): InstallationProfileListSnapshot {
  return {
    registryRevision: value.registry_revision,
    activeProfileId: value.active_profile_id,
    profiles: value.profiles.map(profile => ({
      profileId: profile.profile_id,
      displayName: profile.display_name,
      active: profile.active,
      createdAt: profile.created_at,
      updatedAt: profile.updated_at
    }))
  }
}

export function registerInstallationProfilesIpc(
  ipc: Pick<IpcMain, 'handle' | 'on'>,
  assertTrustedRenderer: (event: IpcMainInvokeEvent | IpcMainEvent) => void,
  dependencies: {
    getExpectedProfileId: () => string | null
    getClient: () => InstallationProfilesClient
    completeSwitch: (target: ProfileSwitchTarget) => Promise<void>
    restart: () => void
  }
): void {
  ipc.on('jobos:installation-profiles:expected-id', event => {
    assertTrustedRenderer(event)
    event.returnValue = dependencies.getExpectedProfileId()
  })
  ipc.handle('jobos:installation-profiles:list', async event => {
    assertTrustedRenderer(event)
    return installationProfileSnapshot(await dependencies.getClient().list())
  })
  ipc.handle('jobos:installation-profiles:rename', async (event, profileId, displayName, expectedRevision, idempotencyKey) => {
    assertTrustedRenderer(event)
    return installationProfileSnapshot(await dependencies.getClient().rename(profileId, displayName, expectedRevision, idempotencyKey))
  })
  ipc.handle('jobos:installation-profiles:activate', async (event, profileId, expectedRevision, idempotencyKey) => {
    assertTrustedRenderer(event)
    await dependencies.completeSwitch({ profileId, expectedRegistryRevision: expectedRevision, activationIdempotencyKey: idempotencyKey })
  })
  ipc.handle('jobos:installation-profiles:create-and-switch', async (event, displayName, idempotencyKey) => {
    assertTrustedRenderer(event)
    await dependencies.completeSwitch({ displayName, creationIdempotencyKey: idempotencyKey })
  })
  ipc.handle('jobos:installation-profiles:restart', event => {
    assertTrustedRenderer(event)
    dependencies.restart()
  })
}
