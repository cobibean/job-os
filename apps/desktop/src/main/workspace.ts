import { randomUUID } from 'node:crypto'

import {
  createJobOsApiClient,
  workspaceGetV1WorkspaceGet,
  workspacePutV1WorkspacePut
} from '@jobos/contracts'
import type { WorkspaceSnapshotResponse } from '@jobos/contracts'

import type { BrowserTabMetadata, LayoutPreset, PanelId, WorkspaceSnapshot } from '../shared/contracts.js'
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
    repairedPresets: value.repaired_presets ?? [],
    browserTabs: (value.browser_tabs ?? []).map(tab => ({
      tabId: tab.tab_id,
      url: tab.url,
      title: tab.title ?? 'New tab',
      faviconUrl: tab.favicon_url ?? null,
      associatedJobId: tab.associated_job_id ?? null
    })) satisfies BrowserTabMetadata[],
    activeBrowserTabId: value.active_browser_tab_id ?? null,
    repairedBrowser: value.repaired_browser ?? false
  }
}

function toApi(snapshot: WorkspaceSnapshot, idempotencyKey: string) {
  return {
    revision: snapshot.revision,
    origin: 'user' as const,
    idempotency_key: idempotencyKey,
    selected_preset: snapshot.selectedPreset,
    layouts: snapshot.layouts,
    selected_job_id: snapshot.selectedJobId,
    active_center_surface: snapshot.activeCenterSurface,
    browser_tabs: (snapshot.browserTabs ?? []).map(tab => ({
      tab_id: tab.tabId,
      url: tab.url,
      title: tab.title,
      favicon_url: tab.faviconUrl,
      associated_job_id: tab.associatedJobId
    })),
    active_browser_tab_id: snapshot.activeBrowserTabId ?? null
  }
}

export function createMainWorkspaceClient(config: JobsConfig) {
  const client = createJobOsApiClient(config.baseUrl, config.deviceToken)
  return {
    async get(): Promise<WorkspaceSnapshot> {
      return fromApi(unwrap(await workspaceGetV1WorkspaceGet({ client }), 'Workspace unavailable'))
    },
    async save(snapshot: WorkspaceSnapshot): Promise<WorkspaceSnapshot> {
      const body = toApi(snapshot, randomUUID())
      let result
      let retried = false
      try {
        result = await workspacePutV1WorkspacePut({ client, body })
      } catch {
        retried = true
        result = await workspacePutV1WorkspacePut({ client, body })
      }
      if (!retried && result.response === undefined && result.error !== undefined) {
        result = await workspacePutV1WorkspacePut({ client, body })
      }
      return fromApi(unwrap(result, 'Workspace save failed'))
    }
  }
}
