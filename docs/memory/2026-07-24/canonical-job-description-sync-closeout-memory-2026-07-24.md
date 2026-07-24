# Canonical Job Description Sync Closeout Memory - 2026-07-24

## Session summary

Shipped a database-authoritative full job-description workflow across JobHunter and JobOS. Agents can now replace a job's complete canonical description through MCP, JobOS records an idempotent mutation event, JobHunter updates its canonical database, and the matching `job.md` packet is refreshed as a derived compatibility artifact.

The desktop keeps navigation rows lightweight, loads selected-job detail separately through `job_inspect`, and shows a short preview plus an expandable **Full listing** disclosure.

## Shipped commits

- JobOS `main`: `b7d782b56ffd8168916e01f66f6a1d2fc9cda845` — `feat: sync full job descriptions`
- JobHunter `main`: `69f724a18e9e2cc8116278e6e416e9a63dd03f88` — `feat: add canonical job description updates`
- For both repositories, local `HEAD`, `origin/main`, and the remote `refs/heads/main` were verified at the same commit.
- Cobi's unrelated edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` was preserved and left unstaged.

## Architecture and decisions

- Canonical endpoint: `PUT /v1/jobs/{job_id}/description`.
- MCP tool: `job_update_description`.
- Requests carry the complete replacement description, source/provenance, origin, and an idempotency key.
- Writes cross the `JobHunterFacade`; HTTP, MCP, Electron, and React layers do not issue raw job-database SQL.
- The JobHunter database is authoritative. `job.md` is materialized afterward inside one managed section:
  - `<!-- JOBOS:FULL-LISTING:START -->`
  - `<!-- JOBOS:FULL-LISTING:END -->`
- Database success is retained if packet materialization fails. Repeating the logical operation can repair the packet without duplicating canonical history.
- Same idempotency key plus the same normalized payload replays the stored result and event identity. Reusing the key with a different payload conflicts.
- Canonical no-op replacements do not append redundant JobHunter history or rewrite packets.
- Packet materialization preserves unrelated content and fails closed for ambiguous matches, unsafe paths, symlink escapes, and malformed managed sections.
- Navigation lists remain lightweight. Selected-job detail is fetched separately, with request-identity and selection-revision guards preventing stale asynchronous results from replacing a newer selection.

## Black Duck live proof

Target job:

- Job ID: `3e8a7a3aad6e0e4903e57085`
- Company/title: Black Duck — Technical Product Manager (AI/ Agentic systems)
- Before the update, the canonical description was 252 characters.
- After the live MCP-originated update, the canonical description is 4,504 characters.
- Canonical description SHA-256: `c96a3449e5b1fcc697266678f6572f0642262488eed2bf96f9f7d9d50599c422`.
- The managed `job.md` description is also 4,504 characters with the same SHA-256 and an exact content match.
- The packet contains exactly one managed start marker and one managed end marker.

Recorded JobOS mutation:

- Event ID: `1115`
- Event type: `job_description_updated`
- Command: `job.update_description`
- Origin: `mcp`
- Outcome: `completed`
- Idempotency key: `black-duck-full-listing-20260724-v1`
- The stored replay result also identifies event `1115` and the 4,504-character normalized job description.

JobHunter history independently records one `description_updated` event from 252 to 4,504 characters, source `jobhunter_agent`, and provenance stating that the complete user-supplied listing was recovered from the durable Black Duck packet.

## Verification

### JobOS

Final `pnpm check` passed end to end:

- lint passed;
- TypeScript checks passed;
- contract generation passed;
- desktop tests: 178 passed;
- Python tests: 339 passed, 1 skipped;
- production build passed;
- `git diff --check` passed.

Focused API, MCP, desktop main-process, hook, race, and component tests also passed during implementation. Generated OpenAPI and TypeScript contracts include the new description-update operation.

### JobHunter

- full suite: 145 passed;
- focused facade suite: 25 passed;
- Ruff passed using the JobOS virtual environment because the JobHunter environment does not currently include Ruff;
- `git diff --check` passed.

Independent Codex staged-diff reviews were run during implementation. Findings drove stable event-ID replay, pre-side-effect idempotency reservation, packet-repair semantics, independent-facade concurrency coverage, and stronger desktop stale-response protection.

## Installed application and visual proof

The exact arm64 desktop package was built, deeply code-signature verified, installed at `/Users/jacobilangemm/Applications/JobOS.app`, and launched from that path.

Artifact:

- `release/desktop/JobOS-0.1.0-arm64.zip`
- size: `143883555` bytes;
- SHA-256: `b6d6a6f6a3db1d72f99722c10f117d179f86a821721a77efbfeb89ef4c3ce276`;
- executable architecture: arm64.

Installed-app visual acceptance showed Black Duck selected with:

- Burlington, MA (Remote);
- a short canonical-description preview;
- an expandable **Full listing** section;
- the expanded listing beginning with `### About the job` and displaying the canonical Black Duck text.

The app was quit and relaunched into a new process. After resolving the service issue below, Job Navigation loaded successfully and Black Duck remained the selected active job. macOS locked before the disclosure could be expanded a second time, so the strict post-relaunch expanded screenshot was not captured. Database, packet, stored mutation, and live selected-job state remained consistent.

Local screenshot evidence is under the Devonte cache, including `jobos-black-duck-full-listing.png` and `jobos-final-proof-relaunch.png`; these are not repository artifacts.

## MacBook delivery correction

An initial Taildrop mistakenly sent the inner `JobOS-0.1.0-arm64.zip` by itself. That artifact did not contain the operator-facing update command and must not be used as the MacBook handoff. Cobi identified the artifact-shape error, and the delivery was corrected through the repository-owned two-layer updater workflow.

Corrected build command:

```bash
pnpm --filter @jobos/desktop package:macbook-update
```

Correct outer updater artifact:

- filename: `JobOS-MacBook-Update-20260724202117365-6faa7bb9-af9d2b3a90ea2273c75b61f21059a502.zip`;
- source commit recorded by the receipt: `6faa7bb96cc3034f764aa9efbfa365d8fd16432a`;
- outer size: `143494382` bytes;
- outer SHA-256: `b1a638c2faf9bedd1040dc4ebd21ce387628d0c58744a1d2131a923cd40059c3`;
- embedded app ZIP size: `143883555` bytes;
- embedded app ZIP SHA-256: `b1bc69e427268d48ffbcbc2b7f90bd34ba433d665c42e9d3b9c065114a4ff8ec`.

The packaging command rebuilt the contracts and desktop app, ad-hoc signed and deeply verified the app, verified both ZIP layers, extracted the outer archive, required the exact member set, syntax-checked the updater, validated `VERIFIED.txt`, and ran `Update JobOS.command` against a disposable installation path. The updater smoke installation passed.

The verified outer ZIP contains exactly:

- `Update JobOS.command` (executable operator entry point);
- `VERIFIED.txt`;
- `JobOS-0.1.0-arm64.zip` (inner payload consumed by the updater).

Delivery:

- Taildrop target: `jacobis-macbook-pro` (`100.111.119.83`);
- peer replied directly before transfer;
- the first corrected transfer printed `sent` but returned a non-zero process status, so it was not accepted as clean evidence;
- the immediate retry printed `sent "JobOS-MacBook-Update-20260724202117365-6faa7bb9-af9d2b3a90ea2273c75b61f21059a502.zip"` and returned `TAILDROP_EXIT=0`.

Cobi should discard the earlier generic inner ZIP, confirm the uniquely named outer updater arrived, unzip it, and double-click `Update JobOS.command`. Taildrop proves delivery only; the MacBook is not considered updated until that command runs successfully on the destination.

## Runtime gotcha discovered

Repeated installed-app relaunch acceptance exposed open-file pressure in the local JobOS API LaunchAgent:

- macOS supplied a soft `RLIMIT_NOFILE` of 256;
- concurrent desktop streams and SQLite readers eventually produced `sqlite3.OperationalError: unable to open database file`;
- the desktop temporarily showed `Jobs unavailable` and an authentication failure while the service was unhealthy.

The live local wrapper at `~/Library/Application Support/JobOS/service/demo_service.py` was given a service-scoped startup mitigation that raises its soft open-file limit to 1,024 before `execve` starts Uvicorn. The API then restarted successfully, `/v1/health` returned `ready`, the agent connection reported online, and the installed app relaunched with Job Navigation restored.

Rollback copies of the original wrapper and LaunchAgent plist were retained in the Devonte cache. No token or credential value belongs in project memory.

This mitigation is local runtime configuration and is not part of the pushed JobOS commit. A later source-controlled reliability slice should investigate connection/stream lifetime and make the durable runtime launcher set an appropriate service limit rather than depending on the generated local wrapper edit.

## Files changed by the shipped feature

Primary JobOS areas:

- `services/api/jobos_api/app.py`
- `services/api/jobos_api/jobs.py`
- `services/api/jobos_api/state_store.py`
- `services/api/tests/test_jobs_contract.py`
- `services/api/tests/test_health_contract.py`
- `services/mcp/jobos_mcp/jobs.py`
- `services/mcp/jobos_mcp/server.py`
- `services/mcp/tests/test_jobs_tools.py`
- `packages/contracts/openapi.json`
- `packages/contracts/src/generated/`
- `apps/desktop/src/main/jobs.ts`
- `apps/desktop/src/main/main.ts`
- `apps/desktop/src/preload/preload.cts`
- `apps/desktop/src/shared/contracts.ts`
- `apps/desktop/src/renderer/App.tsx`
- `apps/desktop/src/renderer/hooks/useJobs.ts`
- `apps/desktop/src/renderer/components/JobNavigator.tsx`
- corresponding desktop tests and `styles.css`.

Primary JobHunter areas:

- `src/job_hunter/facade.py`
- `src/job_hunter/storage.py`
- `tests/test_facade.py`.

## Remaining follow-ups

1. Confirm the uniquely named outer updater ZIP appeared on the MacBook, discard the earlier generic inner ZIP, run `Update JobOS.command`, and verify the installed MacBook application afterward.
2. Add a source-controlled fix for the API service's open-file/SQLite stream pressure, then remove dependence on the local wrapper mitigation.
3. Capture a fresh standalone MCP `tools/list` transcript containing `job_update_description` if catalog-level acceptance evidence is still desired.
4. Capture the live same-key replay and changed-payload conflict HTTP responses if explicit runtime idempotency transcripts are still desired; automated tests and event `1115` already cover the contract and stored replay identity.
5. If strict visual persistence evidence is required, unlock the Mac and re-expand Black Duck's **Full listing** after a clean relaunch.
