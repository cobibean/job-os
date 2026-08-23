// @vitest-environment node

import { lstatSync, mkdtempSync, realpathSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  LEGACY_BROWSER_PARTITION,
  RECOVERY_RENDERER_PARTITION,
  browserPartition,
  profileClientPaths,
  profileClientRoot,
  prepareProfileClientPaths,
  rendererPartition
} from './profileStorage.js'

const PROFILE_A = 'jprof_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const PROFILE_B = 'jprof_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'

describe('profile desktop storage', () => {
  it('keeps every anchored location exactly compatible', () => {
    const userData = '/safe/JobOS'
    const identity = { kind: 'anchored', profileId: PROFILE_A } as const
    expect(rendererPartition(identity)).toBeUndefined()
    expect(browserPartition(identity)).toBe(LEGACY_BROWSER_PARTITION)
    expect(profileClientPaths(userData, identity)).toEqual({
      root: userData,
      recoveryRoot: `${userData}/docx-recovery`,
      artifactRoot: `${userData}/editable-docx-artifacts`,
      bindingsPath: `${userData}/docx-bindings.json`
    })
  })

  it('isolates managed renderer, browser, and DOCX state by opaque identity', () => {
    const first = { kind: 'managed', profileId: PROFILE_A } as const
    const second = { kind: 'managed', profileId: PROFILE_B } as const
    expect(rendererPartition(first)).not.toBe(rendererPartition(second))
    expect(browserPartition(first)).not.toBe(browserPartition(second))
    expect(profileClientPaths('/safe/JobOS', first).root)
      .not.toBe(profileClientPaths('/safe/JobOS', second).root)
    expect(profileClientPaths('/safe/JobOS', first).root).not.toContain('Fresh setup')
  })

  it('uses one fixed recovery partition with no normal workspace identity', () => {
    expect(rendererPartition({ kind: 'recovery' })).toBe(RECOVERY_RENDERER_PARTITION)
  })

  it('prepares private managed roots before profile-local state is opened', () => {
    const root = mkdtempSync(path.join(realpathSync(tmpdir()), 'jobos-profile-storage-'))
    const paths = prepareProfileClientPaths(root, { kind: 'managed', profileId: PROFILE_A })
    expect(lstatSync(paths.root).isDirectory()).toBe(true)
    expect(lstatSync(paths.recoveryRoot).isDirectory()).toBe(true)
    expect(lstatSync(paths.artifactRoot).isDirectory()).toBe(true)
  })

  it('rejects malformed identities, traversal, and symlinked client roots', () => {
    expect(() => profileClientRoot('/safe/JobOS', '../../escape')).toThrow('Invalid JobOS Profile')
    const root = mkdtempSync(path.join(tmpdir(), 'jobos-profile-storage-'))
    const target = mkdtempSync(path.join(tmpdir(), 'jobos-profile-target-'))
    const linked = path.join(root, 'linked')
    symlinkSync(target, linked, 'dir')
    expect(() => profileClientRoot(linked, PROFILE_A)).toThrow('unavailable')
    symlinkSync(target, path.join(root, 'profile-client-data'), 'dir')
    expect(() => profileClientRoot(root, PROFILE_A)).toThrow('unavailable')
  })
})
