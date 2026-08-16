import { readFile } from 'node:fs/promises'
import path from 'node:path'

export type RuntimeMode = 'local-service' | 'remote-client'

export interface DesktopRuntimeConfig {
  schemaVersion: 1
  mode: RuntimeMode
  apiBaseUrl: string
  deviceId: string
  launchdLabel?: string
  credentialStore?: { provider: 'keychain' | 'file', path?: string }
  paths?: {
    stateDatabase: string
    jobsDatabase: string
    artifacts: string
    logs: string
  }
  agentProvider?: 'offline' | 'hermes'
  demoEnabled?: boolean
}

interface RuntimeConfigOptions {
  configPath: string
  environment: Record<string, string | undefined>
  readText?: (path: string) => Promise<string>
}

const CONFIG_KEYS = new Set([
  'schemaVersion',
  'mode',
  'apiBaseUrl',
  'deviceId',
  'launchdLabel',
  'credentialStore',
  'paths',
  'jobProvider',
  'artifactProvider',
  'agentProvider',
  'demoEnabled'
])
const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])
const DEFAULT_LAUNCHD_LABEL = 'com.cobibean.jobos.api'

export function runtimeConfigPath(appDataPath: string): string {
  return path.join(appDataPath, 'JobOS', 'config.json')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validateIdentifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value)) {
    throw new Error('JobOS device identifier is invalid')
  }
  return value
}

function validateBaseUrl(value: unknown, mode: RuntimeMode): string {
  if (typeof value !== 'string' || value.length > 2048) {
    throw new Error('JobOS API URL is invalid')
  }
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new Error('JobOS API URL is invalid')
  }
  if (url.username || url.password) throw new Error('JobOS API URL must not contain credentials')
  if (url.search) throw new Error('JobOS API URL must not contain a query')
  if (url.hash) throw new Error('JobOS API URL must not contain a fragment')
  if (url.pathname !== '/' && url.pathname !== '') {
    throw new Error('JobOS API URL must not contain a path')
  }

  if (mode === 'local-service') {
    if (url.protocol !== 'http:' || !LOCAL_HOSTS.has(url.hostname)) {
      throw new Error('Local JobOS API URL must use HTTP loopback')
    }
  } else {
    if (url.protocol !== 'https:') throw new Error('Remote JobOS API URL must use HTTPS')
    if (LOCAL_HOSTS.has(url.hostname)) {
      throw new Error('Remote JobOS API URL must not use loopback')
    }
  }
  return url.origin
}

function validateLaunchdLabel(value: unknown): string {
  if (value !== DEFAULT_LAUNCHD_LABEL) {
    throw new Error('JobOS launchd label is invalid')
  }
  return value
}

function validateConfig(value: unknown): DesktopRuntimeConfig {
  if (!isRecord(value)) throw new Error('JobOS runtime configuration is invalid')
  const unknown = Object.keys(value).filter(key => !CONFIG_KEYS.has(key))
  if (unknown.length > 0) throw new Error('JobOS runtime configuration contains unknown fields')
  if (value.schemaVersion !== 1) throw new Error('JobOS runtime configuration schema is unsupported')
  if (value.mode !== 'local-service' && value.mode !== 'remote-client') {
    throw new Error('JobOS runtime mode is invalid')
  }

  const config: DesktopRuntimeConfig = {
    schemaVersion: 1,
    mode: value.mode,
    apiBaseUrl: validateBaseUrl(value.apiBaseUrl, value.mode),
    deviceId: validateIdentifier(value.deviceId)
  }
  if (value.credentialStore !== undefined) {
    if (!isRecord(value.credentialStore)
      || (value.credentialStore.provider !== 'keychain' && value.credentialStore.provider !== 'file')
      || (value.credentialStore.provider === 'file' && typeof value.credentialStore.path !== 'string')) {
      throw new Error('JobOS credential configuration is invalid')
    }
    config.credentialStore = {
      provider: value.credentialStore.provider,
      ...(typeof value.credentialStore.path === 'string' ? { path: value.credentialStore.path } : {})
    }
  }
  if (value.paths !== undefined) {
    const paths = value.paths
    if (!isRecord(paths) || ['stateDatabase', 'jobsDatabase', 'artifacts', 'logs']
      .some(key => typeof paths[key] !== 'string')) {
      throw new Error('JobOS path configuration is invalid')
    }
    config.paths = paths as unknown as DesktopRuntimeConfig['paths']
  }
  if (value.agentProvider === 'offline' || value.agentProvider === 'hermes') {
    config.agentProvider = value.agentProvider
  } else if (value.agentProvider !== undefined) throw new Error('JobOS agent configuration is invalid')
  if (typeof value.demoEnabled === 'boolean') config.demoEnabled = value.demoEnabled
  else if (value.demoEnabled !== undefined) throw new Error('JobOS demo configuration is invalid')
  if (value.mode === 'local-service') {
    if (value.launchdLabel !== undefined) config.launchdLabel = validateLaunchdLabel(value.launchdLabel)
  } else if (value.launchdLabel !== undefined) {
    throw new Error('Remote JobOS runtime must not configure launchd')
  }
  return Object.freeze(config)
}

function environmentConfig(environment: Record<string, string | undefined>): unknown | null {
  const baseUrl = environment.JOBOS_API_BASE_URL
  const mode = environment.JOBOS_RUNTIME_MODE
  const deviceId = environment.JOBOS_DEVICE_ID
  if (!baseUrl && !mode && !deviceId) return null
  if (!baseUrl) throw new Error('JOBOS_API_BASE_URL is required for a runtime environment override')
  const inferredMode = mode ?? (LOCAL_HOSTS.has(new URL(baseUrl).hostname) ? 'local-service' : 'remote-client')
  return {
    schemaVersion: 1,
    mode: inferredMode,
    apiBaseUrl: baseUrl,
    deviceId: deviceId ?? 'primary-device',
    ...(inferredMode === 'local-service'
      ? { launchdLabel: environment.JOBOS_LAUNCHD_LABEL ?? DEFAULT_LAUNCHD_LABEL }
      : {})
  }
}

export async function loadDesktopRuntimeConfig(options: RuntimeConfigOptions): Promise<DesktopRuntimeConfig> {
  const override = environmentConfig(options.environment)
  if (override) return validateConfig(override)

  try {
    const contents = await (options.readText ?? (path => readFile(path, 'utf8')))(options.configPath)
    return validateConfig(JSON.parse(contents))
  } catch (error) {
    if (isRecord(error) && error.code === 'ENOENT') {
      const legacyPath = path.join(path.dirname(options.configPath), 'runtime.json')
      if (legacyPath !== options.configPath) {
        try {
          const contents = await (options.readText ?? (path => readFile(path, 'utf8')))(legacyPath)
          return validateConfig(JSON.parse(contents))
        } catch (legacyError) {
          if (!(isRecord(legacyError) && legacyError.code === 'ENOENT')) throw legacyError
        }
      }
      throw new Error('JobOS setup is required')
    }
    if (error instanceof SyntaxError) throw new Error('JobOS runtime configuration is invalid')
    throw error
  }
}
