# Hermes Session Resume File-Descriptor Leak Memory - 2026-08-07

## Session summary

JobOS Agent Chat repeatedly failed during rapid job saves and immediate follow-up questions with:

```text
API call failed after 3 retries. [Errno 24] Too many open files
```

The failure was diagnosed as a Hermes Dashboard resource leak, not a defect in JobOS job persistence or the model provider. JobOS exercised the failing lifecycle through repeated `session.resume` calls and surfaced the terminal Hermes error correctly.

Cobi confirmed after the repair that JobOS was working again.

## Root cause and system boundary

- **Owning failure:** Hermes Dashboard, serving JobOS through `ws://127.0.0.1:9119/api/ws` under the `job-hunter` profile.
- **Trigger:** JobOS resumes the named Hermes profile when starting or continuing agent turns. Rapid job saves and follow-ups caused that path to run frequently.
- **Leak:** profile-scoped `SessionDB` connections opened by Hermes `session.resume` and related build/teardown paths were not consistently closed or transferred to an owner that would close them later.
- **Result:** database and WAL descriptors accumulated until Hermes hit its process limit. Once exhausted, Hermes could not open auth files, SQLite databases, macOS system files, or new provider-network resources.
- **JobOS role:** JobOS triggered the lifecycle and displayed the failure, but did not own the leaked handles. JobOS API logs were useful corroborating evidence, including collateral SQLite/artifact failures during exhaustion.
- **Not the cause:** model rate limits, an intentional cooldown, JobOS job-saving logic, or inability to process more than one request in a short period.

## Runtime evidence

Before repair, the live Hermes Dashboard process was pinned at its full `256`-descriptor ceiling:

- `115` open handles to `~/.hermes/profiles/job-hunter/state.db`
- `110` open handles to `~/.hermes/profiles/job-hunter/state.db-wal`
- additional log, socket, pipe, and normal runtime descriptors filling the remainder

Hermes logs at the failed-turn timestamp showed:

- failure to read `job-hunter/auth.json` with `[Errno 24]`;
- failure to open SQLite session storage;
- terminal cleanup failures with `[Errno 24]`;
- provider retries ending in `API call failed after 3 retries`;
- the JobOS-facing gateway turn failing while opening a macOS system file because no descriptors remained.

The JobOS API process was healthy after its restart, but the separately supervised Hermes Dashboard process remained the exhausted owner until it was repaired and restarted.

## Fix applied

The local customized Hermes checkout at `~/.hermes/hermes-agent` received the exact upstream ownership fixes already present on Hermes `origin/main`:

- `ee094b6bdc` — `tui_gateway: session.resume abandons the profile SessionDB it opens`
- `aa6dfb8286` — `tui_gateway: close dedicated profile SessionDB handles at teardown too`
- `03bb17d9c8` — regression coverage for raising-close/idempotent teardown behavior

The fix establishes explicit connection ownership:

1. A profile-scoped database handle belongs to the resume/build caller until transfer.
2. Every early-return or failed-build path closes its still-owned handle.
3. A successfully initialized agent takes ownership of its dedicated handle.
4. Agent teardown closes owned handles without closing the shared launch database.
5. Branch, deferred-build, compute-host, lazy-recall, and orphan-reaper paths follow the same ownership rule.

No JobOS application source, job data, or job-hunter data was modified as part of the fix.

## Hermes files changed

- `agent/agent_init.py`
- `run_agent.py`
- `tui_gateway/compute_host.py`
- `tui_gateway/methods_session.py`
- `tui_gateway/server.py`
- `tests/tui_gateway/test_session_resume_db_ownership.py`
- `tests/tui_gateway/test_session_db_ownership_teardown.py`

## Verification

Source verification:

- `22` focused Hermes session-database ownership tests passed.
- `7` SQLite lock-safe inspection tests passed.
- Python compilation passed for the changed Hermes modules.
- `git diff --check` passed.

Installed-runtime verification:

- Restarted only the supervised JobOS-facing service: `ai.hermes.dashboard-fleet`.
- Descriptor count dropped from `256` before restart to `15` immediately after restart.
- Warmed one real authenticated JobOS-profile session lifecycle.
- Ran `100` measured back-to-back `session.resume` calls against the installed dashboard.
- Database-handle growth across that measured batch:
  - `state.db`: `0`
  - `state.db-wal`: `0`
  - `state.db-shm`: `0`
- After detached-session teardown, all profile database/WAL handles were released.
- Final bounded runtime check showed:
  - JobOS API health: `ready`
  - JobOS agent connection: `online`
  - active stuck turn: none
  - Hermes Dashboard `CLOSE_WAIT` sockets: `0`
  - no new `[Errno 24]` events after restart
- Cobi subsequently confirmed the JobOS workflow was working.

## Logging assessment

The available logs were detailed enough to diagnose this incident when correlated across process boundaries:

- JobOS API logs identified collateral file-open and SQLite failures.
- Hermes agent/error logs contained the exact exhausted resource and provider retry timeline.
- `lsof` on the exact dashboard PID identified the owning leak by repeated filename.
- launchd and listener evidence separated the JobOS API lifecycle from the Hermes Dashboard lifecycle.

The key diagnostic rule is to inspect the exact exhausted PID rather than treating the user-visible `API call failed` text as evidence of provider instability.

## Gotchas and constraints

- One-time agent initialization legitimately opens a small number of long-lived SQLite readers. A leak test must warm the agent, wait for initialization to settle, then measure a separate repeated-resume batch.
- Validate both steady-state growth and teardown. Zero per-resume growth is required; owned handles must also disappear when the detached session is reaped.
- Raising only the file limit would delay the failure without fixing ownership. The connection lifecycle had to be corrected.
- The JobOS app and Hermes Dashboard are separately supervised processes. Restarting JobOS alone does not necessarily clear a wedged dashboard.
- The unrelated existing edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` remained untouched and unstaged.

## Fleet update note

Cobi planned to update Hermes installations across the fleet on 2026-08-07. The corrective commits above are already upstream, so updating other installations to current Hermes should carry this fix.

On the Mac mini, `~/.hermes/hermes-agent` is a customized checkout with local commits. Do not hard-reset it merely to update. Use the normal Hermes update path and verify afterward that:

1. the relevant ownership tests remain present and green;
2. the supervised dashboard returns online;
3. repeated named-profile session resumes do not grow `state.db` or WAL descriptors;
4. JobOS reports the agent connection as online.
