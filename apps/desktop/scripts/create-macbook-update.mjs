import { createHash, randomBytes } from 'node:crypto'
import {
  chmodSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  linkSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync
} from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(desktopRoot, '../..')
const releaseRoot = path.join(repoRoot, 'release/desktop')
const handoffRoot = path.join(releaseRoot, 'macbook')
const packageJson = JSON.parse(readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'))
const innerArchiveName = `JobOS-${packageJson.version}-arm64.zip`
const innerArchivePath = path.join(releaseRoot, innerArchiveName)
const packagePaths = [
  'apps/desktop',
  'packages/contracts',
  'package.json',
  'pnpm-lock.yaml',
  'pnpm-workspace.yaml'
]

function run(command, arguments_, options = {}) {
  const result = spawnSync(command, arguments_, {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, ...options.env },
    stdio: options.capture ? 'pipe' : 'inherit'
  })
  if (result.status !== 0) {
    const detail = options.capture ? result.stderr || result.stdout : ''
    throw new Error(`${command} exited ${result.status ?? 'without a status'}${detail ? `\n${detail}` : ''}`)
  }
  return options.capture ? result.stdout.trim() : ''
}

function assertCleanPackageSources(expectedCommit) {
  for (const arguments_ of [
    ['diff', '--quiet', '--', ...packagePaths],
    ['diff', '--cached', '--quiet', '--', ...packagePaths]
  ]) {
    const result = spawnSync('/usr/bin/git', arguments_, { cwd: repoRoot, stdio: 'ignore' })
    if (result.status !== 0) {
      throw new Error('Package-affecting source changes must be committed before creating a MacBook updater')
    }
  }

  const untracked = run('/usr/bin/git', ['ls-files', '--others', '--exclude-standard', '--', ...packagePaths], { capture: true })
  if (untracked) throw new Error(`Untracked package-affecting files must be committed first:\n${untracked}`)

  const currentCommit = run('/usr/bin/git', ['rev-parse', 'HEAD'], { capture: true })
  if (expectedCommit && currentCommit !== expectedCommit) {
    throw new Error(`Source commit changed during packaging: expected ${expectedCommit}, found ${currentCommit}`)
  }
  return currentCommit
}

function sha256(filePath) {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex')
}

function shellSingleQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`
}

function createUpdater(innerSha256) {
  return `#!/bin/zsh
set -euo pipefail

BUNDLE_DIR="\${0:A:h}"
PACKAGE="$BUNDLE_DIR/${innerArchiveName}"
EXPECTED_SHA256=${shellSingleQuote(innerSha256)}
TEMP_DIR="$(mktemp -d)"
STAGE_ROOT=""
BACKUP_ROOT=""
BACKUP_APP=""
REPLACED=0
COMMITTED=0

cleanup() {
  local exit_status=$?
  if (( COMMITTED == 0 && REPLACED == 1 )); then
    rollback_pids=(\${(f)"$(running_target_pids)"})
    if (( \${#rollback_pids} > 0 )); then
      /bin/kill -TERM $rollback_pids 2>/dev/null || true
      for _ in {1..50}; do
        rollback_pids=(\${(f)"$(running_target_pids)"})
        (( \${#rollback_pids} == 0 )) && break
        /bin/sleep 0.1
      done
      rollback_pids=(\${(f)"$(running_target_pids)"})
      (( \${#rollback_pids} > 0 )) && /bin/kill -KILL $rollback_pids 2>/dev/null || true
    fi
    [[ -e "$APP_PATH" ]] && /bin/rm -rf "$APP_PATH"
    if [[ -n "$BACKUP_APP" && -d "$BACKUP_APP" ]]; then
      /bin/mv "$BACKUP_APP" "$APP_PATH"
      print -u2 "The update did not complete. The previous JobOS app was restored."
    fi
  fi
  [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]] && /bin/rm -rf "$STAGE_ROOT"
  [[ -n "$BACKUP_ROOT" && -d "$BACKUP_ROOT" ]] && /bin/rm -rf "$BACKUP_ROOT"
  /bin/rm -rf "$TEMP_DIR"
  return "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

actual_sha256="$(/usr/bin/shasum -a 256 "$PACKAGE" | /usr/bin/cut -d' ' -f1)"
if [[ "$actual_sha256" != "$EXPECTED_SHA256" ]]; then
  print -u2 "Update package checksum mismatch. Delete this download and get a fresh copy."
  exit 1
fi

if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" ]]; then
  if [[ -z "\${JOBOS_INSTALL_PATH:-}" ]]; then
    print -u2 "Smoke-test mode requires JOBOS_INSTALL_PATH."
    exit 1
  fi
  APP_PATH="\${JOBOS_INSTALL_PATH:A}"
  if [[ "$APP_PATH" != /*/JobOS.app || "$APP_PATH" == "/Applications/JobOS.app" || "$APP_PATH" == "$HOME/Applications/JobOS.app" || -e "$APP_PATH" || -L "$APP_PATH" ]]; then
    print -u2 "Unsafe or pre-existing smoke-test install path: $APP_PATH"
    exit 1
  fi
elif [[ -n "\${JOBOS_INSTALL_PATH:-}" ]]; then
  print -u2 "JOBOS_INSTALL_PATH is reserved for the packaged updater smoke test."
  exit 1
elif [[ -d "/Applications/JobOS.app" ]]; then
  APP_PATH="/Applications/JobOS.app"
elif [[ -d "$HOME/Applications/JobOS.app" ]]; then
  APP_PATH="$HOME/Applications/JobOS.app"
else
  APP_PATH="$HOME/Applications/JobOS.app"
fi

if [[ "$APP_PATH" != "/Applications/JobOS.app" && "$APP_PATH" != "$HOME/Applications/JobOS.app" && "\${JOBOS_UPDATER_SMOKE_TEST:-0}" != "1" ]]; then
  print -u2 "Refusing unexpected JobOS destination: $APP_PATH"
  exit 1
fi
if [[ ( -e "$APP_PATH" && ! -d "$APP_PATH" ) || -L "$APP_PATH" ]]; then
  print -u2 "Refusing a non-directory or symbolic-link JobOS destination: $APP_PATH"
  exit 1
fi
/bin/mkdir -p "\${APP_PATH:h}"
print "Updating $APP_PATH…"

running_target_pids() {
  /bin/ps -axo pid=,command= | while read -r pid command; do
    if [[ "$command" == "$APP_PATH/Contents/"* ]]; then
      print -r -- "$pid"
    fi
  done
}

old_pids=(\${(f)"$(running_target_pids)"})
if (( \${#old_pids} > 0 )); then
  /usr/bin/osascript -e 'tell application id "com.cobibean.jobos" to quit' 2>/dev/null || true
fi
for _ in {1..100}; do
  current_pids=(\${(f)"$(running_target_pids)"})
  (( \${#current_pids} == 0 )) && break
  /bin/sleep 0.1
done
current_pids=(\${(f)"$(running_target_pids)"})
if (( \${#current_pids} > 0 )); then
  print -u2 "JobOS did not quit cleanly. Quit it manually, then run this updater again."
  exit 1
fi

/usr/bin/ditto -x -k "$PACKAGE" "$TEMP_DIR"
if [[ ! -d "$TEMP_DIR/JobOS.app" ]]; then
  print -u2 "JobOS.app was not found in the update package."
  exit 1
fi
/usr/bin/codesign --verify --deep --strict "$TEMP_DIR/JobOS.app"

PARENT_DIR="\${APP_PATH:h}"
STAGE_ROOT="$(/usr/bin/mktemp -d "$PARENT_DIR/.JobOS.update.XXXXXX")"
STAGED_APP="$STAGE_ROOT/JobOS.app"
BACKUP_ROOT="$(/usr/bin/mktemp -d "$PARENT_DIR/.JobOS.backup.XXXXXX")"
BACKUP_APP="$BACKUP_ROOT/JobOS.app"
/usr/bin/ditto "$TEMP_DIR/JobOS.app" "$STAGED_APP"
/usr/bin/codesign --verify --deep --strict "$STAGED_APP"

trap '' INT TERM HUP
if [[ -d "$APP_PATH" ]]; then
  /bin/mv "$APP_PATH" "$BACKUP_APP"
  REPLACED=1
else
  REPLACED=1
fi
/bin/mv "$STAGED_APP" "$APP_PATH"
trap 'exit 130' INT TERM HUP
/usr/bin/codesign --verify --deep --strict "$APP_PATH"
/usr/bin/xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true

if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" ]]; then
  COMMITTED=1
  print "JobOS updater smoke test passed at $APP_PATH."
  exit 0
fi

/usr/bin/open "$APP_PATH"
new_pid=""
for _ in {1..100}; do
  while read -r pid command; do
    if [[ "$command" == "$APP_PATH/Contents/MacOS/JobOS" || "$command" == "$APP_PATH/Contents/MacOS/JobOS "* ]]; then
      new_pid="$pid"
      break
    fi
  done < <(/bin/ps -axo pid=,command=)
  [[ -n "$new_pid" ]] && break
  /bin/sleep 0.1
done
if [[ -z "$new_pid" ]] || ! /bin/kill -0 "$new_pid" 2>/dev/null; then
  print -u2 "The updated JobOS app did not stay running."
  exit 1
fi

COMMITTED=1
print "JobOS updated and opened successfully."
print "You can close this window."
`
}

function main() {
  const sourceCommit = assertCleanPackageSources()
  let buildRoot = ''
  rmSync(innerArchivePath, { force: true })

  try {
    run('pnpm', ['--filter', '@jobos/contracts', 'build'])
    run('pnpm', ['--filter', '@jobos/desktop', 'package:mac'])
    assertCleanPackageSources(sourceCommit)

    if (!existsSync(innerArchivePath) || !statSync(innerArchivePath).isFile()) {
      throw new Error(`Packaging did not produce ${innerArchivePath}`)
    }
    run('/usr/bin/unzip', ['-tq', innerArchivePath])

    const generatedAt = new Date().toISOString()
    const timestamp = generatedAt.replaceAll(/\D/g, '')
    const nonce = randomBytes(16).toString('hex')
    const bundleName = `JobOS-MacBook-Update-${timestamp}-${sourceCommit.slice(0, 8)}-${nonce}`
    const publishedArchivePath = path.join(handoffRoot, `${bundleName}.zip`)
    const innerSize = statSync(innerArchivePath).size
    const innerSha256 = sha256(innerArchivePath)
    const verification = `JobOS MacBook Update\n\nGenerated: ${generatedAt}\nSource commit: ${sourceCommit}\nApp version: ${packageJson.version}\nArchitecture: arm64\n\nInstall\n1. Unzip this outer MacBook update.\n2. Double-click “Update JobOS.command”.\n3. The updater verifies, replaces, and reopens JobOS while preserving external runtime config and Keychain data.\n\nInner app package\nFilename: ${innerArchiveName}\nSize: ${innerSize} bytes\nSHA-256: ${innerSha256}\n\nThis is an ad-hoc-signed updater for an existing private installation. It is not a public release.\n`

    mkdirSync(handoffRoot, { recursive: true })
    buildRoot = mkdtempSync(path.join(handoffRoot, '.build-'))
    const bundleDir = path.join(buildRoot, bundleName)
    const stagedArchivePath = path.join(buildRoot, `${bundleName}.zip`)
    mkdirSync(bundleDir)
    cpSync(innerArchivePath, path.join(bundleDir, innerArchiveName))
    const updaterPath = path.join(bundleDir, 'Update JobOS.command')
    writeFileSync(updaterPath, createUpdater(innerSha256))
    chmodSync(updaterPath, 0o755)
    writeFileSync(path.join(bundleDir, 'VERIFIED.txt'), verification)

    run('/bin/zsh', ['-n', updaterPath])
    run('/usr/bin/ditto', ['-c', '-k', '--keepParent', bundleDir, stagedArchivePath])
    run('/usr/bin/unzip', ['-tq', stagedArchivePath])

    const verifyRoot = mkdtempSync(path.join(tmpdir(), 'jobos-macbook-verify-'))
    try {
      run('/usr/bin/ditto', ['-x', '-k', stagedArchivePath, verifyRoot])
      const outerMembers = readdirSync(verifyRoot).sort()
      if (outerMembers.length !== 1 || outerMembers[0] !== bundleName) {
        throw new Error(`Unexpected outer archive layout: ${outerMembers.join(', ')}`)
      }

      const extractedDir = path.join(verifyRoot, bundleName)
      const extractedMembers = readdirSync(extractedDir).sort()
      const expectedMembers = [innerArchiveName, 'Update JobOS.command', 'VERIFIED.txt'].sort()
      if (JSON.stringify(extractedMembers) !== JSON.stringify(expectedMembers)) {
        throw new Error(`Unexpected updater members: ${extractedMembers.join(', ')}`)
      }

      const extractedUpdater = path.join(extractedDir, 'Update JobOS.command')
      const extractedInner = path.join(extractedDir, innerArchiveName)
      const extractedVerification = path.join(extractedDir, 'VERIFIED.txt')
      run('/bin/zsh', ['-n', extractedUpdater])
      run('/usr/bin/unzip', ['-tq', extractedInner])
      if (sha256(extractedInner) !== innerSha256) throw new Error('Extracted inner archive checksum mismatch')
      if (readFileSync(extractedVerification, 'utf8') !== verification) throw new Error('Extracted verification manifest mismatch')

      const smokeInstallPath = path.join(verifyRoot, 'smoke-install', 'JobOS.app')
      run('/bin/zsh', [extractedUpdater], {
        env: {
          JOBOS_INSTALL_PATH: smokeInstallPath,
          JOBOS_UPDATER_SMOKE_TEST: '1'
        }
      })
      run('/usr/bin/codesign', ['--verify', '--deep', '--strict', smokeInstallPath])
    } finally {
      rmSync(verifyRoot, { recursive: true, force: true })
    }

    const outerSize = statSync(stagedArchivePath).size
    const outerSha256 = sha256(stagedArchivePath)
    linkSync(stagedArchivePath, publishedArchivePath)

    process.stdout.write(`MACBOOK_UPDATE=${publishedArchivePath}\n`)
    process.stdout.write(`SOURCE_COMMIT=${sourceCommit}\n`)
    process.stdout.write(`OUTER_SIZE=${outerSize}\n`)
    process.stdout.write(`OUTER_SHA256=${outerSha256}\n`)
    process.stdout.write(`INNER_SIZE=${innerSize}\n`)
    process.stdout.write(`INNER_SHA256=${innerSha256}\n`)
  } finally {
    if (buildRoot) rmSync(buildRoot, { recursive: true, force: true })
    rmSync(innerArchivePath, { force: true })
  }
}

main()
