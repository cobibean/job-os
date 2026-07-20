export type PanelId = 'jobs' | 'center' | 'agent'
export type LayoutPreset = 'research' | 'review' | 'agent-focus'
export type CenterSurface = 'browser' | 'document'

export interface PanelLayout {
  order: PanelId[]
  widths: Record<PanelId, number>
  collapsed: PanelId[]
}

export interface WorkspaceSnapshot {
  revision: number
  selectedPreset: LayoutPreset
  layouts: Record<LayoutPreset, PanelLayout>
  selectedJobId: string | null
  activeCenterSurface: CenterSurface
  repairedPresets: LayoutPreset[]
  browserTabs?: import('../shared/contracts').BrowserTabMetadata[]
  activeBrowserTabId?: string | null
  repairedBrowser?: boolean
}

export const panelNames: Record<PanelId, string> = {
  jobs: 'Job navigation',
  center: 'Center workspace',
  agent: 'Agent chat'
}

const defaults: Record<LayoutPreset, PanelLayout> = {
  research: { order: ['jobs', 'center', 'agent'], widths: { jobs: 260, center: 760, agent: 350 }, collapsed: [] },
  review: { order: ['jobs', 'center', 'agent'], widths: { jobs: 280, center: 700, agent: 380 }, collapsed: [] },
  'agent-focus': { order: ['jobs', 'center', 'agent'], widths: { jobs: 220, center: 420, agent: 650 }, collapsed: [] }
}

function cloneLayout(layout: PanelLayout): PanelLayout {
  return { order: [...layout.order], widths: { ...layout.widths }, collapsed: [...layout.collapsed] }
}

export function canonicalWorkspace(): WorkspaceSnapshot {
  return {
    revision: 0,
    selectedPreset: 'review',
    layouts: {
      research: cloneLayout(defaults.research),
      review: cloneLayout(defaults.review),
      'agent-focus': cloneLayout(defaults['agent-focus'])
    },
    selectedJobId: null,
    activeCenterSurface: 'document',
    repairedPresets: [],
    browserTabs: [],
    activeBrowserTabId: null,
    repairedBrowser: false
  }
}

function updateActiveLayout(workspace: WorkspaceSnapshot, update: (layout: PanelLayout) => PanelLayout) {
  return {
    ...workspace,
    layouts: { ...workspace.layouts, [workspace.selectedPreset]: update(workspace.layouts[workspace.selectedPreset]) },
    repairedPresets: []
  }
}

export function resizeAdjacentPanels(workspace: WorkspaceSnapshot, before: PanelId, after: PanelId, delta: number) {
  return updateActiveLayout(workspace, layout => {
    const beforeWidth = layout.widths[before]
    const afterWidth = layout.widths[after]
    const applied = Math.max(220 - beforeWidth, Math.min(delta, afterWidth - 220))
    return {
      ...cloneLayout(layout),
      widths: { ...layout.widths, [before]: beforeWidth + applied, [after]: afterWidth - applied }
    }
  })
}

export function movePanel(workspace: WorkspaceSnapshot, panel: PanelId, targetIndex: number) {
  return updateActiveLayout(workspace, layout => {
    const order = layout.order.filter(item => item !== panel)
    order.splice(Math.max(0, Math.min(targetIndex, order.length)), 0, panel)
    return { ...cloneLayout(layout), order }
  })
}

export function setPanelCollapsed(workspace: WorkspaceSnapshot, panel: PanelId, collapsed: boolean) {
  return updateActiveLayout(workspace, layout => ({
    ...cloneLayout(layout),
    collapsed: collapsed
      ? [...new Set([...layout.collapsed, panel])]
      : layout.collapsed.filter(item => item !== panel)
  }))
}

export function resetActivePreset(workspace: WorkspaceSnapshot) {
  return {
    ...workspace,
    layouts: { ...workspace.layouts, [workspace.selectedPreset]: cloneLayout(defaults[workspace.selectedPreset]) },
    repairedPresets: []
  }
}
