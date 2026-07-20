# JobOS V1 Agent Parity Matrix

## Scope

This is the Phase 7 single-user/local MVP mapping. The JobOS API remains the
product boundary and MCP remains a thin authenticated HTTP adapter. Browser
cookies, sessions, live views, and fixed page interaction scripts remain owned
by Electron main. No row permits caller JavaScript, selectors, raw IPC, direct
SQLite/filesystem access, or automatic application submission.

## Shared commands

| Workbench action | UI path | MCP tool | JobOS API/application command |
| --- | --- | --- | --- |
| List jobs | Job navigator | `job_list` | `GET /v1/jobs` |
| Inspect job | Job navigator/detail | `job_inspect` | `GET /v1/jobs/{job_id}` |
| Select active job | Job navigator | `job_select` | `PUT /v1/workspace/jobs/selection` / `job.select` |
| Reorder jobs | Manual job ordering | `job_reorder` | `PUT /v1/jobs/order` / `jobs.reorder` |
| Change job status | Job row status control | `job_update_status` | `PUT /v1/jobs/{job_id}/status` / `job.update_status` |
| Inspect workspace | Workbench restoration | `workspace_inspect` | `GET /v1/workspace` |
| Choose layout/center/document | Workspace controls | `workspace_update`, `document_select` | `PUT /v1/workspace` / `workspace_snapshot.save` |
| List artifacts | Document workspace | `document_list` | `GET /v1/jobs/{job_id}/artifacts` |
| Refresh artifacts | Refresh Preview | `document_refresh` | `POST /v1/jobs/{job_id}/artifacts/refresh` / `document.refresh` |
| Render resume | Chat/golden-path command | `document_render` | `POST /v1/jobs/{job_id}/artifacts/render` / `document.render` |
| Register artifact | Document discovery | `document_register` | `POST /v1/jobs/{job_id}/artifacts/register` / `document.register` |
| Inspect tabs | Browser tab strip | `browser_tabs_inspect` | `POST /v1/browser/commands` / `tabs.inspect` |
| Create/select/close/reorder tab | Browser tab strip | corresponding `browser_tab_*` tool | `tab.create`, `tab.select`, `tab.close`, `tabs.reorder` |
| Navigate/back/forward/reload/stop | Browser toolbar | corresponding `browser_*` tool | `tab.navigate`, `tab.back`, `tab.forward`, `tab.reload`, `tab.stop` |
| Inspect visible page controls | Agent browser action | `browser_snapshot` | `page.snapshot` |
| Click/type/scroll page | Agent browser action | `browser_click`, `browser_type`, `browser_scroll` | `element.click`, `element.type`, `page.scroll` |
| Report an action | Existing activity chronology | `activity_report` | `POST /v1/activity` / `activity.report` |

Browser toolbar actions and remote browser commands converge on the same
validated `BrowserManager` methods in Electron main. The remote path adds the
authenticated capability broker, correlation, deadline, idempotency, and
activity audit boundaries; it does not introduce a second browser backend.

## Deliberately local presentation operations

Panel pointer geometry, live resize bounds, focus, scroll position in JobOS
chrome, disclosure expansion, draft text, and browser-view attachment are
presentation-only. They stay local and are not MCP capabilities. Durable layout,
selection, center surface, artifact selection, and browser-tab metadata remain
shared through the atomic Workspace API.

## Failure and approval rules

- One configured device may hold the short in-memory desktop lease. Commands
  fail immediately with `desktop_unavailable`; there is no offline queue.
- Every command has a server-generated command ID, caller idempotency key,
  origin, and a bounded deadline. Safe terminal error codes are
  `desktop_unavailable`, `tab_not_found`, `timeout`, `validation`, and
  `execution`.
- Semantic snapshots contain bounded visible text and at most 100 interactive
  elements. Targets are opaque IDs issued by JobOS; callers cannot supply
  selectors or scripts.
- Agent-origin job, workspace, document, browser, and explicit activity
  mutations append concise events to the existing Phase 6 chronology.
- Application submission and other consequential external actions remain
  outside this capability set and require an explicit human approval boundary.
