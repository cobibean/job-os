import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export function defaultCareerProfileArchiveWriterPath(): string {
  if (!process.defaultApp && process.resourcesPath) {
    return path.join(process.resourcesPath, 'jobos-career-profile-archive-write')
  }
  const moduleUrl = new URL(import.meta.url)
  if (moduleUrl.protocol === 'file:') {
    return fileURLToPath(new URL('../../build/jobos-career-profile-archive-write', moduleUrl))
  }
  return path.resolve(process.cwd(), 'build/jobos-career-profile-archive-write')
}

export function writeCareerProfileArchiveNative(
  target: string,
  bytes: Buffer,
  expectedSha256: string,
  helperPath = defaultCareerProfileArchiveWriterPath()
): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(helperPath, [target, expectedSha256], {
      stdio: ['pipe', 'ignore', 'pipe'],
      windowsHide: true
    })
    const stderr: Buffer[] = []
    let stderrBytes = 0
    child.stderr.on('data', (chunk: Buffer) => {
      if (stderrBytes >= 8_192) return
      stderr.push(chunk.subarray(0, 8_192 - stderrBytes))
      stderrBytes += chunk.length
    })
    child.once('error', reject)
    child.once('close', code => {
      if (code === 0) resolve()
      else reject(new Error(Buffer.concat(stderr).toString('utf8').trim() || `Career Profile archive writer exited ${code}`))
    })
    child.stdin.end(bytes)
  })
}
