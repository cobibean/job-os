import { useCallback, useEffect, useRef, useState } from 'react'

import type { ConnectedAgentModelsSnapshot, ConnectedAgentsSnapshot } from '../../../shared/contracts'

export function useConnectedAgents() {
  const bridge = window.jobos?.connectedAgents
  const [snapshot, setSnapshot] = useState<ConnectedAgentsSnapshot | null>(null)
  const [models, setModels] = useState<Record<string, ConnectedAgentModelsSnapshot>>({})
  const [loading, setLoading] = useState(Boolean(bridge))
  const [error, setError] = useState<string | null>(null)
  const pendingRefresh = useRef<Promise<ConnectedAgentsSnapshot | null> | null>(null)

  const refresh = useCallback(() => {
    if (!bridge) return Promise.resolve(null)
    if (pendingRefresh.current) return pendingRefresh.current
    setLoading(true)
    const request = Promise.resolve().then(() => bridge.list()).then(value => {
      setSnapshot(value)
      setError(null)
      return value
    }).catch(cause => {
      setError(cause instanceof Error ? cause.message : 'Connected Agents unavailable')
      return null
    }).finally(() => {
      pendingRefresh.current = null
      setLoading(false)
    })
    pendingRefresh.current = request
    return request
  }, [bridge])

  const loadModels = useCallback(async (agentId: string, force = false) => {
    if (!bridge) throw new Error('Connected Agents unavailable')
    if (!force && models[agentId]?.live) return models[agentId]
    const value = await bridge.models(agentId)
    setModels(current => ({ ...current, [agentId]: value }))
    return value
  }, [bridge, models])

  useEffect(() => { void refresh() }, [refresh])

  return { bridge, snapshot, models, loading, error, refresh, loadModels }
}
