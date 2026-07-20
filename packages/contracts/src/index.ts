import { createClient } from './generated/client/index.js'

export type JobOsApiClient = ReturnType<typeof createJobOsApiClient>

export function createJobOsApiClient(baseUrl: string, deviceToken: string) {
  return createClient({
    baseUrl,
    headers: {
      Authorization: `Bearer ${deviceToken}`
    }
  })
}

export * from './generated/index.js'
