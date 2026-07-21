# Active Browser Job Save Session Memory — 2026-07-21

## Session summary

Implemented and shipped the cross-repository doorway for saving the complete job listing from JobOS's active rendered Electron browser tab into canonical Job Hunter storage.

The production path is:

```text
active rendered WebContentsView
→ fixed read-only extraction script
→ required-field and URL validation
→ authenticated POST /v1/jobs
→ JobHunterFacade.add_job(...)
→ existing matcher and JobStorage.upsert_job(...)
→ canonical job response
→ navigator refresh and selection
→ page re-check
→ tab association to canonical job ID
```

## What shipped

- Extraction prefers schema.org `JobPosting` JSON-LD, including arrays, `@graph`, and expanded `https://schema.org/JobPosting` types.
- Conservative DOM/meta fallbacks support rendered pages without structured data.
- Required fields are company, role, canonical URL, location, description, and application URL.
- Job Hunter's `jobs.db` remains the only canonical jobs store.
- Existing matcher/upsert behavior remains authoritative for duplicates.
- Existing enriched records retain upstream salary, requirements, department, status context, and primary source identity; the browser capture is recorded as a `jobos_browser` observation.
- API mutation replay is idempotent.
- The left navigator refreshes using its active sort/search/status settings, selects the canonical job, and then links the browser tab.
- Navigation/close failures after persistence are reported as "saved but not linked," not as failed saves.

## Verification evidence

### Automated

- JobOS `pnpm check` passed:
  - lint passed;
  - generated OpenAPI/TypeScript contracts passed;
  - TypeScript checks passed;
  - 145 desktop tests passed;
  - 323 API tests passed and 1 skipped;
  - production renderer build passed.
- Job Hunter facade: 137 tests passed.
- `git diff --check` passed in both repositories before commit.
- Several medium-reasoning Codex review rounds found and drove fixes for idempotency, provenance, enrichment preservation, navigation races, expanded JSON-LD types, URL limits, filter/sort refreshes, and post-save linking semantics.

### Real Electron smoke

A production-bundle Electron app was launched against isolated local JobOS state, canonical Job Hunter SQLite storage, and a deterministic rendered job-listing fixture.

The app's actual preload/IPC/API/facade/storage path was exercised. The save button was triggered through Chrome DevTools Protocol (CDP) against the live Electron renderer. The resulting runtime state proved:

- one canonical job existed after save;
- a second save returned `Already in JobOS` rather than creating a duplicate;
- the canonical job was immediately selected;
- the active tab's `associatedJobId` matched the returned canonical job ID;
- the rendered active tab URL/title remained the expected fixture listing.

Final renderer screenshot:

`/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-browser-save-chrome.png`

The screenshot's blank center rectangle is expected because Electron exposes the embedded `WebContentsView` as a separate CDP target instead of compositing it into the renderer capture.

## Important truth about manual testing

The macOS `computer_use` driver was attempted but did not expose a usable Electron application/window during this run. Therefore this was **not** a full operating-system mouse-and-keyboard test through Computer Use.

The live smoke was stronger than a unit test because it used the actual production Electron bundle and real IPC/API/SQLite flow, but its button click was programmatic through CDP. It also used a deterministic local job page rather than Cobi's installed app against a third-party authenticated job board.

Cobi's two-minute in-app acceptance test is still the final proof for the exact installed environment and a real job board session:

1. Open a complete job listing in JobOS's browser.
2. Click `Save this job to JobOS`.
3. Confirm the job appears selected in the left navigator and the control reads `Saved`.
4. Click save again and confirm `Already in JobOS` with no duplicate row.

## Commits and remote verification

- Job Hunter facade `main`: `d1783ed86f8bd552ce3abce23d8718332be0f28e`
- JobOS `main`: `60eb4b17463ffe6395af2570f2c9fdcbe7675307`
- Both remote `origin/main` refs were fetched and verified equal to the local commits after push.
- No production deployment or runtime cutover was performed.

## Key files

JobOS:

- `services/api/jobos_api/jobs.py`
- `services/api/jobos_api/app.py`
- `services/api/tests/test_jobs_contract.py`
- `apps/desktop/src/main/browser.ts`
- `apps/desktop/src/main/jobs.ts`
- `apps/desktop/src/main/main.ts`
- `apps/desktop/src/preload/preload.cts`
- `apps/desktop/src/shared/contracts.ts`
- `apps/desktop/src/renderer/components/CenterWorkspace.tsx`
- `apps/desktop/src/renderer/hooks/useBrowser.ts`
- `apps/desktop/src/renderer/hooks/useJobs.ts`
- `apps/desktop/src/renderer/styles.css`

Job Hunter facade:

- `src/job_hunter/facade.py`
- `src/job_hunter/storage.py`
- `tests/test_facade.py`
