# Browser job save closeout — 2026-07-21

## Outcome

JobOS can now save the complete listing from the active rendered Electron browser tab into canonical Job Hunter storage.

The flow is:

1. Read schema.org `JobPosting` data first, then conservative DOM fallbacks.
2. Require company, role, canonical URL, location, description, and application URL.
3. Send one authenticated `POST /v1/jobs` mutation.
4. Reuse Job Hunter's existing matcher and upsert path.
5. Return the final canonical job and whether it was newly created.
6. Refresh and select that canonical job in the navigator.
7. Re-check the active page and associate the tab only after persistence succeeds.

## Canonical storage and deployment boundary

- Job Hunter's `jobs.db` remains canonical. JobOS does not own a second jobs table.
- The matching facade release must land before or with the JobOS release because the API calls `JobHunterFacade.add_job(...)`.
- The facade preserves existing enriched fields and primary source identity when a browser capture matches an existing job, while recording `jobos_browser` as the observation source.
- Browser and navigation failures after persistence are reported as linking failures, not as failed saves.

## Verification

- JobOS `pnpm check`: lint, type checks, generated contracts, 145 desktop tests, 323 API tests with 1 skipped, and production renderer build all passed.
- Job Hunter facade: 137 tests passed.
- Medium-reasoning Codex review findings were addressed for idempotency, source preservation, extraction races, URL limits, and post-save linking.
- A production-bundle Electron smoke against an isolated local API/fixture confirmed one canonical job, duplicate feedback (`Already in JobOS`), immediate selection, and tab association to the returned canonical ID.
- Final visual QA confirmed the save control and feedback are coherent with the existing dark JobOS design.

## Visual evidence

The final renderer capture is shared from Devonte's cache as `jobos-browser-save-chrome.png`. The white center region in that CDP capture is expected because Electron exposes the embedded `WebContentsView` as a separate target; the active page itself was loaded and extracted in the end-to-end smoke.
