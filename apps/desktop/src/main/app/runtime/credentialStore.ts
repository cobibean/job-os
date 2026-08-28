import { execFile } from 'node:child_process'
import { lstat, readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const KEYCHAIN_SERVICE = 'com.cobibean.jobos.device-token'

interface CommandResult {
  stdout: string
}

interface CredentialOptions {
  deviceId: string
  environment: Record<string, string | undefined>
  helperPath?: string
  run?: (file: string, arguments_: string[]) => Promise<CommandResult>
  credentialStore?: { provider: 'keychain' | 'file', path?: string }
  configPath?: string
}

function runCommand(file: string, arguments_: string[]): Promise<CommandResult> {
  return new Promise((resolve, reject) => {
    execFile(file, arguments_, { encoding: 'utf8', maxBuffer: 8192, timeout: 10_000 }, (error, stdout) => {
      if (error) {
        reject(error)
        return
      }
      resolve({ stdout })
    })
  })
}

function defaultHelperPath(): string {
  if (!process.defaultApp && process.resourcesPath) {
    return path.join(process.resourcesPath, 'jobos-keychain')
  }
  return fileURLToPath(new URL('../../../../build/jobos-keychain', import.meta.url))
}

function validateCredential(value: string): string {
  const credential = value.trim()
  const hasControlCharacter =
    credential.includes('\r') ||
    credential.includes('\n') ||
    credential.includes(String.fromCharCode(0))
  if (!credential || credential.length > 4096 || hasControlCharacter) {
    throw new Error('JobOS device credential is invalid')
  }
  return credential
}

export async function loadDeviceCredential(options: CredentialOptions): Promise<string> {
  const override = options.environment.JOBOS_DEVICE_TOKEN
  if (override) return validateCredential(override)

  if (options.credentialStore?.provider === 'file') {
    if (!options.credentialStore.path || !options.configPath) {
      throw new Error('JobOS device credential is unavailable')
    }
    try {
      const credentialPath = path.resolve(path.dirname(options.configPath), options.credentialStore.path)
      const metadata = await lstat(credentialPath)
      if (metadata.isSymbolicLink() || !metadata.isFile()) {
        throw new Error('unsafe credential file')
      }
      if ((metadata.mode & 0o777) !== 0o600) throw new Error('unsafe permissions')
      const value: unknown = JSON.parse(await readFile(credentialPath, 'utf8'))
      if (!value || typeof value !== 'object' || !('deviceToken' in value)) throw new Error('invalid')
      if (typeof value.deviceToken !== 'string') throw new Error('invalid')
      return validateCredential(value.deviceToken)
    } catch {
      throw new Error('JobOS device credential is unavailable')
    }
  }

  try {
    const result = await (options.run ?? runCommand)(
      options.helperPath ?? defaultHelperPath(),
      ['get', KEYCHAIN_SERVICE, options.deviceId]
    )
    return validateCredential(result.stdout)
  } catch (error) {
    if (error instanceof Error && error.message === 'JobOS device credential is invalid') throw error
    throw new Error('JobOS device credential is unavailable')
  }
}
