import path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Session } from 'electron'

export interface RendererTrustPolicy {
  developmentOrigin?: string
  rendererRoot: string
}

export function applyDenyAllPermissionPolicy(target: Pick<Session, 'setPermissionCheckHandler' | 'setPermissionRequestHandler'>): void {
  target.setPermissionCheckHandler(() => false)
  target.setPermissionRequestHandler((_contents, _permission, callback) => callback(false))
}

export function isTrustedRendererUrl(urlValue: string, policy: RendererTrustPolicy): boolean {
  try {
    const candidate = new URL(urlValue)
    if (policy.developmentOrigin && candidate.origin === policy.developmentOrigin) {
      return candidate.protocol === 'http:' || candidate.protocol === 'https:'
    }
    if (candidate.protocol !== 'file:') return false

    const rendererRoot = path.resolve(policy.rendererRoot)
    const candidatePath = path.resolve(fileURLToPath(candidate))
    return candidatePath === rendererRoot || candidatePath.startsWith(`${rendererRoot}${path.sep}`)
  } catch {
    return false
  }
}

export function assertTrustedRendererEvent(
  event: { senderFrame?: { url: string } | null },
  policy: RendererTrustPolicy
): void {
  if (!isTrustedRendererUrl(event.senderFrame?.url ?? '', policy)) throw new Error('Untrusted renderer')
}
