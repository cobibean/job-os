# Wellfound Location Extraction Fix — 2026-07-21

## Report

Cobi installed the browser-save MacBook update and attempted to save the live Wellfound listing shown in JobOS. The save failed immediately with:

`Could not extract a complete job listing; missing location.`

## Direct runtime evidence

The authenticated JobOS desktop capability channel confirmed the MacBook was online and exposed the exact active tab:

- URL: `https://wellfound.com/jobs/starred?job_listing_slug=4467759-sales-ai-agent-builder`
- Detail listing: `Recurring Decimal — Sales AI Agent Builder`
- Rendered location immediately below the role: `Remote (United States)`

The page snapshot contained the location, so the defect was in extraction rather than page loading or API persistence.

## Root cause and fix

Wellfound's authenticated saved-jobs detail pane did not expose the location through the structured-data or class-based selectors JobOS supported. The location was rendered immediately after the exact job heading.

Added a narrow final location fallback that:

1. finds the last rendered heading exactly matching the extracted job title;
2. searches only a small nearby rendered-text window;
3. accepts the parenthesized remote-location shape used by Wellfound;
4. does not scan the entire saved-jobs page, which contains many unrelated listings and locations.

## Verification

- Added a failing-first regression fixture representing Wellfound's saved-list/detail-pane layout.
- Focused regression passed after the fix.
- Full `pnpm check` passed:
  - 147 desktop tests;
  - 323 API tests passed and 1 skipped;
  - lint, typecheck, generated contracts, and production build passed.
- `git diff --check` passed.

## Corrected MacBook update

- Source commit: `74f2f1ce06d412ba3ae4c35a46d1d3dd94714c8b`
- Artifact: `JobOS-Wellfound-Fix-2026-07-21.zip`
- Private URL: `https://jacobis-mac-mini.tailf1a3a1.ts.net:10449/JobOS-Wellfound-Fix-2026-07-21.zip`
- Size: `143121021` bytes
- SHA-256: `7e31daec805d17c19cac56522fe351508e33bcd5fdf207e9850e3acfc3879fd7`
- Fresh arm64 package build, deep signature verification, nested ZIP integrity, updater syntax, served-root copy, and private Tailscale re-download verification all passed.
