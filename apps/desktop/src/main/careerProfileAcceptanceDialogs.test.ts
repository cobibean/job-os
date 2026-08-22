import path from 'node:path'
import { expect, test } from 'vitest'

import { careerProfileAcceptanceDialogPaths } from './careerProfileAcceptanceDialogs.js'

function environment(overrides: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const root = '/private/tmp/disposable/jobos-career-profile-native'
  return {
    TMPDIR: '/private/tmp/disposable',
    JOBOS_CAREER_PROFILE_ACCEPTANCE_MODE: 'career-profile-native-flow-v1',
    JOBOS_CAREER_PROFILE_ACCEPTANCE_ROOT: root,
    JOBOS_CAREER_PROFILE_ACCEPTANCE_RESTORE_PATH: path.join(root, 'profile-only.zip'),
    JOBOS_CAREER_PROFILE_ACCEPTANCE_EXPORT_PATHS: JSON.stringify([
      path.join(root, 'zero-evidence.zip'), path.join(root, 'profile-only.zip'),
      path.join(root, 'selected.zip'), path.join(root, 'all.zip')
    ]),
    ...overrides
  }
}

test('acceptance dialog override is absent unless explicitly enabled', () => {
  expect(careerProfileAcceptanceDialogPaths({})).toBeNull()
})

test('acceptance dialog override consumes four deterministic disposable exports', async () => {
  const paths = careerProfileAcceptanceDialogPaths(environment())!
  await expect(paths.chooseArchivePath()).resolves.toBe('/private/tmp/disposable/jobos-career-profile-native/profile-only.zip')
  await expect(Promise.all(Array.from({ length: 4 }, () => paths.chooseExportPath()))).resolves.toEqual([
    '/private/tmp/disposable/jobos-career-profile-native/zero-evidence.zip',
    '/private/tmp/disposable/jobos-career-profile-native/profile-only.zip',
    '/private/tmp/disposable/jobos-career-profile-native/selected.zip',
    '/private/tmp/disposable/jobos-career-profile-native/all.zip'
  ])
  await expect(paths.chooseExportPath()).rejects.toThrow('queue exhausted')
})

test('acceptance dialog override fails closed for invalid mode and escaped paths', () => {
  expect(() => careerProfileAcceptanceDialogPaths(environment({ JOBOS_CAREER_PROFILE_ACCEPTANCE_MODE: '1' }))).toThrow('Invalid')
  expect(() => careerProfileAcceptanceDialogPaths(environment({ JOBOS_CAREER_PROFILE_ACCEPTANCE_RESTORE_PATH: '/tmp/outside.zip' }))).toThrow('escaped')
})
