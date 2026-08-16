import { createHash, randomBytes } from 'node:crypto'
import { inflateRawSync } from 'node:zlib'
import {
  chmodSync,
  cpSync,
  existsSync,
  linkSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  realpathSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync
} from 'node:fs'
import { spawnSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repoRoot = path.resolve(desktopRoot, '../..')
const releaseRoot = path.join(repoRoot, 'release/desktop')
const handoffRoot = path.join(releaseRoot, 'macbook')
const packageJson = JSON.parse(readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'))
const innerArchiveName = `JobOS-${packageJson.version}-arm64.zip`
const innerArchivePath = path.join(releaseRoot, innerArchiveName)
const appIdentifier = 'com.cobibean.jobos'
const keychainService = 'com.cobibean.jobos.device-token'
const productName = 'JobOS'

function run(command, arguments_, options = {}) {
  const result = spawnSync(command, arguments_, {
    cwd: options.cwd ?? repoRoot,
    encoding: 'utf8',
    env: { ...process.env, ...options.env },
    stdio: options.capture ? 'pipe' : 'inherit'
  })
  if (result.status !== 0) {
    const detail = options.capture ? result.stderr || result.stdout : ''
    throw new Error(`${command} exited ${result.status ?? 'without a status'}${detail ? `\n${detail.trim()}` : ''}`)
  }
  return options.capture ? `${result.stdout ?? ''}${result.stderr ?? ''}`.trim() : ''
}

export function validateProvenance({ expectedCommit, currentCommit, worktreeStatus }) {
  if (!/^[0-9a-fA-F]{40}$/.test(expectedCommit ?? '')) {
    throw new Error('JOBOS_EXPECTED_SOURCE_COMMIT must be a full 40-hex Git commit SHA')
  }
  if (!/^[0-9a-f]{40}$/.test(currentCommit)) {
    throw new Error(`Git returned an invalid HEAD commit: ${currentCommit}`)
  }
  if (currentCommit !== expectedCommit.toLowerCase()) {
    throw new Error(`Source commit mismatch: expected ${expectedCommit.toLowerCase()}, found ${currentCommit}`)
  }
  if (worktreeStatus) {
    throw new Error(`The entire worktree must be clean before and after packaging:\n${worktreeStatus}`)
  }
  return currentCommit
}

function assertCleanWorktree(expectedCommit) {
  return validateProvenance({
    expectedCommit,
    currentCommit: run('/usr/bin/git', ['rev-parse', 'HEAD'], { capture: true }),
    worktreeStatus: run('/usr/bin/git', ['status', '--porcelain=v1', '--untracked-files=all'], { capture: true })
  })
}

function sha256(filePath) {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex')
}

function shellSingleQuote(value) {
  return `'${value.replaceAll("'", "'\\''")}'`
}

function findEndOfCentralDirectory(bytes) {
  const minimumOffset = Math.max(0, bytes.length - 65_557)
  for (let offset = bytes.length - 22; offset >= minimumOffset; offset -= 1) {
    if (bytes.readUInt32LE(offset) === 0x06054b50) return offset
  }
  throw new Error('ZIP end-of-central-directory record was not found')
}

function checkedSlice(bytes, start, length, label) {
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(length) || start < 0 || length < 0 || start + length > bytes.length) {
    throw new Error(`Invalid ZIP ${label} bounds`)
  }
  return bytes.subarray(start, start + length)
}

function decodeZipName(bytes, flags) {
  return bytes.toString((flags & 0x800) !== 0 ? 'utf8' : 'latin1')
}

function safeArchiveName(name) {
  if (!name || name.includes('\0') || name.includes('\\') || name.startsWith('/') || /^[A-Za-z]:/.test(name)) {
    throw new Error(`Unsafe ZIP entry path: ${JSON.stringify(name)}`)
  }
  const parts = name.replace(/\/$/, '').split('/')
  if (parts.some((part) => !part || part === '.' || part === '..')) {
    throw new Error(`Unsafe ZIP entry path: ${JSON.stringify(name)}`)
  }
  return parts.join('/')
}

export function inspectZip(input) {
  const bytes = Buffer.isBuffer(input) ? input : readFileSync(input)
  const eocdOffset = findEndOfCentralDirectory(bytes)
  const disk = bytes.readUInt16LE(eocdOffset + 4)
  const centralDisk = bytes.readUInt16LE(eocdOffset + 6)
  const diskEntries = bytes.readUInt16LE(eocdOffset + 8)
  const entryCount = bytes.readUInt16LE(eocdOffset + 10)
  const centralSize = bytes.readUInt32LE(eocdOffset + 12)
  const centralOffset = bytes.readUInt32LE(eocdOffset + 16)
  const commentLength = bytes.readUInt16LE(eocdOffset + 20)
  if (disk !== 0 || centralDisk !== 0 || diskEntries !== entryCount) throw new Error('Multi-disk ZIP archives are not supported')
  if (entryCount === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) throw new Error('ZIP64 archives are not supported')
  if (eocdOffset + 22 + commentLength !== bytes.length) throw new Error('Malformed ZIP end record or trailing data')
  if (centralOffset + centralSize !== eocdOffset) throw new Error('Malformed ZIP central-directory bounds')

  const entries = []
  const names = new Set()
  const localRanges = []
  let offset = centralOffset
  for (let index = 0; index < entryCount; index += 1) {
    if (checkedSlice(bytes, offset, 46, 'central-directory header').readUInt32LE(0) !== 0x02014b50) {
      throw new Error('Malformed ZIP central-directory entry')
    }
    const versionMadeBy = bytes.readUInt16LE(offset + 4)
    const flags = bytes.readUInt16LE(offset + 8)
    const method = bytes.readUInt16LE(offset + 10)
    const compressedSize = bytes.readUInt32LE(offset + 20)
    const uncompressedSize = bytes.readUInt32LE(offset + 24)
    const nameLength = bytes.readUInt16LE(offset + 28)
    const extraLength = bytes.readUInt16LE(offset + 30)
    const entryCommentLength = bytes.readUInt16LE(offset + 32)
    const externalAttributes = bytes.readUInt32LE(offset + 38)
    const localOffset = bytes.readUInt32LE(offset + 42)
    if (compressedSize === 0xffffffff || uncompressedSize === 0xffffffff || localOffset === 0xffffffff) {
      throw new Error('ZIP64 entries are not supported')
    }
    if ((flags & 1) !== 0) throw new Error('Encrypted ZIP entries are not supported')
    if (method !== 0 && method !== 8) throw new Error(`Unsupported ZIP compression method: ${method}`)
    const rawName = checkedSlice(bytes, offset + 46, nameLength, 'entry name')
    const name = decodeZipName(rawName, flags)
    const canonicalName = safeArchiveName(name)
    const collisionKey = canonicalName.normalize('NFC').toLowerCase()
    if (names.has(collisionKey)) throw new Error(`Duplicate ZIP entry: ${canonicalName}`)
    names.add(collisionKey)

    if (checkedSlice(bytes, localOffset, 30, 'local header').readUInt32LE(0) !== 0x04034b50) throw new Error(`Malformed local ZIP header: ${name}`)
    const localFlags = bytes.readUInt16LE(localOffset + 6)
    const localMethod = bytes.readUInt16LE(localOffset + 8)
    const localNameLength = bytes.readUInt16LE(localOffset + 26)
    const localExtraLength = bytes.readUInt16LE(localOffset + 28)
    const localName = decodeZipName(checkedSlice(bytes, localOffset + 30, localNameLength, 'local entry name'), localFlags)
    if (localName !== name || localMethod !== method || localFlags !== flags) throw new Error(`Inconsistent ZIP headers: ${name}`)
    const dataOffset = localOffset + 30 + localNameLength + localExtraLength
    const compressed = checkedSlice(bytes, dataOffset, compressedSize, 'entry data')
    if (dataOffset + compressedSize > centralOffset) throw new Error(`ZIP entry overlaps its central directory: ${name}`)
    localRanges.push({ start: localOffset, end: dataOffset + compressedSize, name })

    const platform = versionMadeBy >>> 8
    const mode = platform === 3 ? externalAttributes >>> 16 : 0
    const fileType = mode & 0o170000
    const nameIsDirectory = name.endsWith('/')
    let type = nameIsDirectory ? 'directory' : 'file'
    if (fileType === 0o040000) type = 'directory'
    else if (fileType === 0o120000) type = 'symlink'
    else if (fileType !== 0 && fileType !== 0o100000) throw new Error(`Unsafe special ZIP entry type: ${name}`)
    if (nameIsDirectory && type !== 'directory') throw new Error(`Inconsistent ZIP directory entry: ${name}`)

    let symlinkTarget
    if (type === 'symlink') {
      if (uncompressedSize > 4096 || compressedSize > 8192) throw new Error(`Unreasonably large ZIP symlink target: ${name}`)
      const content = method === 0 ? compressed : inflateRawSync(compressed, { maxOutputLength: 4097 })
      if (content.length !== uncompressedSize) throw new Error(`Invalid ZIP entry size: ${name}`)
      symlinkTarget = content.toString('utf8')
    }
    entries.push({ name, canonicalName, type, mode, symlinkTarget })
    offset += 46 + nameLength + extraLength + entryCommentLength
  }
  if (offset !== centralOffset + centralSize) throw new Error('ZIP central-directory size does not match its entries')
  localRanges.sort((left, right) => left.start - right.start)
  for (let index = 1; index < localRanges.length; index += 1) {
    if (localRanges[index].start < localRanges[index - 1].end) {
      throw new Error(`Overlapping ZIP local entries: ${localRanges[index - 1].name} and ${localRanges[index].name}`)
    }
  }
  return entries
}

function assertRelativeSymlinkWithinApp(entry) {
  const target = entry.symlinkTarget
  if (!target || target.includes('\0') || target.includes('\\') || path.posix.isAbsolute(target) || /^[A-Za-z]:/.test(target)) {
    throw new Error(`Unsafe symlink target in ${entry.name}: ${JSON.stringify(target)}`)
  }
  const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(entry.canonicalName), target))
  if (resolved !== 'JobOS.app' && !resolved.startsWith('JobOS.app/')) {
    throw new Error(`Symlink escapes JobOS.app: ${entry.name} -> ${target}`)
  }
}

export function validateInnerArchive(input) {
  const entries = inspectZip(input)
  if (entries.length === 0 || entries.some((entry) => entry.canonicalName !== 'JobOS.app' && !entry.canonicalName.startsWith('JobOS.app/'))) {
    throw new Error('Inner archive must contain exactly one top-level JobOS.app')
  }
  if (!entries.some((entry) => entry.canonicalName === 'JobOS.app' && entry.type === 'directory')) {
    throw new Error('Inner archive is missing its JobOS.app root directory')
  }
  for (const entry of entries) if (entry.type === 'symlink') assertRelativeSymlinkWithinApp(entry)
  return entries
}

export function validateOuterArchive(input, rootName, expectedInnerArchiveName = innerArchiveName) {
  const entries = inspectZip(input)
  const expected = new Set([
    rootName,
    `${rootName}/Update JobOS.command`,
    `${rootName}/VERIFIED.txt`,
    `${rootName}/${expectedInnerArchiveName}`
  ])
  if (entries.length !== expected.size || entries.some((entry) => !expected.has(entry.canonicalName))) {
    throw new Error('Outer archive must contain one root directory and exactly the updater, receipt, and inner archive')
  }
  const root = entries.find((entry) => entry.canonicalName === rootName)
  if (root?.type !== 'directory') throw new Error('Outer archive root must be a directory')
  if (entries.some((entry) => entry.type === 'symlink')) throw new Error('Outer archive must not contain symlinks')
  const updater = entries.find((entry) => entry.canonicalName === `${rootName}/Update JobOS.command`)
  if (updater?.type !== 'file' || (updater.mode & 0o777) !== 0o755) {
    throw new Error('Update JobOS.command must have archived mode 0755')
  }
  return entries
}

export function validateAppIdentityFacts(facts) {
  const architectures = facts.architectures.trim().split(/\s+/).filter(Boolean)
  if (architectures.length !== 1 || architectures[0] !== 'arm64') throw new Error(`Packaged executable must be arm64-only, found: ${facts.architectures}`)
  if (facts.bundleIdentifier !== appIdentifier) throw new Error(`Unexpected bundle identifier: ${facts.bundleIdentifier}`)
  if (facts.bundleName !== productName) throw new Error(`Unexpected product name: ${facts.bundleName}`)
  if (facts.bundleExecutable !== productName) throw new Error(`Unexpected executable name: ${facts.bundleExecutable}`)
  if (!new RegExp(`^Identifier=${appIdentifier.replaceAll('.', '\\.')}$`, 'm').test(facts.codesignDetails)) {
    throw new Error('Packaged code-signing identifier does not match the bundle identifier')
  }
  if (!/^Signature=adhoc$/m.test(facts.codesignDetails)) throw new Error('Packaged app must use an ad-hoc code signature')
  if (!/^TeamIdentifier=not set$/m.test(facts.codesignDetails)) throw new Error('Ad-hoc packaged app must not declare a signing team')
}

function plistValue(appPath, key) {
  return run('/usr/bin/plutil', ['-extract', key, 'raw', '-o', '-', path.join(appPath, 'Contents/Info.plist')], { capture: true })
}

function verifyPackagedApp(appPath) {
  const executablePath = path.join(appPath, 'Contents/MacOS/JobOS')
  if (!statSync(executablePath).isFile()) throw new Error('Packaged JobOS executable is missing')
  run('/usr/bin/codesign', ['--verify', '--deep', '--strict', appPath])
  const codesignDetails = run('/usr/bin/codesign', ['-d', '--verbose=4', appPath], { capture: true })
  validateAppIdentityFacts({
    architectures: run('/usr/bin/lipo', ['-archs', executablePath], { capture: true }),
    bundleIdentifier: plistValue(appPath, 'CFBundleIdentifier'),
    bundleName: plistValue(appPath, 'CFBundleName'),
    bundleExecutable: plistValue(appPath, 'CFBundleExecutable'),
    codesignDetails
  })
  const packagedSource = readFileSync(path.join(appPath, 'Contents/Resources/app.asar'))
  if (!packagedSource.includes(Buffer.from(keychainService))) throw new Error(`Packaged app does not contain the stable Keychain service identifier ${keychainService}`)
}

function verifySourceIdentity() {
  const desktopPackage = JSON.parse(readFileSync(path.join(desktopRoot, 'package.json'), 'utf8'))
  if (desktopPackage.build?.appId !== appIdentifier || desktopPackage.build?.productName !== productName) {
    throw new Error('Desktop package identity does not match the updater identity')
  }
  const credentialSource = readFileSync(path.join(desktopRoot, 'src/main/credentialStore.ts'), 'utf8')
  if (!credentialSource.includes(`const KEYCHAIN_SERVICE = '${keychainService}'`)) {
    throw new Error(`Source does not declare the stable Keychain service identifier ${keychainService}`)
  }
}

export function createVerificationReceipt({ generatedAt, sourceCommit, version, innerSize, innerSha256 }) {
  return `JobOS MacBook Update Receipt\n\nThis file is a build receipt, not a cryptographic signature.\n\nGenerated: ${generatedAt}\nSource commit: ${sourceCommit}\nApp version: ${version}\nProduct: ${productName}\nExecutable: ${productName}\nBundle identifier: ${appIdentifier}\nArchitecture: arm64\nKeychain service identifier: ${keychainService}\nSigning: ad-hoc signed; not Developer ID signed; not notarized\n\nScope\nThis updater replaces only the JobOS desktop app. It does not update the JobOS API service, runtime configuration, application data, or Keychain credentials.\n\nInstall\n1. Unzip this outer MacBook update.\n2. Double-click “Update JobOS.command”.\n3. The updater verifies, replaces, and reopens JobOS while preserving external runtime config and Keychain data.\n\nInner archive receipt\nFilename: ${innerArchiveName}\nSize: ${innerSize} bytes\nSHA-256: ${innerSha256}\n\nThe outer ZIP SHA-256 is not embedded here because changing this receipt changes that ZIP. It is printed separately by the generation command.\n`
}

export function createUpdater(innerSha256) {
  return `#!/bin/zsh
set -euo pipefail

BUNDLE_DIR="\${0:A:h}"
PACKAGE="$BUNDLE_DIR/${innerArchiveName}"
EXPECTED_SHA256=${shellSingleQuote(innerSha256)}
TEMP_DIR="$(mktemp -d)"
STAGE_ROOT=""
BACKUP_ROOT=""
BACKUP_APP=""
TRANSACTION_MARKER=""
LOCK_FILE=""
REPLACED=0
COMMITTED=0

running_target_pids() {
  /bin/ps -axo pid=,command= | while read -r pid command; do
    [[ "$command" == "$APP_PATH/Contents/"* ]] && print -r -- "$pid"
  done
  return 0
}

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
  [[ -n "$TRANSACTION_MARKER" && -f "$TRANSACTION_MARKER" && ! -L "$TRANSACTION_MARKER" ]] && /bin/rm -f "$TRANSACTION_MARKER"
  [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]] && /bin/rm -rf "$STAGE_ROOT"
  [[ -n "$BACKUP_ROOT" && -d "$BACKUP_ROOT" ]] && /bin/rm -rf "$BACKUP_ROOT"
  /bin/rm -rf "$TEMP_DIR"
  return "$exit_status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

if [[ ( -n "\${JOBOS_UPDATER_TEST_FAIL_AFTER_REPLACE:-}" || -n "\${JOBOS_UPDATER_TEST_FAIL_AFTER_COMMIT:-}" || -n "\${JOBOS_UPDATER_TEST_STOP_AFTER_RECOVERY:-}" ) && "\${JOBOS_UPDATER_SMOKE_TEST:-0}" != "1" ]]; then
  print -u2 "Updater test failure flags are only available in smoke-test mode."
  exit 1
fi

actual_sha256="$(/usr/bin/shasum -a 256 "$PACKAGE" | /usr/bin/cut -d' ' -f1)"
if [[ "$actual_sha256" != "$EXPECTED_SHA256" ]]; then
  print -u2 "Update package checksum mismatch. Delete this download and get a fresh copy."
  exit 1
fi

if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" ]]; then
  if [[ -z "\${JOBOS_INSTALL_PATH:-}" || -z "\${JOBOS_UPDATER_TEST_ROOT:-}" ]]; then
    print -u2 "Smoke-test mode requires JOBOS_INSTALL_PATH and JOBOS_UPDATER_TEST_ROOT."
    exit 1
  fi
  TEST_ROOT="\${JOBOS_UPDATER_TEST_ROOT:A}"
  APP_PATH="\${JOBOS_INSTALL_PATH:A}"
  if [[ ! -d "$TEST_ROOT" || -L "$TEST_ROOT" || "\${APP_PATH:t}" != "JobOS.app" || ( "\${APP_PATH:h}" != "$TEST_ROOT" && "\${APP_PATH:h}" != "$TEST_ROOT/"* ) ]]; then
    print -u2 "Unsafe smoke-test install path: $APP_PATH"
    exit 1
  fi
elif [[ -n "\${JOBOS_INSTALL_PATH:-}" || -n "\${JOBOS_UPDATER_TEST_ROOT:-}" ]]; then
  print -u2 "JOBOS_INSTALL_PATH and JOBOS_UPDATER_TEST_ROOT are reserved for packaged updater smoke tests."
  exit 1
elif [[ -d "/Applications/JobOS.app" ]]; then
  APP_PATH="/Applications/JobOS.app"
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
PARENT_DIR="\${APP_PATH:h}"
TRANSACTION_MARKER="$PARENT_DIR/.JobOS.update-transaction"
LOCK_FILE="$PARENT_DIR/.JobOS.update-lock"

acquire_update_lock() {
  if [[ -L "$LOCK_FILE" || ( -e "$LOCK_FILE" && ! -f "$LOCK_FILE" ) ]]; then
    print -u2 "Refusing unsafe JobOS updater lock: $LOCK_FILE"
    return 1
  fi
  ( umask 077; : >> "$LOCK_FILE" )
  exec 9>>"$LOCK_FILE"
  if ! /usr/bin/lockf -s -t 0 9; then
    print -u2 "Another JobOS updater is already running."
    return 1
  fi
}

recover_interrupted_update() {
  [[ ! -e "$TRANSACTION_MARKER" ]] && return
  if [[ -L "$TRANSACTION_MARKER" || ! -f "$TRANSACTION_MARKER" ]]; then
    print -u2 "Refusing unsafe JobOS update transaction marker: $TRANSACTION_MARKER"
    exit 1
  fi
  interrupted_root="$(<"$TRANSACTION_MARKER")"
  if [[ -z "$interrupted_root" || "$interrupted_root" == *$'\n'* || "$interrupted_root" != "$PARENT_DIR/.JobOS.backup."* || "\${interrupted_root:h}" != "$PARENT_DIR" || -L "$interrupted_root" || ! -d "$interrupted_root" ]]; then
    print -u2 "Refusing invalid JobOS update transaction marker."
    exit 1
  fi
  interrupted_backup="$interrupted_root/JobOS.app"
  interrupted_had_app="$interrupted_root/had-existing-app"
  if [[ -f "$interrupted_had_app" && ! -L "$interrupted_had_app" ]]; then
    if [[ -d "$interrupted_backup" && ! -L "$interrupted_backup" ]]; then
      [[ -e "$APP_PATH" ]] && /bin/rm -rf "$APP_PATH"
      /bin/mv "$interrupted_backup" "$APP_PATH"
      print "Recovered the previous JobOS app from an interrupted update."
    elif [[ ! -d "$APP_PATH" || -L "$APP_PATH" ]]; then
      print -u2 "The interrupted JobOS update cannot be recovered automatically."
      exit 1
    fi
  else
    [[ -e "$APP_PATH" ]] && /bin/rm -rf "$APP_PATH"
  fi
  /bin/rm -rf "$interrupted_root"
  /bin/rm -f "$TRANSACTION_MARKER"
}

commit_update() {
  # Removing the durable marker is the commit point. Until that succeeds, the
  # complete previous app remains available for rollback or next-run recovery.
  trap '' INT TERM HUP
  /bin/rm -f "$TRANSACTION_MARKER"
  TRANSACTION_MARKER=""
  COMMITTED=1
  BACKUP_APP=""
  if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" && "\${JOBOS_UPDATER_TEST_FAIL_AFTER_COMMIT:-0}" == "1" ]]; then
    print -u2 "Deliberate post-commit smoke-test failure."
    exit 88
  fi
  /bin/rm -rf "$BACKUP_ROOT"
  BACKUP_ROOT=""
  trap 'exit 130' INT TERM HUP
}

acquire_update_lock
recover_interrupted_update
if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" && "\${JOBOS_UPDATER_TEST_STOP_AFTER_RECOVERY:-0}" == "1" ]]; then
  print "Interrupted-update recovery smoke test passed."
  exit 87
fi
print "Updating $APP_PATH…"

/usr/bin/ditto -x -k "$PACKAGE" "$TEMP_DIR"
if [[ ! -d "$TEMP_DIR/JobOS.app" ]]; then
  print -u2 "JobOS.app was not found in the update package."
  exit 1
fi
/usr/bin/codesign --verify --deep --strict "$TEMP_DIR/JobOS.app"

STAGE_ROOT="$(/usr/bin/mktemp -d "$PARENT_DIR/.JobOS.update.XXXXXX")"
STAGED_APP="$STAGE_ROOT/JobOS.app"
BACKUP_ROOT="$(/usr/bin/mktemp -d "$PARENT_DIR/.JobOS.backup.XXXXXX")"
BACKUP_APP="$BACKUP_ROOT/JobOS.app"
/usr/bin/ditto "$TEMP_DIR/JobOS.app" "$STAGED_APP"
/usr/bin/codesign --verify --deep --strict "$STAGED_APP"

old_pids=(\${(f)"$(running_target_pids)"})
if (( \${#old_pids} > 0 )); then
  /usr/bin/osascript -e 'tell application id "${appIdentifier}" to quit' 2>/dev/null || true
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

trap '' INT TERM HUP
if [[ -d "$APP_PATH" ]]; then
  /usr/bin/touch "$BACKUP_ROOT/had-existing-app"
fi
marker_temp="$TRANSACTION_MARKER.$$"
( umask 077; print -r -- "$BACKUP_ROOT" > "$marker_temp" )
/bin/mv "$marker_temp" "$TRANSACTION_MARKER"
current_pids=(\${(f)"$(running_target_pids)"})
if (( \${#current_pids} > 0 )); then
  /bin/rm -f "$TRANSACTION_MARKER"
  print -u2 "JobOS restarted before replacement. Quit it, then run this updater again."
  exit 1
fi
if [[ -d "$APP_PATH" ]]; then
  /bin/mv "$APP_PATH" "$BACKUP_APP"
fi
REPLACED=1
/bin/mv "$STAGED_APP" "$APP_PATH"
trap 'exit 130' INT TERM HUP
/usr/bin/codesign --verify --deep --strict "$APP_PATH"

if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" && "\${JOBOS_UPDATER_TEST_FAIL_AFTER_REPLACE:-0}" == "1" ]]; then
  print -u2 "Deliberate post-replacement smoke-test failure."
  exit 86
fi

/usr/bin/xattr -dr com.apple.quarantine "$APP_PATH" 2>/dev/null || true
if [[ "\${JOBOS_UPDATER_SMOKE_TEST:-0}" == "1" ]]; then
  commit_update
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

commit_update
print "JobOS updated and opened successfully."
print "You can close this window."
`
}

function treeDigest(root) {
  const hash = createHash('sha256')
  function visit(current, relative) {
    const metadata = lstatSync(current)
    hash.update(`${relative}\0${metadata.mode & 0o7777}\0`)
    if (metadata.isSymbolicLink()) hash.update(`L${readlinkSync(current)}\0`)
    else if (metadata.isDirectory()) {
      hash.update('D\0')
      for (const name of readdirSync(current).sort()) visit(path.join(current, name), relative ? `${relative}/${name}` : name)
    } else if (metadata.isFile()) hash.update('F\0').update(readFileSync(current))
    else throw new Error(`Unexpected smoke fixture type: ${current}`)
  }
  visit(root, '.')
  return hash.digest('hex')
}

function runSmokeTests(updaterPath, verifyRoot) {
  const testRootPath = path.join(verifyRoot, 'updater-smoke')
  mkdirSync(testRootPath)
  const testRoot = realpathSync(testRootPath)
  const sentinels = [path.join(testRoot, 'external-config.json'), path.join(testRoot, 'external-state.sqlite')]
  writeFileSync(sentinels[0], '{"synthetic":"config"}\n')
  writeFileSync(sentinels[1], randomBytes(257))
  const sentinelHashes = sentinels.map(sha256)
  const smokeEnv = (installPath, extra = {}) => ({
    JOBOS_INSTALL_PATH: installPath,
    JOBOS_UPDATER_SMOKE_TEST: '1',
    JOBOS_UPDATER_TEST_ROOT: testRoot,
    ...extra
  })

  const freshApp = path.join(testRoot, 'fresh', 'JobOS.app')
  run('/bin/zsh', [updaterPath], { env: smokeEnv(freshApp) })
  verifyPackagedApp(freshApp)

  const replacementApp = path.join(testRoot, 'replacement', 'JobOS.app')
  mkdirSync(path.join(replacementApp, 'Contents'), { recursive: true })
  writeFileSync(path.join(replacementApp, 'Contents', 'old-version.txt'), 'synthetic previous version')
  run('/bin/zsh', [updaterPath], { env: smokeEnv(replacementApp) })
  verifyPackagedApp(replacementApp)
  if (existsSync(path.join(replacementApp, 'Contents', 'old-version.txt'))) throw new Error('Replacement smoke test retained previous app bytes')

  const rollbackApp = path.join(testRoot, 'rollback', 'JobOS.app')
  mkdirSync(path.join(rollbackApp, 'Contents', 'Resources'), { recursive: true })
  writeFileSync(path.join(rollbackApp, 'Contents', 'previous.bin'), randomBytes(513))
  writeFileSync(path.join(rollbackApp, 'Contents', 'Resources', 'marker.txt'), 'exact rollback fixture\n')
  symlinkSync('../previous.bin', path.join(rollbackApp, 'Contents', 'Resources', 'previous-link'))
  const previousDigest = treeDigest(rollbackApp)
  const failed = spawnSync('/bin/zsh', [updaterPath], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, ...smokeEnv(rollbackApp, { JOBOS_UPDATER_TEST_FAIL_AFTER_REPLACE: '1' }) },
    stdio: 'pipe'
  })
  if (failed.status !== 86) throw new Error(`Rollback smoke test did not fail at the intended point (status ${failed.status})`)
  if (treeDigest(rollbackApp) !== previousDigest) throw new Error('Rollback did not restore the exact previous app bytes and metadata')

  const committedApp = path.join(testRoot, 'committed-cleanup', 'JobOS.app')
  mkdirSync(path.join(committedApp, 'Contents'), { recursive: true })
  writeFileSync(path.join(committedApp, 'Contents', 'old-version.txt'), 'synthetic previous version')
  const committedFailure = spawnSync('/bin/zsh', [updaterPath], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, ...smokeEnv(committedApp, { JOBOS_UPDATER_TEST_FAIL_AFTER_COMMIT: '1' }) },
    stdio: 'pipe'
  })
  if (committedFailure.status !== 88) throw new Error(`Post-commit smoke test stopped unexpectedly (status ${committedFailure.status})`)
  verifyPackagedApp(committedApp)
  const committedParentEntries = readdirSync(path.dirname(committedApp))
  if (committedParentEntries.some((name) => name === '.JobOS.update-transaction' || name.startsWith('.JobOS.backup.'))) {
    throw new Error('Post-commit cleanup retained transaction state')
  }

  const interruptedExistingParent = path.join(testRoot, 'interrupted-existing')
  const interruptedExistingApp = path.join(interruptedExistingParent, 'JobOS.app')
  mkdirSync(interruptedExistingParent)
  const interruptedExistingBackupRoot = mkdtempSync(path.join(interruptedExistingParent, '.JobOS.backup.'))
  const interruptedExistingBackupApp = path.join(interruptedExistingBackupRoot, 'JobOS.app')
  mkdirSync(path.join(interruptedExistingApp, 'Contents'), { recursive: true })
  writeFileSync(path.join(interruptedExistingApp, 'Contents', 'partial-new.txt'), 'partial replacement\n')
  mkdirSync(path.join(interruptedExistingBackupApp, 'Contents'), { recursive: true })
  writeFileSync(path.join(interruptedExistingBackupApp, 'Contents', 'old-version.txt'), 'recover this exact app\n')
  symlinkSync('old-version.txt', path.join(interruptedExistingBackupApp, 'Contents', 'old-version-link'))
  writeFileSync(path.join(interruptedExistingBackupRoot, 'had-existing-app'), '')
  const interruptedExistingDigest = treeDigest(interruptedExistingBackupApp)
  const interruptedExistingMarker = path.join(interruptedExistingParent, '.JobOS.update-transaction')
  writeFileSync(interruptedExistingMarker, `${interruptedExistingBackupRoot}\n`, { mode: 0o600 })
  const recoveredExisting = spawnSync('/bin/zsh', [updaterPath], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, ...smokeEnv(interruptedExistingApp, { JOBOS_UPDATER_TEST_STOP_AFTER_RECOVERY: '1' }) },
    stdio: 'pipe'
  })
  if (recoveredExisting.status !== 87) throw new Error(`Interrupted existing-app recovery stopped unexpectedly (status ${recoveredExisting.status})`)
  if (treeDigest(interruptedExistingApp) !== interruptedExistingDigest) throw new Error('Interrupted update did not restore the exact previous app')
  if (existsSync(interruptedExistingMarker) || existsSync(interruptedExistingBackupRoot)) throw new Error('Interrupted existing-app recovery retained transaction state')

  const interruptedFreshParent = path.join(testRoot, 'interrupted-fresh')
  const interruptedFreshApp = path.join(interruptedFreshParent, 'JobOS.app')
  mkdirSync(interruptedFreshParent)
  const interruptedFreshBackupRoot = mkdtempSync(path.join(interruptedFreshParent, '.JobOS.backup.'))
  mkdirSync(path.join(interruptedFreshApp, 'Contents'), { recursive: true })
  writeFileSync(path.join(interruptedFreshApp, 'Contents', 'partial-new.txt'), 'partial fresh install\n')
  const interruptedFreshMarker = path.join(interruptedFreshParent, '.JobOS.update-transaction')
  writeFileSync(interruptedFreshMarker, `${interruptedFreshBackupRoot}\n`, { mode: 0o600 })
  const recoveredFresh = spawnSync('/bin/zsh', [updaterPath], {
    cwd: repoRoot,
    encoding: 'utf8',
    env: { ...process.env, ...smokeEnv(interruptedFreshApp, { JOBOS_UPDATER_TEST_STOP_AFTER_RECOVERY: '1' }) },
    stdio: 'pipe'
  })
  if (recoveredFresh.status !== 87) throw new Error(`Interrupted fresh-install recovery stopped unexpectedly (status ${recoveredFresh.status})`)
  if (existsSync(interruptedFreshApp)) throw new Error('Interrupted fresh-install recovery retained a partial app')
  if (existsSync(interruptedFreshMarker) || existsSync(interruptedFreshBackupRoot)) throw new Error('Interrupted fresh-install recovery retained transaction state')

  sentinels.forEach((sentinel, index) => {
    if (sha256(sentinel) !== sentinelHashes[index]) throw new Error(`Updater changed external sentinel: ${sentinel}`)
  })
}

export async function main() {
  if (process.platform !== 'darwin') throw new Error('MacBook updater packaging requires macOS')
  const expectedCommit = process.env.JOBOS_EXPECTED_SOURCE_COMMIT
  const sourceCommit = assertCleanWorktree(expectedCommit)
  verifySourceIdentity()
  let buildRoot = ''
  rmSync(innerArchivePath, { force: true })

  try {
    run('pnpm', ['--filter', '@jobos/contracts', 'build'])
    run('pnpm', ['--filter', '@jobos/desktop', 'package:mac'])
    assertCleanWorktree(expectedCommit)

    if (!existsSync(innerArchivePath) || !statSync(innerArchivePath).isFile()) throw new Error(`Packaging did not produce ${innerArchivePath}`)
    validateInnerArchive(innerArchivePath)
    run('/usr/bin/unzip', ['-tq', innerArchivePath])

    const appVerifyRoot = mkdtempSync(path.join(tmpdir(), 'jobos-inner-verify-'))
    try {
      run('/usr/bin/ditto', ['-x', '-k', innerArchivePath, appVerifyRoot])
      verifyPackagedApp(path.join(appVerifyRoot, 'JobOS.app'))
    } finally {
      rmSync(appVerifyRoot, { recursive: true, force: true })
    }

    const generatedAt = new Date().toISOString()
    const timestamp = generatedAt.replaceAll(/\D/g, '')
    const nonce = randomBytes(16).toString('hex')
    const bundleName = `JobOS-MacBook-Update-${timestamp}-${sourceCommit.slice(0, 8)}-${nonce}`
    const publishedArchivePath = path.join(handoffRoot, `${bundleName}.zip`)
    const innerSize = statSync(innerArchivePath).size
    const innerSha256 = sha256(innerArchivePath)
    const verification = createVerificationReceipt({ generatedAt, sourceCommit, version: packageJson.version, innerSize, innerSha256 })

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
    run('/usr/bin/ditto', ['-c', '-k', '--norsrc', '--keepParent', bundleDir, stagedArchivePath])
    validateOuterArchive(stagedArchivePath, bundleName)
    run('/usr/bin/unzip', ['-tq', stagedArchivePath])

    const verifyRoot = mkdtempSync(path.join(tmpdir(), 'jobos-macbook-verify-'))
    try {
      run('/usr/bin/ditto', ['-x', '-k', stagedArchivePath, verifyRoot])
      const extractedDir = path.join(verifyRoot, bundleName)
      const extractedUpdater = path.join(extractedDir, 'Update JobOS.command')
      const extractedInner = path.join(extractedDir, innerArchiveName)
      const extractedVerification = path.join(extractedDir, 'VERIFIED.txt')
      run('/bin/zsh', ['-n', extractedUpdater])
      validateInnerArchive(extractedInner)
      if (sha256(extractedInner) !== innerSha256) throw new Error('Extracted inner archive checksum mismatch')
      if (readFileSync(extractedVerification, 'utf8') !== verification) throw new Error('Extracted verification receipt mismatch')
      runSmokeTests(extractedUpdater, verifyRoot)
    } finally {
      rmSync(verifyRoot, { recursive: true, force: true })
    }

    const outerSize = statSync(stagedArchivePath).size
    const outerSha256 = sha256(stagedArchivePath)
    linkSync(stagedArchivePath, publishedArchivePath)
    process.stdout.write(`MACBOOK_UPDATE=${publishedArchivePath}\nSOURCE_COMMIT=${sourceCommit}\nOUTER_SIZE=${outerSize}\nOUTER_SHA256=${outerSha256}\nINNER_SIZE=${innerSize}\nINNER_SHA256=${innerSha256}\n`)
  } finally {
    if (buildRoot) rmSync(buildRoot, { recursive: true, force: true })
    rmSync(innerArchivePath, { force: true })
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
