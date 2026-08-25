# Phase 7 acceptance evidence

Issue: [#118](https://github.com/cobibean/job-os/issues/118)

This phase intentionally hardens the existing provider-neutral runtime instead of adding another orchestration layer. Negative provider, host, auth, and device cases use deterministic fakes; no deactivated account or production credential is required.

## Acceptance map

| ID | Proof |
|---|---|
| CON-01 | `test_manager_submissions_overlap_at_the_gateway_boundary`, `test_manager_runs_conversations_concurrently_and_shuts_each_gateway`, and `test_conversation_scope_isolates_busy_events_idempotency_and_sessions` prove concurrent chats preserve transcript, turn, event, session, and cancellation ownership. |
| CON-02 | `test_conversation_scope_isolates_busy_events_idempotency_and_sessions` proves a second same-chat send is rejected while other chats remain available. |
| ISO-01 | `test_manager_start_failure_keeps_sibling_available_and_reconnect_alive`, router fail-closed tests, and Codex transport/MCP tests prove provider failures stay scoped and optional Codex startup cannot disable Hermes or JobOS. |
| ISO-02 | `test_cancellation_is_scoped_to_the_current_chat_and_turn` and the concurrent-manager test prove cancellation targets one chat and turn without killing shared infrastructure. |
| REC-01 | API restart, transport-loss, cancellation-race, and event-consumer recovery tests prove active work reaches an explicit terminal or recovery-required state. |
| REC-02 | `test_ambiguous_attachment_survives_restart_without_blind_retry`, linked-retry tests, and Codex exact-thread recovery prove retries retain JobOS correlation and ambiguous delivery never fresh-submits blindly. |
| REC-03 | Desktop connectivity and lifecycle tests prove remote clients do not launch a local fallback; the renderer now says **JobOS host unavailable**, disables Send, and recovers on a later successful probe. |
| SEC-01 | Persistence redaction tests plus the recursive canary scanner cover SQLite, JSON, text, journals, archives, chat output, runtime metadata, and packaged evidence. |
| SEC-02 | `test_support_diagnostics_are_bounded_and_credential_free` and `DiagnosticsPanel.test.tsx` prove support output is bounded, normalized, and free of paths, authorization data, and credentials. |
| SEC-03 | `test_device_session_requires_the_runtime_credential`, direct-user auth-route tests, and `test_remote_device_fixture_detects_reachable_but_unauthorized_access` prove network reachability alone grants no management authority. |
| RATE-01 | Codex rate-limit tests prove usage exhaustion is affected-turn-only, never blindly resubmits or switches providers/models, refreshes authoritative countdown state without racing newer updates, interrupts provider-declared retries, and preserves explicit recovery when interruption cannot be confirmed. |
| HOST-01 | `CodexAuthFlowBroker` and `CredentialVault` remain the application/domain boundary; Tailscale and Keychain details stay in host adapters and packaging code. |

## Security boundary

- JobOS continues to run the pinned Codex app-server from a dedicated `CODEX_HOME` with an explicit minimal environment.
- Device-scoped MCP credentials remain in platform-backed secure storage and are not passed through the Codex app-server environment.
- No raw token, authorization header, device code, or credential value belongs in this evidence file, test output, support diagnostics, or issue closeout.
