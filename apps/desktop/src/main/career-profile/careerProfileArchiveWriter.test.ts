import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rename, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import type { Readable, Writable } from 'node:stream'
import { afterEach, expect, test } from 'vitest'

import { defaultCareerProfileArchiveWriterPath, writeCareerProfileArchiveNative } from './careerProfileArchiveWriter.js'

const roots: string[] = []
afterEach(async () => {
  await Promise.all(roots.splice(0).map(root => rm(root, { force: true, recursive: true })))
})

test.runIf(process.platform === 'darwin')('native writer atomically persists verified archive bytes', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jobos-archive-writer-'))
  roots.push(root)
  const target = path.join(root, 'profile.zip')
  const bytes = Buffer.from('(FAKE) descriptor-relative archive')
  await writeCareerProfileArchiveNative(target, bytes, createHash('sha256').update(bytes).digest('hex'))
  expect(await readFile(target)).toEqual(bytes)
})

test.runIf(process.platform === 'darwin')('directory replacement cannot redirect descriptor-relative archive export', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'jobos-archive-directory-race-'))
  roots.push(root)
  const selectedDirectory = path.join(root, 'selected')
  const openedDirectory = path.join(root, 'opened')
  await mkdir(selectedDirectory)
  const target = path.join(selectedDirectory, 'profile.zip')
  const bytes = Buffer.from('(FAKE) directory replacement race archive')
  const digest = createHash('sha256').update(bytes).digest('hex')
  const child = spawn(defaultCareerProfileArchiveWriterPath(), [target, digest, '--test-handshake'], {
    stdio: ['pipe', 'ignore', 'pipe', 'pipe', 'pipe']
  })
  const ready = child.stdio[3] as Readable | null
  const resume = child.stdio[4] as Writable | null
  if (!ready || !resume || !child.stdin || !child.stderr) throw new Error('helper handshake pipes unavailable')
  await new Promise<void>((resolve, reject) => {
    ready.once('data', () => resolve())
    child.once('error', reject)
  })
  await rename(selectedDirectory, openedDirectory)
  await mkdir(selectedDirectory)
  resume.write(Buffer.from([1]))
  child.stdin.end(bytes)
  const stderr: Buffer[] = []
  child.stderr.on('data', chunk => stderr.push(chunk))
  const code = await new Promise<number | null>((resolve, reject) => {
    child.once('close', resolve)
    child.once('error', reject)
  })
  expect(Buffer.concat(stderr).toString('utf8')).toBe('')
  expect(code).toBe(0)
  expect(await readFile(path.join(openedDirectory, 'profile.zip'))).toEqual(bytes)
  await expect(readFile(path.join(selectedDirectory, 'profile.zip'))).rejects.toMatchObject({ code: 'ENOENT' })
})
