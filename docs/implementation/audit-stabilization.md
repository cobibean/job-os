# Audit stabilization

This pass addresses confirmed correctness and recovery findings from the
2026-10-18 product audit against `fc32c7d`. It preserves the existing workbench,
local-first startup, optional agent integrations, and authorization boundaries.

Repository verification and native acceptance passed. The full check ran
2,621 passing tests with 8 skips: 450 document engine, 261 editor core,
768 desktop, 10 updater, and 1,132 API/public-boundary tests. Lint, types,
build, public-tree/fixture/license checks, and generated-contract checks passed.

## 1. Document identity and fidelity

The preview and toolbar previously derived their targets independently. Local
DOCX edits could be visible while Open, Reveal, or Export selected a published
artifact. Review now derives actions from the displayed source. Local actions
carry the binding ID and reviewed checksum; export checks the source again
after the save dialog. Published exports identify their revision, and the UI
explains when a published PDF omits local edits.

Refresh reloads the authoritative local registry even when an optional provider
is unavailable. It preserves a valid older selection and keeps the packet label,
preview, exports, and approval on that selected revision. Missing-file notices
apply only to the affected document; changing documents clears stale messages.

Export copies use atomic destination replacement with checksum conflict
checks. An overwritten destination does not enter the source document's
recovery history. Local DOCX export does not regenerate a published PDF/DOCX
pair. Submission-ready claims still require a matched pair from one revision
with both files and checksums verified.

Native acceptance also exposed a blank production PDF. Paged.js reconstructed
the attached source's `html`/`body` ancestors inside printed pages, while an
empty stylesheet override skipped its page-rule processing. The print renderer
now paginates a detached fragment and lets Paged.js process the document styles.
Running headers and footers register before body pagination, with their column
grid inside the running root and explicit left alignment. Header/footer
first-page settings apply independently. Synthetic production exports verified
two-page footers, page counters, both mixed first-page configurations, and
unequal margins without body clipping. The clean-clone export gate parses the
actual PDF and requires canonical text, page count, configured running text,
and column alignment, in addition to file signatures and checksums. It rejects
the original blank output and the intermediate layout failures.

Binding-only documents remain navigable without inventing registry artifacts.
Approval targets the displayed eligible artifact, including cover letters.
Imported style rules share a document-specific scope with the editor DOM and
inherit through editor defaults. Pagination measures document coordinates
consistently across zoom and responsive scale; Recovery remains accessible at
narrow widths.

Regression coverage includes document remount/navigation, exact action IDs and
hashes, edited export bytes, stale-source rejection, atomic destination
conflicts, recovery isolation, cover-letter approval, style isolation, and
page boundaries across zoom, edits, resize, and font completion. See desktop
`DocumentWorkspace`, `docxDocuments`, `docxFileStore`, and `useDocxPagination`
tests, plus the editor core's document style tests.

## 2. Local jobs, navigation, and event delivery

Job selection and document view state previously depended on an active chat.
The workbench now retains local job context when no conversation is active,
while conversation context remains scoped to its owning chat. Document view
updates preserve navigator filters. Request generations prevent stale search,
selection, and browser responses from replacing newer state.

Browser association choices use the canonical job collection rather than the
filtered navigator list. Browse details refresh when their job changes, saved
feedback remains tied to its captured URL, and failed loading or browser
restoration exposes recovery instead of leaving navigation pending forever.

Mutation reservations previously reused an old event ID at completion, so a
client could advance past a late result. Schema migration 33 adds an optional
publication reference. Successful create, refresh, remove, status, and
description mutations append a separate delivery event at settlement. The
audit row, publication reference, and stored response ID commit together.
Idempotent replay returns the same result without duplicate publication.
Legacy completed status and description events retain their existing cursors.

Regression coverage includes zero-chat local selection, chat/local context
isolation, stale responses, URL changes, failed-load retry, browser restoration,
two-client delivery, overtaking mutations, reconnect/restart replay, migration
compatibility, reservation ownership, and transaction rollback. See desktop
jobs/browser/workspace tests and API `test_job_event_delivery.py`.

## 3. Agent protocols, recovery, and drafts

Recovery metadata was lost between the provider, API, main process, and
renderer. The stored recovery lease now determines cleanup capability across
transcript reads and streams, including sparse older events. Cleanup uses the
saved session and never creates a replacement prompt. Successful cleanup
clears the exact lease and appends readiness atomically. Temporary cleanup
failure retains quarantine and returns a controlled HTTP conflict.

Retry remains an explicit resubmission with the source turn's context and
authoritative scope checks. Explicit `retry=false` remains binding. Unsupported
Hermes interactions produce a bounded error without persisting the requested
answer or credential payload. First-seen terminal continuations establish
their cleanup lease atomically only after existing source and Career Profile
checks succeed.

Codex deltas use the shared working state; older streaming rows normalize on
read without rewriting stored transcripts. Activity IDs connect tool start,
progress, and completion. Unfinished activity stops appearing active after its
turn ends. Transcript restoration can retry and merge live events by durable
ID without replacing drafts or newer recovery state.

Send acknowledgement clears only the submitted draft revision. Closing a chat
with unsent text requires discard confirmation. Once archive starts, the
confirmation cannot dismiss and its draft cannot change until the result is
known. An unconfirmed close retains the draft and restores controls without
claiming that the server rejected the archive.

Regression coverage includes cleanup failure/success, sparse quarantine,
continuation binding and rollback, forbidden retries, partial text, activity
identity, second turns, warm resume with incomplete metadata, restart
continuation, delayed acknowledgements, hydration overlap, and pending archive
success/failure. See [Agent conversation recovery](agent-conversation-recovery.md)
and the API/desktop agent regression suites.

## 4. Startup, Career Profile, and settings recovery

The root UI now owns the native close responder in setup, recovery, and the
workbench. Opening an editor installs a blocking guard before loading begins;
dirty edits retain protection, while a fatal load can exit. Existing
installations retry their retained runtime configuration and credential,
verify the reported profile, and restart after successful recovery. Connection
recovery cannot run first-time initialization. Source shutdown recognizes
signal exits and acts only on its owned child process.

Career Profile refresh preserves dirty preference fields and their editing
revision. Cached/stale complete profiles can return to live editing even when
the server revision is unchanged. Preference Undo resolves the namespace value
at the requested global revision, preserves immutable history, and rejects
future revisions.

Profile menus refresh after uncertain create/switch outcomes. Connected Agent
Refresh retries model discovery. Agent access retries retain the exact payload
and idempotency key; authority conflicts require reviewing current access
before a new request. Context scope and optimistic concurrency remain strict.

Regression coverage includes startup outages, profile verification, setup
restrictions, root close states, signal shutdown, dirty refresh, cached recovery,
interleaved Undo, uncertain profile creation, model reload, and exact access
retry/conflict behavior.

## 5. Accessibility and interaction basics

App-owned modal behavior supplies background inertness, focus containment,
safe Escape handling, and focus restoration across settings, profile, Career
Profile, and document dialogs. Nested dialogs preserve their owning modal's
focus. Pending destructive actions keep their operation guards.

Focus indicators, secondary text contrast, valid theme tokens, session status
labels, accessible current-job state, and narrow Recovery access address the
confirmed interaction defects. Regression coverage includes modal entry,
keyboard wrapping, nested dismissal, focus return, pending actions, theme token
usage, session states, and responsive controls. This is not a screen-reader or
WCAG compliance certification.

## Verification boundaries and exclusions

- Native acceptance passed with disposable synthetic data in a source-built
  native application. It covered edited DOCX export with matching bytes,
  binding-only navigation, three-page layout at 60/100/150 percent zoom,
  narrow Recovery access, new local publication refresh, and matching PDF
  preview/revision identity. The published PDF/DOCX pair was generated from
  one revision and both downloaded files passed checksum verification.
- Native job checks covered selection/review with no chats, retained context
  while filtered out, and a status change visible in Browse with one replayable
  publication event. Chat checks covered two turns, warm resume with incomplete
  metadata, continuation after app/API restart, draft-close cancellation,
  unsupported-interaction quarantine, cleanup without resubmission, and a
  successful ordinary follow-up. The gateway was synthetic; live-provider
  availability and model behavior are not established.
- Native startup retry reopened the same profile after an outage, and the
  recovery screen quit normally. Career Profile kept a dirty preference across
  an unrelated update, recovered explicitly from a revision conflict, and
  restored a sparse historical preference without overwriting the unrelated
  item. Settings keyboard wrapping, Escape, and opener focus restoration passed.
- This is source-built native acceptance, not a full packaged release. The
  installed application and real user data were not replaced or migrated.
- Frozen pnpm/uv installation, `pnpm check`, and `pnpm contracts:check` passed.
  Clean-clone results are recorded against immutable candidate revisions in
  the task acceptance report. The public negative-test suite passed 63 cases;
  the release documentation does not define a separate canonical expected-red
  command. Source execution or a build alone does not establish packaged support.
- Real records, documents, credentials, runtime databases, logs, screenshots,
  and private deployment details are excluded from source-controlled evidence.
- Redesign, new information hierarchy, fit-to-width work, and other unapproved
  visual changes are excluded. Dormant editor behavior is not acceptance evidence
  for the active local DOCX editor.
- Agent-required Save job/session-capacity behavior and irreversible job status
  transitions remain product decisions. This pass does not introduce agent-free
  capture, change chat limits, or add lifecycle Undo/reopen policy.
- No live Career Profile migration, authority cutover, publication, or external
  application submission is implied by these fixes.
