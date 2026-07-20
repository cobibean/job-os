import {
  createJobOsApiClient,
  workspaceGetV1WorkspaceGet,
  workspacePutV1WorkspacePut
} from '@jobos/contracts'
import type { WorkspaceSnapshotResponse } from '@jobos/contracts'

import type { LayoutPreset, PanelId, WorkspaceSnapshot } from '../shared/contracts.js'
import type { JobsConfig } from './jobs.js'

interface ApiResult<T> { data?: T; error?: unknown; response?: Response }

function unwrap<T>(result: ApiResult<T>, fallback: string): T {
  if (result.response?.status === 200 && result.data !== undefined) return result.data
  const detail = typeof result.error === 'object' && result.error && 'detail' in result.error
    ? String(result.error.detail)
    : fallback
  throw new Error(detail)
}

function fromApi(value: WorkspaceSnapshotResponse): WorkspaceSnapshot {
  const presets: LayoutPreset[] = ['research', 'review', 'agent-focus']
  const layouts = Object.fromEntries(presets.map(preset => {
    const layout = value.layouts[preset]!
    return [preset, {
      order: layout.order as PanelId[],
      widths: layout.widths as Record<PanelId, number>,
      collapsed: layout.collapsed as PanelId[]
    }]
  })) as WorkspaceSnapshot['layouts']
  return {
    revision: value.revision,
    selectedPreset: value.selected_preset,
    layouts,
    selectedJobId: value.selected_job_id,
    activeCenterSurface: value.active_center_surface,
    repairedPresets: value.repaired_presets ?? []
  }
}

function toApi(snapshot: WorkspaceSnapshot) {
  return {
    revision: snapshot.revision,
    selected_preset: snapshot.selectedPreset,
    layouts: snapshot.layouts,
    selected_job_id: snapshot.selectedJobId,
    active_center_surface: snapshot.activeCenterSurface,
    repaired_presets: snapshot.repairedPresets
  }
}

export function createMainWorkspaceClient(config: JobsConfig) {
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)
  return {
    async get(): Promise<WorkspaceSnapshot> {
      return fromApi(unwrap(await workspaceGetV1WorkspaceGet({ client }), 'Workspace unavailable'))
    },
    async save(snapshot: WorkspaceSnapshot): Promise<WorkspaceSnapshot> {
      return fromApi(unwrap(await workspacePutV1WorkspacePut({ client, body: toApi(snapshot) }), 'Workspace save failed'))
    }
  }
}
