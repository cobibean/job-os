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
