#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const rootIndex = process.argv.indexOf('--root')
const mediaRoot = rootIndex >= 0 ? path.resolve(process.argv[rootIndex + 1] ?? '') : path.join(repositoryRoot, 'docs/media')
const screenshots = [
  'screenshots/jobos-hero-1440x1024.png',
  'screenshots/jobos-browse-detail-1440x1024.png',
  'screenshots/jobos-ooxml-editor-saved-1440x1024.png'
]
const gif = 'jobos-demo.gif'

function resolveTool(name, environmentName) {
  const configured = process.env[environmentName]
  if (configured) return configured
  return execFileSync('/usr/bin/env', ['which', name], { encoding: 'utf8' }).trim()
}

const exiftool = resolveTool('exiftool', 'JOBOS_MEDIA_EXIFTOOL')
const ffprobe = resolveTool('ffprobe', 'JOBOS_MEDIA_FFPROBE')

function command(executable, args) {
  try { return execFileSync(executable, args, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }) }
  catch { throw new Error(`Media verification command failed: ${path.basename(executable)}`) }
}

function pngDimensions(bytes) {
  if (bytes.subarray(1, 4).toString('ascii') !== 'PNG' || bytes.subarray(12, 16).toString('ascii') !== 'IHDR') {
    throw new Error('Invalid PNG signature')
  }
  return [bytes.readUInt32BE(16), bytes.readUInt32BE(20)]
}

const checksums = new Map((await readFile(path.join(mediaRoot, 'checksums.sha256'), 'utf8')).trim().split('\n').map(line => {
  const match = line.match(/^([a-f0-9]{64})  ([A-Za-z0-9._/-]+)$/)
  if (!match) throw new Error('Invalid media checksum manifest')
  return [match[2], match[1]]
}))
if (checksums.size !== screenshots.length + 1 || [...screenshots, gif].some(relative => !checksums.has(relative))) {
  throw new Error('Media checksum manifest does not list the exact accepted asset set')
}
for (const relative of [...screenshots, gif]) {
  const bytes = await readFile(path.join(mediaRoot, relative))
  const maximumBytes = relative === gif ? 20_000_000 : 3_000_000
  if (!bytes.length || bytes.length > maximumBytes) throw new Error(`Media size budget exceeded: ${relative}`)
  if (createHash('sha256').update(bytes).digest('hex') !== checksums.get(relative)) throw new Error(`Checksum mismatch: ${relative}`)
  const metadata = JSON.parse(command(exiftool, ['-j', '-EXIF:all', '-XMP:all', '-IPTC:all', '-PNG:Comment', '-GIF:Comment', path.join(mediaRoot, relative)]))[0]
  if (Object.keys(metadata).some(key => key !== 'SourceFile')) throw new Error(`Metadata remains: ${relative}`)
}
for (const relative of screenshots) {
  const dimensions = pngDimensions(await readFile(path.join(mediaRoot, relative)))
  if (dimensions[0] !== 1440 || dimensions[1] !== 1024) throw new Error(`Invalid screenshot dimensions: ${relative}`)
}
const probe = JSON.parse(command(ffprobe, [
  '-v', 'error', '-show_entries', 'format=duration:stream=width,height,r_frame_rate,nb_frames',
  '-of', 'json', path.join(mediaRoot, gif)
]))
const stream = probe.streams?.[0]
const duration = Number(probe.format?.duration)
if (stream?.width !== 960 || stream?.height !== 683 || stream?.r_frame_rate !== '12/1' || stream?.nb_frames !== '120') {
  throw new Error('Invalid GIF stream settings')
}
if (!(duration >= 8 && duration <= 15)) throw new Error('GIF duration must be 8 to 15 seconds')
process.stdout.write('Media checksums, dimensions, duration, frame count, and metadata verified.\n')
