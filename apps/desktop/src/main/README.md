# Electron main-process ownership

`main.ts` is the stable Electron entrypoint. It delegates application composition to `app/bootstrap.ts`; feature behavior and IPC registration live in the ownership directories below.

```text
main.ts                    Electron lifecycle routing only
app/                       bootstrap, runtime, security, windows, native capabilities, automation
agents/                    conversations, Connected Agents, agent IPC and event streams
browser/                   live browser views, persistence, restore, navigation and browser IPC
career-profile/            Career Profile clients, archive helpers, dialogs and IPC
documents/artifacts/       immutable registered artifacts and artifact IPC
documents/editable/        canonical editable documents, import, export, publication and IPC
documents/docx/            device-local DOCX bindings, recoveries, worker, watcher and IPC
installation-profiles/     JobOS Profile identity, storage, switching and profile IPC
jobs/                      jobs, job events and save-from-browser transaction
workspace/                 durable workbench layout, selection and workspace IPC
../shared/document-rendering/ pure editable-document rendering shared with the renderer
```

## Request and lifecycle flow

Renderer requests cross the trust boundary through the sandboxed preload and then exactly one feature registrar:

```text
renderer -> preload bridge -> feature IPC registrar -> feature client/service -> JobOS API or local owned storage
```

Bootstrap is the only module that composes multiple feature implementations. Dependencies flow from `main.ts` to `app/bootstrap.ts`, then to app infrastructure and feature registrars. Features may use shared contracts, pure shared modules, narrow runtime types, and their own implementation. Features must not import `main.ts` or bootstrap. Renderer code must not import main-process implementation.

Application-scoped state includes runtime/configuration, active profile identity, source API ownership, global IPC registration, artifact/editable clients, and DOCX services/workers. Window-scoped state includes the `BrowserWindow`, `BrowserManager`, renderer safety coordinator, capability connection, event streams, and their cleanup. Profile-scoped state includes renderer/browser partitions, profile-local paths, browser/workspace metadata, conversations, and DOCX bindings/recoveries.

## Adding a feature or IPC channel

Put implementation and tests in the directory that owns the behavior. Expose one explicit `registerFeatureIpc(ipc, assertTrustedRenderer, liveAccessors, narrowNativeDependencies)` seam and test its exact channels, registration kind, validation order, forwarding, trust enforcement, errors, and cleanup. Use live getters for dependencies created after global registration—especially `getBrowserManager`—rather than capturing a current `null` value. Update the preload parity test whenever a channel is intentionally added or removed.

Do not add broad barrel exports, a service locator, a dependency container, a generic IPC framework, or new root-level feature files. The `src/main` root is reserved for this README, `main.ts`, and the approved ownership directories.

## Path-sensitive verification

Electron compilation first cleans only `dist/main`, `dist/preload`, and `dist/shared`, then verifies `dist/main/main.js` and rejects legacy compiled paths. When moving a native-helper caller, preload, renderer asset, fixture, or public exporter, update its direct path consumer and run the focused path tests, `build:electron` twice, the inventory verifier, updater tests, fixture/public checks, and packaged synthetic gates when available. Preserve NodeNext `.js` import extensions and keep sandboxed preloads self-contained.
