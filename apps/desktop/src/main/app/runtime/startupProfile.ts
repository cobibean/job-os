import { createInstallationProfilesClient, resolveProfileStorageIdentity } from '../../installation-profiles/installationProfiles.js'
import type { DesktopProfileStorageIdentity } from '../../installation-profiles/profileStorage.js'
import type { DesktopRuntimeState } from './desktopRuntime.js'

export async function resolveStartupProfile(
  state: DesktopRuntimeState,
  createClient: (config: Parameters<typeof createInstallationProfilesClient>[0]) => Pick<ReturnType<typeof createInstallationProfilesClient>, 'list'> = createInstallationProfilesClient
): Promise<DesktopProfileStorageIdentity> {
  const { runtime, deviceToken, connectivity } = state
  const profileId = connectivity.installationProfileId
  if (!runtime || !deviceToken || connectivity.state !== 'connected' || !profileId) return { kind: 'recovery' }
  try {
    const profiles = await createClient({ baseUrl: runtime.apiBaseUrl, deviceToken, installationProfileId: profileId }).list()
    return resolveProfileStorageIdentity(profiles, profileId)
  } catch {
    return { kind: 'recovery' }
  }
}
