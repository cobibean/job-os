import { readFile } from 'node:fs/promises'

export type RuntimeMode = 'local-service' | 'remote-client'

export interface DesktopRuntimeConfig {
  schemaVersion: 1
  mode: RuntimeMode
  apiBaseUrl: string
  deviceId: string
  launchdLabel?: string
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
  'launchdLabel'
])
const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])
const DEFAULT_LAUNCHD_LABEL = 'com.cobibean.jobos.api'

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
  if (value.mode === 'local-service') {
    config.launchdLabel = validateLaunchdLabel(value.launchdLabel ?? DEFAULT_LAUNCHD_LABEL)
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
      throw new Error('JobOS runtime configuration is required')
    }
    if (error instanceof SyntaxError) throw new Error('JobOS runtime configuration is invalid')
    throw error
  }
}
