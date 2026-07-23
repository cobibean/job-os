import { readFileSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const releaseRoot = path.resolve(desktopRoot, '../../release/desktop')
const packageJson = JSON.parse(readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'))
const appPath = path.join(releaseRoot, 'mac-arm64', 'JobOS.app')
const archivePath = path.join(releaseRoot, `JobOS-${packageJson.version}-arm64.zip`)

function run(command, arguments_) {
  const result = spawnSync(command, arguments_, { stdio: 'inherit' })
  if (result.status !== 0) process.exit(result.status ?? 1)
}

run('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', appPath])
run('/usr/bin/codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath])
rmSync(archivePath, { force: true })
rmSync(`${archivePath}.blockmap`, { force: true })
run('/usr/bin/ditto', [
  '-c',
  '-k',
  '--sequesterRsrc',
  '--keepParent',
  appPath,
  archivePath
])
