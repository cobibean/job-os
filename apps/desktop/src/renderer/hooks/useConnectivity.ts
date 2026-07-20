import { useEffect, useState } from 'react'

import type { ConnectivitySnapshot, ConnectivityState } from '../../shared/contracts'


type ConnectivityView = Omit<Partial<ConnectivitySnapshot>, 'state'> & {
  state: ConnectivityState
}

const initialConnectivity: ConnectivityView = { state: 'connecting' }


export function useConnectivity(): ConnectivityView {
  const [connectivity, setConnectivity] = useState<ConnectivityView>(initialConnectivity)

  useEffect(() => {
    let active = true

    if (!window.jobos?.connectivity) {
      setConnectivity({
        state: 'disconnected',
        checkedAt: new Date().toISOString(),
        message: 'Desktop bridge unavailable'
      })
      return undefined
    }

    window.jobos.connectivity.get().then(
      snapshot => {
        if (active) setConnectivity(snapshot)
      },
      error => {
        if (active) {
          setConnectivity({
            state: 'disconnected',
            checkedAt: new Date().toISOString(),
            message: error instanceof Error ? error.message : 'Mac Mini unavailable'
          })
        }
      }
    )

    return () => {
      active = false
    }
  }, [])

  return connectivity
}
