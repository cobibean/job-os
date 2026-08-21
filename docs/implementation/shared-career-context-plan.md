# Shared Career Context Implementation Plan

**Status:** Build-ready specification  
**Wayfinder:** [#40](https://github.com/cobibean/job-os/issues/40)  
**Planning ticket:** [#49](https://github.com/cobibean/job-os/issues/49)

**Context/export semantic contract:**
[`career-profile-context-and-export-contract.md`](career-profile-context-and-export-contract.md)

**Goal:** Make one user-owned Career Profile the durable source of career facts, preferences, and Evidence used by JobOS and authorized connected agents.

**Architecture:** JobOS owns a typed, versioned Career Profile store and immutable Evidence vault. The desktop and connected agents use authenticated JobOS API/MCP contracts; every new user turn atomically binds to one authorized immutable profile snapshot before dispatch. Development starts with a staging-only work-arrangement tracer bullet, expands to the complete product, and ends in one explicitly approved all-at-once authority cutover.

**Tech stack:** FastAPI, Pydantic, SQLite, generated OpenAPI/TypeScript contracts, Electron, React, TypeScript, and the existing MCP adapter.

---

## 1. Product outcome

A JobOS user can understand and control the career information JobOS and connected agents use without reading profile files, repository instructions, or raw configuration.

The product has three areas:

1. **My Career** — identity, education, skills, positioning, experience, projects, and accomplishments.
2. **What I’m Looking For** — target roles, compensation, location, work arrangement, industries, priorities, and dealbreakers.
3. **My Evidence** — source résumés, portfolio material, supporting proof, citations, required qualifiers, and “never say this” boundaries.

The first implementation slice proves this architecture with one user-edited work-arrangement preference. It runs only in a fresh disposable profile until the complete migration candidate is ready and receives explicit cutover approval.

## 2. Canonical glossary

These terms are product and contract language. Implementations must not introduce competing names.

| Term | Meaning |
|---|---|
| **Career Profile** | User-facing JobOS feature containing user-owned career information shared with connected agents. |
| **Shared Career Context** | Internal architecture and contract name behind Career Profile. |
| **My Career** | User-facing area for career facts and positioning. |
| **What I’m Looking For** | User-facing area for search preferences and constraints. |
| **My Evidence** | User-facing area for imported sources, proof, citations, qualifiers, and usage boundaries. |
| **Accomplishment** | User-facing name for a reusable assertion about the user’s work or impact. |
| **Claim** | Internal typed representation of an Accomplishment. Evidence links are optional support; qualifiers and forbidden uses constrain reuse. |
| **Evidence** | Optional supporting material or citation that records provenance for a Career Profile entry; it never grants permission for the user to state or use that entry. |
| **Source Evidence** | Original imported résumé, portfolio item, or supporting document. Its imported bytes never change. |
| **Revision** | Immutable accepted Career Profile change with actor, base revision, changed fields, timestamp, and resulting head. |
| **Snapshot** | Immutable, authorized projection of one profile revision bound to an agent turn. |
| **Proposal** | Agent-authored change set awaiting atomic user approval or rejection. |

Database namespace names remain internal. They do not replace the three user-facing navigation labels.

## 3. Domain and ownership boundaries

### 3.1 Included in Career Profile

- Career facts, identity, education, experience, skills, projects, and positioning.
- Accomplishments/Claims and their Evidence links.
- Search goals, preferences, requirements, dealbreakers, and qualifiers.
- Source Evidence references and provenance.
- Reusable application truth and “never say this” boundaries.

### 3.2 Excluded from Career Profile

- Agent permissions, communication style, and trust mode; these belong in Settings.
- Agent working context, transcripts, system/repository policy, skills, and credentials.
- Browser state, runtime state, and tool permissions.
- Job/application workflow state.
- Generated résumé, cover-letter, score, Review Brief, and application-answer bytes.

Generated outputs consume a snapshot but never become a competing source of user truth.

### 3.3 Ownership

- The user owns the Career Profile and its data.
- JobOS is the canonical custodian and system of record.
- V1 has one user-global Career Profile.
- Connected agents are authorized collaborators, not owners.
- Connecting, disconnecting, or replacing an agent never resets, transfers, or forks the profile.
- V1 has no agent-specific fact override or inheritance tree.
- A narrower agent projection is a view, not a copy or fork.
- The profile remains portable independently of any agent.

### 3.4 User agency

- The user may enter, accept, edit, and use their own Career Profile information without supplying Evidence.
- Evidence strengthens provenance; it is not a permission gate for a user-authored or user-approved fact or Accomplishment.
- Removing Evidence never deletes, deactivates, or automatically demotes a user-authored or user-approved profile entry. The historical provenance link may remain visible as unavailable Evidence.
- Agent-authored inferred, ambiguous, or conflicting information remains proposed until the user accepts it. This protects user control without making JobOS the judge of what the user may say.
- Product warnings are advisory unless an operation would violate structural validity, authorization, concurrency, privacy/security boundaries, or data integrity.

## 4. Data architecture

### 4.1 Storage responsibilities

Use JobOS-owned SQLite storage for:

- the one user-global Career Profile head;
- typed current records;
- immutable accepted revisions;
- agent proposals and decisions;
- actor attribution;
- immutable snapshots and bounded projections;
- Evidence metadata and immutable managed Source Evidence references;
- compact audit events describing actors, changed fields, and revision transitions.

Use a JobOS-owned Evidence vault beneath the configured application-data root for imported Source Evidence bytes. Desktop and MCP contracts receive opaque IDs, never absolute server paths.

The MCP adapter remains a thin translation layer. Domain validation, authorization, revision checks, and snapshot resolution live in the API core.

### 4.2 First typed record

The tracer bullet introduces `search_preferences.work_arrangement` with:

- `mode`: `remote | hybrid | onsite | flexible`;
- `strength`: `requirement | strong_preference | preference | dealbreaker` where semantically applicable;
- optional plain-language `note`;
- one 1,000-Unicode-code-point cross-layer resource limit for that additional context, preserved
  unchanged by API, desktop validation, cache validation, and renderer input;
- stable opaque record ID;
- profile revision and item revision;
- actor and timestamp metadata.

The model must allow later typed records without creating generic unvalidated JSON blobs or prebuilding every future namespace.

### 4.3 Revision rules

- Every accepted edit creates exactly one immutable revision.
- Mutations carry an idempotency key and expected base revision.
- Idempotent replay returns the original result and does not create another revision.
- A stale base revision fails closed and never overwrites newer data.
- Undo writes a compensating revision; it never rewrites or removes history.
- Prior values and removed active entries remain in local history during ordinary
  edit/remove/Undo flows.
- Separately confirmed owner erasure is the only exception: one Evidence object can
  be permanently erased, and the complete local Career Profile can be reset.
  Destructive operations remove recoverable in-scope history instead of writing a
  compensating revision.

### 4.4 Snapshot rules

A snapshot contains:

- opaque snapshot ID;
- authorized principal and scope;
- profile revision;
- deterministic content hash;
- bounded typed projection;
- creation timestamp.

A snapshot is immutable. Expansion requires a separately authorized operation; possession of a snapshot ID or local credential does not imply access to every namespace.

The complete product must offer exactly three user-understandable scope choices:
none, selected items or areas, or a broader authorized projection. `none` binds
an empty projection rather than skipping authorization. A selected scope records
the exact item IDs and/or canonical areas. A broader projection is limited by an
explicit grant and is never inferred from agent connection, credentials, or a
prior snapshot. Retry, recovery, continuation, and subagent follow-up preserve
the exact bound scope; unauthorized expansion fails closed before dispatch.

## 5. API and authorization contract

The authenticated API is the only Career Profile writer.

The first slice needs operations to:

- read current work arrangement and revision;
- validate and apply a user edit with expected base revision and idempotency key;
- read item history;
- restore a prior value by creating a compensating revision;
- resolve/bind an authorized immutable snapshot for a new turn;
- read an already-bound snapshot by opaque ID for trusted dispatch and diagnostics.

Later operations add:

- typed reads and mutations for all Career Profile areas;
- Source Evidence import/status/read/remove workflows;
- proposal creation, approval, rejection, and direct-edit audit;
- per-agent grants and editing modes;
- portable current-state export with an explicit profile-only, profile plus
  selected Evidence, or profile plus all Evidence choice, and baseline restore.

Server enforcement distinguishes user, desktop, connected agent, migration, and internal dispatch principals. Unauthorized principals cannot read, mutate, expand, bind, approve, or restore data outside their grant.

OpenAPI is contract authority. Every public schema change regenerates committed TypeScript contracts and passes `pnpm contracts:check`.

## 6. Conversation and artifact semantics

### 6.1 New user turn

Extend the existing turn-creation transaction in `services/api/jobos_api/conversations.py`:

1. authorize the turn principal;
2. resolve the latest permitted Career Profile head;
3. create or select the immutable authorized snapshot;
4. persist snapshot ID, profile revision, and hash on the turn;
5. commit the turn and binding;
6. load the bounded projection from that exact snapshot;
7. dispatch through the existing `AgentContext` in `services/api/jobos_api/agent_gateway.py`.

The full profile payload is not copied into conversation event or audit JSON.

### 6.2 Running turn, retry, and recovery

- A running logical turn never re-resolves profile context.
- Mid-turn edits apply to the next new user turn.
- Automatic retry, transport recovery, resumed execution, subagent follow-up, and continuation retain the original snapshot.
- A missing, invalid, or unauthorized bound snapshot fails before dispatch.
- An old turn never silently upgrades to a newer snapshot.
- An explicitly requested new generation is a new turn and resolves the latest authorized snapshot.

### 6.3 Complete-profile context selection (future, dormant)

- The user can choose no Career Profile context, selected items/areas, or a
  broader projection within the agent's explicit grant.
- Selection is part of the immutable turn binding alongside profile revision and
  content hash.
- Retries, transport recovery, resumed execution, continuations, and subagents
  reuse the exact original selection.
- Any attempted scope widening fails before dispatch and requires a separately
  authorized operation plus a new turn.
- Accepted user-authored or user-approved content remains eligible for the
  selected projection without Evidence. Autonomous unapproved agent content
  remains proposed whether or not Evidence exists.
- This contract does not activate complete-profile projection; the staging-only
  work-arrangement boundary remains in force until later approved work.

### 6.4 Generated outputs

- Generated artifacts remain immutable after profile edits.
- V1 does not auto-regenerate, label them stale, or prompt regeneration.
- Artifacts record their producing snapshot ID and hash internally for provenance.
- This provenance does not imply a user-facing freshness judgment.

## 7. Agent editing model

Each connected agent has one Settings-controlled mode:

1. **Review every change** — default for every new agent.
2. **Allow direct edits**.

Only the user changes this mode.

### Review every change

- The agent submits one complete proposal with base revision, reason, any available Evidence, and every changed field.
- The UI shows an understandable before/after comparison.
- Approval or rejection is atomic for the exact proposal.
- A stale base or changed payload requires a regenerated proposal.

### Allow direct edits

- Ordinary valid edits apply immediately.
- The UI provides lightweight confirmation and a prominent Undo action.
- Validation, authorization, attribution, history, conflict checks, and Undo remain mandatory.

### Always requires approval

Even for direct-edit agents:

- direct identifier edits, including professional name, email, phone, city/address, legal identity, immigration/document identifiers, and optional demographic disclosures;
- destructive Evidence changes or severed provenance links;
- destructive or loosening qualifier and “never say this” boundaries.

These controls protect factual accuracy and provenance. Contact and identity information is ordinary authorized profile data in v1; it is displayed normally and receives no masking or reveal UI.

## 8. Evidence, privacy, retention, and recovery

### 8.1 Evidence

- Import each source as an immutable managed copy.
- Preserve source hash, captured/imported date, provenance, and opaque ID.
- Structured edits never rewrite imported bytes.
- User-entered or user-approved facts and Accomplishments may exist without Evidence.
- Exact structured facts may import directly when provenance is preserved.
- Extracted, inferred, ambiguous, or conflicting facts remain proposals until reviewed.
- Removing Evidence preserves the status and content of user-authored or user-approved entries; it only changes the availability of that supporting source.
- Ordinary Evidence removal remains reversible and preserves bytes/history.
  **Permanently erase Evidence** is a separately named owner operation with exact
  confirmation; it removes the managed bytes, metadata, source-derived unaccepted
  proposals, and recoverable references/history for that source while retaining
  accepted profile information without the source link.
- Imported content is untrusted input and cannot override higher-level instructions, policy, or tool permissions.

### 8.2 Local data and audit

- Career Profile and Evidence are ordinary local JobOS app data.
- V1 adds no field-level encryption or Keychain-held content-encryption key.
- Connected authorized agents can read the profile fields granted by the established v1 access model.
- Audit events record actor, affected fields, and revision transitions without duplicating full values into separate payloads.

### 8.3 Export and recovery

- V1 creates no automatic app-managed backup.
- Every portable export requires an explicit Evidence inclusion choice: profile-only, profile plus selected Evidence, or profile plus all Evidence.
- Profile-only exports include the current structured Career Profile and provenance metadata but no Source Evidence bytes.
- Selected-Evidence exports include exactly the selected active Source Evidence, hashes, and provenance. All-Evidence export is a separately explicit choice; Source Evidence is never silently bundled because it is linked or present.
- An unavailable historical Evidence link may remain in profile provenance without demoting the accepted item; unavailable bytes are not represented as included files.
- Export excludes prior revision history and agent settings.
- Import restores current data as a new baseline; it cannot reconstruct the old timeline.
- Losing local JobOS data can permanently lose revision history. This is an accepted v1 limitation.
- **Reset Career Profile permanently** is an exact-confirmation owner operation that
  removes current profile data, proposals, Evidence, snapshots, sensitive revision
  and idempotency payloads, and Career Profile audit history. It does not reset jobs,
  documents, generated artifacts, app settings, conversations, or credentials.
- Erasure covers JobOS-managed local storage only. User-created exports, manual
  copies, system/cloud backups, screenshots, and copies already shared externally
  remain outside JobOS control and must be deleted separately.
- Destructive operations journal intent, fail without a success response on partial
  failure, recover pending work at startup, durably sync vault deletion, and use
  SQLite secure-delete/checkpoint/compaction before completion.

## 9. Career Profile experience

### 9.1 Shared product shell

- Mockup 3 supplies the shared shell and default information hierarchy.
- Mockup 2 is the product-taste benchmark.
- Mockup 1 supplies the advanced Evidence workspace.

Select a fact, preference, accomplishment, or source to open a consistent detail drawer. On narrow layouts, the same surface becomes a sheet or full-page drill-in. Essential actions never depend on hover.

The detail surface can show:

- current human-readable value;
- editable form;
- status such as verified, user-stated, needs review, or conflicting;
- provenance and supporting Evidence;
- how JobOS and agents interpret/use the item;
- history and Undo.

### 9.2 Mockup 2 product-taste requirement

Preferences must feel like understandable product behavior rather than configuration:

- use requirement, strong preference, preference, and dealbreaker;
- explain the current interpretation in one normal-language sentence;
- show a concrete pass/filter example when useful;
- state whether it affects research, browsing, matching, agent focus, or alerts;
- warn about contradictory or seemingly impossible rules before save, explain the likely consequence, and let the user intentionally keep them;
- never expose model weights, raw rule syntax, JSON, or plumbing.

### 9.3 Editing and conflicts

- User edits happen in the detail surface with field-local plain-language validation.
- Successful saves update immediately and create a revision.
- During an active turn, confirmation says **“Saved — applies to the next turn.”**
- Stale edits reload current data and let the user intentionally reapply rather than overwriting.
- Conflicts compare current value, proposed value, and sources, with choices to keep current, accept proposed, or preserve both when the domain permits multiple values.
- Evidence conflicts surface at the affected fact/accomplishment with a path into sources.

### 9.4 Provenance and freshness

- Default provenance uses normal language: source name, origin/extraction method, captured/import date, and verification state.
- Hashes, analyzer versions, confidence values, and dense diagnostics are progressively disclosed in deeper Evidence details.
- Use actionable freshness states such as **Source changed**, **Review suggested**, or **Last confirmed [date]**.
- Do not use an opaque context-health percentage. A summary may show actionable counts for pending reviews, conflicts, changed sources, and sources the user chose to review.
- Absent Evidence is not profile debt: no score, task, filter, or generation rule may treat it as a defect.
- Trust modes live in **Settings → Connected agents**, never on an Evidence source.

### 9.5 Required states

- **Loading:** stable skeletons preserve structure; background refresh does not blank existing content.
- **Empty profile:** explain the benefit and offer manual entry or optional source import without presenting Evidence as required.
- **Empty section:** section-specific guidance and one primary action.
- **Importing/analyzing:** per-source progress persists while the user leaves the page; completion/failure remains recoverable.
- **Failure:** preserve entered data when possible, explain plainly, and offer Retry or Save again.
- **Offline:** existing profile remains readable; mutations are disabled unless safe replay is fully guaranteed.
- **Needs review/conflict:** identify exact affected items and next action rather than showing a global alarm.

Exact colors, spacing, animation, iconography, and breakpoints remain implementation details, but they must preserve this hierarchy and product taste.

## 10. Migration and authority cutover

### 10.1 Development boundary

- The first slice runs only in a fresh disposable/staging JobOS profile with synthetic `(FAKE)` data.
- It does not partially migrate Cobi’s live profile.
- A dormant feature flag or initialization state is an activation boundary, not a second authority or synchronization system.

### 10.2 Migration candidate

Build and test the complete Career Profile and all required consumers before authority changes:

- import all three Career Profile areas;
- preserve sparse areas, unknown values, and accepted user-authored or user-approved content with no Evidence;
- preserve exact-fact provenance and immutable original Evidence;
- leave inferred/ambiguous/conflicting entries as proposals;
- migrate required JobOS and Job Hunter consumers to direct JobOS API/MCP projections;
- make legacy write attempts fail closed;
- provide no dual-write, bidirectional sync, calendar shadow period, or generated compatibility-file layer.

Automated schema, migration, API, MCP, consumer-contract, generation, and end-to-end tests form the technical cutover gate.

### 10.3 Live cutover

The authority handoff is one atomic release event. Current sources remain authoritative before it; only JobOS is authoritative after it.

Because rollback is explicitly unrehearsed, the exact tested release candidate, migration evidence, accepted limitation, and fresh explicit user approval must exist immediately before any live cutover action. Planning approval is not cutover approval.

## 11. Ordered delivery plan

### Phase 1 — Prove the tracer bullet

1. [#50 — Build the versioned Career Profile store and authenticated API](https://github.com/cobibean/job-os/issues/50)
2. [#51 — Ship the work-arrangement Career Profile desktop slice](https://github.com/cobibean/job-os/issues/51), blocked by #50.
3. [#52 — Bind every agent turn to an immutable Career Profile snapshot](https://github.com/cobibean/job-os/issues/52), blocked by #50 and executable alongside #51.
4. [#53 — Prove the first Career Profile slice in the packaged app](https://github.com/cobibean/job-os/issues/53), blocked by #51 and #52.

### Phase 2 — Complete the product

5. [#54 — Expand JobOS to the complete Career Profile and Evidence model](https://github.com/cobibean/job-os/issues/54), blocked by #53.
6. [#55 — Add connected-agent edit modes and review flows](https://github.com/cobibean/job-os/issues/55), blocked by #54.
7. [#56 — Complete the three-area Career Profile experience and portable export](https://github.com/cobibean/job-os/issues/56), blocked by #55.

### Phase 3 — Prepare and execute authority handoff

8. [#57 — Build the one-time Career Profile migration and consumer cutover candidate](https://github.com/cobibean/job-os/issues/57), blocked by #56.
9. [#58 — Execute the approved atomic Career Profile cutover and verify it](https://github.com/cobibean/job-os/issues/58), blocked by #57 and fresh explicit user approval.

## 12. Likely implementation surfaces

Exact edits are chosen inside each implementation issue, but the present source seams are:

- `services/api/jobos_api/state_store.py` and/or a focused new Career Profile store module for SQLite persistence and migrations;
- `services/api/jobos_api/app.py` and focused Career Profile route/service modules for authenticated API operations;
- `services/api/jobos_api/conversations.py`, `conversation_store.py`, and `agent_gateway.py` for transactional snapshot binding and dispatch;
- `services/api/tests/` for store, API, authorization, turn-binding, recovery, and migration proofs;
- `services/mcp/jobos_mcp/server.py` and focused MCP tests for thin authorized agent operations;
- generated `packages/contracts/src/generated/` outputs from the OpenAPI authority;
- `apps/desktop/src/renderer/App.tsx`, `WorkspaceBar.tsx`, `SettingsPanel.tsx`, and new focused Career Profile components/hooks/tests;
- packaged acceptance scripts/fixtures following existing synthetic-fixture and public-release rules;
- `docs/public/architecture.md`, `data-privacy.md`, and product contract documentation when the implemented behavior changes the public contract.

## 13. Verification contract

### Every implementation issue

- Add focused failing tests before or with the smallest coherent behavior.
- Exercise authenticated public boundaries, not direct database shortcuts.
- Regenerate/check contracts after schema changes.
- Preserve synthetic-fixture policy and mark committed user-like data `(FAKE)`.
- Run affected API, MCP, renderer, and TypeScript checks.
- Run `pnpm check` and `pnpm contracts:check` before integration.
- Read back persisted/external state after mutations.

### First-slice installed acceptance

Using a packaged app and fresh disposable profile:

1. initialize known work-arrangement value A;
2. edit and persist through desktop/API;
3. restart app/API and verify revision/history;
4. start a turn and verify persisted snapshot A plus captured gateway input A;
5. save value B while the turn runs;
6. prove the active turn, retry, recovery, and continuation remain on A;
7. prove the next new turn receives B;
8. verify projection contains only work-arrangement fields and metadata;
9. verify no legacy profile file changed or influenced dispatch;
10. preserve screenshots and sanitized evidence.

### Full cutover acceptance

- Exact migration and consumer contract gates pass.
- Sparse and zero-Evidence profiles are first-class migration and cutover acceptance cases.
- None, selected, and broader authorized context scopes bind exactly, survive retry/recovery/continuation, and reject unauthorized expansion before dispatch.
- Accepted Evidence-free claims remain usable, and export proves profile-only, selected-Evidence, and explicit all-Evidence choices.
- The complete profile migrates as one authority set.
- Required consumers switch together.
- Legacy writes fail closed.
- Installed UI, imports, edits, proposals, history/Undo, snapshots, export, and agent flows survive restart and real workflow verification.
- Fresh explicit cutover approval is recorded before live mutation.
- On a disposable synthetic candidate, permanently erase one active and one
  ordinarily removed Evidence object, restart, and prove their managed bytes,
  metadata, source references, and sensitive source history cannot be read back.
- On a separate disposable synthetic candidate, permanently reset the complete
  Career Profile, restart, and prove current data, proposals, all Evidence bytes,
  snapshots, revision/idempotency payloads, and sensitive audit history cannot be
  read back from the filesystem or SQLite database.
- Inject a partial destructive-operation failure and prove no success is reported;
  startup recovery must finish the journal or continue failing honestly.
- Record the local-erasure/external-copy limitation in the reviewed #58 evidence.
  These proofs are acceptance prerequisites only and do not activate #58 or replace
  its fresh explicit cutover approval.

## 14. Explicit non-goals

- General-purpose filesystem or agent-memory browsing.
- Editing `SOUL.md`, `AGENTS.md`, skills, policy, transcripts, credentials, browser state, or generated document bytes as Career Profile data.
- Agent-specific fact forks or inheritance in v1.
- Bidirectional synchronization, shadow authority, or compatibility profile files.
- Automatic backup or full revision-history export. Permanent purge exists only
  through the two narrowly scoped, exact-confirmation owner erasure operations.
- Automatic generated-artifact regeneration or stale warnings.
- Activating complete-profile projection, migration, or live authority as part of the semantic-contract work in Issue #69.
- Review Brief behavior beyond consuming this foundation in a later separately specified feature.

## 15. Completion definition

Shared Career Context is implemented only when:

- JobOS is the sole canonical Career Profile authority;
- the complete three-area product works in the installed app;
- authorized connected agents receive bounded immutable projections;
- turns, retries, recovery, and continuations obey snapshot semantics;
- proposals/direct edits, history, Undo, Evidence, provenance, export, and conflicts behave as specified;
- required consumers have switched atomically and legacy writers are fenced;
- the real installed workflows are verified with reviewable evidence.

A passing unit suite alone is not completion. A partially activated live profile is not completion.
