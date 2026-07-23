# Agent Chat Orphan Stream Fix Memory - 2026-07-23

## Session summary

Fixed and shipped the JobOS Agent Chat failure where a long-running Hermes response appeared as hundreds of one-token Agent messages after the JobOS API restarted mid-turn.

The shipped solution has two boundaries:

- the Hermes adapter drops turn-scoped events when no active JobOS turn owns them;
- the desktop renderer refuses to display already-persisted assistant events with a null `turnId`.

The exact installed Mac mini app was rebuilt, updated, visually accepted, and returned to a normal non-debug launch. The MacBook updater was sent through Tailscale Taildrop.

## Root cause and decisions

- Restarting the API erased the adapter's in-memory active turn ID while the Hermes session continued streaming.
- JobOS accepted 564 assistant delta events with `turn_id = NULL`.
- The renderer fell back to each event ID, so every token became a separate Agent bubble.
- Fix at the source boundary rather than coalescing unknown events: ownerless message/tool/status events are quarantined by dropping them.
- Add a renderer defense for historical pollution rather than deleting the persisted events.
- Preserve session/activity history and explicitly settle only the known stale turn after verifying its remote Hermes task had completed.

## Product release identity

- Backend prevention commit: `da579e64a84d082686377c8db29ed5da3ca7c961`.
- Final product/release commit: `a6ecfd1b84365b33872305439f1077c69667339a`.
- Both commits were pushed and independently verified against `origin/main` and the live remote ref before packaging.

## Files changed

- `services/api/jobos_api/hermes_adapter.py`
- `services/api/tests/test_hermes_adapter.py`
- `apps/desktop/src/renderer/hooks/useAgentConversation.ts`
- `apps/desktop/src/renderer/hooks/useAgentConversation.test.tsx`

The unrelated existing edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` remained untouched and unstaged.

## Verification and review

- Regression tests were observed red before each source fix and green afterward.
- Full Python pytest suite passed with one expected skip.
- Ruff passed.
- Desktop: 26 files and 159 tests passed.
- Desktop typecheck and oxlint passed.
- `git diff --check` passed.
- Static added-line security scan returned no findings.
- Two independent Codex reviews at medium reasoning passed with no security or logic findings.
- Production Electron build passed.
- Inner and outer ZIP integrity checks passed.
- Ad-hoc code-signature verification passed.
- The outer updater's disposable install smoke passed.

## Installed Mac mini acceptance

- Installed path: `~/Applications/JobOS.app`.
- The launchd-owned API was restarted and listened only on `127.0.0.1:8766`.
- Final normal launch had no remote debugging port open.
- Visual evidence showed:
  - Mac Mini connected;
  - no one-token orphan Agent messages;
  - normal workbench and resume rendering;
  - available New Session and composer controls;
  - no Stop/send-after-turn lock;
  - one honest terminal status: `Turn closed after verified completed remote session`.
- Screenshot: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/screenshots/jobos-orphan-stream-fix-normal-launch-2026-07-23.png`.

## Stale-turn operator repair

Before repair, a SQLite online backup was created:

- `data/backups/jobos-before-orphan-stream-repair-20260723-132120.db`
- SHA-256: `7815410274eef09afafd512866795c312c6b9d6a9b453b2c57560109f76a731f`

JobOS's transactional state-store methods then settled only the affected waiting/cancel-requested turn as interrupted. The final state had no active turn and no recovery quarantine. No raw SQL mutation or conversation deletion was used.

## Release artifact and delivery

Outer MacBook updater:

- Filename: `JobOS-MacBook-Update-20260723181758912-a6ecfd1b-3104fb68ff199f6f6e81ba4d5ea94b8c.zip`
- Size: `143491911` bytes
- SHA-256: `980893215dbdb9d7053493b903baa5e677593d7226a990c6ba22f7d9837f3dea`
- Embedded source commit: `a6ecfd1b84365b33872305439f1077c69667339a`

Delivery target `jacobis-macbook-pro` was online over a direct Tailscale path. Taildrop accepted the transfer, and the source checksum was unchanged afterward.

Receiver-side checksum verification was not completed because Tailscale SSH correctly refused the unknown MacBook ED25519 host key under strict checking. The host-key check was not bypassed. Cobi still needs to confirm the updater appeared on the MacBook; the updater performs its own inner-package checksum verification when run.
