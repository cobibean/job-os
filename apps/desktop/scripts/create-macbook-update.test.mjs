import assert from 'node:assert/strict'
import { spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { describe, test } from 'node:test'
import {
  createUpdater,
  createVerificationReceipt,
  inspectZip,
  validateAppIdentityFacts,
  validateInnerArchive,
  validateOuterArchive,
  validateProvenance
} from './create-macbook-update.mjs'

function makeZip(entries) {
  const localParts = []
  const centralParts = []
  let localOffset = 0
  for (const entry of entries) {
    const name = Buffer.from(entry.name)
    const content = Buffer.from(entry.content ?? '')
    const mode = entry.mode ?? (entry.name.endsWith('/') ? 0o040755 : 0o100644)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt16LE(0x800, 6)
    local.writeUInt32LE(content.length, 18)
    local.writeUInt32LE(content.length, 22)
    local.writeUInt16LE(name.length, 26)
    localParts.push(local, name, content)

    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE((3 << 8) | 20, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt16LE(0x800, 8)
    central.writeUInt32LE(content.length, 20)
    central.writeUInt32LE(content.length, 24)
    central.writeUInt16LE(name.length, 28)
    central.writeUInt32LE((mode << 16) >>> 0, 38)
    central.writeUInt32LE(localOffset, 42)
    centralParts.push(central, name)
    localOffset += local.length + name.length + content.length
  }
  const centralDirectory = Buffer.concat(centralParts)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(centralDirectory.length, 12)
  end.writeUInt32LE(localOffset, 16)
  return Buffer.concat([...localParts, centralDirectory, end])
}

const validInnerEntries = [
  { name: 'JobOS.app/' },
  { name: 'JobOS.app/Contents/' },
  { name: 'JobOS.app/Contents/MacOS/' },
  { name: 'JobOS.app/Contents/MacOS/JobOS', mode: 0o100755 },
  { name: 'JobOS.app/Contents/Frameworks/' },
  { name: 'JobOS.app/Contents/Frameworks/Electron Framework.framework/' },
  { name: 'JobOS.app/Contents/Frameworks/Electron Framework.framework/Versions/' },
  { name: 'JobOS.app/Contents/Frameworks/Electron Framework.framework/Versions/A/' },
  {
    name: 'JobOS.app/Contents/Frameworks/Electron Framework.framework/Versions/Current',
    mode: 0o120777,
    content: 'A'
  }
]

describe('source provenance', () => {
  const commit = '0123456789abcdef0123456789abcdef01234567'

  test('requires an exact full expected commit and a completely clean status', () => {
    assert.equal(validateProvenance({ expectedCommit: commit.toUpperCase(), currentCommit: commit, worktreeStatus: '' }), commit)
    assert.throws(() => validateProvenance({ expectedCommit: commit.slice(0, 12), currentCommit: commit, worktreeStatus: '' }), /full 40-hex/)
    assert.throws(() => validateProvenance({ expectedCommit: 'f'.repeat(40), currentCommit: commit, worktreeStatus: '' }), /Source commit mismatch/)
    assert.throws(
      () => validateProvenance({ expectedCommit: commit, currentCommit: commit, worktreeStatus: '?? packages/docx-engine/new.ts' }),
      /entire worktree must be clean/
    )
  })
})

describe('ZIP validation', () => {
  test('accepts one JobOS.app and a relative Electron framework symlink that stays inside it', () => {
    assert.equal(validateInnerArchive(makeZip(validInnerEntries)).length, validInnerEntries.length)
  })

  test('rejects duplicate, traversal, absolute, and special-type entries', () => {
    assert.throws(() => inspectZip(makeZip([{ name: 'same' }, { name: 'same' }])), /Duplicate/)
    assert.throws(() => inspectZip(makeZip([{ name: 'Same' }, { name: 'same' }])), /Duplicate/)
    assert.throws(() => inspectZip(makeZip([{ name: '../escape' }])), /Unsafe ZIP entry path/)
    assert.throws(() => inspectZip(makeZip([{ name: '/absolute' }])), /Unsafe ZIP entry path/)
    assert.throws(() => inspectZip(makeZip([{ name: 'device', mode: 0o020666 }])), /Unsafe special ZIP entry type/)
  })

  test('rejects extra inner roots and escaping or absolute symlinks', () => {
    assert.throws(() => validateInnerArchive(makeZip([...validInnerEntries, { name: 'extra.txt' }])), /one top-level JobOS.app/)
    const escaping = validInnerEntries.map((entry) =>
      entry.mode === 0o120777 ? { ...entry, content: '../../../../../../outside' } : entry
    )
    assert.throws(() => validateInnerArchive(makeZip(escaping)), /Symlink escapes/)
    const absolute = validInnerEntries.map((entry) => (entry.mode === 0o120777 ? { ...entry, content: '/tmp/outside' } : entry))
    assert.throws(() => validateInnerArchive(makeZip(absolute)), /Unsafe symlink target/)
  })

  test('requires the exact outer layout and updater mode 0755', () => {
    const root = 'JobOS-MacBook-Update-test'
    const inner = 'JobOS-0.1.0-arm64.zip'
    const entries = [
      { name: `${root}/` },
      { name: `${root}/Update JobOS.command`, mode: 0o100755 },
      { name: `${root}/VERIFIED.txt` },
      { name: `${root}/${inner}` }
    ]
    assert.equal(validateOuterArchive(makeZip(entries), root, inner).length, 4)
    assert.throws(() => validateOuterArchive(makeZip([...entries, { name: `${root}/extra` }]), root, inner), /exactly/)
    const wrongMode = entries.map((entry) => (entry.name.endsWith('.command') ? { ...entry, mode: 0o100744 } : entry))
    assert.throws(() => validateOuterArchive(makeZip(wrongMode), root, inner), /0755/)
  })
})

describe('packaged identity and generated artifacts', () => {
  const identity = {
    architectures: 'arm64',
    bundleIdentifier: 'com.cobibean.jobos',
    bundleName: 'JobOS',
    bundleExecutable: 'JobOS',
    codesignDetails: 'Executable=/tmp/JobOS\nIdentifier=com.cobibean.jobos\nSignature=adhoc\nTeamIdentifier=not set'
  }

  test('requires arm64-only JobOS identity and an ad-hoc signature', () => {
    assert.doesNotThrow(() => validateAppIdentityFacts(identity))
    assert.throws(() => validateAppIdentityFacts({ ...identity, architectures: 'x86_64 arm64' }), /arm64-only/)
    assert.throws(() => validateAppIdentityFacts({ ...identity, bundleIdentifier: 'example.invalid' }), /bundle identifier/)
    assert.throws(
      () => validateAppIdentityFacts({ ...identity, codesignDetails: identity.codesignDetails.replace('Identifier=com.cobibean.jobos', 'Identifier=example.mismatch') }),
      /code-signing identifier/
    )
    assert.throws(
      () => validateAppIdentityFacts({ ...identity, codesignDetails: identity.codesignDetails.replace('TeamIdentifier=not set', 'TeamIdentifier=EXAMPLETEAM') }),
      /signing team/
    )
    assert.throws(
      () => validateAppIdentityFacts({ ...identity, codesignDetails: identity.codesignDetails.replace('Signature=adhoc', 'Authority=Developer ID Application') }),
      /ad-hoc/
    )
  })

  test('writes a truthful receipt with source and identity provenance', () => {
    const receipt = createVerificationReceipt({
      generatedAt: '2026-08-16T00:00:00.000Z',
      sourceCommit: 'a'.repeat(40),
      version: '0.1.0',
      innerSize: 123,
      innerSha256: 'b'.repeat(64)
    })
    assert.match(receipt, /build receipt, not a cryptographic signature/)
    assert.match(receipt, /ad-hoc signed; not Developer ID signed; not notarized/)
    assert.match(receipt, /Source commit: a{40}/)
    assert.match(receipt, /Bundle identifier: com\.cobibean\.jobos/)
    assert.match(receipt, /replaces only the JobOS desktop app/)
    assert.match(receipt, /does not update the JobOS API service, runtime configuration, application data, or Keychain credentials/)
    assert.match(receipt, /outer ZIP SHA-256.*printed separately/si)
    assert.doesNotMatch(receipt, /cryptographically signed|signature verifies/i)
  })

  test('keeps failure injection smoke-only and after replacement verification', () => {
    const updater = createUpdater('c'.repeat(64))
    assert.match(updater, /Updater test failure flags are only available in smoke-test mode/)
    assert.match(updater, /JOBOS_UPDATER_TEST_ROOT/)
    assert.match(updater, /recover_interrupted_update/)
    assert.match(updater, /\.JobOS\.update-transaction/)
    assert.match(updater, /JOBOS_UPDATER_TEST_STOP_AFTER_RECOVERY/)
    assert.match(updater, /JOBOS_UPDATER_TEST_FAIL_AFTER_COMMIT/)
    assert.match(updater, /\( umask 077; print -r -- "\$BACKUP_ROOT"/)
    const commitFunction = updater.slice(updater.indexOf('commit_update()'), updater.indexOf('recover_interrupted_update\n'))
    assert.ok(commitFunction.indexOf('/bin/rm -f "$TRANSACTION_MARKER"') < commitFunction.indexOf('COMMITTED=1'))
    assert.ok(commitFunction.indexOf('COMMITTED=1') < commitFunction.indexOf('/bin/rm -rf "$BACKUP_ROOT"'))
    assert.ok(updater.indexOf('ditto "$TEMP_DIR/JobOS.app" "$STAGED_APP"') < updater.indexOf('tell application id'))
    assert.ok(updater.lastIndexOf('current_pids=') < updater.indexOf('/bin/mv "$APP_PATH" "$BACKUP_APP"'))
    assert.ok(updater.indexOf('codesign --verify --deep --strict "$APP_PATH"') < updater.indexOf('Deliberate post-replacement smoke-test failure'))
    assert.ok(updater.indexOf('Deliberate post-replacement smoke-test failure') < updater.indexOf('xattr -dr com.apple.quarantine'))
    assert.match(updater, /\/Applications\/JobOS\.app/)
    assert.match(updater, /\$HOME\/Applications\/JobOS\.app/)
  })

  test('an empty process scan remains successful under zsh errexit', { skip: process.platform !== 'darwin' }, () => {
    const updater = createUpdater('d'.repeat(64))
    const functionStart = updater.indexOf('running_target_pids()')
    const functionEnd = updater.indexOf('\n}\n\ncleanup()', functionStart) + 3
    const processFunction = updater.slice(functionStart, functionEnd)
    const probe = spawnSync('/bin/zsh', ['-c', `set -euo pipefail\nAPP_PATH=/tmp/jobos-no-such-app-$$\n${processFunction}\npids=(\${(f)"$(running_target_pids)"})\nprint REACHED`], {
      encoding: 'utf8'
    })
    assert.equal(probe.status, 0, probe.stderr)
    assert.equal(probe.stdout.trim(), 'REACHED')
  })

  test('the updater lock excludes a live owner and recovers after owner death', { skip: process.platform !== 'darwin' }, async () => {
    const updater = createUpdater('e'.repeat(64))
    const functionStart = updater.indexOf('acquire_update_lock()')
    const functionEnd = updater.indexOf('\n}\n\nrecover_interrupted_update()', functionStart) + 3
    const lockFunction = updater.slice(functionStart, functionEnd)
    const parentDir = mkdtempSync(path.join(tmpdir(), 'jobos-updater-lock-'))
    const common = `set -euo pipefail\nPARENT_DIR=${JSON.stringify(parentDir)}\nLOCK_FILE="$PARENT_DIR/.JobOS.update-lock"\n${lockFunction}\n`
    const owner = spawn('/bin/zsh', ['-c', `${common}acquire_update_lock\nprint READY\n/bin/sleep 30`], {
      stdio: ['ignore', 'pipe', 'pipe']
    })
    try {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Lock owner did not become ready')), 3000)
        owner.stdout.once('data', (chunk) => {
          clearTimeout(timer)
          assert.match(String(chunk), /READY/)
          resolve()
        })
        owner.once('exit', (code) => reject(new Error(`Lock owner exited early (${code})`)))
      })
      const contender = spawnSync('/bin/zsh', ['-c', `${common}acquire_update_lock`], { encoding: 'utf8' })
      assert.notEqual(contender.status, 0)
      assert.match(contender.stderr, /already running/)
      owner.kill('SIGKILL')
      await new Promise((resolve) => owner.once('exit', resolve))
      const recovery = spawnSync('/bin/zsh', ['-c', `${common}acquire_update_lock`], { encoding: 'utf8' })
      assert.equal(recovery.status, 0, recovery.stderr)
    } finally {
      if (owner.exitCode === null) owner.kill('SIGKILL')
      rmSync(parentDir, { recursive: true, force: true })
    }
  })
})
