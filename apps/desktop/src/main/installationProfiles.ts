import {
  createJobOsApiClient,
  installationProfileActivateV1InstallationProfilesProfileIdActivatePost,
  installationProfileCreateV1InstallationProfilesPost,
  installationProfileListV1InstallationProfilesGet,
  installationProfileRenameV1InstallationProfilesProfileIdPatch,
  installationProfileSwitchStatusV1InstallationProfilesSwitchesSwitchIdGet
} from '@jobos/contracts'
import type {
  JobOsProfileList,
  JobOsProfileSwitchAccepted,
  JobOsProfileSwitchStatus
} from '@jobos/contracts'
import type { BrowserDownload } from '../shared/contracts.js'
import type { DesktopProfileStorageIdentity } from './profileStorage.js'

export interface InstallationProfilesConfig {
  baseUrl: string
  deviceToken: string
  installationProfileId?: string
  fetch?: typeof fetch
  requestTimeoutMs?: number
}

export interface SwitchPollingOptions {
  timeoutMs?: number
  intervalMs?: number
  now?: () => number
  sleep?: (milliseconds: number) => Promise<void>
}

export class InstallationProfileClientError extends Error {
  constructor(
    readonly code: string,
    message: string
  ) {
    super(message)
  }
}

interface ApiResult<T> {
  data?: T
  error?: unknown
  response?: Response
}

export function assertProfileSwitchDownloadSafe(
  download: Pick<BrowserDownload, 'state'> | null | undefined
): void {
  if (download?.state === 'starting' || download?.state === 'progressing') {
    throw new Error('Wait for the browser download to finish before switching profiles.')
  }
}

export async function prepareDesktopProfileSwitch(options: {
  assertDownloadSafe: () => void
  requestWorkspaceSafety: () => Promise<boolean>
  hideBrowser: () => void
}): Promise<void> {
  options.assertDownloadSafe()
  if (!await options.requestWorkspaceSafety()) {
    throw new Error('Save or resolve the current workspace before switching profiles.')
  }
  options.assertDownloadSafe()
  options.hideBrowser()
}

export async function prepareAndActivateDesktopProfileSwitch(options: {
  prepare: () => Promise<void>
  resolveTarget: () => Promise<{
    profileId: string
    expectedRegistryRevision: number
    activationIdempotencyKey: string
  }>
  activate: (
    profileId: string,
    expectedRegistryRevision: number,
    activationIdempotencyKey: string
  ) => Promise<JobOsProfileSwitchAccepted>
}): Promise<{ profileId: string, accepted: JobOsProfileSwitchAccepted }> {
  await options.prepare()
  const target = await options.resolveTarget()
  const accepted = await options.activate(
    target.profileId,
    target.expectedRegistryRevision,
    target.activationIdempotencyKey
  )
  return { profileId: target.profileId, accepted }
}

export async function rollbackSourceProfileRuntime(options: {
  stopTargetApi: () => Promise<void>
  rollbackRegistry: () => Promise<void>
  reopenPreviousApi: () => Promise<unknown>
}): Promise<void> {
  await options.stopTargetApi()
  await options.rollbackRegistry()
  await options.reopenPreviousApi()
}

function errorValue(value: unknown, fallback: string): InstallationProfileClientError {
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    const detail = record.detail && typeof record.detail === 'object'
      ? record.detail as Record<string, unknown>
      : record
    const code = typeof record.code === 'string'
      ? record.code
      : typeof detail.code === 'string' ? detail.code : 'installation_profile_request_failed'
    const message = typeof record.message === 'string'
      ? record.message
      : typeof detail.message === 'string'
        ? detail.message
        : typeof record.detail === 'string' ? record.detail : fallback
    return new InstallationProfileClientError(code, message)
  }
  return new InstallationProfileClientError('installation_profile_request_failed', fallback)
}

function unwrap<T>(result: ApiResult<T>, statuses: number[], fallback: string): T {
  if (result.data !== undefined && result.response && statuses.includes(result.response.status)) {
    return result.data
  }
  throw errorValue(result.error, fallback)
}

export function resolveProfileStorageIdentity(
  profiles: JobOsProfileList,
  activeProfileId: string
): Exclude<DesktopProfileStorageIdentity, { kind: 'recovery' }> {
  const active = profiles.profiles.find(profile => profile.profile_id === activeProfileId)
  if (!active || profiles.active_profile_id !== activeProfileId) {
    throw new InstallationProfileClientError(
      'profile_switch_identity_mismatch',
      'JobOS Profile identity changed during startup.'
    )
  }
  if (!profiles.profiles.some(profile => profile.profile_id === profiles.anchored_profile_id)) {
    throw new InstallationProfileClientError(
      'profile_registry_invalid',
      'JobOS Profile registry has no anchored profile.'
    )
  }
  return profiles.anchored_profile_id === activeProfileId
    ? { kind: 'anchored', profileId: activeProfileId }
    : { kind: 'managed', profileId: activeProfileId }
}

export function createInstallationProfilesClient(config: InstallationProfilesConfig) {
  const client = createJobOsApiClient(
    config.baseUrl,
    config.deviceToken,
    config.installationProfileId
  )
  if (config.fetch) client.setConfig({ fetch: config.fetch })
  const sleep = (milliseconds: number) => new Promise<void>(resolve => setTimeout(resolve, milliseconds))

  return {
    async list(): Promise<JobOsProfileList> {
      return unwrap(
        await installationProfileListV1InstallationProfilesGet({ client }),
        [200],
        'JobOS Profiles are unavailable'
      )
    },

    async create(displayName: string, idempotencyKey: string): Promise<{
      profiles: JobOsProfileList
      createdProfileId: string
    }> {
      const result = await installationProfileCreateV1InstallationProfilesPost({
          client,
          body: { display_name: displayName, idempotency_key: idempotencyKey }
        })
      const profiles = unwrap(
        result,
        [201],
        'JobOS Profile could not be created'
      )
      const createdProfileId = result.response?.headers.get('x-jobos-created-profile-id') ?? ''
      if (!profiles.profiles.some(profile => profile.profile_id === createdProfileId)) {
        throw new InstallationProfileClientError(
          'profile_creation_identity_mismatch',
          'JobOS Profile creation identity could not be verified.'
        )
      }
      return { profiles, createdProfileId }
    },

    async rename(
      profileId: string,
      displayName: string,
      expectedRegistryRevision: number,
      idempotencyKey: string
    ): Promise<JobOsProfileList> {
      return unwrap(
        await installationProfileRenameV1InstallationProfilesProfileIdPatch({
          client,
          path: { profile_id: profileId },
          body: {
            display_name: displayName,
            expected_registry_revision: expectedRegistryRevision,
            idempotency_key: idempotencyKey
          }
        }),
        [200],
        'JobOS Profile could not be renamed'
      )
    },

    async activate(
      profileId: string,
      expectedRegistryRevision: number,
      idempotencyKey: string
    ): Promise<JobOsProfileSwitchAccepted> {
      return unwrap(
        await installationProfileActivateV1InstallationProfilesProfileIdActivatePost({
          client,
          path: { profile_id: profileId },
          body: {
            expected_registry_revision: expectedRegistryRevision,
            idempotency_key: idempotencyKey
          }
        }),
        [202],
        'JobOS Profile switch could not start'
      )
    },

    async status(switchId: string): Promise<JobOsProfileSwitchStatus> {
      return unwrap(
        await installationProfileSwitchStatusV1InstallationProfilesSwitchesSwitchIdGet({
          client,
          path: { switch_id: switchId }
        }),
        [200],
        'JobOS Profile switch status is unavailable'
      )
    },

    async waitForTarget(
      switchId: string,
      targetProfileId: string,
      options: SwitchPollingOptions = {}
    ): Promise<JobOsProfileSwitchStatus> {
      const now = options.now ?? Date.now
      const wait = options.sleep ?? sleep
      const deadline = now() + (options.timeoutMs ?? config.requestTimeoutMs ?? 20_000)
      do {
        let value: JobOsProfileSwitchStatus
        try {
          value = await this.status(switchId)
        } catch (error) {
          if (now() >= deadline) throw error
          await wait(options.intervalMs ?? 200)
          continue
        }
        if (value.status === 'rolled_back') {
          throw new InstallationProfileClientError(
            value.error_code ?? 'profile_switch_rolled_back',
            'JobOS stayed in the previous profile; no workspace data was changed.'
          )
        }
        if (value.status === 'succeeded') {
          if (
            value.target_profile_id !== targetProfileId
            || value.active_profile_id !== targetProfileId
          ) {
            throw new InstallationProfileClientError(
              'profile_switch_identity_mismatch',
              'JobOS did not open the requested profile.'
            )
          }
          return value
        }
        await wait(options.intervalMs ?? 200)
      } while (now() < deadline)
      throw new InstallationProfileClientError(
        'profile_switch_timeout',
        'JobOS Profile switch did not finish in time.'
      )
    }
  }
}
