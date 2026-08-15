# JobOS open-source readiness Phase 2 sprint memory — 2026-08-15

## Publication classification

**Private implementation memory.** This file belongs in the current private
`docs/memory/**` tree only for continuity during the open-source sprint. Phase 5
must archive useful memory privately, checksum that archive, and remove
`docs/memory/**`—including this file—from the publication candidate. Phase 9 must
remove historical copies during the separately approved history rewrite. This
file must not survive in the eventual public tree or rewritten public history.

## Outcome

Phase 2 shipped the built-in mutable local job repository and moved private
JobHunter support behind an optional adapter. It merged through pull request
`#4` at merge commit `72a47331e57008090e1730bb682c75147ba16705`.
Post-merge `main` CI run `31908800906` passed.

The repository remains private. No history rewrite, force-push, visibility
change, release publication, production deployment, or branch deletion occurred.
The canonical checkout's two pre-existing edits remained untouched:

- `services/api/jobos_api/jobs.py`
- `services/api/tests/test_jobs_contract.py`

## Architecture shipped

JobOS now has a focused canonical-job boundary instead of reaching through the
old broad JobHunter facade:

- `job_repository.py` defines immutable typed records and commands, plus stable
  `NotFound`, `Conflict`, `Validation`, and `Unavailable` errors.
- `artifact_gateway.py` separates artifact operations from canonical job
  persistence.
- `composition.py` defaults public/source startup to local SQLite.
- `private_adapters/job_hunter.py` dynamically loads the private JobHunter
  implementation only when the explicit `job-hunter` provider is selected.
- Public startup no longer statically imports or requires `job_hunter`.
- The private installed macOS runtime explicitly selects the JobHunter provider,
  preserving the existing operator installation while the public default becomes
  local.

Canonical jobs live in a dedicated `jobs.db`, separate from the workbench-state
`jobos.db`. That boundary is intentional: job records and job history belong to
the job repository; selection, ordering, mutation audit, editable-document
state, and workbench projections remain in `JobOsStateStore`.

## SQLite repository behavior

The local repository includes:

- transactional schema initialization;
- a version/name/checksum migration ledger;
- rejection of ahead, divergent, or non-prefix migration history;
- thread and process locking for concurrent initialization;
- verified SQLite backups before any future destructive migration;
- foreign-key enforcement on every repository connection;
- list, get, create, description update, status update, and history operations;
- canonical-URL normalization and race-safe duplicate convergence;
- preserved original discovery time and monotonic `last_seen_at`;
- immutable nested listing evidence;
- stable URL/job-ID conflict behavior;
- direct-application status transitions without hidden intermediate states.

Duplicate refreshes use conservative provenance rules. An incoming listing cannot
move capture time backward, lower completeness, or erase populated verification,
evidence, or capture attribution. A fresher verified/evidenced capture may replace
equal or higher-quality content even when optional derived analysis is absent.

Changing listing bytes through the description-update command recomputes the
digest and capture attribution while invalidating stale analysis, completeness,
verification, source URL, and evidence. This prevents old proof from appearing to
verify new bytes.

## Cross-store ordering and retry lesson

Canonical job storage and workbench audit storage are separate SQLite databases,
so one transaction cannot cover both. Canonical mutations commit first, but
status commands now reserve their idempotent workbench event before changing the
canonical job. The reservation stores the original `from_status` and intended
`to_status`.

If workbench settlement fails after the canonical commit, retrying the same
idempotency key reuses that reservation. The canonical update safely converges
on its existing target while the final event still records the original
transition rather than an incorrect `target → target` transition. A fail-once
regression test proves one settled event row and preserved events/SSE behavior.

## Review findings resolved

Independent staged-diff review ran repeatedly until approval. The hardening pass
resolved these findings:

1. duplicate URL refreshes could overwrite stronger/newer listing provenance;
2. description changes could leave stale verification and evidence attached;
3. provenance freshness and quality were initially joined too loosely;
4. optional `analysis_text` initially blocked legitimate verified refreshes;
5. description changes initially retained stale source attribution;
6. a status settlement failure could lose the original transition on retry.

The final exact 31-file staged diff received explicit independent approval with
no high- or medium-severity findings.

## Verification evidence

Final local evidence used the repository-required Node `26.5.0`:

- `pnpm check`: passed;
- desktop suite: 50 files / 360 tests passed;
- full Python suite: `428 passed, 2 skipped, 2 xfailed`;
- lint, TypeScript checks, generated contracts, and production build passed;
- post-commit `pnpm contracts:check`: passed;
- focused repository/API tests, including concurrency, migrations, provenance,
  and failure/retry behavior: passed;
- real JobHunter cross-repository adapter contract: `2 passed` in the JobHunter
  environment;
- public-release suite: `14 passed, 2 xfailed`;
- strict `--runxfail` verification failed for exactly the two intended remaining
  publication blockers;
- real local uvicorn smoke passed create, URL convergence, list, status, history,
  separate-database ownership, clean shutdown, restart, and persistence;
- pull-request CI run `31908676224`: passed;
- CodeRabbit review: passed;
- post-merge `main` CI run `31908800906`: passed.

## Public-boundary status after Phase 2

The original direct-JobHunter-import red gate is now green. Exactly two strict
expected-red gates remain:

1. operator/private-network defaults still describe Cobi's private installation;
2. private tracked paths—including `docs/memory/**`—and `.DS_Store` remain in the
   private tree.

Those are intentional later-phase blockers, not skipped work in Phase 2.

## Decisions intentionally deferred

Phase 2 did **not** seed the synthetic demo job or add demo/dataset fields to the
repository contract. Phase 3 owns idempotent first-run initialization, the real
local workspace, and exactly one clearly labeled synthetic demo job. Keeping
that behavior out of Phase 2 prevented initialization policy from leaking into
the persistence seam before its lifecycle is designed and tested.

Artifact ownership also remains incomplete for the public default. The local
composition exposes a stable unavailable artifact capability rather than
silently reaching into JobHunter. Later phases must provide local artifact
ownership and accepted first-run UX.

## Next phase

Phase 3 should build idempotent local initialization on top of the now-shipped
SQLite repository:

- create the real local workspace;
- seed exactly one clearly labeled synthetic demo job;
- never recreate it after the user deletes it;
- preserve deterministic dataset/version provenance;
- prove first run, second run, deletion, restart, and migration behavior;
- keep private JobHunter optional and absent from public startup.

The overall release is still not ready for publication. Phase 5 archival/removal
of private memory and OS metadata, later clean-clone/package acceptance, launch
media, and the separately approval-gated history rewrite remain mandatory.
