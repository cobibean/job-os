# Full browser job description save closeout memory

Date: 2026-07-24

## Goal

Make **Save job** persist the complete job description shown on the listing by default, including long descriptions and already-existing canonical jobs, without changing tab association, canonical matching, navigator refresh, or the Saved state.

## Implementation

- Replaced the renderer's former 300-character summary instruction with a complete-description extraction contract.
- The app-owned agent now:
  - begins detail-page coverage at `textStart = 0`;
  - uses overlapping semantic snapshot windows until the relevant listing is completely covered;
  - includes responsibilities, qualifications, compensation, benefits, logistics, EEO, and final application notices;
  - must not summarize, shorten, paraphrase, or rewrite the listing;
  - is capped at 30 detail-page snapshots;
  - fails closed without mutating JobOS if the listing cannot be completely covered within that cap;
  - still clicks at most one matching job-detail link and never clicks Apply or submits a form.
- Kept `job_create_from_browser` as the single canonical mutation. The existing backend already accepts descriptions up to 100,000 characters and updates the description when a canonical job already exists.

## Coverage added

- Renderer prompt contract verifies complete-description extraction, bounded pagination, fail-closed behavior, single mutation, and unchanged association/refresh/Saved sequencing.
- Browser runtime test proves:
  - a list-page job-detail link is clicked instead of Apply;
  - same-tab navigation resets semantic pagination to zero;
  - overlapping snapshots cover 10,000 characters of page chrome plus an exact 100,000-character description;
  - the full beginning, middle, and ending are present across collected windows.
- API contract test proves a long multi-section description is preserved exactly for a new job, preserved on idempotent replay, and replaced exactly when the same canonical job is saved again.

## Verification

Passed:

- `pnpm --filter @jobos/desktop test -- App.test.tsx`
- `pnpm --filter @jobos/desktop test -- browser.test.ts capabilityClient.test.ts`
- `uv run pytest -q services/api/tests/test_jobs_contract.py services/mcp/tests/test_jobs_tools.py`
- `uv run pytest -q tests/test_facade.py -k 'add_job'` in the JobHunter repository
- `pnpm --filter @jobos/desktop typecheck`
- `pnpm check` after implementation
- `pnpm check` again after review fixes
- Independent Codex medium-reasoning review: passed after three review/fix loops. Final result: `passed: true`, `logic_error: false`.

## Review fixes

1. Increased detail-page coverage from 25 to 30 snapshots and made list-page inspection explicitly separate, allowing 100,000 characters of description plus realistic page chrome.
2. Reworked the browser test so it clicks the job-detail link, never the Apply target, and causally simulates same-tab navigation before validating cursor reset.
3. Added a fail-closed instruction: reaching the snapshot cap with unread relevant text forbids all JobOS mutations.

## Commit and remote

- Implementation commit: `b713095` (`feat: save complete browser job descriptions`)
- Pushed to `origin/main`; local `HEAD` and `origin/main` matched after push.

## Installed artifact proof

- Packaged with `pnpm --filter @jobos/desktop package:mac`.
- Zip test passed for `release/desktop/JobOS-0.1.0-arm64.zip`.
- Zip SHA-256: `48a0f32f1afc61dc55e4481b3dc8c797886a0da9abb45b68dff053476f136138`.
- Zip size: `134159839` bytes.
- Packaged executable architecture: arm64.
- Packaged and installed app signatures passed deep strict verification.
- Installed to `/Users/jacobilangemm/Applications/JobOS.app` with rollback at `/Users/jacobilangemm/Applications/JobOS.app.rollback-20260724-181309`.
- Running executable was verified as `/Users/jacobilangemm/Applications/JobOS.app/Contents/MacOS/JobOS`.
- Packaged and installed `app.asar` matched byte-for-byte with SHA-256 `c9d26b03d18c3fc966f60401c305dc504ab3f6102944670cc91f78dd147b58a5`.
- Extracted installed bundle contains the new complete-description and fail-closed prompt strings.
- Visual launch screenshot: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-full-description-before.png`.

## Installed-control limitation

The exact installed **Save job** button was not clicked during this closeout. Background macOS input did not land, and foreground control required explicit approval. Approval was requested and timed out after ten minutes, so no focus-stealing action or real JobOS data mutation was performed. The installed bundle, launch path, prompt payload, tests, and byte identity are verified; the remaining gap is one foreground click-through on an unsaved or safely duplicated listing.

## Workspace boundary

The pre-existing modification in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` was not staged or changed by this work.
