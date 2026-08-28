import type { BrowserBounds, BrowserState } from '../../shared/contracts.js'
import type { DesktopRuntimeState } from '../app/runtime/desktopRuntime.js'
import type { ApiLifecycle } from '../app/runtime/apiLifecycle.js'
import type { createSourceApiProcess } from '../app/runtime/sourceApiProcess.js'
import { probeConnectivity } from '../app/runtime/connectivity.js'
import {
  assertProfileSwitchDownloadSafe,
  prepareAndActivateDesktopProfileSwitch,
  prepareDesktopProfileSwitch,
  rollbackSourceProfileRuntime,
  type createInstallationProfilesClient
} from './installationProfiles.js'

type ProfilesClient = ReturnType<typeof createInstallationProfilesClient>
type SourceApiProcess = ReturnType<typeof createSourceApiProcess>
export interface ProfileSwitchBrowserAccess {
  getBounds: () => BrowserBounds
  getState: () => BrowserState
  setBounds: (bounds: BrowserBounds) => void
  setDownloadsAllowed: (allowed: boolean) => void
}
export type ProfileSwitchTarget = {
  profileId: string
  expectedRegistryRevision: number
  activationIdempotencyKey: string
} | {
  displayName: string
  creationIdempotencyKey: string
}

export function createProfileSwitchCoordinator(dependencies: {
  getBrowserManager: () => ProfileSwitchBrowserAccess | null
  requestWorkspaceSafety: () => Promise<boolean>
  getClient: () => ProfilesClient
  getRuntimeState: () => DesktopRuntimeState
  apiLifecycle: ApiLifecycle
  sourceApi: Pick<SourceApiProcess, 'stop' | 'rollbackProfileSwitch'>
  probe?: typeof probeConnectivity
  now?: () => number
  sleep?: (milliseconds: number) => Promise<void>
  relaunchAndQuit: () => void
}) {
  let inProgress = false
  const probe = dependencies.probe ?? probeConnectivity
  const now = dependencies.now ?? Date.now
  const sleep = dependencies.sleep ?? (milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds)))

  return {
    isInProgress: () => inProgress,
    async complete(target: ProfileSwitchTarget): Promise<void> {
      if (inProgress) throw new Error('A JobOS Profile switch is already in progress')
      inProgress = true
      const browserManager = dependencies.getBrowserManager()
      const previousBrowserBounds = browserManager?.getBounds()
      let switchCompleted = false
      browserManager?.setDownloadsAllowed(false)
      try {
        const client = dependencies.getClient()
        const resolved = await prepareAndActivateDesktopProfileSwitch({
          prepare: () => prepareDesktopProfileSwitch({
            assertDownloadSafe: () => assertProfileSwitchDownloadSafe(dependencies.getBrowserManager()?.getState().download),
            requestWorkspaceSafety: dependencies.requestWorkspaceSafety,
            hideBrowser: () => dependencies.getBrowserManager()?.setBounds({ x: 0, y: 0, width: 0, height: 0, visible: false })
          }),
          resolveTarget: async () => {
            if ('profileId' in target) return target
            const created = await client.create(target.displayName, target.creationIdempotencyKey)
            return {
              profileId: created.createdProfileId,
              expectedRegistryRevision: created.profiles.registry_revision,
              activationIdempotencyKey: `${target.creationIdempotencyKey}-activate`
            }
          },
          activate: (profileId, expectedRegistryRevision, activationIdempotencyKey) => client.activate(profileId, expectedRegistryRevision, activationIdempotencyKey)
        })
        const { profileId, accepted } = resolved
        if (accepted.to_profile_id !== profileId) throw new Error('JobOS Profile switch identity changed')
        const state = dependencies.getRuntimeState()
        if (state.connectivity.installationProfileId === profileId) return
        const runtime = state.runtime
        const deviceToken = state.deviceToken
        if (!runtime || !deviceToken) throw new Error('JobOS runtime became unavailable')
        const sourceDriven = !runtime.launchdLabel && runtime.mode !== 'remote-client'
        try {
          if (!sourceDriven) await client.waitForTarget(accepted.switch_id, profileId)
          else await dependencies.sourceApi.stop()
          const deadline = now() + 20_000
          let confirmed = false
          do {
            const snapshot = await probe({ baseUrl: runtime.apiBaseUrl, deviceToken })
            if (snapshot.installationProfileId === profileId && snapshot.state === 'connected') {
              confirmed = true
              break
            }
            if (snapshot.state === 'disconnected' && sourceDriven) await dependencies.apiLifecycle.ensureApiReady(runtime, deviceToken)
            await sleep(200)
          } while (now() < deadline)
          if (!confirmed) throw new Error('JobOS did not open the requested profile')
        } catch (error) {
          if (!sourceDriven) throw error
          await rollbackSourceProfileRuntime({
            stopTargetApi: dependencies.sourceApi.stop,
            rollbackRegistry: () => dependencies.sourceApi.rollbackProfileSwitch(accepted.switch_id),
            reopenPreviousApi: () => dependencies.apiLifecycle.ensureApiReady(runtime, deviceToken)
          })
          throw new Error('JobOS stayed in the previous profile; no workspace data was changed.')
        }
        switchCompleted = true
        dependencies.relaunchAndQuit()
      } finally {
        const currentBrowserManager = dependencies.getBrowserManager()
        if (!switchCompleted && previousBrowserBounds) currentBrowserManager?.setBounds(previousBrowserBounds)
        currentBrowserManager?.setDownloadsAllowed(true)
        inProgress = false
      }
    }
  }
}
