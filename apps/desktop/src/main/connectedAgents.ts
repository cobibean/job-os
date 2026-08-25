import {
  connectedAgentAuthCancelV1ConnectedAgentAuthTransactionIdDelete,
  connectedAgentAuthReadV1ConnectedAgentAuthTransactionIdGet,
  connectedAgentAuthStartV1ConnectedAgentsAgentIdAuthDeviceCodePost,
  connectedAgentCreateV1ConnectedAgentsPost,
  connectedAgentDisconnectImpactV1ConnectedAgentsAgentIdDisconnectImpactGet,
  connectedAgentDisconnectV1ConnectedAgentsAgentIdDisconnectPost,
  connectedAgentModelsV1ConnectedAgentsAgentIdModelsGet,
  connectedAgentsListV1ConnectedAgentsGet,
  connectedAgentTestV1ConnectedAgentsAgentIdTestPost,
  connectedAgentUpdateV1ConnectedAgentsAgentIdPatch,
  createJobOsApiClient,
  installationProfileDefaultAgentV1InstallationProfilesProfileIdDefaultAgentPut
} from '@jobos/contracts'

import type {
  ConnectedAgentModelsSnapshot,
  ConnectedAgentsSnapshot,
  ConnectedAgentSummary
} from '../shared/contracts.js'

interface ConnectedAgentsConfig {
  baseUrl: string
  deviceToken: string
  installationProfileId?: string
  fetch?: typeof fetch
}

interface ApiResult<T> {
  data?: T
  error?: unknown
  response?: Response
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function errorMessage(error: unknown, fallback: string): string {
  if (!isRecord(error)) return fallback
  if (typeof error.detail === 'string') return error.detail.slice(0, 500)
  if (isRecord(error.detail) && typeof error.detail.message === 'string') return error.detail.message.slice(0, 500)
  return fallback
}

function unwrap<T>(result: ApiResult<T>, statuses: number[], fallback: string): T {
  if (result.response && statuses.includes(result.response.status) && result.data !== undefined) return result.data
  throw new Error(errorMessage(result.error, fallback))
}

function impact(value: unknown): { activeChats: number; lockedChats: number; defaultProfileIds: string[] } {
  if (!isRecord(value)) return { activeChats: 0, lockedChats: 0, defaultProfileIds: [] }
  return {
    activeChats: Number.isInteger(value.active_chats) ? Number(value.active_chats) : 0,
    lockedChats: Number.isInteger(value.locked_chats) ? Number(value.locked_chats) : 0,
    defaultProfileIds: Array.isArray(value.default_profile_ids)
      ? value.default_profile_ids.filter((item): item is string => typeof item === 'string')
      : []
  }
}

function agent(value: unknown): ConnectedAgentSummary {
  if (!isRecord(value) || typeof value.id !== 'string' || !['hermes', 'codex'].includes(String(value.provider))
    || typeof value.display_name !== 'string' || typeof value.avatar_id !== 'string'
    || !['connected', 'disconnected'].includes(String(value.lifecycle)) || !isRecord(value.health)) {
    throw new Error('Connected Agent response unavailable')
  }
  const totals = impact(value.impact)
  return {
    id: value.id,
    provider: value.provider as ConnectedAgentSummary['provider'],
    displayName: value.display_name,
    avatarId: value.avatar_id,
    defaultModelId: typeof value.default_model_id === 'string' ? value.default_model_id : null,
    defaultReasoningEffort: typeof value.default_reasoning_effort === 'string' ? value.default_reasoning_effort : null,
    lifecycle: value.lifecycle as ConnectedAgentSummary['lifecycle'],
    accountSummary: isRecord(value.account_summary)
      ? Object.fromEntries(Object.entries(value.account_summary).filter((entry): entry is [string, string] => typeof entry[1] === 'string'))
      : null,
    accountFingerprint: typeof value.account_fingerprint === 'string' ? value.account_fingerprint : null,
    health: {
      state: typeof value.health.state === 'string' ? value.health.state : 'unavailable',
      label: typeof value.health.label === 'string' ? value.health.label : 'Unavailable',
      providerAvailable: value.health.provider_available === true,
      toolsAvailable: value.health.tools_available === true,
      retryAfterSeconds: typeof value.health.retry_after_seconds === 'number' ? value.health.retry_after_seconds : null
    },
    activeChats: totals.activeChats,
    lockedChats: totals.lockedChats
  }
}

function auth(value: unknown) {
  if (!isRecord(value) || typeof value.transaction_id !== 'string' || typeof value.status !== 'string'
    || typeof value.expires_at !== 'string') throw new Error('Authentication status unavailable')
  return {
    transactionId: value.transaction_id,
    status: value.status,
    userCode: typeof value.user_code === 'string' ? value.user_code : null,
    verificationUrl: typeof value.verification_url === 'string' ? value.verification_url : null,
    expiresAt: value.expires_at,
    errorCode: typeof value.error_code === 'string' ? value.error_code : null
  }
}

export function createMainConnectedAgentsClient(config: ConnectedAgentsConfig) {
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken, config.installationProfileId)
  if (config.fetch) client.setConfig({ fetch: config.fetch })
  return {
    async list(): Promise<ConnectedAgentsSnapshot> {
      const value = unwrap(await connectedAgentsListV1ConnectedAgentsGet({ client }), [200], 'Connected Agents unavailable')
      const raw = value as unknown as Record<string, unknown>
      if (!Number.isInteger(raw.registry_revision) || typeof raw.profile_id !== 'string' || !Array.isArray(raw.agents)) {
        throw new Error('Connected Agents response unavailable')
      }
      return {
        registryRevision: Number(raw.registry_revision),
        profileId: raw.profile_id,
        defaultConnectedAgentId: typeof raw.default_connected_agent_id === 'string' ? raw.default_connected_agent_id : null,
        agents: raw.agents.map(agent)
      }
    },
    async models(agentId: string): Promise<ConnectedAgentModelsSnapshot> {
      const value = unwrap(await connectedAgentModelsV1ConnectedAgentsAgentIdModelsGet({ client, path: { agent_id: agentId } }), [200], 'Models unavailable')
      return {
        live: value.live,
        models: value.models.map(model => ({ modelId: model.model_id, displayName: model.display_name, reasoningEfforts: model.reasoning_efforts }))
      }
    },
    async test(agentId: string) {
      return agent(unwrap(await connectedAgentTestV1ConnectedAgentsAgentIdTestPost({ client, path: { agent_id: agentId } }), [200], 'Connection test failed'))
    },
    async createCodex(displayName: string, avatarId: string, expectedRegistryRevision: number, idempotencyKey: string) {
      return agent(unwrap(await connectedAgentCreateV1ConnectedAgentsPost({
        client,
        body: { provider: 'codex', display_name: displayName, avatar_id: avatarId, expected_registry_revision: expectedRegistryRevision, idempotency_key: idempotencyKey }
      }), [201], 'Codex connection could not be created'))
    },
    async update(current: ConnectedAgentSummary, modelId: string | null, reasoningEffort: string | null, expectedRegistryRevision: number, idempotencyKey: string) {
      return agent(unwrap(await connectedAgentUpdateV1ConnectedAgentsAgentIdPatch({
        client,
        path: { agent_id: current.id },
        body: { display_name: current.displayName, avatar_id: current.avatarId, default_model_id: modelId, default_reasoning_effort: reasoningEffort, expected_registry_revision: expectedRegistryRevision, idempotency_key: idempotencyKey }
      }), [200], 'Connected Agent could not be updated'))
    },
    async setDefault(profileId: string, agentId: string | null, expectedRevision: number, idempotencyKey: string) {
      const value = unwrap(await installationProfileDefaultAgentV1InstallationProfilesProfileIdDefaultAgentPut({
        client,
        path: { profile_id: profileId },
        body: { connected_agent_id: agentId, expected_profile_revision: expectedRevision, expected_agent_registry_revision: expectedRevision, idempotency_key: idempotencyKey }
      }), [200], 'Profile default could not be updated')
      return value.registry_revision
    },
    async impact(agentId: string) {
      return impact(unwrap(await connectedAgentDisconnectImpactV1ConnectedAgentsAgentIdDisconnectImpactGet({ client, path: { agent_id: agentId } }), [200], 'Disconnect impact unavailable'))
    },
    async disconnect(agentId: string, expectedRegistryRevision: number, idempotencyKey: string) {
      return agent(unwrap(await connectedAgentDisconnectV1ConnectedAgentsAgentIdDisconnectPost({
        client,
        path: { agent_id: agentId },
        body: { confirmation_token: agentId, expected_registry_revision: expectedRegistryRevision, idempotency_key: idempotencyKey }
      }), [200], 'Connected Agent could not be disconnected'))
    },
    async startAuth(agentId: string, mode: 'connect' | 'reconnect' | 'replace', expectedAccountFingerprint: string | null) {
      return auth(unwrap(await connectedAgentAuthStartV1ConnectedAgentsAgentIdAuthDeviceCodePost({
        client, path: { agent_id: agentId }, body: { mode, expected_account_fingerprint: expectedAccountFingerprint }
      }), [200], 'Authentication could not start'))
    },
    async readAuth(transactionId: string) {
      return auth(unwrap(await connectedAgentAuthReadV1ConnectedAgentAuthTransactionIdGet({ client, path: { transaction_id: transactionId } }), [200], 'Authentication status unavailable'))
    },
    async cancelAuth(transactionId: string) {
      unwrap(await connectedAgentAuthCancelV1ConnectedAgentAuthTransactionIdDelete({ client, path: { transaction_id: transactionId } }), [200, 204], 'Authentication could not be cancelled')
    }
  }
}
