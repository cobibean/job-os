# Phase 3 — Portable First Run and Synthetic Demo Memory

**Date:** 2026-08-15

**Status:** Shipped and verified on `main`

**Phase 3 implementation PR:** https://github.com/cobibean/job-os/pull/6

**Implementation commit:** `e29ef35663a519109caaa9070fe67dc4c34fb536`

**Merge commit:** `74093752a7683e3ee4a748cf979071db3ee64a56`

**Post-merge CI:** https://github.com/cobibean/job-os/actions/runs/31916148202 — passed

> [!WARNING]
> This is **private operational sprint memory**, not public product documentation.
> Phase 5 must archive/checksum any useful records privately and remove `docs/memory/**`
> from the publication candidate. Phase 9 must remove historical copies during the
> separately approved history rewrite. This file must not survive in the eventual public
> tree or sanitized public history.

## Why Phase 3 existed

Phase 2 gave JobOS a built-in local SQLite job repository and removed the public runtime's
hard dependency on the private JobHunter implementation. Phase 3 made that local runtime
approachable: a clean source checkout can initialize a real local profile, securely create
its credentials, start with one unmistakably fictional job, and explain its capabilities
without requiring Cobi's Mac mini or private services.

The architectural boundary remains the one established during the open-source sprint:
**open-source the reusable JobOS workbench, not Cobi's personal installation.** Cobi can
continue using the same repository with private adapters and network configuration supplied
outside the public defaults.

## What shipped

### Idempotent local initialization

- Added `jobos-init` and a reusable initializer.
- Creates a portable profile with configurable paths for:
  - workbench state;
  - canonical jobs;
  - artifacts;
  - logs;
  - credentials.
- Supports an exact custom config path, including when the config directory and data
  directory are different.
- Re-running setup preserves the profile and does not duplicate the demo.
- Missing or malformed credentials are repaired while preserving the device identity.
- Service configuration failures now tell the operator how to initialize JobOS instead of
  failing with an opaque import-time traceback.

### Credential safety

- macOS source builds now compile the native Keychain helper before Electron startup.
- macOS uses Keychain when the helper is available.
- The portable fallback is a regular, non-symlink credential file with mode `0600` inside a
  mode `0700` directory.
- Credential values must be bounded, non-empty strings without control characters.
- Secrets are absent from config files, initializer output, logs, and this memory.

### Exactly one synthetic demo

- Added one deterministic fixture: **Northstar Kites (Fictional Demo)** / **Imaginary Kite
  Systems Tuner — Demo Role**.
- The description explicitly says it is fictional, is not a vacancy, and does not accept
  applications.
- A dedicated ledger in `jobs.db` distinguishes first seed, intentional deletion, and
  explicit reset.
- Ordinary edits persist across restart.
- Intentional deletion persists across restart and does not silently reseed.
- Reset requires explicit confirmation and restores the job and ledger atomically in one
  SQLite transaction. A failure rolls the whole reset back.
- ID and canonical-URL collisions with real user jobs fail safely rather than overwriting
  data.

### Desktop onboarding and diagnostics

- Added a polished first-run screen instead of mounting a broken/disconnected workbench.
- Setup exposes working, error, success, retry, and restart states.
- Source mode can start the local FastAPI runtime and then poll it through the existing
  connectivity seam.
- Generated config credentials take precedence over inherited shell tokens for the spawned
  local service.
- Added capability copy that distinguishes local service, desktop, and optional agent
  availability.
- Added data/log shortcuts using configured paths.
- Synthetic jobs receive a visible demo badge and can be intentionally removed.
- Packaged public-binary provisioning remains deferred; this phase is source-first and does
  not claim otherwise.

### Contracts and documentation

- Regenerated OpenAPI and TypeScript contracts for demo metadata and intentional removal.
- Documented local profile layout, configurable-path behavior, credential storage, backups,
  and privacy boundaries.
- The demo-removal API declares its `403`, `404`, and `409` outcomes.

## Important implementation decisions

1. **Restart after setup instead of hot-rebuilding Electron dependencies.** Main-process API,
   workspace, document, and capability clients are captured at launch. Restarting is the
   smallest reliable way to construct them from the new profile.
2. **Keep jobs separate from workbench state.** Canonical jobs and the demo ledger remain in
   `jobs.db`; selection/layout/audit state remains in `jobos.db`.
3. **Deletion is durable intent.** Absence of a demo row is not enough to decide whether to
   reseed; the ledger records deletion explicitly.
4. **Reset is transactional.** The restored job, history event, and ledger update succeed or
   fail together.
5. **Configuration is portable, not operator-specific.** Local loopback, SQLite, local
   artifacts, and offline agent mode are the generated defaults. Private adapters and
   Tailscale remain optional personal configuration outside this public first-run path.
6. **Source-first truthfulness.** The existing packaged updater is not presented as a public
   installer. Public packaging remains later work.

## Verification evidence

### Local full matrix under required Node `v26.5.0`

`pnpm check` passed:

- license inventory verification passed;
- lint passed;
- TypeScript and Python type/build checks passed;
- **368 desktop tests passed**;
- **446 Python tests passed, 2 skipped, 2 strict expected xfails**;
- desktop and contracts builds passed;
- packaged renderer verification passed.

### Focused acceptance

- Repeated initialization produced one profile and one demo.
- A real CLI/API smoke used a custom config filename and successfully listed exactly one
  synthetic demo through the authenticated API.
- A separate smoke proved config and data directories can differ while resolving the same
  databases correctly.
- Credential repair, malformed credential values, symlink rejection, URL collision,
  persistent deletion, selected-real-job preservation, idempotent removal, confirmed reset,
  and reset rollback all have focused tests.
- Desktop onboarding was run and visually inspected; screenshot evidence was captured during
  implementation.

### Expected-red public gates

Running the public-release tests with `--runxfail` still produces exactly two intentional
failures:

1. operator/private-network defaults that will be removed or isolated in later phases;
2. tracked private memory and `.DS_Store` files scheduled for Phase 5 cleanup and Phase 9
   historical scrubbing.

The earlier direct-JobHunter-import blocker was removed in Phase 2 and remains green.

### Review and CI

- Independent read-only Codex review of the exact staged Phase 3 diff ended with
  `VERDICT: APPROVED` after three review/fix rounds.
- Pull-request quality CI and CodeRabbit passed.
- Post-merge `main` CI run `31916148202` passed in 2m19s.

## Reviewer-driven hardening completed

The independent review directly caused these corrections before shipment:

- source desktop builds now compile the Keychain helper;
- custom config filenames converge instead of writing a different `config.json`;
- config and data roots may differ without path-resolution drift;
- malformed JSON credential values trigger safe repair instead of string coercion;
- credential fallback rejects symlinks;
- demo ID and URL collisions preserve user data;
- demo reset became a single rollback-safe transaction;
- demo removal became idempotent and only clears selection when the demo is selected;
- source API startup no longer inherits device/MCP token overrides;
- generated API error responses and privacy/path documentation were corrected.

## Environment gotchas

- Use repository Node `26.5.0` for the complete matrix.
- Use `uv run python` for repository scripts requiring the managed Python environment.
- Contract generation changes tracked files by design. Before committing, run generation,
  stage it, and verify no generated drift remains.
- `pnpm contracts:check` examines worktree cleanliness against `HEAD`; before a commit, use
  generation plus an explicit generated-file diff check instead.
- Source-mode Electron depends on the local `uv` toolchain. Public packaged-runtime
  provisioning is intentionally deferred.

## Remaining work after Phase 3

- Remove or isolate operator/private-network defaults without breaking Cobi's private
  configuration path.
- Archive and remove private tracked material, all `docs/memory/**`, and tracked `.DS_Store`
  files in Phase 5.
- Complete clean-clone installation/startup/packaging acceptance.
- Produce polished synthetic screenshots and a short silent README GIF.
- Rehearse history sanitation in isolation, then stop for fresh approval before any force-push,
  repository visibility change, or publication.

## Safety boundary

Phase 3 did **not** rewrite history, force-push, change repository visibility, publish a
release, deploy production, delete branches, expose credentials, or replace Cobi's private
JobOS setup.
