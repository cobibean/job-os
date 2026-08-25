#!/usr/bin/env node
import { spawnSync } from 'node:child_process'
import { chmod, mkdir, mkdtemp, rm } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const taskRoot = path.join(root, '.task')
await mkdir(taskRoot, { recursive: true, mode: 0o700 })
const temporaryRoot = await mkdtemp(path.join(taskRoot, 'packaged-frontend-'))
const runtime = path.join(temporaryRoot, 'runtime')
const output = path.join(temporaryRoot, 'output')
const privateDirectories = [
  runtime,
  output,
  path.join(runtime, 'home'),
  path.join(runtime, 'tmp'),
  path.join(runtime, 'xdg-config'),
  path.join(runtime, 'xdg-cache'),
  path.join(runtime, 'xdg-data'),
]

const isolatedEnvironment = {
  HOME: path.join(runtime, 'home'),
  TMPDIR: path.join(runtime, 'tmp'),
  XDG_CONFIG_HOME: path.join(runtime, 'xdg-config'),
  XDG_CACHE_HOME: path.join(runtime, 'xdg-cache'),
  XDG_DATA_HOME: path.join(runtime, 'xdg-data'),
}

function run(command, args, environment = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    env: { ...process.env, ...isolatedEnvironment, ...environment },
    stdio: 'inherit',
  })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status}`)
}

function runCaptured(command, args, environment = {}) {
  const result = spawnSync(command, args, {
    cwd: root,
    env: { ...process.env, ...isolatedEnvironment, ...environment },
    encoding: 'utf8',
    maxBuffer: 4 * 1024 * 1024,
  })
  if (result.error) throw result.error
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status}; child output withheld`)
  return result.stdout
}

try {
  for (const directory of privateDirectories) {
    await mkdir(directory, { recursive: true, mode: 0o700 })
    await chmod(directory, 0o700)
  }

  const disabledKeychainHelper = path.join(runtime, 'disabled-keychain-helper')
  run('uv', ['run', 'jobos-init', '--data-dir', path.join(runtime, 'profile'), '--no-demo'], {
    JOBOS_KEYCHAIN_HELPER_PATH: disabledKeychainHelper,
  })
  try {
    const report = runCaptured(process.execPath, ['docs/acceptance/career-profile-product-experience/capture.mjs'], {
      JOBOS_ACCEPTANCE_CI: '1',
      JOBOS_ACCEPTANCE_RUNTIME: runtime,
      JOBOS_ACCEPTANCE_OUTPUT: output,
      JOBOS_KEYCHAIN_HELPER_PATH: disabledKeychainHelper,
    })
    process.stdout.write(report)
  } catch (error) {
    process.stderr.write('\nPackaged frontend smoke failed; disposable runtime logs were withheld from CI output to prevent credential disclosure.\n')
    throw error
  }
} finally {
  await rm(temporaryRoot, { recursive: true, force: true })
}
