# Desktop Main-Process Organization Plan

- **Status:** Proposed; planning only; implementation requires separate approval
- **Verified source baseline:** `0ecea42205cb5485197e019e8244cf537a2dffc2` on `origin/main`, verified 2026-08-27
- **Scope:** `apps/desktop/src/main`, its direct build/runtime consumers, and documentation needed to keep the new ownership model understandable

**Goal:** Make the Electron main process easy for humans and agents to navigate, change, and review without weakening JobOS behavior, security, persistence, packaging, or local-first product boundaries.

**Architecture:** Keep `apps/desktop/src/main/main.ts` as the stable Electron entrypoint and composition root. Move implementation and tests into explicit ownership folders, give each feature one tested IPC seam, separate the three document trust domains, and make startup/lifecycle ordering visible rather than mixing it with feature behavior.

**Non-authorizing statement:** This file defines the proposed refactor. It does not authorize moving files or changing code. Re-read the current tree and obtain explicit implementation approval before starting Phase 0.

---

## 1. Product and maintainer outcome

After this refactor, a contributor should be able to answer these questions without searching the entire desktop app:

1. Which folder owns this behavior?
2. Where does the renderer request cross into trusted Electron code?
3. Which state is application-scoped, profile-scoped, or window-scoped?
4. Which invariants and verification gates apply before changing it?
5. Where should a new main-process file or test go?

The result should reduce accidental edits in unrelated systems, merge conflicts in `main.ts`, duplicated IPC wiring, broken runtime-relative paths, stale packaged modules, and changes that pass unit tests while weakening a real desktop workflow.

This is an organization and ownership refactor. User-visible behavior should not change.

## 2. Verified current state

At the verified baseline:

- `apps/desktop/src/main` has 65 top-level TypeScript files: 34 production files and 31 tests.
- `main.ts` is 1,072 lines and combines global state, source API process control, profile switching, feature IPC, security, native windows, browser ownership, event streams, media capture, startup, and shutdown.
- Feature ownership is inconsistent:
  - Agent, Connected Agent, editable-document, and most local-DOCX handlers have dedicated IPC modules.
  - `browserIpc.ts` owns only restoration while the rest of the browser IPC remains in `main.ts`.
  - Career Profile, jobs, workspace, artifacts, installation profiles, setup, diagnostics, connectivity, and shell IPC remain inline in `main.ts`.
- Three different document trust models share similar flat names:
  - registered immutable artifacts in `documents.ts`;
  - API-owned canonical editable documents in `editableDocuments.ts`;
  - device-local bound DOCX files in `docxDocuments.ts`.
- `JobsConfig` is used as a generic authenticated/profile-pinned desktop API configuration by Career Profile, workspace, artifacts, and editable documents. Its current owner and name imply false dependencies on jobs.
- `agentIpc.ts` imports validation from `connectedAgentsIpc.ts`, creating an IPC-adapter-to-IPC-adapter dependency.
- `document-export/documentHtml.ts` is imported by renderer code, so it is process-neutral code living under a process-specific folder.
- The current dependency graph is effectively acyclic. The target must preserve that property.

### 2.1 Why this is a correctness refactor, not cosmetic sorting

File moves affect runtime behavior in this codebase:

- TypeScript mirrors nested source paths under `dist/`.
- Native helper, preload, and renderer paths are currently calculated relative to individual module locations.
- The updater, clean-clone exporter, renderer print surface, fixture manifest, and implementation docs name current source or compiled paths directly.
- `tsc` does not remove outputs for source files that moved or were deleted.
- Electron Builder packages `dist/**/*`; a stale non-test module can therefore survive a green build and ship beside its replacement.

The implementation must make clean generated output and packaged path verification part of the refactor itself.

## 3. Decision and alternatives

### Selected: feature ownership plus a thin composition root

Group files by real ownership, co-locate tests, extract feature IPC registrars, and reduce `main.ts` to an ordered application lifecycle checklist. Use narrow injected dependencies at cross-feature seams.

This provides the strongest improvement in navigability and mistake prevention without redesigning product behavior.

### Rejected: alphabetize or rename the flat folder

This makes scanning marginally easier but leaves unrelated ownership, inline feature IPC, lifecycle state, and merge conflicts in one place.

### Rejected: move files into folders but leave `main.ts` unchanged

This reduces root noise but preserves the most dangerous ambiguity: features would appear to own their code while their trust checks, validation, and wiring still live in a 1,000-line global file.

### Rejected: redesign the large feature clients during this work

`careerProfile.ts`, `browser.ts`, `editableDocuments.ts`, and `agent.ts` may deserve later deep-module reviews. Splitting their internal behavior now would mix organization changes with behavioral risk and obscure Git rename history.

## 4. Target ownership model

The provisional `profiles/` name is tightened to `installation-profiles/` so contributors cannot confuse JobOS installation profiles with the separate Career Profile product feature.

```text
apps/desktop/src/main/
├── README.md
├── main.ts
├── app/
│   ├── automation/
│   ├── capabilities/
│   ├── ipc/
│   ├── runtime/
│   ├── security/
│   └── window/
├── agents/
├── browser/
├── career-profile/
├── documents/
│   ├── artifacts/
│   ├── docx/
│   └── editable/
│       ├── export/
│       └── import/
├── installation-profiles/
├── jobs/
└── workspace/

apps/desktop/src/shared/
└── document-rendering/
```

### 4.1 Ownership definitions

| Module | Owns | Does not own |
| --- | --- | --- |
| `app/` | Electron startup, runtime configuration, native capability transport, security, windows, and deterministic capture automation | Feature rules, feature validation, or user-domain persistence |
| `agents/` | Conversations, Connected Agent configuration, agent/model selection validation, agent IPC, and agent event streams | Browser or document implementations |
| `browser/` | Live `WebContentsView` tabs, browser persistence/restore, browser IPC, navigation, downloads, and browser security | Job creation or job persistence |
| `career-profile/` | Career Profile clients, native archive behavior, acceptance path adapter, and Career Profile IPC | JobOS installation profiles |
| `documents/artifacts/` | Registered artifact listing, loading, verification, native open/reveal/export, and artifact IPC | Editable source documents or bound local DOCX files |
| `documents/editable/` | Canonical editable documents, import/export, snapshots, publication, and editable-document IPC | Local DOCX file bindings |
| `documents/docx/` | Device-local DOCX binding, worker, watcher, recoveries, atomic replacement, and DOCX IPC | Canonical artifact or editable-document ownership |
| `installation-profiles/` | JobOS Profile registry client, profile storage identity/paths, switching, rollback, and profile IPC | Career Profile data |
| `jobs/` | Job clients, job IPC, job event streams, and the browser-listing-to-job transaction | Browser tab implementation |
| `workspace/` | Durable workbench layout, selected surfaces, browser metadata, artifact view state, and workspace IPC | Electron application lifecycle |
| `shared/document-rendering/` | Pure rendering logic used by both main and renderer processes | Electron APIs or native side effects |

## 5. Exact relocation map

Matching `*.test.ts` files move beside the production files they test. Preserve existing basenames and `.js` import extensions during the mechanical move phase unless this table explicitly names a cross-process correction.

| Current source | Target source |
| --- | --- |
| `main.ts` | stays at `main/main.ts` |
| `apiLifecycle*` | `main/app/runtime/` |
| `connectivity*` | `main/app/runtime/` |
| `credentialStore*` | `main/app/runtime/` |
| `desktopRuntime*` | `main/app/runtime/` |
| `runtimeConfig*` | `main/app/runtime/` |
| `capabilityClient*` | `main/app/capabilities/` |
| `mainWindowLifecycle*` | `main/app/window/` |
| `security*` | `main/app/security/` |
| `mediaCapture.ts`, `mediaCaptureSpec*` | `main/app/automation/` |
| `agent*`, `agentIpc*` | `main/agents/` |
| `connectedAgents*`, `connectedAgentsIpc*` | `main/agents/` |
| `browser.ts`, `browserIpc*` | `main/browser/` |
| `browserJobExtraction*` | `main/jobs/` because its only production use is validated job creation from browser evidence |
| `careerProfile*` | `main/career-profile/` |
| `careerProfileAcceptanceDialogs*` | `main/career-profile/` |
| `careerProfileArchiveWriter*` | `main/career-profile/` |
| `documents*` | `main/documents/artifacts/` |
| `editableDocuments*`, `editableDocumentsIpc*` | `main/documents/editable/` |
| `document-export/documentDocx*` | `main/documents/editable/export/` |
| `document-export/pdfExporter*` | `main/documents/editable/export/` |
| `document-export/documentHtml*` | `shared/document-rendering/editableDocumentHtml*` because both main and renderer consume it |
| entire `document-import/` tree | `main/documents/editable/import/`, including the fixture builder and every approved synthetic DOCX fixture |
| `DocxWorkerManager*` | `main/documents/docx/`; preserve its current casing during this refactor |
| `docxDocuments*`, `docxDocumentsIpc*` | `main/documents/docx/` |
| `docxFileStore*`, `docxFileWatcher*` | `main/documents/docx/` |
| `localDocxBindingStore*` | `main/documents/docx/` |
| `installationProfiles*`, `profileStorage*` | `main/installation-profiles/` |
| `jobs*` | `main/jobs/` |
| `workspace*` | `main/workspace/` |

### 5.1 Neutral seam corrections

These corrections should land in their own behavior-neutral commit before the bulk moves:

1. Move the unchanged `{ baseUrl, deviceToken, installationProfileId }` shape from `JobsConfig` to `app/runtime/desktopApiConfig.ts` as `DesktopApiConfig`. Feature clients may extend it with their own optional test/runtime fields, but this work must not introduce a generalized API client or change request behavior.
2. Move `validateAgentChatSelection` out of `connectedAgentsIpc.ts` into `agents/agentChatSelection.ts` with `agentChatSelection.test.ts` so one IPC adapter does not import another.
3. Move pure editable-document HTML rendering to `src/shared/document-rendering/` so renderer code no longer imports from `src/main`.

## 6. `main.ts` decomposition

Line references below are anchors at the verified baseline. Re-find functions by name before implementation because line numbers will move.

| Current responsibility | Current anchor | Target |
| --- | --- | --- |
| renderer, preload, and repository-root path resolution | `main.ts:63-65`, `864`, `932` | `app/runtime/desktopPaths.ts`; inject resolved paths into runtime, window, and automation modules |
| trusted renderer assertion | `main.ts:99-104` | extend `app/security/security.ts` with the trusted-event assertion |
| generic authenticated desktop configuration | `main.ts:106-114` | `app/runtime/desktopApiConfig.ts` |
| profile snapshots, switching, source rollback, relaunch | `main.ts:116-318` | `installation-profiles/profileSwitchCoordinator.ts` and `installationProfilesIpc.ts` |
| external-link IPC | `main.ts:320-327` | `app/ipc/shellIpc.ts` |
| source API start/stop | `main.ts:329-370` and `162-180` | `app/runtime/sourceApiProcess.ts` |
| setup initializer and IPC | `main.ts:372-425` | `app/ipc/setupIpc.ts` with a narrow initializer dependency |
| diagnostics IPC | `main.ts:427-473` | `app/ipc/diagnosticsIpc.ts` |
| connectivity IPC | `main.ts:475-489` | `app/ipc/connectivityIpc.ts` |
| Career Profile IPC | `main.ts:491-583` | `career-profile/careerProfileIpc.ts` |
| Agent and Connected Agent composition | `main.ts:585-599` | use the existing registrars from `agents/`; construction remains in composition |
| jobs IPC and save-from-browser transaction | `main.ts:601-686` | `jobs/jobsIpc.ts` |
| workspace IPC | `main.ts:688-709` | `workspace/workspaceIpc.ts` |
| remaining browser IPC | `main.ts:711-752` | expand `browser/browserIpc.ts` |
| registered artifact IPC | `main.ts:754-793` | `documents/artifacts/documentsIpc.ts` |
| DOCX construction and `open-artifact` | `main.ts:795-833` | expand `documents/docx/docxDocumentsIpc.ts`; service construction remains composition-owned |
| editable-document composition | `main.ts:835-845` | continue using `documents/editable/editableDocumentsIpc.ts` |
| secure window construction and window-scoped services | `main.ts:847-1009` | `app/window/mainWindow.ts` |
| ordered startup and Electron global lifecycle | `main.ts:1011-1072` | root `main.ts` plus a tested `app/bootstrap.ts` coordinator |

### 6.1 Final `main.ts` responsibility test

The final root entrypoint should read as a short ordered startup checklist. It may:

- create the desktop application coordinator;
- start after `app.whenReady()`;
- route `activate`, `before-quit`, `will-quit`, and `window-all-closed` lifecycle events;
- report fatal startup failure and exit safely.

It must not contain:

- raw `jobos:` channel strings;
- feature-specific validators or error messages;
- document, browser, job, agent, Career Profile, or installation-profile implementation logic;
- native helper or generated asset path calculations;
- feature-owned mutable state.

Do not enforce an arbitrary line-count test. Enforce responsibility and interface rules instead.

## 7. Module interfaces and dependency direction

### 7.1 Allowed direction

```text
main.ts
  -> app/bootstrap
      -> app runtime/security/window modules
      -> feature IPC modules and feature clients

feature modules
  -> shared contracts and pure shared modules
  -> narrow app runtime types where required
  -> their own internal implementation

preload
  <-> main only through fixed IPC channels and shared contract types

renderer
  -> preload bridge and process-neutral shared modules
  -X-> main implementation modules
```

Only `app/bootstrap.ts` is allowed to compose multiple feature implementations. Feature modules must not import `main.ts` or `app/bootstrap.ts`.

`app/window/mainWindow.ts` owns Electron window creation and security, but it does not import feature implementations. Bootstrap injects an `attachWindowFeatures(window)` callback or equivalent narrow factory. That callback constructs the window-scoped browser, capability connection, agent stream, and job stream and returns one cleanup handle. This keeps multi-feature composition in bootstrap while keeping Electron window mechanics in the window module.

### 7.2 IPC seam

Each feature owns one explicit registrar interface, following the existing successful pattern:

```text
registerFeatureIpc(ipc, assertTrustedRenderer, liveClientAccessors, narrowNativeDependencies)
```

Rules:

- Preserve `handle` versus `on`, channel strings, argument order, return shapes, validation order, and safe error text exactly.
- Keep trust enforcement at every renderer-originated operation.
- Use live getter callbacks when a dependency is created after IPC registration. In particular, browser handlers must receive `getBrowserManager`, not the current `null` manager value.
- Return explicit cleanup handles for listeners that need removal.
- Do not introduce a generic IPC framework, service locator, dependency container, or runtime channel registry.
- Do not add broad `index.ts` barrel exports. Explicit paths make ownership and cycles visible.

### 7.3 Cross-feature seams

| Workflow | Owner | Injected seam |
| --- | --- | --- |
| save browser listing as a job | `jobs/jobsIpc.ts` | narrow live browser state/access interface; browser never imports jobs |
| open registered artifact as local DOCX | `documents/docx/docxDocumentsIpc.ts` | registered-artifact loader plus DOCX service |
| execute authenticated desktop capability command | `app/capabilities/capabilityClient.ts` | structural browser and DOCX capability interfaces; it owns neither implementation |
| switch JobOS Profile | `installation-profiles/profileSwitchCoordinator.ts` | browser download/bounds, renderer safety, runtime restart/rollback, identity probe, and app relaunch ports |

### 7.4 Lifecycle ownership

| Lifetime | State/services |
| --- | --- |
| application-scoped | runtime/configuration, active profile identity, source API process, global IPC registration, document/artifact clients, `DocxDocumentsService`, and `DocxWorkerManager` |
| window-scoped | `BrowserWindow`, `BrowserManager`, `RendererSafetyCoordinator`, desktop capability connection, agent event stream, job event stream, and their cleanup handles |
| profile-scoped | renderer/browser partitions, client paths, local DOCX bindings/recoveries, profile-pinned requests, workspace state, conversations, and browser metadata |

macOS activation may create another window. It must not register global IPC twice or dispose application-scoped DOCX services when one window closes.

## 8. Locked behavior and trust invariants

### 8.1 Entry, build, and contracts

- `apps/desktop/src/main/main.ts` must continue compiling to `apps/desktop/dist/main/main.js`.
- `apps/desktop/package.json` must retain that Electron entrypoint.
- Generated OpenAPI and TypeScript contracts must have no semantic diff.
- No dependency or package addition is expected.
- Preserve NodeNext `.js` extensions in TypeScript imports.

### 8.2 Renderer and Electron security

- Preserve trusted renderer URL enforcement, development-origin handling, packaged path containment, and sender checks.
- Preserve context isolation, sandboxing, disabled Node integration, disabled insecure content, denied renderer navigation/window opening, and deny-all permission policies.
- Remote browser pages remain main-owned and receive no privileged preload or renderer bridge.
- Sandboxed preloads remain self-contained; do not add local runtime `require` dependencies.

### 8.3 Installation profiles and continuity

- Resolve and verify the opaque active profile before choosing renderer/browser partitions or profile-local paths.
- Preserve anchored-installation compatibility and managed-profile isolation.
- Preserve profile-switch ordering: disable new downloads, perform the first active-download preflight, request renderer safety acknowledgment, perform the second active-download check, hide the browser, activate, confirm the exact target identity, roll back when required, restore bounds on failure, re-enable downloads, and relaunch only after success.
- Browser restore must complete before the desktop capability connection begins.
- Job selection must not close browser tabs, reset authenticated sessions, or discard in-progress forms.

### 8.4 Browser and jobs

- Preserve profile-specific partitions, restoration filtering, metadata redaction, repair/deduplication before the 50-tab cap, popup handling, download state, and ordinary-web URL policy.
- Preserve save-from-browser checks before and after job creation: active tab, expected URL, loading state, document epoch, and association state.
- Browser remains unaware of job persistence; the jobs workflow receives only a narrow browser seam.

### 8.5 Documents

- Keep registered artifacts, editable documents, and local DOCX bindings as separate modules and trust models.
- Preserve opaque IDs, media limits, artifact identity headers, checksum verification, and one-read byte/hash consistency.
- Preserve authoritative PDF and last-good artifact behavior.
- Preserve immutable artifact roots, path containment, symlink resistance, recovery creation, external-change detection, serialized writes, and atomic native replacement.
- Preserve DOCX worker sender binding and sandboxed preload behavior.
- Preserve same-revision DOCX/PDF publication and unresolved-suggestion confirmation.
- Moving synthetic fixtures may change manifest paths but must not change fixture bytes, checksums, classifications, or provenance meaning.

### 8.6 Career Profile and agents

- Preserve no-follow archive reads, size limits, bounded selection state, exact confirmation semantics, and verified atomic archive writes.
- Preserve profile-pinned agent requests, immutable agent/model bindings, conversation registry serialization, event buffering/recovery, redaction, and fixed capability-command validation.
- Preserve stable local-first degraded states and capability errors when optional agents or private integrations are unavailable.

## 9. Runtime and build path migration checklist

Every row must be updated and verified atomically with the move that affects it.

| Consumer | Current dependency | Required proof after move |
| --- | --- | --- |
| `credentialStore.ts` | source Keychain helper path relative to `import.meta.url` | source and packaged helper paths resolve to the intended executable |
| `careerProfileArchiveWriter.ts` | archive helper path relative to module depth | source and packaged archive export use the intended helper |
| `docxFileStore.ts` | atomic-replace helper path relative to module depth | source and packaged atomic replacement use the intended helper |
| `DocxWorkerManager.ts` | DOCX worker preload and renderer HTML relative to module depth | compiled worker loads only the expected sandboxed assets |
| `pdfExporter.ts` | print preload and renderer HTML relative to module depth | compiled print window loads the intended assets and preserves sandbox settings |
| root `main.ts` path setup | renderer root, preload path, repository source root, and media fixture root are derived at `main.ts:63-65`, `864`, and `932` | `app/runtime/desktopPaths.ts` produces the same validated source and packaged locations; bootstrap injects them into `sourceApiProcess`, `mainWindow`, and media automation |
| `apps/desktop/scripts/create-macbook-update.mjs` | exact `src/main/credentialStore.ts` source path | updater source identity verification points to the new file |
| `scripts/public-release/export-editable-document.mjs` | exact `dist/main/document-export/*` dynamic imports | clean-clone export imports the new compiled modules and produces a verified DOCX/PDF pair |
| `apps/desktop/src/renderer/documentPrint.ts` | imports pure HTML renderer from `src/main` | imports only the new shared process-neutral module |
| `tests/public-release/synthetic-fixtures.json` | exact import-fixture and builder paths | all approved fixtures and tracked provenance paths validate at their new locations |
| browser/import tests | `import.meta.url` fixture paths | tests resolve the same fixture bytes after relocation |
| current implementation docs | exact active source paths | references point to the new authoritative files; historical commit links remain historical |

### 9.1 Clean generated output

Before moving production files, make Electron compilation relocation-safe:

1. Add a bounded clean step for generated `apps/desktop/dist/main/`, `apps/desktop/dist/preload/`, and `apps/desktop/dist/shared/` output immediately before Electron TypeScript compilation.
2. The clean target must be resolved from the desktop package root and refuse broader workspace or source paths.
3. Do not delete `src/`, application data, release artifacts, or unrelated workspace output.
4. After a clean build, assert that `dist/main/main.js` exists and that every moved legacy production path is absent.
5. Package only the clean output. A second build must produce the same file inventory.

This guard is required because the current ignored `dist/main` contains stale compiled files that `tsc` no longer owns, while Electron Builder includes non-test `dist/**/*` files.

## 10. Ordered implementation phases

Each phase must begin from a synchronized baseline and end with a reviewable, passing commit. Do not combine mechanical moves with formatting or opportunistic logic cleanup.

### Phase 0 — Lock the externally observable baseline

Do not import executable `main.ts` into unit tests and do not export private functions solely for testing. Add only tests that can observe current seams without starting the Electron application:

- exact preload/main IPC channel inventory and registration-kind parity from the existing preload and source registrations;
- existing registrar trust, validation, forwarding, and safe-error behavior;
- current source and packaged helper/preload/renderer path expectations;
- current clean-build file inventory;
- the already exported profile-switch helper ordering and browser-restore barrier.

Private inline behavior uses an extract-and-characterize sequence later:

1. create the focused registrar or coordinator with narrow injected dependencies;
2. copy the existing handler/orchestration body without cleanup;
3. add direct tests for the new seam, including exact channels, validation order, errors, and side effects;
4. switch `main.ts` to the tested seam in the same commit;
5. remove the old inline body only after the direct tests pass.

This sequence applies to Career Profile, jobs, workspace, remaining browser handlers, artifacts, `docx:open-artifact`, installation profiles, system IPC, profile switching, secure window construction, and startup/cleanup. Record the passing observable baseline before moving files.

### Phase 1 — Make builds safe for relocation

- Add the bounded Electron output clean step.
- Add a clean-build inventory assertion.
- Build twice and prove no stale top-level production modules remain.
- Keep `dist/main/main.js` stable.

### Phase 2 — Correct neutral ownership seams

In one or more small commits:

- extract `DesktopApiConfig` from `jobs.ts` without changing its shape or request behavior;
- extract Agent chat-selection validation from the IPC adapter dependency;
- move pure editable-document HTML rendering into `src/shared/document-rendering/` and update its main/renderer consumers.

### Phase 3 — Mechanical ownership moves

Use `git mv`; move each implementation with its tests; change imports, direct path consumers, and fixture manifest paths only. Preserve basenames and function bodies.

Recommended move order follows. Each numbered item is ordering only: every named ownership directory/domain gets its own buildable commit rather than one grouped commit.

1. agents;
2. browser, then jobs, then workspace;
3. Career Profile, then installation profiles;
4. app runtime, then capabilities, then security, then window helpers, then automation;
5. registered artifacts, then local DOCX;
6. editable documents, then import, then export and fixtures.

The documents move gets its own dedicated review because source paths, compiled imports, renderer imports, fixtures, and native assets are coupled.

### Phase 4 — Extract feature IPC one family at a time

For each family, use the extract-and-characterize sequence from Phase 0: create the seam, copy the unchanged handler body, add direct tests, switch `main.ts`, and only then delete the inline body.

1. Career Profile;
2. jobs and save-from-browser;
3. workspace;
4. remaining browser operations;
5. registered artifacts;
6. DOCX `open-artifact` and remaining composition;
7. installation profiles;
8. setup, diagnostics, connectivity, and shell.

Do not change validation order or normalize error messages during extraction.

### Phase 5 — Extract lifecycle coordinators

- Extract source API process ownership.
- Extract profile-switch coordination behind narrow injected ports, with tests for download disabling before both preflight checks and the complete rollback/relaunch order.
- Extract secure window construction and window-scoped cleanup. Bootstrap supplies the feature-attachment factory; the window module does not import feature implementations.
- Add `app/bootstrap.ts` as the only multi-feature composition module.
- Reduce root `main.ts` to the application lifecycle checklist.

Preserve the distinction between application-, profile-, and window-scoped state.

### Phase 6 — Document and guard the architecture

Add `apps/desktop/src/main/README.md` containing:

- a one-screen directory map;
- the renderer -> preload -> feature IPC -> client/service request flow;
- ownership definitions and lifecycle scopes;
- allowed dependency direction;
- the registrar pattern and live-getter rule;
- instructions for adding a feature or IPC channel;
- path-sensitive and packaged verification gates;
- a rule against broad barrels and new root-level feature files.

Add a focused architecture test that permits only `main.ts`, `README.md`, and directories at the `src/main` root. Do not add a new lint dependency solely for this rule.

Update current source references in durable implementation documentation. Do not rewrite historical evidence or old commit-specific paths as if history changed.

### Phase 7 — Complete verification and review

- Run the full verification contract below.
- Review with rename detection enabled.
- Inspect every changed line that is not an import/path update or direct handler relocation.
- Confirm generated contracts have no semantic diff.
- Confirm old compiled module paths are absent.
- Perform source and packaged synthetic smoke checks without real user records.
- Obtain human review before merge because this changes the navigation model for future contributors.

## 11. Verification contract

### 11.1 Focused gates during implementation

Run after each moved or extracted feature:

For example, after the browser IPC move (replace the concrete path with the current moved or extracted tests for other commits):

```bash
pnpm --filter @jobos/desktop exec vitest run src/main/browser/browserIpc.test.ts
pnpm --filter @jobos/desktop lint
pnpm --filter @jobos/desktop typecheck
pnpm --filter @jobos/desktop build:electron
pnpm public:check
git diff --check
```

For updater, native-helper, fixture, document-export, or compiled path changes, run the relevant focused tests immediately. `public:smoke-clean-clone` always clones committed `HEAD`, so run that command only after the candidate commit exists. Record the exact tested SHA; if a follow-up commit is needed, rerun it against the new SHA.

```bash
pnpm test:updater
pnpm public:smoke-clean-clone
```

### 11.2 Final repository gates

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
pnpm contracts:test-drift
pnpm public:smoke-clean-clone
```

Because this refactor moves compiled Electron assets and native-helper callers, run on supported macOS with synthetic/test data only:

```bash
pnpm test:macos-native
pnpm test:frontend:packaged
```

Final smoke coverage must include:

- source startup and packaged startup;
- exact active profile identity before partitions and local document paths are used;
- browser restore, persistence, download state, and ordinary navigation;
- job selection without browser/session loss;
- save-from-browser race protection;
- document preview, export, reveal/open, and artifact-to-DOCX opening;
- DOCX editing, recovery, external-change handling, and worker lifecycle;
- agent offline/not-configured behavior plus configured synthetic conversation flow where available;
- profile-switch success and safe rollback;
- app close and macOS reactivation without duplicate IPC or leaked listeners.

### 11.3 Verification documentation mismatch

`AGENTS.md` currently requires an “expected-red” gate documented in `docs/public/release-process.md`, but the verified release-process document does not name an exact expected-red command. Do not invent one. Before implementation is declared complete, resolve that instruction/document mismatch through an approved documentation correction or obtain the repository owner’s explicit direction on the canonical gate.

## 12. Review rules and mistake-prevention guardrails

- One commit should represent one kind of change: characterization, neutral seam correction, one ownership move, one IPC extraction, lifecycle extraction, or documentation.
- Keep every commit buildable and testable.
- Use rename-aware review and preserve file history.
- Do not reformat moved files.
- Do not rename `DocxWorkerManager` during this work; a case-only rename is risky on macOS and unrelated to ownership.
- Do not add barrel exports, a generic feature registry, service locator, dependency container, or new IPC framework.
- Do not centralize channel strings into a runtime module imported by sandboxed preloads. Use parity tests instead.
- Do not change API routes, schemas, database formats, persistence semantics, public errors, product language, security policy, or capability states.
- Do not regenerate and commit contracts unless an unexpected drift is investigated and separately authorized.
- Stop if a path-only commit changes behavioral test output, contracts, fixture bytes/checksums, bundle identity, or IPC inventory.

## 13. Explicit non-goals and pre-existing issues

This plan does not include:

- splitting the internal logic of `careerProfile.ts`, `browser.ts`, `editableDocuments.ts`, or `agent.ts`;
- consolidating legacy browser partition constants;
- redesigning the API/client transport layer;
- changing Connected Agent, Career Profile, document, browser, job, or JobOS Profile behavior;
- introducing new providers, packages, storage, or network requirements;
- changing bundle, LaunchAgent, Keychain, or other stable macOS identifiers;
- changing fixture contents or public-release evidence;
- packaging, publishing, deploying, or merging the implementation.

One existing DOCX typing discrepancy must not be silently fixed during mechanical moves: shared/preload types allow recovery reason `agent`, while the current `docxDocumentsIpc.ts` annotation omits it. Add characterization and adjudicate it as a separately approved behavior correction.

## 14. Completion definition

The refactor is complete only when all of the following are true:

- `src/main` root contains only `main.ts`, `README.md`, and the approved ownership directories.
- Root `main.ts` is the stable `dist/main/main.js` entrypoint and contains no raw feature IPC channels, feature validators, or feature implementation logic.
- Every feature IPC family has one explicit registrar and direct registrar tests.
- Every preload/main channel has exactly one intended registration with the correct registration kind.
- Renderer code imports no implementation from `src/main`.
- Feature clients no longer depend on a jobs-owned config type for generic authentication.
- IPC adapters do not import other IPC adapters for shared validation.
- The three document trust domains remain distinct.
- Application-, window-, and profile-scoped lifetimes are explicit and verified.
- Clean builds cannot retain deleted or moved main-process modules.
- Clean Electron builds remove stale outputs from `dist/main`, `dist/preload`, and `dist/shared` before compilation.
- All helper, preload, renderer, updater, public-export, fixture-manifest, and active-doc paths point to the new locations.
- IPC names, payloads, validation/error semantics, security controls, API behavior, storage, and product behavior are unchanged.
- Focused, full, clean-clone, native, and packaged verification gates pass with actual outcomes recorded.
- `apps/desktop/src/main/README.md` lets a new human or agent place a change correctly without reconstructing the architecture from imports.
- The implementation receives separate human approval and review before merge.

## 15. Required reading before implementation

Read in this order and trust newer source-of-truth material over this baseline if the tree has changed:

1. `AGENTS.md`
2. `docs/public/product-contract.md`
3. `docs/public/architecture.md`
4. `docs/public/data-privacy.md`
5. `docs/public/capability-parity.md`
6. `docs/public/release-process.md`
7. this plan
8. the current `apps/desktop/src/main`, preload bridge, renderer call sites, build scripts, and relevant tests

Re-run the topology and hard-coded path searches before implementation. `main.ts` is an active integration point; this plan’s line numbers and inventory are evidence from `0ecea42`, not permission to ignore newer changes.
