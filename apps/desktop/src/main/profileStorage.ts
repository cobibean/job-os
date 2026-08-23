import { chmodSync, lstatSync, mkdirSync } from 'node:fs'
import path from 'node:path'

export const LEGACY_BROWSER_PARTITION = 'persist:jobos-browser-v1'
export const RECOVERY_RENDERER_PARTITION = 'persist:jobos-recovery-v1'
export const RECOVERY_BROWSER_PARTITION = 'persist:jobos-recovery-browser-v1'
const PROFILE_ID = /^jprof_[a-f0-9]{32}$/

export type DesktopProfileStorageIdentity =
  | { kind: 'recovery' }
  | { kind: 'anchored'; profileId: string }
  | { kind: 'managed'; profileId: string }

export interface ProfileClientPaths {
  root: string
  recoveryRoot: string
  artifactRoot: string
  bindingsPath: string
}

export function validInstallationProfileId(profileId: string): string {
  if (!PROFILE_ID.test(profileId)) throw new Error('Invalid JobOS Profile identity')
  return profileId
}

export function rendererPartition(identity: DesktopProfileStorageIdentity): string | undefined {
  if (identity.kind === 'anchored') return undefined
  if (identity.kind === 'recovery') return RECOVERY_RENDERER_PARTITION
  return `persist:jobos-renderer-${validInstallationProfileId(identity.profileId)}`
}

export function browserPartition(identity: DesktopProfileStorageIdentity): string {
  if (identity.kind === 'recovery') return RECOVERY_BROWSER_PARTITION
  return identity.kind === 'anchored'
    ? LEGACY_BROWSER_PARTITION
    : `persist:jobos-browser-${validInstallationProfileId(identity.profileId)}`
}

function assertNoSymlinkParents(root: string): void {
  const resolved = path.resolve(root)
  let current = path.parse(resolved).root
  for (const part of resolved.slice(current.length).split(path.sep).filter(Boolean)) {
    current = path.join(current, part)
    try {
      if (lstatSync(current).isSymbolicLink()) {
        throw new Error('JobOS Profile client storage is unavailable')
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      break
    }
  }
}

export function profileClientRoot(userData: string, profileId: string): string {
  validInstallationProfileId(profileId)
  assertNoSymlinkParents(userData)
  const base = path.resolve(userData, 'profile-client-data')
  const root = path.resolve(base, profileId)
  if (path.dirname(root) !== base) throw new Error('Invalid JobOS Profile client storage')
  assertNoSymlinkParents(root)
  return root
}

export function profileClientPaths(
  userData: string,
  identity: Exclude<DesktopProfileStorageIdentity, { kind: 'recovery' }>
): ProfileClientPaths {
  if (identity.kind === 'anchored') {
    const root = path.resolve(userData)
    assertNoSymlinkParents(root)
    return {
      root,
      recoveryRoot: path.join(root, 'docx-recovery'),
      artifactRoot: path.join(root, 'editable-docx-artifacts'),
      bindingsPath: path.join(root, 'docx-bindings.json')
    }
  }
  const root = profileClientRoot(userData, identity.profileId)
  return {
    root,
    recoveryRoot: path.join(root, 'docx-recovery'),
    artifactRoot: path.join(root, 'editable-docx-artifacts'),
    bindingsPath: path.join(root, 'docx-bindings.json')
  }
}

function ensurePrivateDirectory(candidate: string): void {
  let created = false
  try {
    mkdirSync(candidate, { mode: 0o700 })
    created = true
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
  }
  assertNoSymlinkParents(candidate)
  const stat = lstatSync(candidate)
  if (!stat.isDirectory() || stat.isSymbolicLink()) {
    throw new Error('JobOS Profile client storage is unavailable')
  }
  if (created) chmodSync(candidate, 0o700)
}

export function prepareProfileClientPaths(
  userData: string,
  identity: Exclude<DesktopProfileStorageIdentity, { kind: 'recovery' }>
): ProfileClientPaths {
  const paths = profileClientPaths(userData, identity)
  assertNoSymlinkParents(userData)
  if (identity.kind === 'managed') {
    ensurePrivateDirectory(path.join(path.resolve(userData), 'profile-client-data'))
  }
  ensurePrivateDirectory(paths.root)
  ensurePrivateDirectory(paths.recoveryRoot)
  ensurePrivateDirectory(paths.artifactRoot)
  try {
    const bindingStat = lstatSync(paths.bindingsPath)
    if (bindingStat.isSymbolicLink() || !bindingStat.isFile()) {
      throw new Error('JobOS Profile client storage is unavailable')
    }
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
  }
  return paths
}
