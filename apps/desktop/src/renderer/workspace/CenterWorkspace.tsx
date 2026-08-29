import type { ReactNode } from 'react'

interface CenterWorkspaceProps {
  activeSurface: 'browser' | 'document'
  browser: ReactNode
  document: ReactNode
}

export function CenterWorkspace({ activeSurface, browser, document }: CenterWorkspaceProps) {
  return activeSurface === 'browser' ? browser : document
}
