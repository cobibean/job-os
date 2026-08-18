import ninjaAvatar from './assets/ninja.webp'

export const AGENT_AVATARS = [
  {
    id: 'ninja',
    label: 'Ninja',
    description: 'Focused and ready',
    src: ninjaAvatar
  }
] as const

export type AgentAvatarId = typeof AGENT_AVATARS[number]['id']

export const DEFAULT_AGENT_AVATAR_ID: AgentAvatarId = 'ninja'

export function isAgentAvatarId(value: unknown): value is AgentAvatarId {
  return typeof value === 'string' && AGENT_AVATARS.some(avatar => avatar.id === value)
}

export function getAgentAvatar(avatarId: AgentAvatarId) {
  return AGENT_AVATARS.find(avatar => avatar.id === avatarId) ?? AGENT_AVATARS[0]
}
