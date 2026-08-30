import { useEffect, useState } from 'react'

import type { ConnectivitySnapshot, ConnectivityState } from '../../../shared/contracts'


type ConnectivityView = Omit<Partial<ConnectivitySnapshot>, 'state'> & {
  state: ConnectivityState
}

const initialConnectivity: ConnectivityView = { state: 'connecting' }


const defaultRefreshMs = 15_000

export function useConnectivity(refreshMs = defaultRefreshMs): ConnectivityView {
  const [connectivity, setConnectivity] = useState<ConnectivityView>(initialConnectivity)

  useEffect(() => {
    let active = true
    let probeInFlight = false

    if (!window.jobos?.connectivity) {
      setConnectivity({
        state: 'disconnected',
        checkedAt: new Date().toISOString(),
        message: 'Desktop bridge unavailable'
      })
      return undefined
    }

    const refresh = async () => {
      if (!active || probeInFlight) return
      probeInFlight = true
      try {
        const snapshot = await window.jobos.connectivity.get()
        if (active) setConnectivity(snapshot)
      } catch (error) {
        if (active) {
          setConnectivity({
            state: 'disconnected',
            checkedAt: new Date().toISOString(),
            message: error instanceof Error ? error.message : 'Local service unavailable'
          })
        }
      } finally {
        probeInFlight = false
      }
    }

    const refreshOnVisibility = () => {
      if (document.visibilityState === 'visible') void refresh()
    }
    const refreshOnFocus = () => void refresh()
    const interval = window.setInterval(() => void refresh(), refreshMs)
    window.addEventListener('focus', refreshOnFocus)
    document.addEventListener('visibilitychange', refreshOnVisibility)
    void refresh()

    return () => {
      active = false
      window.clearInterval(interval)
      window.removeEventListener('focus', refreshOnFocus)
      document.removeEventListener('visibilitychange', refreshOnVisibility)
    }
  }, [refreshMs])

  return connectivity
}
