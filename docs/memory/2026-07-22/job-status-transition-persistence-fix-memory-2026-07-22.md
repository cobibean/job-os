# Job-status transition persistence fix memory - 2026-07-22

## Session summary

Fixed the left-side job navigator presenting invalid status changes that the Job Hunter domain rejects. The navigator now offers only legal next statuses, and stale invalid requests preserve a safe transition explanation through Electron's IPC wrapper.

## Root cause

- `JobNavigator.tsx` offered all 12 statuses for every job.
- Job Hunter enforces a canonical lead-state transition graph. Invalid jumps return HTTP `409 Conflict` and intentionally leave the durable record unchanged.
- The controlled dropdown therefore snapped back to the persisted status, which looked like a failed save.
- `useJobs` replaced the backend's specific transition explanation with `Status change failed`.
- Electron wraps rejected IPC errors, so preserving a safe explanation requires extracting an allowlisted transition phrase from the wrapper rather than matching the whole message.

## Implementation

- `apps/desktop/src/renderer/components/JobNavigator.tsx`
  - Replaced the unrestricted status list with the Job Hunter transition graph.
  - Each row shows its current status plus only legal next statuses.
- `apps/desktop/src/renderer/hooks/useJobs.ts`
  - Extracts `Invalid lead state transition: <source> -> <target>` from Electron-wrapped errors only when both states belong to the 12-value `JobStatus` vocabulary.
  - Unknown or malformed transition-shaped errors remain the generic `Status change failed` message.
- Regression coverage:
  - `JobNavigator.test.tsx` verifies legal options for representative states.
  - `useJobs.test.tsx` verifies the real IPC-wrapped message shape and fail-closed handling for unknown states.

## Verification

TDD regressions were observed failing before each implementation and passing afterward.

Final repository gate:

- `pnpm check` passed.
- Desktop: 26 test files, **158 tests passed**.
- API: **336 passed, 1 skipped**.
- Lint, TypeScript typecheck, generated contracts, renderer build, Electron build, and packaged-renderer verification passed.
- Focused API contract tests confirmed allowed transitions persist and invalid transitions produce no event or partial write.
- `git diff --check` passed.

## Review hardening

Independent Codex review identified and drove fixes for:

1. Electron IPC-wrapped rejection messages, which defeated an initial exact-string sanitizer.
2. A native-browser resize race while Settings detachment was pending; `settingsPreparing` now also suppresses browser visibility.
3. Transition-shaped messages containing unknown states; only known `JobStatus` values are surfaced.

## Verification boundary

- No real user job was mutated for acceptance. Persistence behavior was verified against disposable contract fixtures and the renderer bridge tests.
- No updater was built and no installed app was replaced.
- The renderer transition table duplicates Job Hunter's canonical graph; future domain transition changes must update this table and its tests together.

## Worktree constraint

The pre-existing modification to `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` remained untouched and must not be staged with this fix.
