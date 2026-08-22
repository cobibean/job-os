# Career Profile cutover candidate acceptance

This evidence is for the synthetic disposable-copy Issue #57 candidate only. No live profile was activated, no real migration bundle was built, no private workspace was modified, no private values were copied into evidence, and no external legacy writer was changed. Legacy reader and writer entry points were inspected read-only to build the inventory.

## Reproduction

```bash
uv run python docs/acceptance/career-profile-cutover-candidate/run_disposable_candidate.py
```

The runner creates a fresh temporary JobOS application-data root, imports the registered `(FAKE)` full fixture, verifies idempotent replay, configures an exact selected-item scope, proves the consumer projection is dormant, activates authority only in that disposable copy with the exact phrase, reads through authenticated API and MCP boundaries, proves the actual migration command is mechanically fenced after cutover, restarts the API, verifies persisted authority and projection readback, writes sanitized evidence, and deletes the temporary root.

## Evidence

- `migration-report.json`: machine-readable counts, source/content hashes, candidate revision, and the explicit `staging` result of candidate construction.
- `sanitized-transcript.json`: dormant status, disposable activation, exact selected projection, stable writer refusal, authenticated MCP projection, and restart readback.
- `run_disposable_candidate.py`: reproducible disposable runner.

## Candidate result

- Candidate construction ended in `staging`; activation was a separate exact-confirmation operation.
- The disposable activation persisted `cutover` and incremented `authority_epoch` without creating a profile revision.
- API and MCP returned one exact selected item and did not expand to linked Evidence or the broader profile.
- A legacy writer boundary refused with `career_profile_legacy_writer_fenced`.
- Restart readback preserved the cutover state and exact scope.
- Migration replay returned the original report with no duplicate revision, Proposal, or Evidence.

## Accepted limitation

**Rollback is unrehearsed.** This candidate does not implement or rehearse a restore path for authority cutover. Preserved inputs may support a later release rollback, but they are not live co-authorities. This limitation must be accepted again with the exact release candidate before any separately approved Issue #58 live action.
