# Desktop Renderer Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` when available to implement this plan task by task. Keep every task independently reviewable and use test-first extraction at behavior-bearing seams. The objective and safety boundaries are locked; you own small implementation choices and may recommend a smaller correction when current repository evidence contradicts this baseline.

- **Status:** Approved for implementation by Cobi on 2026-08-29
- **Verified planning baseline:** `c05aede9194e3ee709d3354b5c74321adca32502` on synchronized `main` / `origin/main`, verified 2026-08-29
- **Primary scope:** `apps/desktop/src/renderer`
- **Direct-consumer scope:** renderer HTML/Vite inputs, path-sensitive tests, public synthetic-fixture paths, active implementation docs, and verification scripts that name moved renderer files

**Goal:** Give every renderer behavior one obvious product owner, reduce `App.tsx` to the setup gate, make `WorkbenchApp.tsx` the explicit cross-feature composition root, co-locate UI/state/tests/styles, and make the renderer as safe for humans and agents to navigate as the newly organized Electron main process.

**Architecture:** Keep the renderer root as a stable Vite entry directory. Organize implementation vertically into `app/`, `agents/`, `browser/`, `career-profile/`, `documents/`, `installation-profiles/`, `jobs/`, and `workspace/`; only app composition may assemble multiple owners. Preserve the existing preload bridge, user experience, security posture, state timing, and packaged renderer paths.

**Tech stack:** React 19, TypeScript 7, Vite 8, Vitest 4, Testing Library, Electron 43, CSS, generated JobOS contracts, and the retained-OOXML document packages.

## Global constraints

- This is a behavior-preserving ownership refactor. Do not redesign layouts, copy, interaction, state, accessibility, or visual styling.
- Do not change IPC channel names, preload bridge shapes, API/OpenAPI schemas, generated contracts, persistence payloads, profile semantics, browser security, or document trust rules.
- Do not refactor `services/api/`, `services/mcp/`, `apps/desktop/src/main/`, `apps/desktop/src/preload/`, `packages/contracts/`, `packages/docx-engine/`, or `packages/docx-editor-core/` beyond a direct path consumer made necessary by a renderer move.
- Do not add a state-management library, router, dependency container, generic bridge framework, broad barrel export, CSS-in-JS system, or new package dependency.
- Do not keep heavy feature trees mounted when the current renderer unmounts them, duplicate bridge-backed controllers, or turn a current dynamic/separate bundle edge into eager application code.
- Preserve the stable renderer HTML entrypoints `index.html`, `print.html`, and `docx-worker.html` at the renderer root.
- Preserve `main.tsx` as the stable application entrypoint and `styles.css` as the stable stylesheet entrypoint.
- Keep tests beside the implementation they prove. Replace old-path tests after the equivalent owner-level test passes; do not layer duplicate test suites indefinitely.
- Use explicit relative imports. Do not introduce path aliases as part of this refactor.
- Preserve existing untracked or unrelated user work. Stage and commit only files owned by the current task.
- A source move is not complete until focused tests, desktop typecheck/lint/build, packaged-renderer verification, public fixture validation, and the relevant real workflow checks pass.
- Do not push, merge, deploy, package a release, or alter remote state unless the user separately authorizes that action for the implementation run.

---

## 1. Decision provenance

### User decisions

- Refactor the renderer next.
- Use the feature-owned shape proposed after the Electron main-process refactor.
- Publish this plan so Hermes can pull it and implement it.
- Apply the performance safeguards from the two-agent review before Hermes starts implementation.

### Verified facts at the planning baseline

- `apps/desktop/src/renderer` contains 103 TypeScript/TSX files: 11,972 production lines and 9,118 test lines.
- `App.tsx` is 525 lines and `App.test.tsx` is 1,853 lines.
- `CareerProfileProductExperience.tsx` is 1,604 lines.
- `DocumentWorkspace.tsx` is 792 lines.
- `CenterWorkspace.tsx` is 531 lines and owns browser presentation, document composition, and the save-job-from-browser transaction.
- `useAgentSessions.ts` is 556 lines and is already a coherent agent-owned controller despite living in a generic `hooks/` directory.
- `styles.css` is 2,182 lines and mixes every product owner in one cascade.
- Renderer feature behavior is split horizontally across `components/`, `hooks/`, `document-editor/`, `agent-avatar/`, and root files.
- The Electron main process now documents the same product owners and enforces its root/composition rules with an architecture test.
- Two independent read-only reviewers used the same performance brief and returned `safe with plan amendments`. Their findings were confirmed against the current mount lifecycle and a fresh Vite manifest. This revision includes all four agreed amendments.

### Working assumptions Hermes must re-check

- The verified SHA is planning evidence, not implementation proof. Fetch and re-read the current branch, `AGENTS.md`, the renderer tree, and this plan before moving code.
- The target ownership names remain correct unless newer product documentation has explicitly changed the domain model.
- Existing test behavior is authoritative when it conflicts with a proposed mechanical move, unless the test itself is clearly stale and the user approves changing behavior.

## 2. Selected approach and rejected alternatives

### Selected: vertical product ownership with a thin app composition root

Move each component, controller/hook, pure model, test, asset, and stylesheet to the product owner that changes with it. Keep cross-owner assembly in `app/`. Deepen the three largest mixed modules only after their current behavior is characterized and their dependencies can be injected through narrow interfaces.

This gives contributors one answer to "where does this behavior live?" while keeping the renderer's trusted bridge and UI timing unchanged.

### Rejected: retain `components/` and `hooks/`, but add more subfolders

This preserves the current horizontal split. A Career Profile change would still require searching unrelated technical buckets, and generic folders would continue accumulating new features.

### Rejected: move files into feature folders while leaving `App.tsx`, `CenterWorkspace.tsx`, and the global stylesheet unchanged

That improves scanning but leaves the highest-risk orchestration and ownership ambiguity intact. The folder names would promise boundaries the implementation does not honor.

### Rejected: rewrite the renderer around a new state library or router

That combines architecture cleanup with product-state redesign, adds dependencies, weakens rename history, and makes regressions harder to attribute.

### Rejected: split every file to an arbitrary line limit

Size is evidence, not the design rule. Split only where a smaller interface hides a coherent behavior and improves test locality.

## 3. Target ownership model

```text
apps/desktop/src/renderer/
├── README.md
├── main.tsx
├── index.html
├── print.html
├── docx-worker.html
├── env.d.ts
├── pagedjs.d.ts
├── styles.css                         # ordered CSS import manifest only
├── app/
│   ├── App.tsx                        # setup gate only
│   ├── WorkbenchApp.tsx               # cross-owner composition root
│   ├── runtime/
│   │   └── useConnectivity.ts
│   ├── onboarding/
│   ├── settings/
│   │   ├── SettingsPanel.tsx
│   │   └── SettingsSection.tsx
│   ├── status/
│   │   └── StatusBar.tsx
│   ├── theme/
│   └── styles/
│       ├── foundation.css
│       ├── app-shell.css
│       └── settings.css
├── agents/
│   ├── avatar/
│   │   └── assets/
│   ├── chat/
│   ├── connected-agents/
│   ├── new-chat/
│   └── agents.css
├── browser/
│   ├── BrowserWorkspace.tsx
│   ├── useBrowser.ts
│   └── browser.css
├── career-profile/
│   ├── CareerProfileWorkspace.tsx
│   ├── collaboration/
│   ├── product/
│   ├── work-arrangement/
│   ├── settings/
│   └── career-profile.css
├── documents/
│   ├── artifacts/
│   │   ├── DocumentWorkspace.tsx
│   │   ├── artifactProjection.ts
│   │   └── artifacts.css
│   ├── editable/
│   │   ├── editor/
│   │   ├── print/
│   │   └── editable.css
│   ├── docx/
│   │   ├── editor/
│   │   ├── worker/
│   │   └── docx.css
│   └── previews/
├── installation-profiles/
│   ├── InstallationProfileMenu.tsx
│   └── installation-profiles.css
├── jobs/
│   ├── browse/
│   ├── navigator/
│   ├── save-from-browser/
│   ├── jobStatus.ts
│   ├── useJobs.ts
│   └── jobs.css
└── workspace/
    ├── CenterWorkspace.tsx
    ├── WorkbenchLayout.tsx
    ├── WorkspaceBar.tsx
    ├── useWorkspace.ts
    ├── workspaceLayout.ts
    └── workspace.css
```

Do not create empty directories merely to match the tree. Create a directory in the task that gives it a real owner. Do not create `renderer/shared/ui/` yet: no current UI helper has two independent owners that justify that seam. Add it later only when real reuse exists.

### Ownership definitions

| Owner | Owns | Does not own |
| --- | --- | --- |
| `app/` | Setup gate, renderer composition, cross-feature coordination, global settings shell, connectivity projection, theme, status, ordered stylesheet composition | Feature rules or feature persistence |
| `agents/` | Conversation projection, multi-session state, chat presentation, Connected Agent settings, New Chat selection, avatars | Job selection, browser implementation, document behavior |
| `browser/` | Native-browser tab chrome, navigation state, restore projection, browser errors/download visibility | Job extraction policy or canonical job mutation |
| `career-profile/` | Work arrangement, complete-profile product, collaboration, evidence UI, cache validation, Career Profile agent-access UI | JobOS installation profiles or general Connected Agent identity |
| `documents/artifacts/` | Registered immutable artifact projection, revision/format selection, PDF/DOCX viewing, approval/export entry points | Editable document mutation or device-local DOCX binding lifecycle |
| `documents/editable/` | Canonical editable-document editor, autosave, snapshots, comments, preview/export/publication, print renderer entry module | Bound local DOCX editing |
| `documents/docx/` | Device-local DOCX editing, worker entry module, source reload, recovery, pagination, autosave | Canonical editable-document publication |
| `documents/previews/` | Process-neutral renderer previews genuinely used by more than one document owner | Document mutation or bridge orchestration |
| `installation-profiles/` | Profile list/create/rename/switch presentation and rollback messaging | Career Profile product state |
| `jobs/` | Job list/detail/status state, Browse experience, navigator, and the browser-listing-to-job transaction | Native browser tab implementation |
| `workspace/` | Layout model, panel geometry, workspace persistence projection, workspace bar, and feature-neutral slot composition | Agent, browser, job, or document rules |

## 4. Dependency and interface rules

1. `main.tsx` imports only `app/App`, the stable renderer stylesheet entrypoint, and the retained editor package stylesheet.
2. `app/WorkbenchApp.tsx` is the only module allowed to compose multiple top-level product owners.
3. Feature owners must not import `main.tsx`, `app/App.tsx`, or `app/WorkbenchApp.tsx`.
4. `workspace/` receives feature surfaces as props/React nodes. It must not import agent, browser, job, Career Profile, or document implementations.
5. Each feature may import generated/shared contracts and process-neutral code from `apps/desktop/src/shared`.
6. Keep bridge access inside the owner that uses it. Do not build a generic renderer client layer around `window.jobos`.
7. Cross-owner workflows belong to the product owner of the outcome and receive narrow dependencies from app composition. The save-from-browser workflow belongs to `jobs/`, even though it coordinates agent and browser capabilities.
8. Bridge-backed controllers have one live instance at their existing owner lifetime. Do not call `useBrowser`, `useSaveJobFromBrowser`, `useAgentSessions`, `useConnectedAgents`, `useJobs`, `useWorkspace`, `useConnectivity`, `useCareerProfileProduct`, or another bridge-owning controller in both composition and a leaf module. Pass narrow state and commands instead.
9. Callbacks or adapters consumed by effects and subscriptions must retain stable identity or read changing values through a latest-value ref. Unrelated renderer updates must not restart IPC subscriptions, polling intervals, restoration, observers, or initial loads.
10. Tests use the same owner interface as production callers. Do not reach through an owner to assert private hook state. Count active subscriptions/listeners after React StrictMode setup and cleanup rather than assuming an effect sets up only once in development.
11. Avoid broad `index.ts` barrels. Import the explicit module that provides the needed interface.
12. Root HTML files remain stable runtime assets; their script `src` values may point into an owner directory.

## 5. Stable entrypoints and path-sensitive consumers

The following locations remain stable:

| Stable path | Required behavior |
| --- | --- |
| `src/renderer/index.html` | Loads `/main.tsx` under the existing CSP |
| `src/renderer/main.tsx` | Mounts `<App />` and imports `styles.css` before the retained editor package stylesheet, preserving current cascade order |
| `src/renderer/print.html` | Remains the file loaded by `pdfExporter.ts` |
| `src/renderer/docx-worker.html` | Remains the file loaded by `DocxWorkerManager.ts` |
| `src/renderer/styles.css` | Remains the single stylesheet imported by `main.tsx`, but becomes an ordered `@import` manifest |
| `dist/renderer/index.html` | Remains the packaged main renderer |
| `dist/renderer/print.html` | Remains the packaged print renderer |
| `dist/renderer/docx-worker.html` | Remains the packaged DOCX worker renderer |

When moving `documentPrint.ts`, update only `print.html` to load `/documents/editable/print/documentPrint.ts`. When moving `docxWorker.ts`, update only `docx-worker.html` to load `/documents/docx/worker/docxWorker.ts`. Do not move the HTML files or alter their CSPs.

Moving avatar assets requires updating all eleven paths in `tests/public-release/synthetic-fixtures.json`. Preserve every asset byte and existing checksum; path changes do not authorize replacing or regenerating artwork.

Update active documentation that asserts current source paths:

- `docs/implementation/connected-agents-codex-plan.md`
- `docs/implementation/career-profile-user-agency-audit.md`

Do not rewrite older acceptance evidence solely to modernize historical paths. Add a short current-location note only when an old document would otherwise misdirect an implementer.

## 6. Behavior invariants

### Application and workspace

- Setup-required state must not start workbench services.
- A profile changed elsewhere must replace the workspace with restart recovery.
- Command-N and Command-1 through Command-5 behavior, modal suppression, focus, and five-chat cap remain exact.
- Panel order, size, collapse, focus transfer, drag preview, and mounted feature identity remain exact.
- Browse activation waits for native-browser detachment and fails closed when detachment fails.
- Settings and profile overlays hide the native browser before becoming visible and restore it safely afterward.

### Agents

- Chat binding metadata, drafts, independent concurrent sessions, activity grouping, approvals, retry/cancel, recovery, unread state, and scroll position remain unchanged.
- Browser save requires the exact configured default Connected Agent, live model, and supported reasoning effort; no fallback is allowed.
- Provider credentials and raw runtime detail never enter renderer state, copy, logs, or tests.

### Browser and jobs

- Browser persistence continues to redact credential-like remote title content.
- Associated-job matching wins over normalized URL matching.
- Save-from-browser captures an immutable source tab and URL, preserves the exact protocol/error mapping, and never reports success before both job creation and tab association succeed.
- Job selection remains conversation-scoped; a global MCP event cannot silently replace the active session job.
- Browse focus remains local until Open Job commits selection and listing navigation.

### Documents

- Registered artifacts, canonical editable documents, and bound local DOCX files remain separate trust domains.
- Artifact revision/format selection, last-successful fallback, approval, export, page/zoom persistence, and stale-response rejection remain exact.
- Editable autosave, conflict handling, snapshots, comments, preview/export, paired publication, and dirty-exit behavior remain exact.
- Bound DOCX source reload, external-change conflict, recovery, page count, autosave, and close protection remain exact.
- Print and worker renderers retain their current CSP and sandboxed preload behavior.

### Career Profile and installation profiles

- Cached Career Profile data remains bounded, checksum-verified, readable offline, and non-writable until live authority returns.
- Revision conflicts preserve the proposed user edit and expose the authoritative latest value.
- Evidence import, erasure, history, Undo, export, restore, and agent-access semantics remain exact.
- Installation Profile create/rename/switch confirmation and uncertain/rolled-back failure language remain exact.

### Accessibility and visuals

- Existing roles, labels, live regions, focus traps, roving tab stops, keyboard alternatives, reduced-motion behavior, and disabled states remain unchanged.
- CSS declaration values and cascade order remain byte-equivalent during the style split except for import plumbing.
- No visible spacing, color, typography, responsive, animation, or layout change is part of this work.

## 7. File movement manifest

Move implementation and its tests together. Use `git mv` so rename history remains legible.

| Current location | Target owner/location |
| --- | --- |
| `App.tsx`, `App.test.tsx` | `app/`; later split into setup and workbench composition tests |
| `onboarding/*` | `app/onboarding/` |
| `diagnostics/*` | `app/settings/diagnostics/` |
| `theme/*` | `app/theme/` |
| `components/SettingsPanel*`, `SettingsSection.tsx` | `app/settings/` |
| `components/StatusBar.tsx`, `hooks/useConnectivity*` | `app/status/` and `app/runtime/` |
| `agent-avatar/**` | `agents/avatar/**` |
| `components/ActivityRow.tsx`, `AgentActivityGroup.tsx`, `AgentPanel*`, `AgentSessionTabs*`, `AssistantMarkdown.tsx` | `agents/chat/` |
| `hooks/useAgentConversation*`, `useAgentSessions*` | `agents/chat/` |
| `components/ConnectedAgentsSettings*`, `hooks/useConnectedAgents*` | `agents/connected-agents/` |
| `components/NewAgentChatDialog*` | `agents/new-chat/` |
| `hooks/useBrowser*` | `browser/` |
| Browser UI extracted from `CenterWorkspace.tsx` | `browser/BrowserWorkspace.tsx` |
| `components/CareerProfileWorkspace*`, `hooks/useCareerProfile*` | `career-profile/`, then the focused subowners described in Task 8 |
| `components/CareerProfileProductExperience*` | `career-profile/product/`, then split by product responsibility |
| `components/InstallationProfileMenu*` | `installation-profiles/` |
| `components/JobNavigator*` | `jobs/navigator/` |
| `components/BrowseWorkspace*`, `hooks/useBrowseJobs.ts` | `jobs/browse/` |
| `hooks/useJobs*`, `jobStatus*` | `jobs/` |
| Save protocol/controller extracted from `CenterWorkspace.tsx` | `jobs/save-from-browser/` |
| `components/DocumentWorkspace*`, `PdfPreview*` | `documents/artifacts/` |
| Editable `document-editor` modules | `documents/editable/editor/` |
| DOCX `document-editor` modules | `documents/docx/editor/` |
| Preview modules used by multiple document owners | `documents/previews/` |
| `documentPrint.ts` | `documents/editable/print/` |
| `docxWorker.ts` | `documents/docx/worker/` |
| `components/WorkbenchLayout*`, `WorkspaceBar*`, `hooks/useWorkspace*`, `workspaceLayout*`, `workspaceSizing.test.ts` | `workspace/` |
| `components/CenterWorkspace.tsx` | `workspace/CenterWorkspace.tsx` after browser/job behavior is extracted |

Editable editor modules are `DocumentEditor.tsx`, `DocumentEditorShell*`, `DocumentInspector*`, `DocumentRibbon.tsx`, `DocumentStatusBar.tsx`, `extensions.ts`, `paginationAdapter*`, `useDocumentAutosave.ts`, `marks/**`, and `nodes/**`.

DOCX editor modules are `DocxDocumentEditorShell*`, `docxDisplay*`, `useDocxAutosave.ts`, and `useDocxPagination.ts`.

`DocxBytesPreview*` and `OriginalDocxPreview*` move to `documents/previews/` because artifact and editor owners both consume them.

## 8. Required deepened modules

### 8.1 App composition

`app/App.tsx` must own only the setup gate:

```tsx
export function App() {
  const setup = useSetupState()
  if (setup.state === 'checking') return <SetupLoading />
  if (setup.state !== 'ready') return <OnboardingScreen initial={setup.snapshot} />
  return <WorkbenchApp />
}
```

Names may adapt to existing types, but the interface must remain this small: setup decides whether the workbench exists; `WorkbenchApp` composes the ready product.

`app/WorkbenchApp.tsx` may import top-level owners and coordinate cross-owner lifecycle. Move feature rules out of it:

- default-agent/model validation -> `agents/connected-agents/defaultSelection.ts`;
- save-job prompt/protocol -> `jobs/save-from-browser/`;
- browser tab implementation -> `browser/`;
- document implementation -> `documents/`;
- layout calculations -> `workspace/`;
- profile switch presentation -> `installation-profiles/`.

Do not impose a line-count gate. Completion means the file reads as ordered composition and lifecycle, not that it falls below an arbitrary number.

### 8.2 Feature-neutral workspace

`workspace/WorkbenchLayout.tsx` already accepts feature nodes and should remain the primary layout interface. Deepen `CenterWorkspace.tsx` without changing the current asymmetric lifecycle:

```tsx
interface CenterWorkspaceProps {
  activeSurface: 'browser' | 'document'
  browser: React.ReactNode
  document: React.ReactNode
}

export function CenterWorkspace({ activeSurface, browser, document }: CenterWorkspaceProps) {
  return activeSurface === 'browser' ? browser : document
}
```

Preserve the current mixed lifecycle exactly:

- `WorkbenchApp` invokes the browser controller and save-reconciliation controller once for the workbench lifetime.
- `BrowserWorkspace` is presentation over the existing browser controller. Its DOM mounts only for the browser view.
- `DocumentWorkspace` mounts only for the document view and unmounts when the browser becomes active.
- The browser controller, browser persistence state, save-operation map, and agent reconciliation subscription do not remount during browser/document switches.
- Leaving Documents unsubscribes document listeners and releases PDF tasks, canvases, payload buffers, object URLs, and portals.

Do not keep both feature trees mounted with plain `hidden`. Do not conditionally mount the browser controller with `BrowserWorkspace`; that would repeat restore/subscription work and could lose in-flight save reconciliation. Characterization tests must prove the browser -> document -> browser lifecycle before extraction.

`workspace/WorkspaceBar.tsx` must likewise receive the Installation Profile control as a `ReactNode` slot. `app/WorkbenchApp.tsx` composes `InstallationProfileMenu` into that slot, so workspace does not import an installation-profile implementation. Preserve the current navigation labels, profile placement, mode toggle, and reset behavior.

### 8.3 Save-from-browser workflow

The outcome is a canonical saved job linked to the immutable source tab, so `jobs/` owns this workflow. Extract:

- `saveJobPrompt.ts`: prompt construction, exact terminal protocol parsing, exact error-code mapping, and expected-navigation validation;
- `useSaveJobFromBrowser.ts`: per-tab operation state, conversation dispatch, terminal reconciliation, stale-operation rejection, and success callback;
- focused unit/controller tests migrated from `App.test.tsx` and `CenterWorkspace` coverage.

Inject the minimum agent/browser operations the workflow actually calls. Do not pass the complete `window.jobos` object and do not duplicate main/API job creation logic in the renderer.

### 8.4 Career Profile product

Keep `CareerProfileProductExperience` as the public product component, but move its implementation behind focused internal modules:

```text
career-profile/product/
├── CareerProfileProductExperience.tsx
├── itemSpecs.ts
├── itemPresentation.ts
├── ItemArea.tsx
├── ItemDetails.tsx
├── ItemEditor.tsx
├── EvidenceArea.tsx
├── dialogs/
│   ├── AgentAccessDialog.tsx
│   ├── ExportDialog.tsx
│   ├── HistoryDialog.tsx
│   └── RestoreDialog.tsx
└── useCareerProfileProduct.ts
```

Extract pure item specification, formatting, draft, validation, and preference-guidance functions first with tests. Then extract visible regions one at a time while the public props and behavior stay unchanged.

### 8.5 Artifact workspace

Extract the pure artifact grouping and selection rules from `DocumentWorkspace.tsx` into `artifactProjection.ts`. Its interface should accept artifacts plus restored/current selection and return the logical documents/revisions and selected preview without accessing React or `window.jobos`.

Keep asynchronous bridge sequencing in a feature controller and visible rendering in `DocumentWorkspace.tsx`. Tests must continue proving latest-wins behavior, job-change invalidation, last-successful fallback, exact viewed revision, approval, export, and editable-DOCX refresh.

Preserve the current memo boundary around artifact grouping and sorting. Projection may recompute only when artifact or logical-selection inputs change. Page, zoom, export-menu, agent-stream, dialog, and unrelated shell updates must reuse the prior projection.

## 9. Ordered implementation tasks

### Task 1: Re-verify baseline and characterize fragile seams

**Files:** No production edits.

- [ ] Read the current `AGENTS.md`, this plan, `apps/desktop/src/main/README.md`, `docs/public/architecture.md`, `apps/desktop/package.json`, and `apps/desktop/vite.config.ts`.
- [ ] Fetch `origin`, inspect branch/worktree state, and fast-forward local `main` only when it is safe under repository instructions.
- [ ] Record the implementation baseline SHA in the implementation handoff.
- [ ] Run `pnpm install --frozen-lockfile` and `uv sync --all-packages --frozen`.
- [ ] Run the focused baseline:

```bash
pnpm --filter @jobos/desktop test
pnpm --filter @jobos/desktop typecheck
pnpm --filter @jobos/desktop lint
pnpm --filter @jobos/desktop build
```

- [ ] Capture a Vite performance baseline:

```bash
pnpm --filter @jobos/desktop exec vite build --manifest
```

- [ ] Record the transformed-module count, warm build duration, every HTML entry's static and dynamic import closure, raw/gzip asset sizes, application CSS file count, and unresolved CSS imports. Preserve the manifest as implementation evidence, not as a committed build artifact.
- [ ] Use the planning measurement as a cross-check: 2,341 transformed modules; index JS 701.13 kB raw / 204.04 kB gzip; eager shared JS 790.36 kB / 245.15 kB; application CSS 118.17 kB / 19.32 kB; dynamic PDF JS 426.36 kB / 127.09 kB; print JS 532.13 kB / 106.48 kB; PDF worker 1,262.39 kB raw. Warm local builds ranged from 362 to 479 ms, but machine timing is diagnostic rather than a hard gate.
- [ ] Confirm the baseline topology: PDF.js is dynamic from the application entry; print and DOCX worker remain independent HTML entries; the application entry has no static dependency on `documentPrint`, `docxWorker`, `pagedjs`, or PDF.js.
- [ ] If any command is red on unchanged baseline, record the exact failure and separate it from refactor regressions. Do not normalize a pre-existing failure by weakening a test.
- [ ] Confirm the current renderer file inventory and every non-renderer path consumer with `rg` before moving files.

**Deliverable:** Verified baseline and a path-consumer inventory; no behavior changes.

### Task 2: Establish renderer ownership documentation and architecture proof

**Files:**

- Create: `apps/desktop/src/renderer/README.md`
- Create: `apps/desktop/src/renderer/app/architecture.test.ts`

- [ ] Write `README.md` with the target owner table, dependency rules, stable entrypoints, bridge flow, and instructions for adding a feature.
- [ ] Add an architecture test that recursively inventories renderer production files.
- [ ] Initially assert rules that are already true and can stay green: feature code cannot import Electron main implementation; `main.tsx` remains the stable mount; HTML entrypoints remain present.
- [ ] Add the final root allowlist and no-`components`/no-`hooks` assertions only in Task 10 when the moves make them true.
- [ ] Run the architecture test directly:

```bash
pnpm --filter @jobos/desktop exec vitest run src/renderer/app/architecture.test.ts
```

- [ ] Commit the green documentation/test foundation.

**Deliverable:** A truthful, enforceable renderer ownership contract without a long-lived expected-red test.

### Task 3: Move the cohesive agent and profile owners

**Files:** Use the movement manifest for `agents/`, `career-profile/`, and `installation-profiles/`, including all co-located tests and avatar assets.

- [ ] Move the agent cluster with `git mv`; update imports without changing implementation bodies.
- [ ] Update the eleven avatar asset paths in `tests/public-release/synthetic-fixtures.json`; preserve bytes and checksums.
- [ ] Run agent and settings tests.
- [ ] Move the Career Profile cluster with `git mv`; update imports without splitting the large product component yet.
- [ ] Update current path references in the Career Profile and Connected Agents implementation docs.
- [ ] Run all Career Profile renderer tests.
- [ ] Move Installation Profile UI/tests and run their focused test.
- [ ] Run desktop typecheck/lint and `pnpm public:check`.
- [ ] Commit this owner move separately from later internal refactors.

**Deliverable:** Agents, Career Profile, and installation profiles each have one discoverable renderer owner with behavior unchanged.

### Task 4: Move jobs, browser, and workspace state modules

**Files:** Use the movement manifest for jobs, browser, and workspace modules. Leave `CenterWorkspace.tsx` intact until its seam is characterized.

- [ ] Move `JobNavigator`, `BrowseWorkspace`, job hooks, and `jobStatus` with tests into `jobs/`.
- [ ] Move `useBrowser` with its test into `browser/`.
- [ ] Move `WorkbenchLayout`, `WorkspaceBar`, `useWorkspace`, `workspaceLayout`, and sizing tests into `workspace/`.
- [ ] Keep current behavior imports temporarily explicit across owners; do not add a barrel to hide them.
- [ ] Run job, Browse, browser, workspace, and App integration tests.
- [ ] Run desktop typecheck/lint/build.
- [ ] Commit the green owner move.

**Deliverable:** Job, browser, and workspace state have explicit owners; mixed center composition is isolated as the next seam.

### Task 5: Organize the three document trust domains

**Files:** Use the movement manifest for `documents/artifacts`, `documents/editable`, `documents/docx`, and `documents/previews`.

- [ ] Move artifact workspace and PDF preview with tests.
- [ ] Move editable-editor modules and tests.
- [ ] Move DOCX-editor modules and tests.
- [ ] Move shared preview modules only after confirming at least two document owners consume each one.
- [ ] Move `documentPrint.ts`, update only the script source in `print.html`, and keep the HTML/CSP stable.
- [ ] Move `docxWorker.ts`, update only the script source in `docx-worker.html`, and keep the HTML/CSP stable.
- [ ] Run every document renderer test plus main-process `pdfExporter` and `DocxWorkerManager` tests.
- [ ] Run desktop typecheck/lint/build and the packaged renderer verifier.
- [ ] Inspect `apps/desktop/dist/renderer` and confirm all three HTML entries reference emitted assets with file-safe relative URLs.
- [ ] Commit the green document organization.

**Deliverable:** Artifacts, editable documents, and bound DOCX files are visibly separate without changing their trust or runtime paths.

### Task 6: Move app-owned support and create the thin setup entry

**Files:**

- Move app-owned modules from the movement manifest.
- Create: `apps/desktop/src/renderer/app/WorkbenchApp.tsx`
- Modify: `apps/desktop/src/renderer/app/App.tsx`
- Modify: `apps/desktop/src/renderer/main.tsx`

- [ ] Move onboarding, theme, diagnostics, settings, status, and connectivity modules with tests.
- [ ] Extract the existing ready-workbench body from `App.tsx` to `WorkbenchApp.tsx` without changing it.
- [ ] Keep setup fetching and setup-required rendering in `App.tsx`.
- [ ] Update `main.tsx` to import `App` from `./app/App` and leave its mount/StrictMode behavior unchanged.
- [ ] Move setup-only tests to `app/App.test.tsx`; leave cross-feature integration tests with `WorkbenchApp`.
- [ ] Run setup, theme, settings, diagnostics, connectivity, App, and Workbench integration tests.
- [ ] Run desktop typecheck/lint/build.
- [ ] Commit the thin entrypoint extraction.

**Deliverable:** `App` decides setup vs workbench; `WorkbenchApp` is the single cross-owner composition root.

### Task 7: Deepen the center and save-from-browser seams

**Files:**

- Move: `components/CenterWorkspace.tsx` -> `workspace/CenterWorkspace.tsx`
- Create: `browser/BrowserWorkspace.tsx`
- Create: `jobs/save-from-browser/saveJobPrompt.ts`
- Create: `jobs/save-from-browser/useSaveJobFromBrowser.ts`
- Create: `app/WorkbenchApp.lifecycle.test.tsx`
- Create focused tests beside each module.
- Modify: `app/WorkbenchApp.tsx`

- [ ] Copy the pure save protocol functions to `saveJobPrompt.ts` and move their existing tests before deleting the old definitions.
- [ ] Run the new pure protocol tests and confirm exact prompt/error behavior.
- [ ] Extract the asynchronous save operation to `useSaveJobFromBrowser` behind narrow injected agent/browser dependencies.
- [ ] Move one reconciliation scenario at a time from the oversized Workbench/App integration test to the controller test.
- [ ] Keep the `useBrowser` implementation owned by `browser/`, but instantiate it exactly once in `WorkbenchApp` for the workbench lifetime. Pass its narrow controller to `BrowserWorkspace` and the save workflow.
- [ ] Reduce `CenterWorkspace` to feature-neutral conditional composition while preserving the mixed lifecycle in Section 8.2.
- [ ] Replace `WorkspaceBar`'s direct `InstallationProfileMenu` import with a profile-control slot composed by `WorkbenchApp`.
- [ ] Compose browser, document, and save workflow in `WorkbenchApp`.
- [ ] Add a Research -> Review -> Research -> Browse -> Research lifecycle test. After StrictMode settles, assert one live browser listener, one live save-reconciliation listener, no repeated browser restore, document bridge work only while `DocumentWorkspace` is mounted, and complete listener cleanup after unmount.
- [ ] Extend the PDF preview test to prove surface exit destroys the active `PDFDocumentLoadingTask` and unmounts its canvas.
- [ ] Assert the Workbench lifecycle removes document payload state and any document export portal after returning to the browser view.
- [ ] Run all browser-save, tab, document-view, job-selection, Browse, and workspace integration tests.
- [ ] Commit only after old mixed definitions are removed and the new interface-level tests pass.

**Deliverable:** Workspace owns layout, browser owns browser UI, and jobs own the save outcome through a tested deep module.

### Task 8: Deepen the Career Profile and artifact hotspots

**Files:** Use the module layouts in Sections 8.4 and 8.5.

- [ ] Extract and test Career Profile pure item specifications/presentation functions.
- [ ] Extract the dialog shell and visible regions one at a time; preserve the public `CareerProfileProductExperience` props.
- [ ] Keep cache integrity and bridge mutation logic in the product controller, not UI leaf modules.
- [ ] Split work-arrangement, collaboration, complete-product, and settings code into their named Career Profile subowners.
- [ ] Extract and test pure artifact projection/selection from `DocumentWorkspace`.
- [ ] Add a rerender test proving page, zoom, export-menu, agent-stream, dialog, and unrelated shell changes do not rerun artifact grouping/sorting when artifact and logical-selection inputs are unchanged.
- [ ] Separate artifact async controller state from rendering only when the existing latest-wins tests pass through the new interface.
- [ ] Delete superseded private-function tests once interface tests provide equivalent proof.
- [ ] Run all Career Profile and document workspace tests.
- [ ] Commit Career Profile and artifact deepening as separate commits so either can be reviewed/reverted independently.

**Deliverable:** The two largest feature modules expose small stable interfaces while their internal complexity becomes local and testable.

### Task 9: Split styles by owner without visual change

**Files:**

- Modify: `apps/desktop/src/renderer/styles.css`
- Create owner CSS files shown in the target tree.
- Modify CSS-reading tests to read the owning stylesheet or the complete ordered stylesheet set.

- [ ] Add a test/helper that resolves `styles.css` imports in order and concatenates the source for selector assertions.
- [ ] Move declarations owner by owner without editing declaration values or selector spelling.
- [ ] Keep order-sensitive shared declarations in `app/styles/foundation.css` or `app/styles/app-shell.css`.
- [ ] Keep chat CSS and Connected Agent settings CSS in separate co-located agent files if their current cascade positions differ.
- [ ] Turn root `styles.css` into only ordered `@import` statements.
- [ ] Run all CSS-source tests, theme tests, component tests, desktop build, and packaged-renderer verification.
- [ ] Build with `--manifest` and confirm the packaged application emits exactly one application CSS asset, contains no unresolved `@import`, and does not duplicate owner rules. Stop for review if CSS grows by more than 2% or 10 KiB gzip, whichever is larger, without a measured explanation.
- [ ] Compare representative before/after screenshots or perform an installed visual check on workbench, Browse, Settings, Career Profile, artifact preview, editable editor, and DOCX editor.
- [ ] Treat any visual difference as a regression unless the user separately approves it.
- [ ] Commit the CSS split separately.

**Deliverable:** Styles are co-located by owner while the stable entrypoint and visual output remain unchanged.

### Task 10: Lock the final architecture and remove legacy buckets

**Files:**

- Modify: `apps/desktop/src/renderer/app/architecture.test.ts`
- Modify: `apps/desktop/src/renderer/README.md`
- Remove empty legacy directories: `components/`, `hooks/`, `document-editor/`, `agent-avatar/`, `diagnostics/`, `onboarding/`, `theme/`

- [ ] Update the architecture test root allowlist to exactly the stable entry files plus approved owner directories.
- [ ] Assert there are no production files beneath legacy technical buckets.
- [ ] Assert feature owners do not import renderer entrypoints or app composition.
- [ ] Assert workspace production modules do not import feature implementations.
- [ ] Assert `main.tsx` contains no feature bridge access or feature implementation.
- [ ] Search the entire repository for old renderer paths and classify every remaining match as current, historical, generated, or stale.
- [ ] Update current docs/tests/manifests; do not rewrite historical evidence without a reason.
- [ ] Run the complete focused renderer suite and architecture test.
- [ ] Commit the ownership lock and cleanup.

**Deliverable:** The directory promises are enforced, legacy catch-all folders are gone, and future drift fails in CI.

### Task 11: Full verification and implementation handoff

**Files:** Only fixes required by failed verification; no opportunistic cleanup.

- [ ] Run repository-required setup and verification:

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
```

- [ ] Run public-boundary checks because synthetic asset paths moved:

```bash
pnpm public:check
```

- [ ] Build the renderer twice and verify the second build has no stale-path dependency:

```bash
pnpm --filter @jobos/desktop build
pnpm --filter @jobos/desktop build
node scripts/verify-packaged-renderer.mjs
pnpm --filter @jobos/desktop exec vite build --manifest
```

- [ ] Compare the candidate Vite manifest and asset measurements with Task 1. PDF.js must remain dynamic. Print and DOCX-worker entries must not acquire application-owner imports. The application entry must not gain a static edge to `documentPrint`, `docxWorker`, `pagedjs`, or `pdfjs-dist`.
- [ ] Stop for review if any initial entry closure or application CSS grows by more than 2% or 10 KiB gzip, whichever is larger, without a measured explanation. Report all size and topology changes even when they remain below the stop threshold.
- [ ] Confirm the built renderer has one application CSS asset with no unresolved `@import`.
- [ ] Commit the candidate before clean-clone verification because that gate tests committed `HEAD`.
- [ ] Run:

```bash
pnpm public:smoke-clean-clone
```

- [ ] When the host supports it, run the packaged frontend smoke/installed visual workflow. If it cannot run, state exactly what remains unverified and why.
- [ ] Inspect `git status`, `git diff --check`, and the candidate diff. Confirm unrelated untracked docs/user work remain untouched.
- [ ] Return an evidence package containing: baseline and candidate SHAs, commits by task, final renderer tree, files changed and why, exact command outcomes, behavioral/visual verification, unresolved risk, and any current docs not updated because they are historical.

**Deliverable:** A reviewable behavior-preserving candidate with end-to-end proof and no hidden release claim.

## 10. Test migration rules

- Keep `App` tests only for setup gating.
- Keep `WorkbenchApp` tests for real cross-owner journeys: profile mismatch, native-browser detachment around overlays/Browse, workspace mounting, job-selection coordination, and keyboard shortcuts spanning the shell.
- Move pure default-agent/model selection tests to `agents/connected-agents/defaultSelection.test.ts`.
- Move save protocol/error/navigation tests to `jobs/save-from-browser/saveJobPrompt.test.ts`.
- Move async save reconciliation tests to `jobs/save-from-browser/useSaveJobFromBrowser.test.tsx`.
- Keep feature UI tests with their feature UI.
- Replace private-helper tests with owner-interface tests when extracting a deep module.
- Preserve race tests. Latest-wins, stale-result rejection, early events, hydration rebasing, and profile-switch ordering are correctness requirements, not incidental implementation detail.
- Do not bulk-update snapshots; this suite uses behavioral assertions and should continue to do so.

### Suggested commit sequence

Use these messages only when they truthfully match the staged diff; never stage unrelated files to satisfy the sequence.

1. `test(renderer): define ownership boundaries`
2. `refactor(renderer): group agent and profile ownership`
3. `refactor(renderer): group jobs browser and workspace ownership`
4. `refactor(renderer): organize document modules`
5. `refactor(renderer): separate setup and workbench composition`
6. `refactor(renderer): deepen center workflow ownership`
7. `refactor(renderer): deepen career profile product modules`
8. `refactor(renderer): deepen artifact workspace`
9. `refactor(renderer): split feature-owned styles`
10. `test(renderer): lock final ownership architecture`

## 11. Review gates

Each task must answer these before proceeding:

1. **Ownership:** Can a contributor find UI, state, tests, and styles from the product term alone?
2. **Interface:** Did the move reduce what callers must know, or only add forwarding files?
3. **Behavior:** Do existing interface-level tests still prove the real outcome and failure modes?
4. **Trust:** Did bridge, browser, profile, document, or credential handling move without weakening its constraints?
5. **Runtime:** Do source, Vite, built, and packaged paths still resolve to the intended renderer assets?

Stop and ask Cobi before proceeding if implementation evidence requires changing user-visible behavior, the ownership model, persistence/contracts, trust boundaries, or the approved design language.

## 12. Completion definition

The refactor is complete only when:

- the renderer root contains only stable entry files, `README.md`, and approved owner directories;
- `components/`, `hooks/`, `document-editor/`, `agent-avatar/`, `diagnostics/`, `onboarding/`, and `theme/` no longer exist as root catch-all directories;
- `App` is a setup gate and `WorkbenchApp` is the single cross-owner composition root;
- workspace modules contain no browser/job/document/agent/Career Profile implementation;
- browser and save controllers keep one workbench-lifetime instance while browser/document presentation remains mutually exclusive;
- surface switching does not multiply subscriptions, restores, observers, polling, workers, canvases, payload buffers, object URLs, or portals;
- save-from-browser, Career Profile product, and artifact selection are tested through narrow owner interfaces;
- artifact grouping/sorting retains its current memo boundary across unrelated renderer updates;
- styles are owner-local behind the stable ordered `styles.css` entrypoint;
- all direct path consumers and current docs are updated;
- the candidate Vite manifest preserves dynamic PDF loading, separate print/DOCX-worker entries, one built application stylesheet, and the approved size threshold;
- focused, full, contracts, public, clean-clone, build-twice, and packaged-renderer checks pass;
- real visual/workflow verification is complete or its precise residual gap is reported;
- the implementation handoff distinguishes verified behavior from unverified risk and makes no deployment/release claim.

## 13. Starting prompt for Hermes

Implement this plan from the current synchronized JobOS repository. First read `AGENTS.md`, this file, `apps/desktop/src/main/README.md`, and `docs/public/architecture.md`; then re-check the current branch/tree against the recorded planning baseline. The user has approved the renderer ownership refactor and behavior-preserving implementation. The product domains, lifecycle/cardinality rules, bundle gates, non-goals, trust constraints, and verification gates are locked. You own small extraction and sequencing choices, but stop for Cobi if current evidence requires a user-visible change, contract/persistence change, security relaxation, different ownership model, heavier mount lifetime, duplicated controller, or new eager bundle edge. Work in independently testable commits, preserve unrelated/untracked work, and return the evidence package defined in Task 11. Implementation authorization does not by itself authorize pushing, merging, deployment, or release.
