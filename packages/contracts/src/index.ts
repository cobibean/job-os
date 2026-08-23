import { createClient } from './generated/client/index.js'

export type JobOsApiClient = ReturnType<typeof createJobOsApiClient>

export function jobOsAuthenticatedHeaders(
  deviceToken: string,
  installationProfileId?: string
): Record<string, string> {
  return {
    Authorization: `Bearer ${deviceToken}`,
    ...(installationProfileId ? { 'X-JobOS-Profile-Id': installationProfileId } : {})
  }
}

export function createJobOsApiClient(
  baseUrl: string,
  deviceToken: string,
  installationProfileId?: string
) {
  return createClient({
    baseUrl,
    headers: jobOsAuthenticatedHeaders(deviceToken, installationProfileId)
  })
}

export * from './generated/index.js'
