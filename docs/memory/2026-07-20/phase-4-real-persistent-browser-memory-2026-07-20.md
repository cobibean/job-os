# JobOS V1 Phase 4 Real Persistent Browser Memory - 2026-07-20

## Session summary

- Implemented Linear `CLO-50` only: a genuine multi-tab Electron browser in the center workspace with live `WebContentsView` instances owned by the main process.
- Added create, select, close, reorder, restore, address/search, back/forward, reload/stop, titles, favicons, loading feedback, explicit downloads, job association, and recoverable network/crash errors.
- Kept browser tabs independent from job selection and preserved accepted Phase 1-3 layout, selection, API audit/idempotency, and preset behavior.
- Did not add the Phase 7 agent command channel, chat, documents, job-hunter changes, Hermes work, or Mac Mini deployment/runtime changes.

## Decisions made

- Use the persistent Electron partition `persist:jobos-browser-v1`. Cookies, cache, login state, and navigation stacks remain local to the desktop host.
- Keep every live tab as one main-process `WebContentsView`. Selecting tabs attaches/detaches existing views; bounds, panel reorder, collapse/reopen, preset changes, and reset never recreate them.
- React owns browser chrome and reports the viewport rectangle through a narrow typed preload bridge. Remote pages receive no preload, Node integration, raw IPC, webview tag, or application capability.
- Persist only sanitized tab metadata in the existing atomic Workspace snapshot: tab ID, ordinary HTTP(S) URL, title, HTTP(S) favicon URL, optional job association, order, and active tab. URL user-info, fragments, and credential-shaped query parameters are stripped before persistence and rejected by the API as defense in depth.
- Job association is optional metadata. Changing selected job has no browser side effect.
- Ordinary HTTP(S) popups become JobOS tabs. External protocols are blocked with recovery copy. Site permissions are denied by an explicit session policy and reported in browser chrome. Downloads always use a native save-location prompt.
- A malformed persisted browser payload repairs only browser metadata; valid layouts and authoritative job selection survive.

## Files created or changed

- Main browser ownership and tests: `apps/desktop/src/main/browser.ts`, `browser.test.ts`, and `main.ts`.
- Narrow renderer boundary: `apps/desktop/src/preload/preload.cts`, `apps/desktop/src/shared/contracts.ts`, and `browserPersistence.ts`.
- Browser UI and layout synchronization: `CenterWorkspace.tsx`, `useBrowser.ts`, `App.tsx`, `useWorkspace.ts`, `workspaceLayout.ts`, styles, CSP, and renderer tests.
- Workspace persistence/API: `workspace.py`, `state_store.py`, `app.py`, response contract, API tests, OpenAPI, and generated TypeScript contracts.

## Commands and verification

- Pinned runtime: Node.js 26.5.0 from `/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin`, pnpm 10.33.1, Python 3.11.15, Electron 43.1.1.
- Final source-tree gate before commit: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm check` passed lint, contract generation, TypeScript checks, 34 desktop tests, 30 Python tests, production Electron/Vite build, and packaged-renderer verification.
- Contract regeneration comparison: generated `packages/contracts/openapi.json` and `packages/contracts/src/generated/` exactly matched the frozen candidate after its full check.
- Frozen candidate clean room `/tmp/jobos-phase4-candidate-clean.UC5LcX` passed `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the complete pinned `pnpm check` with the same 34 desktop and 30 Python tests.
- Native Electron browser proof used a disposable `/tmp/jobos-phase4-proof-profile` and loaded Google, Gmail's public landing flow, and the live OpenAI `Product Manager, Cyber Defense and Blue Team` listing with no page errors. `WebContents` IDs remained `2`, `3`, and `4` before and after tab reorder, selection, and two bounds changes. The manager reported the requested final order and an active Google tab.
- Native persistent-session proof set a disposable HTTP-only probe cookie in the Phase 4 partition, quit Electron, relaunched Electron, confirmed the cookie restored, removed it, and moved the disposable profile to Trash.
- Renderer Playwright proof at `output/playwright/jobos-phase4-renderer-browser-chrome.png` verified Research layout browser chrome, keyboard-accessible controls, and the renderer-only fallback. Its only console error was the proof server's missing `favicon.ico`.
- Automated coverage includes tab lifecycle/restoration, stable live-view identity across bounds/reorder, ordinary URL/search normalization, remote-content isolation, permission/download handler policy, credential stripping/rejection, workspace repair, job-switch independence, and all accepted Phase 1-3 suites.

## Gotchas and constraints

- The Mac was locked during native proof. Chromium page capture returned `UnknownVizError`; native pages still loaded and exposed URL/title/error state through Electron. A visible human MacBook acceptance pass remains mandatory and is not claimed here.
- Gmail redirected to its public Workspace landing page because the disposable profile was unauthenticated. No credentials were entered or inspected. Authenticated continuity is supported by the persistent partition but still needs the human site-permitted acceptance check.
- No real Mac Mini desktop-host check occurred. The Mac Mini job-hunter/Hermes runtime and data were not contacted or changed.
- GitHub Actions was not used as proof. Do not claim CI green; the prior documented Actions run could not start because of account/budget infrastructure.
- Preserve the unrelated `docs/planning/.DS_Store` modification. It is not part of Phase 4.

## Handoff state

- Implementation and implementor verification are complete; PM owns the visible MacBook acceptance gate, issue acceptance, and closure.
- Leave `CLO-50` in `Building`. The implementation owner should comment that the candidate is complete and awaiting PM review after push.

## PM blocker correction - 2026-07-20

### Corrected behavior

- Startup recovery now treats synthesized Google initialization as local browser setup, not user intent. Only an explicit browser action can enter Workspace persistence. If the initial Workspace GET failed and conflict recovery later supplies authoritative tabs, an untouched synthesized default is replaced without replaying it over those tabs.
- Corrupt browser metadata is repaired entry-by-entry. Valid tabs remain in stable order, duplicates/malformed entries and entries beyond 50 are removed, and an invalid active tab resolves to the first recoverable tab. Layouts, preset, and selected job remain independent.
- The renderer now visibly and accessibly reports browser metadata repair instead of silently replacing it.
- One conservative URL persistence policy is mirrored in `apps/desktop/src/shared/browserPersistence.ts` and `services/api/jobos_api/browser_policy.py`: userinfo, fragments, OAuth/SAML assertions, capability/session credentials, and signed-download parameters are removed before desktop emission and rejected by the API. Ordinary query parameters remain intact.
- `BrowserManager` now enforces the Workspace boundary before emission: 50 tabs, 512-character titles, 8192-character URL/favicon bounds, and bounded IDs/associations. Limit or metadata adjustment feedback is shown without breaking later atomic saves.
- Browser tabs now use a valid `tablist`/`tab`/`tabpanel` pattern with roving focus and Arrow/Home/End/Delete keyboard behavior. Select, reorder, close, add, back, forward, and reload/stop controls have accessible names and focus/hover tooltips.
- Blocked external protocols retain only a sanitized, displayable URL and expose explicit `Copy link`; JobOS never launches the protocol automatically. Both `will-navigate` and new-window paths use this recovery.

### Correction verification

- Focused desktop: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm --filter @jobos/desktop test -- --run` passed 37 tests across 9 files.
- Focused API: `uv run --project services/api pytest services/api/tests/test_state_store.py services/api/tests/test_jobs_contract.py -q` passed 28 tests.
- Full pinned gate: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm check` passed lint, generated contracts/type checks, 37 desktop tests, 33 Python tests, production Electron/Vite build, and packaged-renderer verification.
- Contract drift: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm contracts:check` passed.
- Secret scan: `gitleaks detect --source . --no-banner --redact --verbose` with gitleaks 8.30.0 scanned 11 commits / about 697 KB and found no leaks.
- Renderer proof: Playwright CLI loaded the production renderer with a proof bridge, exposed a two-tab browser plus repaired-metadata notice, and ArrowRight moved focus/selection from Gmail to Product Manager. The accessibility snapshot showed only true tabs inside the tablist and adjacent tab actions; console errors/warnings were zero. Screenshot: `output/playwright/jobos-phase4-corrections-accessibility.png`.
- Detached final-commit clean room: `/tmp/jobos-phase4-correction-final-clean` was created from `git archive HEAD`; `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the full pinned `pnpm check` passed with 37 desktop and 33 Python tests plus production/package verification.

### Remaining human gate and constraints

- A native programmatic rerun was attempted with a disposable `/tmp/jobos-phase4-correction-proof-profile`, but Electron did not complete while the Mac was locked and was stopped. The script and profile were moved to Trash; no proof is claimed from that attempt.
- Native visible MacBook acceptance and authenticated Gmail continuity remain explicitly open. The earlier native Phase 4 baseline remains recorded above, but it is not substituted for this human gate.
- No Mac Mini, job-hunter/Hermes, Phase 5, or unrelated planning work was touched. Preserve the unrelated `docs/planning/.DS_Store` modification.
