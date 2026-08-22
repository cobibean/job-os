# Career Profile live cutover acceptance

This is sanitized Issue #58 evidence. It contains counts, checksums, release identifiers, and verification outcomes only. Real profile values, credentials, migration inputs, exports, and screenshots remain outside the repository.

## Approval and recovery boundary

Cobi explicitly approved the live handoff in this conversation: **“i approve. back up jobhunters workspace to github first before the cutover”**. The approval preceded the verified GitHub backup commit created at `2026-08-22T11:53:04-05:00` and all live migration actions.

Accepted limitation: authority rollback was not rehearsed. The pre-cutover JobOS database and legacy JobHunter source commit are recovery inputs, not active co-authorities. Local Career Profile erasure cannot erase the separate GitHub backup.

## Pre-cutover recovery inputs

- Private JobHunter GitHub backup: verified before cutover
- Backup branch: `backup/pre-career-profile-cutover-20260822-112848`
- Backup commit: `287ce9997f1bbd7fd183354eca82f074106e9913`
- Pre-cutover JobOS database SHA-256: `f79c5d714dcab470baa77579a130f859c1f89cf675b03bc81151dfb518a74cc7`
- Private migration bundle file SHA-256: `808d5701dae875446e8a0c6c04b922cc26b5c3409e12708ff96014b1f06b2665`
- The SQLite backup passed `quick_check`.

## Atomic handoff result

- JobOS authority persisted as `cutover`, profile revision `1`, authority epoch `1`.
- Migration produced `120` accepted items, `6` reviewable Proposals, `12` managed Evidence objects, and `0` conflicts.
- Idempotent replay created no duplicate revision, Proposal, or Evidence.
- The required JobHunter consumer change merged at `48f891e33615181429bccb5b0b0e2fbc5e5e2d54` through private PR #3.
- Legacy post-cutover migration failed closed with `career_profile_legacy_writer_fenced`.
- A temporary edit to a backed-up legacy canonical source changed its source hash but did not change the persisted JobOS authority hash; the source was restored byte-for-byte.
- API restart preserved authority, profile, Evidence, Proposals, and context state.

## Installed app verification

The arm64 app was packaged, installed at `/Applications/JobOS.app`, and exercised through its installed Electron window.

- `codesign --verify --deep --strict` passed.
- Installed `app.asar` SHA-256: `1e0079c3464b512fcbcef632815041747e60583802e8e33fdde7126f04683001`
- Package ZIP SHA-256: `4c51a3713d0318332108b30900fddbf547ad8cf7b4e1d60f5180a9a9ef99468c`
- The installed `app.asar` matched the packaged candidate exactly.
- All three Career Profile areas rendered migrated data.
- All six migration Proposals exposed Accept and Reject controls; none were decided during acceptance.
- History, Agent Access, and explicit export controls rendered.
- The installed UI identified JobOS Career Profile as shared authority and contained no stale staging or `(FAKE)` migration-reviewer copy.

Private screenshots were not committed because the installed view contains real user values.

## Behavioral verification

Focused acceptance tests passed for:

- new-turn immutable snapshot binding;
- exact retry, recovery, and continuation reuse;
- fail-closed unauthorized scope expansion before dispatch;
- `none`, exact `selected`, and authorized `broader` context modes;
- proposal review, direct edit history, and compensating Undo;
- profile-only, selected-Evidence, and explicit all-Evidence export behavior;
- no silent Evidence inclusion and historical unavailable-link round trips;
- restart/readback, import, edit, Proposal, and generated-output provenance;
- sparse, zero-Evidence, accepted Evidence-free, and inactive historical Evidence cases.
- confirmed permanent Evidence erasure removed managed bytes, metadata, and source history;
- complete-profile reset scrubbed profile data, Proposals, snapshots, history, vault files, context payloads, bindings, and grants;
- partial destructive failure remained journaled and completed safely after restart;
- restore/migration journal recovery removed or reconciled partial state before returning success.

The combined focused API acceptance run passed `72` tests. Desktop verification passed `67` files / `524` tests, and updater verification passed `10` tests.

The destructive-operation subset was re-exercised with:

```text
uv run pytest \
  services/api/tests/test_career_profile_complete_model.py::test_confirmed_evidence_erasure_removes_managed_bytes_metadata_and_source_history \
  services/api/tests/test_career_profile_complete_model.py::test_full_profile_reset_erases_profile_proposals_snapshots_history_and_all_vault_files \
  services/api/tests/test_career_profile_complete_model.py::test_partial_erasure_failure_is_not_reported_and_restart_finishes_pending_work \
  services/api/tests/test_career_profile_portability_hardening.py::test_restore_journals_before_staging_so_crash_recovery_removes_partial_bytes \
  services/api/tests/test_career_profile_migration.py::test_partial_failure_is_journaled_startup_fails_closed_and_same_bundle_recovers
```

## Residual risk

- Authority rollback remains a manual engineering operation and was not rehearsed.
- The private GitHub backup is intentionally outside JobOS local-erasure control.
- The six imported Proposals still require Cobi’s individual review; migration acceptance did not silently decide them.
- Delivery to another workstation remains gated on the operator confirming the source-machine manual test.
