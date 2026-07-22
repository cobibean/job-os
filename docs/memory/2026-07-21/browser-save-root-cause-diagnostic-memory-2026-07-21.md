# Browser Save Root-Cause Diagnostic — 2026-07-21

## Reproduction

Cobi reproduced the same browser-save failure on the Mac Mini. A diagnostic production build was launched against the persisted authenticated Wellfound browser partition and the same listing was reopened:

`https://wellfound.com/jobs/starred?job_listing_slug=4467759-sales-ai-agent-builder`

Clicking the real JobOS Save button reproduced the failure in `3 ms`.

## Instrumented evidence

At extraction time:

- Browser tab state URL, `webContents` URL, and in-page URL all matched the expected Wellfound URL.
- Browser tab state and `webContents` both reported not loading.
- `document.readyState` was `complete`.
- Rendered body text length was 7,439 characters.
- No `JobPosting` structured data was present.
- Generic location selectors returned zero characters.
- The nearby-location fallback returned zero characters.
- The extractor returned nonblank company, title, and description fields, so validation named only location as missing.

Critical values:

- Extracted `h1`: `Search for jobs`
- Extracted title length: 15 (`Search for jobs`)
- Extracted company length: 9 (generic page-level value)
- Extracted description length: 1,935 (unanchored page content)
- Actual job-detail heading: `Sales AI Agent Builder`
- Actual job-detail company: `Recurring Decimal`
- Actual rendered location: `Remote (United States)`

## Root cause

The failure is not page readiness, navigation, API connectivity, persistence, or an absent location.

The extractor uses a global first-match selector list ending in `h1`. On Wellfound's saved-jobs page, the first `h1` is the page-level heading `Search for jobs`; the actual selected listing appears later in a detail pane. Because several wrong page-level fields are still nonblank, required-field validation reports only `missing location`, which is technically derived from the returned object but materially misleading.

The two prior location-specific changes did not address this extraction-boundary defect.

## Correct architectural direction

The next implementation should identify the selected job-detail container first and derive company, title, location, description, and application URL only inside that container. It should not continue adding global location selectors or treat unrelated nonblank page content as a valid listing.

## Diagnostic artifacts

- Safe runtime diagnostic is gated by `JOBOS_EXTRACTION_DIAGNOSTICS=1`.
- Bounded DOM inspection artifact: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/jobos-wellfound-dom-diagnostic.json`.
- Diagnostic app was stopped after reproduction and JobOS was reopened normally without the diagnostics environment flag or remote-debugging port.
