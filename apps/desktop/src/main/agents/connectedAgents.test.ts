// @vitest-environment node

import { expect, test, vi } from 'vitest'

import { createMainConnectedAgentsClient } from './connectedAgents.js'

const agentId = `jagent_${'e'.repeat(32)}`
const rawAgent = {
  id: agentId,
  provider: 'codex',
  display_name: 'Codex',
  avatar_id: 'spark',
  default_model_id: 'gpt-live',
  default_reasoning_effort: 'medium',
  lifecycle: 'connected',
  account_summary: { label: '(FAKE) account' },
  account_fingerprint: 'f'.repeat(64),
  health: { state: 'ready', label: 'Ready', provider_available: true, tools_available: true, retry_after_seconds: null },
  impact: { active_chats: 1, locked_chats: 0 }
}

test('normalizes the profile roster and uses only runtime-provided models', async () => {
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    const url = new URL(request.url)
    expect(request.headers.get('authorization')).toBe('Bearer test-device-token')
    if (url.pathname.endsWith('/models')) {
      return Response.json({ live: true, models: [{ model_id: 'gpt-live', display_name: 'GPT Live', reasoning_efforts: ['medium'] }] })
    }
    return Response.json({ registry_revision: 8, profile_id: 'default', default_connected_agent_id: agentId, agents: [rawAgent] })
  })
  const client = createMainConnectedAgentsClient({ baseUrl: 'http://jobos.test', deviceToken: 'test-device-token', fetch: fetcher })

  expect(await client.list()).toEqual({
    registryRevision: 8,
    profileId: 'default',
    defaultConnectedAgentId: agentId,
    agents: [expect.objectContaining({ id: agentId, provider: 'codex', displayName: 'Codex', activeChats: 1 })]
  })
  expect(await client.models(agentId)).toEqual({
    live: true,
    models: [{ modelId: 'gpt-live', displayName: 'GPT Live', reasoningEfforts: ['medium'] }]
  })
  expect(JSON.stringify(await client.list())).not.toContain('test-device-token')
})

test('accepts the safe auth transaction returned when cancellation succeeds', async () => {
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    expect(request.method).toBe('DELETE')
    return Response.json({
      transaction_id: `jauth_${'a'.repeat(32)}`,
      agent_id: agentId,
      method: 'device_code',
      status: 'cancelled',
      verification_url: null,
      user_code: null,
      expires_at: '2026-08-25T02:00:00Z',
      error_code: null
    })
  })
  const client = createMainConnectedAgentsClient({ baseUrl: 'http://jobos.test', deviceToken: 'test-device-token', fetch: fetcher })

  await expect(client.cancelAuth(`jauth_${'a'.repeat(32)}`)).resolves.toBeUndefined()
})

test('starts replacement auth from the actual 200 response with continuity fingerprint', async () => {
  const fingerprint = 'f'.repeat(64)
  const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(input, init)
    expect(request.method).toBe('POST')
    expect(await request.json()).toEqual({ mode: 'replace', expected_account_fingerprint: fingerprint })
    return Response.json({
      transaction_id: `jauth_${'b'.repeat(32)}`,
      agent_id: agentId,
      method: 'device_code',
      status: 'login_pending',
      verification_url: 'https://example.test/device',
      user_code: '(FAKE)-CODE',
      expires_at: '2026-08-25T02:00:00Z',
      error_code: null
    }, { status: 200 })
  })
  const client = createMainConnectedAgentsClient({ baseUrl: 'http://jobos.test', deviceToken: 'test-device-token', fetch: fetcher })

  await expect(client.startAuth(agentId, 'replace', fingerprint)).resolves.toEqual(
    expect.objectContaining({ status: 'login_pending' })
  )
})
