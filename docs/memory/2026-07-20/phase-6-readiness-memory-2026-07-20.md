# JobOS V1 Phase 6 Readiness Memory — 2026-07-20

## Session summary

- Cleared all three pre-implementation blockers before beginning `CLO-52` implementation.
- Installed the pinned Node.js 26.5.0 toolchain through Homebrew while preserving pnpm 10.33.1.
- Replaced the Hermes dashboard's process-local random credential with a stable protected service-to-service token mechanism without exposing the token to the repository, logs, renderer, or this document.
- Merged the reviewed JobHunter facade branch into the job-hunter repository's `origin/main` from a separate clean worktree, leaving Cobi's dirty primary job-hunter worktree untouched.
- Ran the untouched JobOS full baseline and generated-contract drift gates before Phase 6 source edits.

## Readiness corrections

### Pinned toolchain

- Homebrew Node.js 26.5.0 is available at `/opt/homebrew/opt/node/bin/node`.
- Phase 6 commands prepend `/opt/homebrew/opt/node/bin` to `PATH`; the Hermes-owned Node 22 installation remains untouched.
- `pnpm install --frozen-lockfile` and `uv sync --all-packages --frozen --python 3.11.15` completed successfully.

### Hermes dashboard credential boundary

- Generated one opaque random dashboard token mechanically; its value was never printed or read into project artifacts.
- Stored it at `~/.hermes/secrets/jobos-dashboard-token` with mode `0600`.
- Added `~/.hermes/bin/dashboard-fleet-with-token` with mode `0700`; the wrapper reads the protected token, exports `HERMES_DASHBOARD_SESSION_TOKEN`, and execs the existing loopback-only dashboard command.
- Updated `~/Library/LaunchAgents/ai.hermes.dashboard-fleet.plist` to run the protected wrapper, then reloaded the LaunchAgent.
- Verified dashboard `0.18.2` returned healthy status and the protected token authenticated a read-only WebSocket `session.list` request that produced `gateway.ready` and a successful JSON-RPC response.
- No Hermes session or prompt was created during readiness work.

### JobHunter facade integration

- Preserved the dirty primary checkout at `/Users/jacobilangemm/DEV/agents/job-hunter`; none of Cobi's unstaged resume, skill, application, or workspace changes were modified.
- Created the isolated integration worktree `/Users/jacobilangemm/DEV/dependencies/job-hunter-jobos-facade` from current `origin/main`.
- Merged reviewed branch `origin/codex/clo-48-jobos-facade` at `2f48190936b92cafb901a47893bc420b4b8e378a` through merge commit `9266facfc4ff7d21bc4354805ede3e3265759620`.
- Focused facade verification: `3 passed`.
- Full job-hunter verification: `123 passed`.
- Pushed and fetched `origin/main`; local integration and remote both resolved to `9266facfc4ff7d21bc4354805ede3e3265759620`.
- Cleaned only generated test-environment artifacts in the isolated worktree; it is clean. The primary dirty worktree was not cleaned, reset, stashed, or switched.

## Untouched JobOS baseline evidence

Using Node.js 26.5.0, pnpm 10.33.1, and Python 3.11.15:

- `pnpm check` passed:
  - lint clean;
  - generated OpenAPI and TypeScript contracts;
  - TypeScript checks;
  - 64 desktop tests across 13 files;
  - 157 Python tests;
  - production Electron/Vite build;
  - packaged-renderer verification.
- `pnpm contracts:check` passed with no generated-contract drift.
- JobOS `main` and `origin/main` matched at readiness start: `6b274dfe954824bf8469adc2fc8a363794d14b98`.

## Phase boundary

- This checkpoint records readiness only; no Phase 6 conversation, activity, AgentGateway, Electron, or renderer implementation is claimed here.
- `CLO-52` moved from `Scoped` to `Building` only after all three readiness blockers were cleared and baseline checks passed.
- Phase 6 implementation must continue to write project-memory checkpoints after backend and desktop major slices and a final closeout memory after full live/native acceptance.
