import type { BrowserRestoreState, BrowserTabMetadata } from './contracts.js'

export const BROWSER_PERSISTENCE_LIMITS = {
  tabs: 50,
  tabId: 128,
  url: 8192,
  title: 512,
  associatedJobId: 512
} as const

// This is the desktop half of the conservative URL persistence policy mirrored
// by services/api/jobos_api/browser_policy.py. Parameter names are normalized so
// casing and separators cannot bypass the policy.
const sensitiveParameterNames = new Set([
  'accesstoken', 'apikey', 'assertion', 'authorization', 'authorizationcode',
  'authcode', 'authtoken', 'bearertoken', 'capability', 'capabilitytoken',
  'code', 'codeverifier', 'credential', 'idtoken', 'jsessionid', 'jwt',
  'macaroon', 'oauthcode', 'oauthstate', 'oauthtoken', 'oauthverifier',
  'password', 'phpsessid', 'refreshtoken', 'relaystate', 'samlart',
  'samlrequest', 'samlresponse', 'secret', 'session', 'sessionid',
  'sessionkey', 'sid', 'sig', 'signature', 'signedurl', 'state', 'ticket',
  'token'
])

function decodeBrowserPolicyComponent(value: string): string {
  let decoded = value
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const next = decodeURIComponent(decoded)
      if (next === decoded) break
      decoded = next
    } catch {
      break
    }
  }
  return decoded
}

export function isSensitiveBrowserParameter(name: string): boolean {
  const decoded = decodeBrowserPolicyComponent(name)
  const normalized = decoded.toLowerCase().replace(/[^a-z0-9]/gu, '')
  return sensitiveParameterNames.has(normalized)
    || normalized.startsWith('xamz')
    || normalized.startsWith('xgoog')
    || /(?:password|secret|token|credential|assertion|signature)$/u.test(normalized)
}

function sanitizePathParameters(pathname: string): string {
  let removedSensitiveParameter = false
  const sanitized = decodeBrowserPolicyComponent(pathname).split('/').map(segment => {
    const parts = segment.split(';')
    const base = parts.shift() ?? ''
    const safeParameters = parts.filter(parameter => {
      const [name = ''] = parameter.split('=', 1)
      const safe = !isSensitiveBrowserParameter(name)
      if (!safe) removedSensitiveParameter = true
      return safe
    })
    return [base, ...safeParameters].join(';')
  }).join('/')
  return removedSensitiveParameter ? sanitized : pathname
}

function hasUnsafeUrlCharacters(value: string): boolean {
  return Array.from(value).some(character => (
    character === '\\' || /\s/u.test(character) || character.charCodeAt(0) < 32
  ))
}

function hasValidBrowserAuthority(url: URL, rawValue: string): boolean {
  if (hasUnsafeUrlCharacters(rawValue)) return false
  if (url.port && (!/^\d+$/u.test(url.port) || Number(url.port) < 1 || Number(url.port) > 65_535)) return false
  const hostname = url.hostname
  if (!hostname) return false
  if (hostname.startsWith('[') && hostname.endsWith(']')) return hostname.length > 2 && hostname.includes(':')
  const asciiHostname = hostname.endsWith('.') ? hostname.slice(0, -1) : hostname
  if (!asciiHostname || asciiHostname.length > 253) return false
  return asciiHostname.split('.').every(label => (
    label.length > 0
    && label.length <= 63
    && !label.startsWith('-')
    && !label.endsWith('-')
    && /^[a-zA-Z0-9_-]+$/u.test(label)
  ))
}

export function sanitizeBrowserUrlForPersistence(value: string): string {
  if (value === 'about:blank') return value
  const url = new URL(value)
  url.username = ''
  url.password = ''
  url.hash = ''
  url.pathname = sanitizePathParameters(url.pathname)
  for (const key of Array.from(url.searchParams.keys())) {
    if (isSensitiveBrowserParameter(key)) url.searchParams.delete(key)
  }
  return url.toString()
}

export function normalizeBrowserUrlForPersistence(value: string, allowBlank = true): string {
  if (value === 'about:blank' && allowBlank) return value
  try {
    if (hasUnsafeUrlCharacters(value)) return allowBlank ? 'about:blank' : ''
    const sanitized = sanitizeBrowserUrlForPersistence(value)
    const parsed = new URL(sanitized)
    if (!['http:', 'https:'].includes(parsed.protocol)) return allowBlank ? 'about:blank' : ''
    if (!hasValidBrowserAuthority(parsed, sanitized)) return allowBlank ? 'about:blank' : ''
    if (sanitized.length <= BROWSER_PERSISTENCE_LIMITS.url) return sanitized
    const origin = `${parsed.origin}/`
    return origin.length <= BROWSER_PERSISTENCE_LIMITS.url ? origin : allowBlank ? 'about:blank' : ''
  } catch {
    return allowBlank ? 'about:blank' : ''
  }
}

export function recoverBrowserTabMetadata(value: unknown): BrowserTabMetadata | null {
  if (!value || typeof value !== 'object') return null
  const tab = value as Partial<BrowserTabMetadata>
  if (
    typeof tab.tabId !== 'string'
    || tab.tabId.length === 0
    || tab.tabId.length > BROWSER_PERSISTENCE_LIMITS.tabId
    || typeof tab.url !== 'string'
    || tab.url.length > BROWSER_PERSISTENCE_LIMITS.url
    || typeof tab.title !== 'string'
    || tab.title.length > BROWSER_PERSISTENCE_LIMITS.title
    || (tab.faviconUrl !== null && tab.faviconUrl !== undefined && typeof tab.faviconUrl !== 'string')
    || (tab.associatedJobId !== null && tab.associatedJobId !== undefined && typeof tab.associatedJobId !== 'string')
    || (typeof tab.associatedJobId === 'string' && tab.associatedJobId.length > BROWSER_PERSISTENCE_LIMITS.associatedJobId)
  ) return null
  const url = normalizeBrowserUrlForPersistence(tab.url)
  if (url === 'about:blank' && tab.url !== 'about:blank') return null
  let faviconUrl: string | null = null
  if (typeof tab.faviconUrl === 'string') {
    if (tab.faviconUrl.length > BROWSER_PERSISTENCE_LIMITS.url) return null
    faviconUrl = normalizeBrowserUrlForPersistence(tab.faviconUrl, false) || null
    if (!faviconUrl) return null
  }
  return {
    tabId: tab.tabId,
    url,
    title: tab.title,
    faviconUrl,
    associatedJobId: tab.associatedJobId ?? null
  }
}

export function recoverBrowserRestoreState(state: BrowserRestoreState): BrowserRestoreState {
  const tabs: BrowserTabMetadata[] = []
  const unique = new Set<string>()
  for (const candidate of state.tabs) {
    const tab = recoverBrowserTabMetadata(candidate)
    if (!tab || unique.has(tab.tabId)) continue
    unique.add(tab.tabId)
    tabs.push(tab)
    if (tabs.length >= BROWSER_PERSISTENCE_LIMITS.tabs) break
  }
  return {
    tabs,
    activeTabId: state.activeTabId && unique.has(state.activeTabId)
      ? state.activeTabId
      : tabs[0]?.tabId ?? null
  }
}

export function sanitizeBrowserMetadata(tab: BrowserTabMetadata): BrowserTabMetadata {
  return {
    ...tab,
    tabId: tab.tabId.slice(0, BROWSER_PERSISTENCE_LIMITS.tabId),
    url: normalizeBrowserUrlForPersistence(tab.url),
    title: tab.title.slice(0, BROWSER_PERSISTENCE_LIMITS.title),
    faviconUrl: tab.faviconUrl?.startsWith('http://') || tab.faviconUrl?.startsWith('https://')
      ? normalizeBrowserUrlForPersistence(tab.faviconUrl, false) || null
      : null,
    associatedJobId: tab.associatedJobId?.slice(0, BROWSER_PERSISTENCE_LIMITS.associatedJobId) ?? null
  }
}
