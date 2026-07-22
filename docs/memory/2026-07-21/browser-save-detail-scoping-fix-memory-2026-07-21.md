# Browser Save Detail-Scoping Fix — 2026-07-21

## Session summary

Fixed the live Wellfound browser-save failure after diagnostics proved the extractor was reading page-level/list content instead of the selected job detail pane.

## Root cause and fix

- Wellfound renders a page-level `Search for jobs` H1, unrelated saved-job cards, and the selected listing detail pane in one document.
- The previous extractor used global first-match selectors, so unrelated truthy values could satisfy company/title/description while location remained empty.
- The extractor now identifies the selected detail region semantically from its visible job H1 and `About the job` heading, computes their nearest shared container, and scopes company, title, location, description, and apply-link extraction to that container.
- `JobPosting` JSON-LD remains the first priority. Greenhouse and conservative global fallbacks remain available when there is no semantic detail container.

## Regression proof

`apps/desktop/src/main/browser.test.ts` now contains:

`job extraction scopes every field to the active Wellfound detail pane despite page-level headings and other listings`

The fixture reproduces the live failure shape:

- page-level `Search for jobs` H1;
- unrelated saved-job companies and roles;
- selected company links with an empty avatar link before the text link;
- selected title and location;
- `About the job` description;
- misleading page/site company metadata and later headings.

The test failed before the fix and passed afterward with exact field equality.

## Verification

- `pnpm check` passed:
  - 147 desktop tests;
  - 323 API tests passed, 1 skipped;
  - lint, typecheck, build, packaged-renderer verification all passed.
- `git diff --check` passed.
- Fresh arm64 production app packaged and ad-hoc deep-signature verified.
- The fresh packaged app was run against the authenticated live Wellfound listing and extracted exactly:
  - company: `Recurring Decimal`;
  - title: `Sales AI Agent Builder`;
  - location: `Remote (United States)`;
  - description length: 1,935 characters;
  - canonical/application URL: the active Wellfound listing URL.

## Changed files

- `apps/desktop/src/main/browser.ts`
- `apps/desktop/src/main/browser.test.ts`
