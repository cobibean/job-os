# Browse-to-workbench height regression fix — 2026-08-13

## Session summary

Fixed the JobOS layout regression where opening a job from Browse caused Research, Review, and Agent Focus to occupy only the top portion of the window.

## What we learned

- The Browse feature wrapped the existing workbench in `.workbench-layer`.
- `.workbench-layer` correctly filled the workspace row, but its preserved `.workbench-wrap` child no longer inherited the app grid row's stretch behavior.
- Without flex growth, `.workbench-wrap` shrink-wrapped its content. Browse remained correct because `.browse-workspace` is a separate `height: 100%` sibling.

## Files changed

- `apps/desktop/src/renderer/styles.css`
  - Added `flex: 1` to `.workbench-wrap`.
- `apps/desktop/src/renderer/workspaceSizing.test.ts`
  - Added a regression contract for the shared workspace height chain.

## Commands and verification

- `pnpm exec vitest run src/renderer/workspaceSizing.test.ts src/renderer/App.test.tsx` — 43 passed.
- `pnpm lint` — passed.
- `pnpm typecheck` — passed.
- `pnpm test` — 50 files / 360 tests passed.
- `pnpm build` — passed.
- `pnpm --filter @jobos/desktop package:mac` — passed; app signature verified.
- Installed the packaged app at `~/Applications/JobOS.app` and verified that exact executable was running.
- Production-binary CDP geometry and screenshots verified Browse, Research, Review, and Agent Focus each used the full 886px workspace row in a 1440×960 window.
- `pnpm --filter @jobos/desktop package:macbook-update` — updater build and canonical smoke-install passed.
- Taildrop reported the outer MacBook updater as sent to `jacobis-macbook-pro`.

## Release identity

- Product fix commit: `299c3d218f55f71d6dc2c7ef0f1a34fe034d4663`
- Outer updater SHA-256: `3ad4ac996feda30223b56a614606cece02af0b89a692dc0b5801c7d403723e98`
- Outer updater size: `154270115` bytes.

## Constraints

Unrelated pre-existing changes in `services/api/jobos_api/jobs.py` and `services/api/tests/test_jobs_contract.py` were left untouched and excluded from the fix commit.
