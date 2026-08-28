import { describe, expect, test } from 'vitest'

import { validateAgentChatSelection } from './agentChatSelection.js'

describe('validateAgentChatSelection', () => {
  test('preserves the accepted selection shape', () => {
    expect(validateAgentChatSelection({
      connectedAgentId: `jagent_${'a'.repeat(32)}`,
      modelId: 'gpt-5',
      reasoningEffort: 'high',
      expectedProfileRevision: 2,
      expectedAgentRegistryRevision: 3,
      idempotencyKey: 'selection-1',
      initialSelectedJobId: null
    })).toEqual({
      connectedAgentId: `jagent_${'a'.repeat(32)}`,
      modelId: 'gpt-5',
      reasoningEffort: 'high',
      expectedProfileRevision: 2,
      expectedAgentRegistryRevision: 3,
      idempotencyKey: 'selection-1',
      initialSelectedJobId: null
    })
  })

  test('preserves validation errors', () => {
    expect(() => validateAgentChatSelection(undefined)).toThrow('Agent selection is required')
    expect(() => validateAgentChatSelection({ connectedAgentId: 'agent' })).toThrow('Invalid Connected Agent')
  })
})
