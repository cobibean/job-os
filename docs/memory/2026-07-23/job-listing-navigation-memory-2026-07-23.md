# Job Listing Navigation Memory - 2026-07-23

## Session summary

Implemented the saved-job navigation plan in `.hermes/plans/2026-07-23_213157-job-listing-navigation.md`.

A direct click in Job Navigation now:

1. selects the job through the shared jobs bridge;
2. switches the center workspace to Browser;
3. waits for browser restoration;
4. activates an existing tab associated with that job;
5. otherwise activates an unassociated tab with the same safely normalized canonical URL;
6. otherwise creates a new browser tab associated with the job.

Startup restoration, MCP selection, refresh, and browser-save reconciliation remain selection-only and do not open listing tabs.

## Decisions made

- Job association wins over URL-only matching.
- URL matching reuses `normalizeBrowserUrlForPersistence`; no second URL-normalization policy was introduced.
- App emits monotonic one-shot request IDs, and `CenterWorkspace` consumes each request at most once after browser restoration.
- Direct navigator selections are serialized. If clicks overlap, backend mutations complete in click order and only the newest click opens a listing. This prevents an earlier slow selection from becoming the final backend selection.
- A workspace persistence failure does not suppress visible listing navigation because the surface state already changed locally.
- Unrelated browser tabs are never closed, navigated, or reassociated by this flow.

## Review findings resolved

Two bounded independent Codex reviews found and drove fixes for:

- navigation being suppressed when workspace persistence failed;
- overlapping selection promises allowing stale navigation;
- the same user selection's SSE event incorrectly invalidating its own successful result;
- local-only race guards not guaranteeing backend latest-wins behavior.

The final implementation serializes direct navigator mutations and includes targeted regression tests for these cases. The configured two-review loop limit was reached; the final fixes were validated by focused tests and the full repository gate.

## Files changed

- `apps/desktop/src/renderer/App.tsx`
- `apps/desktop/src/renderer/App.test.tsx`
- `apps/desktop/src/renderer/components/CenterWorkspace.tsx`
- `apps/desktop/src/renderer/hooks/useBrowser.ts`
- `apps/desktop/src/renderer/hooks/useBrowser.test.ts`
- `apps/desktop/src/renderer/hooks/useJobs.ts`
- `apps/desktop/src/renderer/hooks/useJobs.test.tsx`
- `apps/desktop/src/renderer/hooks/useWorkspace.ts`

The pre-existing local edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` was preserved and left unstaged.

## Commands and verification

- Focused renderer tests: `44 passed` before the final race refinement.
- Final targeted race/failure tests: `3 passed`.
- Final `pnpm check`:
  - desktop: `173 passed`;
  - Python: `337 passed, 1 skipped`;
  - lint, TypeScript checks, production build, and packaged-renderer verification passed.
- Product commit: `ae8b2afad4aad1eb49766304abee3bb650a8c951`.
- `origin/main` was fetched and verified byte-for-byte at the same product commit.

## Exact installed-app acceptance

- Built with `pnpm --filter @jobos/desktop package:mac`.
- Packaged bundle passed deep strict code-sign verification.
- Installed exact bundle at `/Users/jacobilangemm/Applications/JobOS.app`.
- Built and installed `app.asar` SHA-256 both matched:
  `1155bdce1dd1ba006d5fdb4b7393d107b6d929d68557c2a74c62e24542ef9062`.
- Archive SHA-256:
  `37d564f2c72c62c412641a78dc3d485ec31d4f2f044943a72d692067700a6653`.
- Visual proof on the exact installed app:
  - clicking Black Duck selected the job;
  - center workspace switched from the resume document to Browser;
  - the canonical LinkedIn listing URL became active;
  - existing unrelated tabs remained visible;
  - clicking the same job again kept the browser at 12 tabs and reactivated the same listing instead of creating a duplicate.
- The temporary CDP debugging launch used for deterministic acceptance was shut down. JobOS was relaunched normally from the installed path, and the debugging endpoint was confirmed closed.

## Boundary / next action

No MacBook updater artifact was created. The Mac mini exact-build acceptance is complete; pause for Cobi before running `package:macbook-update` or performing any MacBook update action.
