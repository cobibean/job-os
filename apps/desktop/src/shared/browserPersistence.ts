import type { BrowserTabMetadata } from './contracts.js'

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
  'accesstoken', 'assertion', 'authorization', 'authtoken', 'bearertoken',
  'capability', 'capabilitytoken', 'code', 'credential', 'idtoken', 'jwt',
  'macaroon', 'oauthcode', 'oauthstate', 'oauthtoken', 'oauthverifier',
  'password', 'refreshtoken', 'relaystate', 'samlrequest', 'samlresponse',
  'secret', 'session', 'sessionid', 'sessionkey', 'sid', 'sig', 'signature',
  'signedurl', 'state', 'ticket', 'token'
])

export function isSensitiveBrowserParameter(name: string): boolean {
  const normalized = name.toLowerCase().replace(/[^a-z0-9]/gu, '')
  return sensitiveParameterNames.has(normalized)
    || normalized.startsWith('xamz')
    || normalized.startsWith('xgoog')
    || /(?:password|secret|token|credential|assertion|signature)$/u.test(normalized)
}

export function sanitizeBrowserUrlForPersistence(value: string): string {
  if (value === 'about:blank') return value
  const url = new URL(value)
  url.username = ''
  url.password = ''
  url.hash = ''
  for (const key of Array.from(url.searchParams.keys())) {
    if (isSensitiveBrowserParameter(key)) url.searchParams.delete(key)
  }
  return url.toString()
}

export function normalizeBrowserUrlForPersistence(value: string, allowBlank = true): string {
  if (value === 'about:blank' && allowBlank) return value
  try {
    const sanitized = sanitizeBrowserUrlForPersistence(value)
    const parsed = new URL(sanitized)
    if (!['http:', 'https:'].includes(parsed.protocol)) return allowBlank ? 'about:blank' : ''
    if (sanitized.length <= BROWSER_PERSISTENCE_LIMITS.url) return sanitized
    const origin = `${parsed.origin}/`
    return origin.length <= BROWSER_PERSISTENCE_LIMITS.url ? origin : allowBlank ? 'about:blank' : ''
  } catch {
    return allowBlank ? 'about:blank' : ''
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
