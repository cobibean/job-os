# Agent conversation recovery

The stored conversation recovery turn is authoritative. A quarantined turn
exposes `detail.recovery_pending=true` and `detail.recovery_action=cleanup` in
both transcript reads and event streams. The desktop preserves these fields,
the stable `reason`, and the `agent_continuation` flag.

The existing turn cancel operation confirms remote cleanup for a terminal
quarantined turn. It uses the saved recovery session and never submits another
prompt. Failed confirmation preserves quarantine. Successful confirmation
clears the exact recovery lease and records the conversation-level ready event
in one SQLite transaction.

Retry is a separate, explicit prompt resubmission. A terminal event with
`retry=false` rejects resubmission. Eligible foreground retries keep the source
turn's job/profile context and existing authoritative scope validation.
Background continuations cannot become replacement user prompts.

## Stored transcript compatibility

No data migration rewrites prior transcripts. When a sparse older terminal
event belongs to the stored recovery turn, its read projection adds the cleanup
capability. Events cannot establish a recovery lease by claiming metadata.
Historical recovery metadata is a record of that failure; the desktop also
checks the conversation's current recovery state before offering cleanup.

Older Codex assistant rows with `state=streaming` project as `working`. New
Codex deltas and buffered tail flushes use the common working state before
persistence. Completed message text replaces the streamed text. Interrupted
turns retain their partial assistant text.

## Provider capabilities

Hermes does not expose a safely scoped response method for clarification,
approval, sudo, secret, or generic user waits. These requests become terminal
`AGENT_INTERACTION_UNSUPPORTED` errors with fixed explanatory text. Request
payloads are omitted, and remote cleanup must finish before new work. The app
does not solicit credentials or claim that it submitted a clarification answer.
Codex's existing bounded tool approval protocol remains separate.

A first-seen terminal background continuation commits its failed turn and
recovery lease together. Validated continuation identity remains available to
the store's source binding and Career Profile scope checks. Rejected or
unbound continuations cannot create a recovery lease or replace another turn's
pending cleanup.

Codex start, progress, and completion events identify one activity by a hash of
the JobOS turn and provider item ID. Display labels contain only bounded tool
identifiers. Progress frames reuse the known label when they omit item metadata.
Separate provider items and separate turns retain distinct activity identities.

## Desktop interaction

The desktop offers cleanup only when the conversation is quarantined and the
stored recovery turn exposes the cleanup capability. It uses cancel and then
refreshes the conversation. Cleanup never resubmits a prompt. A ready event or
fresh snapshot confirms readiness; a mutation acknowledgement cannot overwrite
a newer quarantine event. Eligible retry remains a separate user action, and
explicit `retry=false` remains authoritative even when `actionable=true`.

Send captures the draft revision. A successful acknowledgement clears only
that revision. Edits made while the send is pending remain in their owning
chat, including after terminal delivery or switching chats. Failed sends leave
the submitted or newer draft intact.

Failed transcript hydration retries on explicit action, tab selection, or a
connection transition to online. A chat has at most one hydration request in
flight, and repeated online notifications do not create a retry loop.
Hydration merges by durable event ID and retains drafts, sealed bindings, and
recovery state from newer events.

Closing a chat with an unsent draft requires a separate discard confirmation.
Keep writing preserves the text. Confirmation applies to the current draft
revision, and a failed archive leaves the draft and chat available. The same
guard applies when making room at the five-chat limit. Drafts remain in memory
and do not persist across application restart.

App quit, window close, and profile switching also guard unsent drafts across
all chats. Document close safety runs first. Profile switching additionally
flushes workspace changes and rejects active or recovering work and pending
chat operations before asking about drafts. The confirmation waits for pending
operations on draft-bearing chats, then rechecks document safety and the
approved draft revisions. Keep working, Escape, backdrop cancellation, or a
failed safety check preserves every draft. Confirmation approves the exit but
does not clear text before the caller actually closes or changes profiles.

The single app close responder carries a request-scoped prompt context through
preload. A mounted confirmation sends a heartbeat every five seconds over the
existing result channel. Main retains its fifteen-second watchdog; stale or
foreign replies cannot extend it. Timeout, renderer failure, or window
destruction cancels that exact request, and late confirmation cannot approve it.
Repeated Quit requests share the pending window-close decision without changing
the original quit intent.

The native browser must acknowledge detach before draft confirmation appears.
Cancellation and rejected detach restore its current workspace bounds. The
confirmation layer sits above profile dialogs and switching overlays. An
approved profile preflight keeps the old workspace read-only until the main
profile operation settles, including when it initially had no drafts. Failure
releases that lock with drafts intact. Only a confirmed target profile permits
the ensuing relaunch close to reuse that approval, avoiding a second discard
prompt. Profile requests have a bounded deadline through response-body parsing;
timeouts report an unknown outcome and never automatically retry a mutation.

After discard is confirmed, the dialog stays open and the draft cannot change
while the archive request is pending. Keep writing, Escape, and backdrop clicks
cannot dismiss that pending action. An unconfirmed close retains the draft and
returns control without claiming that the server definitely rejected the archive.

When a turn ends, activities without an explicit completion become interrupted.
Explicit completed and failed activity outcomes remain unchanged. Interrupted
rows show a pause icon instead of a running spinner.

Focused regression coverage lives in `test_agent_contract.py`,
`test_codex_adapter.py`, `test_hermes_adapter.py`, and desktop `agent.test.ts`.
It includes cleanup failure/success without resubmission, sparse stored
quarantine, source-job retry scope, old streaming rows, second and resumed turns,
restart continuation, safe unsupported requests, and activity identity.
Desktop chat tests also cover cleanup without resubmission, explicit retry
eligibility after cleanup, delayed send acknowledgements, failed transcript
restoration, draft-close confirmation, and terminal activity settlement.
