# Career Profile first-slice acceptance

Issue: [#53](https://github.com/cobibean/job-os/issues/53)

## Result

**Passed in an isolated disposable profile using only visibly `(FAKE)` values.** No live Career Profile migration, activation, production installation, or user career data was used.

The packaged macOS app built from Issue #52 merge commit
`53356f035c37509c7e482daf898f1b797fd0adbb` was exercised through its rendered
controls. The API and desktop app were both stopped and restarted against the
same disposable SQLite database before persistence was accepted.

Packaged artifact used:

- file: `JobOS-0.1.0-arm64.zip`
- size: `154035633` bytes
- SHA-256: `2c97072fb6468cc89550e594489ac321dd2d893b33f85795a28db6fe796a194a`

## Packaged app journey

1. Opened **Career Profile → What I’m Looking For → Work arrangement** in the packaged app.
2. Saved `(FAKE) Remote · Requirement` as revision 1.
3. Stopped both the packaged app and API.
4. Restarted both and confirmed revision 1 and its exact value were restored from durable storage.
5. Saved `(FAKE) Hybrid · Strong preference` as revision 2.
6. Opened revision history and used its real Undo control.
7. Confirmed Undo created revision 3 and restored the revision 1 value instead of rewriting history.

| Evidence | What it proves |
| --- | --- |
| [Saved revision 1](./packaged-save.png) | The packaged renderer edited and persisted the synthetic preference. |
| [Revision 1 after restart](./packaged-after-restart.png) | The packaged renderer and restarted API reloaded the durable value. |
| [Revision history](./packaged-history.png) | Both immutable revisions are visible and revision 2 exposes Undo. |
| [Undo as revision 3](./packaged-undo.png) | Undo restored revision 1's value as a new revision. |

## Turn-binding proof

The regression `test_api_restart_recovery_tracer_keeps_frozen_snapshot_and_bounds_projection` exercises the production Career Profile store, conversation store, recovery path, and gateway boundary with a recording gateway:

- turn A captures revision 1 plus its snapshot ID and content hash;
- the profile changes to revision 2 while A remains active;
- API/service startup recovery interrupts A without rebinding it;
- retry dispatches the exact revision 1 snapshot ID, hash, revision, and value;
- the next genuinely new turn dispatches revision 2 with a different snapshot ID and hash;
- persisted original/retry bindings are byte-for-byte equal while the fresh turn differs;
- the gateway projection has only `snapshot_id`, `profile_revision`, `content_hash`, and the bounded `work_arrangement` projection;
- persisted conversation event details contain no `candidate-profile`, `USER.md`, `AGENTS.md`, or résumé authority.

The existing continuation tests in the same module additionally prove that a late background continuation retains its spawning turn's binding after service recreation and that missing or unauthorized continuation bindings fail closed. Issue #52 separately verified this against the installed Hermes v0.20.4 wire sequence.

## Commands exercised

```text
pnpm contracts:generate
pnpm --filter @jobos/contracts build
pnpm --filter @jobos/desktop package:mac
uv run pytest -q services/api/tests/test_career_profile_turn_binding.py
```

The UI inputs and CDP capture steps are preserved in [`capture.mjs`](./capture.mjs).
The disposable run used a new data directory outside every live JobOS directory,
file-backed synthetic device credentials, `JOBOS_CAREER_PROFILE_ENABLED=1`, a
loopback-only API, and a loopback-only Chromium debugger port. The reproducible
sequence is:

1. Build the package with the commands above and verify the ZIP checksum.
2. Start the API with the disposable data/config/credential paths and the Career
   Profile flag enabled.
3. Start the packaged app against that loopback API with an isolated Electron
   user-data directory and `--remote-debugging-port=57270`.
4. Run `node capture.mjs save <empty-output-directory> 57270`.
5. Stop both processes and confirm their exit.
6. Restart both against the same disposable database and user-data directory.
7. Run `node capture.mjs undo <second-empty-output-directory> 57270`.
8. Strip metadata, visually inspect the captures, pin their hashes in the public
   asset manifest, and stop both processes.

The packaged `.app` passed macOS on-disk validity and designated-requirement
verification during packaging. Repository-wide verification passed before merge;
GitHub CI remains the final remote gate.

## Privacy and scope

- Every visible value is fictional and explicitly `(FAKE)`-labeled.
- Screenshots were inspected for private paths, credentials, provider internals, and real career data.
- Image metadata was removed before tracking.
- The acceptance profile lived outside all live JobOS data directories.
- Career Profile remains staging-only and dormant unless explicitly enabled.
