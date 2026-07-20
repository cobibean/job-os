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

## Final PM re-review correction - 2026-07-20

### Corrected behavior

- Python URL validation is exception-safe across parsing, hostname, port, user-info, and IDNA access. Malformed bracketed IPv6 and invalid port forms are rejected as individual browser metadata entries instead of aborting the Workspace GET. Valid bracketed IPv6 remains accepted.
- API repair and Electron restoration now stream raw tabs in stable order, validate and deduplicate first, and only then retain the first 50 recoverable unique tabs. Invalid, malformed, or duplicate entries before the limit no longer consume capacity or displace the intended active tab.
- Browser tab action tooltips render through a fixed document-level portal. Horizontal tab-strip scrolling remains intact while select, reorder, close, and add-tab tooltips are visibly available on both hover and keyboard focus outside the clipped strip.

### Direct regressions and final source-tree verification

- Direct Python malformed-URL reproduction returned `False` without raising for `https://[::1`, `http://[`, `https://example.com:bad`, and accepted `https://[::1]:443/jobs`.
- API regressions cover malformed tab and favicon URLs surrounded by valid tabs, active-tab continuity, and a real `/v1/workspace` GET that returns `200` with repaired browser metadata. Cap-order coverage places invalid, duplicate, malformed-IPv6, and malformed-favicon entries before 52 valid unique tabs and restores `tab-0` through `tab-49` with `tab-49` active.
- Electron regressions apply the same invalid/duplicate/malformed-before-cap ordering and restore exactly 50 unique tabs with the coherent requested active tab.
- Focused API: `uv run --project services/api pytest services/api/tests/test_state_store.py services/api/tests/test_jobs_contract.py -q` passed.
- Focused desktop: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm --filter @jobos/desktop test -- --run` passed 38 tests across 9 files.
- Full pinned source-tree gate: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm check` passed lint, generated contracts and type checks, 38 desktop tests, 39 Python tests, the production Electron/Vite build, and packaged-renderer verification.
- Contract drift: `PATH=/Users/cobibean/Library/pnpm/nodejs/26.5.0/bin:$PATH pnpm contracts:check` passed.
- Secret scan: gitleaks 8.30.0 scanned the final 13-commit history and found no leaks.
- Current hosted CI evidence before this correction commit: GitHub Actions run `29718496016` for `35ad9fc2284a1a28bdf836962acfbeaaf1a38ee4` failed before executing any steps (`quality` contained zero steps). It is infrastructure-only evidence and is not claimed as green; local and frozen clean-room gates remain the acceptance proof.

### Visible tooltip proof

- Playwright rendered the production renderer with the proof bridge and verified valid `tablist`/`tab` semantics plus the adjacent action group. Select, reorder, close, and add-tab tooltip portals each produced visible 24-pixel-high bounding boxes at renderer y-coordinate 134, below the clipped tab strip, on hover and keyboard focus. Browser console errors and warnings were zero.
- Evidence: `output/playwright/jobos-phase4-final-tooltip-select-hover.png`, `jobos-phase4-final-tooltip-select-focus.png`, `jobos-phase4-final-tooltip-reorder-hover.png`, `jobos-phase4-final-tooltip-reorder-focus.png`, `jobos-phase4-final-tooltip-close-hover.png`, `jobos-phase4-final-tooltip-close-focus.png`, `jobos-phase4-final-tooltip-add-hover.png`, and `jobos-phase4-final-tooltip-add-focus.png`.

### Final handoff constraints

- Detached final-commit clean room: `/tmp/jobos-phase4-final-rereview-clean` was created from `git archive HEAD`; `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, and the full pinned `pnpm check` passed with 38 desktop tests, 39 Python tests, production build, and packaged-renderer verification.
- Native visible MacBook acceptance and authenticated Gmail continuity remain explicitly open while the Mac is locked. No new native or authenticated acceptance is claimed.
- No Mac Mini, job-hunter/Hermes, Phase 5, or unrelated planning work was touched. The unrelated `docs/planning/.DS_Store` modification remains unstaged and unmodified by this correction.
