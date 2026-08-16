#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { execFileSync, spawn } from 'node:child_process'
import { chmod, copyFile, mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, writeFile } from 'node:fs/promises'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const mediaRoot = path.join(root, 'docs/media')
const fixtureManifestPath = path.join(root, 'tests/public-release/synthetic-fixtures.json')
const expectedNode = 'v26.5.0'
function resolveTool(name, environmentName) {
  const configured = process.env[environmentName]
  if (configured) return configured
  return execFileSync('/usr/bin/env', ['which', name], { encoding: 'utf8' }).trim()
}

const ffmpeg = resolveTool('ffmpeg', 'JOBOS_MEDIA_FFMPEG')
const ffprobe = resolveTool('ffprobe', 'JOBOS_MEDIA_FFPROBE')
const exiftool = resolveTool('exiftool', 'JOBOS_MEDIA_EXIFTOOL')
const uv = resolveTool('uv', 'JOBOS_MEDIA_UV')

function fail(message) { throw new Error(message) }

function run(executable, args, options = {}) {
  try {
    return execFileSync(executable, args, {
      cwd: root,
      env: options.env,
      encoding: options.encoding ?? 'utf8',
      input: options.input,
      maxBuffer: 20 * 1024 * 1024,
      stdio: options.stdio ?? ['ignore', 'pipe', 'pipe']
    })
  } catch {
    fail(`Media command failed: ${path.basename(executable)}`)
  }
}

async function freePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') return reject(new Error('Could not reserve a loopback port'))
      const port = address.port
      server.close(error => error ? reject(error) : resolve(port))
    })
  })
}

function captureSpec(outputDirectory) {
  return {
    schemaVersion: 1,
    outputDirectory,
    actions: [
      { kind: 'wait', selector: '.app-shell[data-workspace="review"]', text: 'Northstar Kites', timeoutMs: 15000 },
      { kind: 'wait', selector: '.job-row.selected', text: 'Demo', timeoutMs: 10000 },
      { kind: 'wait', selector: '.document-announcement', text: 'No local artifacts registered; optional artifact refresh is unavailable', timeoutMs: 10000 },
      { kind: 'frames', prefix: 'frame-', start: 1, count: 6, intervalMs: 83 },
      { kind: 'wait', selector: '.job-row.selected', text: 'Demo', timeoutMs: 10000 },
      { kind: 'wait', selector: '.document-announcement', text: 'No local artifacts registered; optional artifact refresh is unavailable', timeoutMs: 10000 },
      { kind: 'capture', filename: 'jobos-hero-1440x1024.png' },
      { kind: 'frames', prefix: 'frame-', start: 7, count: 30, intervalMs: 83 },
      { kind: 'click', selector: '.layout-option', text: 'Browse', timeoutMs: 5000 },
      { kind: 'wait', selector: '.browse-reading-pane', text: 'This is fictional sample data', timeoutMs: 10000 },
      { kind: 'capture', filename: 'jobos-browse-detail-1440x1024.png' },
      { kind: 'frames', prefix: 'frame-', start: 37, count: 30, intervalMs: 83 },
      { kind: 'click', selector: '.layout-option', text: 'Review', timeoutMs: 5000 },
      { kind: 'wait', selector: '.app-shell[data-workspace="review"]', text: 'Resume Editor', timeoutMs: 10000 },
      { kind: 'frames', prefix: 'frame-', start: 67, count: 12, intervalMs: 83 },
      { kind: 'click', selector: '.edit-document-button', text: 'Cover Letter Editor', timeoutMs: 5000 },
      { kind: 'wait', selector: '.docx-document-editor', text: '(FAKE)-cover-letter.docx', timeoutMs: 15000 },
      { kind: 'wait', selector: '.document-save-state.saved', text: 'Saved', timeoutMs: 10000 },
      { kind: 'capture', filename: 'jobos-ooxml-editor-saved-1440x1024.png' },
      { kind: 'frames', prefix: 'frame-', start: 79, count: 42, intervalMs: 83 }
    ]
  }
}

async function terminateProcessGroup(child) {
  if (!child.pid) return
  try { process.kill(-child.pid, 'SIGTERM') } catch {}
  await new Promise(resolve => setTimeout(resolve, 2_000))
  try { process.kill(-child.pid, 'SIGKILL') } catch {}
}

async function launchElectron(environment, userDataDirectory) {
  const electron = path.join(root, 'apps/desktop/node_modules/.bin/electron')
  const child = spawn(electron, [
    'dist/main/main.js',
    `--user-data-dir=${userDataDirectory}`,
    '--disable-features=CalculateNativeWinOcclusion'
  ], {
    cwd: path.join(root, 'apps/desktop'),
    detached: true,
    env: environment,
    stdio: 'ignore'
  })
  const exitCode = await new Promise((resolve, reject) => {
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      void terminateProcessGroup(child).then(() => reject(new Error('Electron media capture timed out')))
    }, 90_000)
    child.once('error', error => {
      clearTimeout(timer)
      if (!timedOut) reject(error)
    })
    child.once('exit', code => {
      clearTimeout(timer)
      if (!timedOut) resolve(code)
    })
  })
  if (exitCode !== 0) fail('Electron media capture failed')
}

async function replaceAccepted(source, target) {
  await mkdir(path.dirname(target), { recursive: true })
  const temporary = `${target}.${process.pid}.tmp`
  await copyFile(source, temporary)
  await chmod(temporary, 0o644)
  await rename(temporary, target)
}

async function main() {
  if (process.version !== expectedNode) {
    fail(`Use Node.js ${expectedNode}`)
  }
  for (const tool of [ffmpeg, ffprobe, exiftool]) run('/bin/test', ['-x', tool])
  const runtime = await mkdtemp(path.join(os.tmpdir(), 'jobos-media-'))
  try {
    const profile = path.join(runtime, 'profile')
    const configPath = path.join(profile, 'config.json')
    const captureOutput = path.join(runtime, 'capture')
    const accepted = path.join(runtime, 'accepted')
    const home = path.join(runtime, 'home')
    const temporary = path.join(runtime, 'tmp')
    await Promise.all([captureOutput, accepted, home, temporary].map(directory => mkdir(directory, { recursive: true, mode: 0o700 })))
    const binPath = [...new Set([
      path.dirname(process.execPath), path.dirname(ffmpeg), path.dirname(ffprobe),
      path.dirname(exiftool), path.dirname(uv), '/usr/bin', '/bin', '/usr/sbin', '/sbin'
    ])].join(':')
    const environment = {
      PATH: binPath,
      HOME: home,
      TMPDIR: temporary,
      XDG_CONFIG_HOME: path.join(runtime, 'xdg-config'),
      XDG_CACHE_HOME: path.join(runtime, 'xdg-cache'),
      XDG_DATA_HOME: path.join(runtime, 'xdg-data'),
      LANG: 'en_US.UTF-8',
      LC_ALL: 'en_US.UTF-8',
      UV_OFFLINE: '1',
      JOBOS_DATA_DIR: profile,
      JOBOS_CONFIG_PATH: configPath,
      JOBOS_KEYCHAIN_HELPER_PATH: path.join(runtime, 'disabled-keychain-helper')
    }
    run(uv, ['run', '--frozen', '--no-sync', 'jobos-init', '--data-dir', profile, '--config-path', configPath], { env: environment })
    const config = JSON.parse(await readFile(configPath, 'utf8'))
    if (config.credentialStore?.provider !== 'file' || config.agentProvider !== 'offline') {
      fail('Synthetic profile did not use disposable file credentials and offline agent')
    }
    const port = await freePort()
    config.apiBaseUrl = `http://127.0.0.1:${port}`
    await writeFile(configPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 })
    const specPath = path.join(runtime, 'capture-spec.json')
    await writeFile(specPath, `${JSON.stringify(captureSpec(await realpath(captureOutput)), null, 2)}\n`, { mode: 0o600 })
    await launchElectron({
      ...environment,
      JOBOS_MEDIA_CAPTURE_SPEC: specPath,
      JOBOS_UV_EXECUTABLE: uv
    }, path.join(runtime, 'electron'))

    const screenshotDirectory = path.join(accepted, 'screenshots')
    await mkdir(screenshotDirectory, { recursive: true, mode: 0o700 })
    for (const filename of [
      'jobos-hero-1440x1024.png',
      'jobos-browse-detail-1440x1024.png',
      'jobos-ooxml-editor-saved-1440x1024.png'
    ]) await copyFile(path.join(captureOutput, filename), path.join(screenshotDirectory, filename))
    run(ffmpeg, [
      '-hide_banner', '-loglevel', 'error', '-y', '-framerate', '12',
      '-i', path.join(captureOutput, 'frame-%04d.png'),
      '-filter_complex', 'fps=12,scale=960:683:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle',
      '-loop', '0', '-an', '-map_metadata', '-1', path.join(accepted, 'jobos-demo.gif')
    ])
    const acceptedImages = (await readdir(screenshotDirectory)).map(name => path.join(screenshotDirectory, name))
    run(exiftool, ['-all=', '-overwrite_original', ...acceptedImages, path.join(accepted, 'jobos-demo.gif')])
    const checksumLines = []
    const mediaChecksums = new Map()
    for (const relative of [
      'screenshots/jobos-hero-1440x1024.png',
      'screenshots/jobos-browse-detail-1440x1024.png',
      'screenshots/jobos-ooxml-editor-saved-1440x1024.png',
      'jobos-demo.gif'
    ]) {
      const digest = createHash('sha256').update(await readFile(path.join(accepted, relative))).digest('hex')
      checksumLines.push(`${digest}  ${relative}`)
      mediaChecksums.set(`docs/media/${relative}`, digest)
    }
    await writeFile(path.join(accepted, 'checksums.sha256'), `${checksumLines.join('\n')}\n`, { mode: 0o600 })
    const fixtureManifest = JSON.parse(await readFile(fixtureManifestPath, 'utf8'))
    for (const asset of fixtureManifest.assets ?? []) {
      const digest = mediaChecksums.get(asset.path)
      if (digest) asset.sha256 = digest
    }
    if ([...mediaChecksums.keys()].some(mediaPath => !fixtureManifest.assets?.some(asset => asset.path === mediaPath))) {
      fail('Synthetic fixture manifest is missing a media asset')
    }
    await writeFile(path.join(accepted, 'synthetic-fixtures.json'), `${JSON.stringify(fixtureManifest, null, 2)}\n`, { mode: 0o600 })
    run(process.execPath, [path.join(root, 'docs/media/source/verify-media.mjs'), '--root', accepted], { env: environment })
    for (const relative of [
      'screenshots/jobos-hero-1440x1024.png',
      'screenshots/jobos-browse-detail-1440x1024.png',
      'screenshots/jobos-ooxml-editor-saved-1440x1024.png',
      'jobos-demo.gif',
      'checksums.sha256'
    ]) await replaceAccepted(path.join(accepted, relative), path.join(mediaRoot, relative))
    await replaceAccepted(path.join(accepted, 'synthetic-fixtures.json'), fixtureManifestPath)
    process.stdout.write('Accepted synthetic media regenerated and verified.\n')
  } finally {
    await rm(runtime, { recursive: true, force: true })
  }
}

await main().catch(error => {
  process.stderr.write(`${error instanceof Error ? error.message : 'Media capture failed'}\n`)
  process.exitCode = 1
})
