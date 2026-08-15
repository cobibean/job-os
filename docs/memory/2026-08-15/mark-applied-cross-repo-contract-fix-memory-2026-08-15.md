# Mark-applied cross-repository contract fix — 2026-08-15

## Session summary

Fixed JobOS job status updates that returned HTTP 500 when the UI tried to mark a job as applied.

## Root cause

- JobOS correctly sent `record_application=True` when a user explicitly marked a job as applied.
- The installed API loaded `JobHunterFacade` from the live JobHunter `main` checkout.
- That facade did not accept `record_application`, producing `TypeError: JobHunterFacade.update_lead_state() got an unexpected keyword argument 'record_application'`.
- The matching JobHunter implementation existed on an unmerged shipment branch while the JobOS caller had already reached `main`. This was a cross-repository release-order failure.
- JobOS adapter tests used a fake facade, so they could not detect drift in the real JobHunter method signature.

## Fix

### JobHunter

- Added `record_application` through the facade and storage layers.
- Allowed an explicit direct transition to `applied` from pre-application states only.
- Kept normal state-machine restrictions for unrelated and post-application transitions.
- Made the read/update/history write occur in one immediate transaction.
- Added positive, negative, and facade persistence regression coverage.

### JobOS

- Added a real-adapter contract test that imports JobHunter, writes a temporary JobHunter database, calls the JobOS adapter with `record_application=True`, reopens storage, and proves there is exactly one `discovered -> applied` transition.
- The test skips in standalone JobOS environments without the private sibling dependency and runs explicitly in the cross-repository gate.

## Verification

- JobHunter full suite: 284 passed, 1 skipped.
- JobOS focused real-adapter and status contract suite: 116 passed.
- JobOS standard full gate before the final assertion refinement: desktop 50 files / 360 tests; API and supporting Python suites 393 passed, 2 skipped, 3 xfailed; lint, typecheck, build, and generated-contract checks passed.
- Installed API was restarted and reported `/v1/health` ready with the agent connection online.
- Live installed API replayed an idempotent `record_application=True` update for an already-applied real job: HTTP 200, status remained `applied`, and history event delta was zero.
- The API log shows the same endpoint returning 500 before restart and 200 after the fix.

## Release identity

- JobHunter fix: `0345d7b689e1d7ccf24ac8afc140e9783c76f145`.
- JobOS cross-repository contract test: `17726661376687443d99cde8f034186c382bfedb`.

## Constraints and follow-up

- The primary JobOS checkout had unrelated pre-existing edits in `services/api/jobos_api/jobs.py` and `services/api/tests/test_jobs_contract.py`; they were left untouched.
- Future JobOS/JobHunter interface changes should ship the dependency first and run the real-adapter contract gate before promoting the JobOS caller.
