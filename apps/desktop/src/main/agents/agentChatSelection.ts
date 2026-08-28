import type { AgentChatSelection } from '../../shared/contracts.js'

const agentPattern = /^jagent_[a-f0-9]{32}$/

function connectedAgentId(value: unknown): string {
  if (typeof value !== 'string' || !agentPattern.test(value)) throw new Error('Invalid Connected Agent')
  return value
}

function revision(value: unknown): number {
  if (!Number.isInteger(value) || Number(value) < 1) throw new Error('Invalid Connected Agent revision')
  return Number(value)
}

function key(value: unknown): string {
  if (typeof value !== 'string' || value.length < 8 || value.length > 200 || /[\r\n]/.test(value)) throw new Error('Invalid idempotency key')
  return value
}

function text(value: unknown, maximum = 120): string {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum) throw new Error('Invalid Connected Agent value')
  return value.trim()
}

export function validateAgentChatSelection(value: unknown): AgentChatSelection {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Agent selection is required')
  const candidate = value as Record<string, unknown>
  return {
    connectedAgentId: connectedAgentId(candidate.connectedAgentId),
    modelId: text(candidate.modelId, 256),
    reasoningEffort: text(candidate.reasoningEffort, 64),
    expectedProfileRevision: revision(candidate.expectedProfileRevision),
    expectedAgentRegistryRevision: revision(candidate.expectedAgentRegistryRevision),
    idempotencyKey: key(candidate.idempotencyKey),
    initialSelectedJobId: candidate.initialSelectedJobId === null || candidate.initialSelectedJobId === undefined
      ? null
      : text(candidate.initialSelectedJobId, 512)
  }
}
