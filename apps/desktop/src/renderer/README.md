# Desktop renderer ownership

`main.tsx` is the stable renderer mount. It delegates setup and workbench composition to `app/`; product behavior lives with the owner that changes with it. The renderer root remains the stable Vite entry directory, not a home for feature implementation.

The ownership refactor is complete. `app/App.tsx` is the setup gate, `app/WorkbenchApp.tsx` is the only cross-owner composition root, and the legacy root-level technical buckets have been removed. The architecture test enforces this final root shape:

```text
README.md
main.tsx
index.html
print.html
docx-worker.html
env.d.ts
pagedjs.d.ts
styles.css
app/
agents/
browser/
career-profile/
documents/
installation-profiles/
jobs/
workspace/
```

| Owner | Owns | Does not own |
| --- | --- | --- |
| `app/` | Setup gate, cross-owner composition, global settings shell, connectivity projection, theme, and status | Feature rules or feature persistence |
| `agents/` | Conversation and multi-session state, chat presentation, Connected Agent settings, New Chat selection, and avatars | Job selection, browser implementation, or document behavior |
| `browser/` | Native-browser chrome, navigation state, restore projection, browser errors, and download visibility | Job extraction policy or canonical job mutation |
| `career-profile/` | Work arrangement, complete-profile product, collaboration, evidence UI, cache validation, and Career Profile agent access | JobOS installation profiles or general Connected Agent identity |
| `documents/artifacts/` | Immutable registered-artifact projection, revision and format selection, viewing, approval, and export entry points | Editable document mutation or local DOCX binding lifecycle |
| `documents/editable/` | Canonical editable-document editing, autosave, snapshots, comments, preview, export, publication, and print entry module | Bound local DOCX editing |
| `documents/docx/` | Device-local DOCX editing, worker entry module, reload, recovery, pagination, and autosave | Canonical editable-document publication |
| `documents/previews/` | Process-neutral previews genuinely shared by more than one document owner | Document mutation or bridge orchestration |
| `installation-profiles/` | Profile list, create, rename, switch presentation, and rollback messaging | Career Profile product state |
| `jobs/` | Job list, detail and status state, Browse, navigator, and the browser-listing-to-job transaction | Native browser tab implementation |
| `workspace/` | Layout model, panel geometry, persistence projection, workspace bar, and feature-neutral slot composition | Agent, browser, job, Career Profile, or document rules |

## Dependency rules

- `main.tsx` imports only `app/App`, the stable renderer stylesheet entrypoint, and the retained editor package stylesheet.
- `app/WorkbenchApp.tsx` is the only module that composes multiple top-level product owners.
- Feature owners do not import `main.tsx`, `app/App.tsx`, or `app/WorkbenchApp.tsx`.
- `workspace/` accepts feature surfaces as props or React nodes; it does not import feature implementations.
- Features may import generated/shared contracts and process-neutral code from `apps/desktop/src/shared`.
- Renderer production code never imports Electron main-process implementation. It reaches native capabilities only through the sandboxed preload bridge.
- Bridge access stays in the owner that uses it. Do not add a generic renderer client or service locator around `window.jobos`.
- A cross-owner workflow belongs to the owner of its outcome and receives narrow dependencies from app composition. For example, saving a browser listing belongs to `jobs/`.
- Instantiate each bridge-backed controller once at its existing owner lifetime. Pass narrow state and commands instead of creating a second controller in a leaf module.
- Keep effect and subscription dependencies stable so unrelated renders do not restart listeners, polling, restoration, observers, or initial loads.
- Import explicit modules through relative paths. Do not add path aliases or broad `index.ts` barrels.

## Stable entrypoints

| Path | Contract |
| --- | --- |
| `index.html` | Loads `/main.tsx` under the existing content security policy. |
| `main.tsx` | Mounts `<App />` and imports `styles.css` before the retained editor package stylesheet. |
| `print.html` | Remains the print renderer loaded by the main-process PDF exporter. |
| `docx-worker.html` | Remains the DOCX worker renderer loaded by the main-process worker manager. |
| `styles.css` | Is the single application stylesheet entry imported by `main.tsx` and is an ordered owner-style import manifest. |

The corresponding packaged files remain `dist/renderer/index.html`, `dist/renderer/print.html`, and `dist/renderer/docx-worker.html`. The HTML files stay at the renderer root even when their script modules move into a document owner.

## Bridge flow

```text
owner component/controller
  -> window.jobos
  -> sandboxed preload bridge
  -> owning main-process IPC registrar
  -> owning client/service
  -> JobOS API or app-owned local storage
```

The renderer owns presentation and renderer-lifetime state. The preload owns the narrow trust-boundary API, and each main-process feature registrar owns native or persistent behavior. Do not bypass a layer or import across the process boundary.

## Adding a feature

1. Choose the product owner that changes with the behavior. Add a new top-level owner only when no existing owner is truthful; do not create an empty directory to reserve a name.
2. Put the component, controller, pure model, tests, assets, and owner stylesheet together. Keep tests beside the interface they prove.
3. Define a narrow owner interface. If multiple owners must be assembled, inject their surfaces or commands from `app/WorkbenchApp.tsx`; keep `workspace/` feature-neutral.
4. Access `window.jobos` only inside the consuming owner, with one live controller instance and stable subscription dependencies. Any new bridge capability must also follow the preload and main-process IPC ownership and parity rules.
5. Preserve the stable HTML, mount, and stylesheet entrypoints. Update only a direct path consumer when an owned entry module or asset moves.
6. Run focused owner tests, the renderer architecture test, desktop typecheck and lint, then the path-sensitive build and packaged-renderer checks when entry modules or assets change.
