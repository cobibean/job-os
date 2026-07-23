# Settings panel native-browser layering fix memory - 2026-07-22

## Session summary

Fixed the Settings panel being hidden beneath the embedded browser in the Research/browser view. The implementation now detaches Electron's native browser surface before rendering Settings and restores the browser after Settings closes.

## Root cause

- The embedded browser is an Electron `WebContentsView`, attached by the main process as a native child view.
- The Settings panel is renderer HTML. CSS `z-index` only orders renderer elements and cannot place renderer HTML above an attached native `WebContentsView`.
- The New Session modal had already been corrected for this boundary, but Settings did not use the same lifecycle. `App.tsx` opened Settings immediately and kept `browserVisible` true.
- Therefore, the native browser remained attached over the Settings overlay. The Settings panel's existing `z-index` was not the controlling layer.

## Implementation

- `apps/desktop/src/renderer/App.tsx`
  - Added an async Settings-open path that requests hidden browser bounds and waits for that IPC call to resolve before rendering the panel.
  - Keeps Settings closed if native detachment fails rather than knowingly rendering it underneath the browser.
  - Includes `settingsOpen` in the browser visibility condition so the browser stays detached for the full Settings lifetime.
  - Closing Settings returns browser visibility to the existing `useBrowser` bounds lifecycle, restoring the same active browser surface.
- `apps/desktop/src/renderer/App.test.tsx`
  - Added a regression test proving Settings is absent while detachment is pending, appears only after detachment resolves, and restores the browser after Close.

## Verification

TDD regression cycle:

1. Added `opening Settings detaches an active native browser surface before showing the panel`.
2. Confirmed RED: the test failed because clicking Settings made zero `visible: false` bounds calls.
3. Implemented the App-shell coordination.
4. Confirmed GREEN: focused test passed (`1 passed`, `26 skipped`).

Full repository gate:

- `pnpm check` passed.
- Desktop: 25 test files, **155 tests passed**.
- API: **336 passed, 1 skipped**.
- Lint, TypeScript typecheck, generated contracts, renderer build, Electron build, and packaged-renderer verification passed.
- `git diff --check` passed.

## Verification boundary

- Source-level regression and the full repository gate are complete.
- No updater was built, no installed app was replaced, and no production/external deployment occurred in this slice.
- Exact installed-app visual acceptance remains optional follow-up if this change is packaged later.

## Worktree and session constraints

- The pre-existing modification to `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` was not edited as part of this fix.
- Issue 3 (job-status persistence) is next.
- Issue 2 (Settings panel additions) is intentionally reserved for the end of the session.
