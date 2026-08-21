# Career Profile user-agency audit

## Product decision

JobOS is a user-owned, open-source career workspace. The user—not JobOS, an agent, or the presence of a supporting document—decides what they may record in their Career Profile and what they may include in a resume or cover letter.

Most ordinary career statements will not have formal Source Evidence. A statement such as “Increased sales by 50%” is valid user-provided context without requiring a spreadsheet, report, or uploaded document.

Evidence is optional provenance. It can preserve an imported source, explain where information came from, or help assess uncertainty. It never grants permission to store, accept, use, export, or publish user-authored or user-approved content.

## Governing rule

A rule may block an operation only when needed for:

- structural validity required to store or exchange a value safely;
- authentication, authorization, or exact user-intent binding for sensitive agent-mediated actions;
- optimistic concurrency and idempotency;
- security or privacy boundaries;
- storage, history, or data-integrity guarantees;
- preventing an agent from silently inventing or autonomously changing user information.

Everything else is advisory. JobOS may explain consequences, surface uncertainty, preserve provenance, show conflicts, or suggest review. It must still let the user make the final choice.

Actor semantics must remain precise:

- **Direct user edit:** accepted without Evidence, subject to technical protections.
- **Ordinary explicit user instruction executed by an agent:** may carry user authority when JobOS has authenticated, exact-payload binding to that instruction.
- **Sensitive agent-mediated action:** identifier changes, destructive Evidence operations, and loosened reuse/privacy boundaries require exact-payload confirmation or an equivalent explicit grant. An agent merely claiming the user asked is insufficient.
- **Autonomous agent inference or generation:** remains a proposal unless the configured trust mode expressly permits it.

## Scope and method

This audit reviewed the Shared Career Context plan, complete API model, first-slice desktop client, document workflow, agent guidance, public product language, tests, and GitHub issues defining remaining Career Profile work.

Each guardrail was tested against five questions:

1. What user or system harm does it prevent?
2. Is blocking necessary, or would a warning preserve safety?
3. Does it distinguish autonomous agent behavior from an authenticated user choice?
4. Does it accidentally treat Evidence as permission?
5. Can users preserve uncertainty, contradictions, partial information, and custom context?

## A. Current implementation bugs

These rules exist in current code and require direct remediation.

### A1. Work-arrangement preferences are coerced and rejected

**Where**

- `apps/desktop/src/main/careerProfile.ts`, `validateValue()`
- `apps/desktop/src/main/careerProfile.test.ts`
- `apps/desktop/src/renderer/components/CareerProfileWorkspace.tsx`
- renderer Career Profile tests

**Current behavior**

The desktop rejects `mode="flexible"` with strengths other than `preference`. The renderer also resets Flexible to `preference`, disables the strength control, and prevents the user from preserving another choice.

**Correction**

Round-trip every structurally valid mode/strength combination unchanged. Ambiguous combinations may show an advisory explanation but must save. Cover both main-process validation and renderer behavior with regression tests.

### A2. The complete schema forces initial vocabulary and details

**Where**

- `services/api/jobos_api/career_profile_complete.py`, typed `ProfileValue` union and required fields

**Current behavior**

The model has no bounded custom/freeform record. Several kinds require details users may not know or wish to provide, including paired education/employment/project fields and non-empty target-role or location lists.

**Correction**

Add a typed custom/freeform record with a user-defined label and bounded text. Permit meaningful partial records without forcing invented details. Keep real identifier, encoding, schema-shape, and resource limits.

### A3. Desktop and server disagree on additional-context length

**Where**

- desktop Career Profile validation and UI input constraints
- complete API model text bounds

**Current behavior**

The desktop limits additional context to 500 characters while the server contract permits 1,000. Valid server-representable user context can be blocked or truncated at the product surface.

**Correction**

Use one documented cross-layer resource limit and prove exact round-trip behavior at that limit. The limit may remain bounded for storage and usability, but it must not differ silently across layers.

### A4. Inactive Evidence can block later edits to accepted content

**Where**

- `services/api/jobos_api/career_profile_complete.py`, Evidence-link validation during item mutation

**Current behavior**

Removing Evidence preserves the accepted item, but a later edit rejects the existing historical Evidence ID because that source is inactive.

**Correction**

Allow already-linked inactive Evidence IDs to round-trip as unavailable historical provenance. Require active Evidence only when adding a new link. Evidence availability must not become permission to edit user-owned content.

### A5. Payload-declared `exact` imports can self-authorize acceptance

**Where**

- `services/api/jobos_api/career_profile_complete.py`, extraction classification
- `services/api/tests/test_career_profile_complete_model.py`

**Current behavior**

A request marked `assessment="exact"` becomes accepted even when the provenance method is `agent_import`; the caller supplies the assessment that authorizes acceptance. Imported proposals also risk losing their original provenance when accepted.

**Correction**

Derive automatic acceptance only from a narrowly server-defined deterministic mapping and authenticated actor context—not a caller-selected label. Model-created, transformed, uncertain, novel, or conflicting content remains proposed. Exact-payload approval/rejection must preserve original import/agent provenance and Evidence links.

### A6. Omitted fields silently create user meaning

**Where**

- `services/api/jobos_api/career_profile_complete.py`, defaults for current employment, currency, compensation period, target-role strength, and relocation

**Current behavior**

Omitted values become assertions such as `current=false`, `USD`, yearly compensation, `preference`, or `consider` relocation.

**Correction**

Represent omitted semantic values as unknown. UI recommendations may remain unpersisted suggestions until selected. Regression tests must prove omission does not invent meaning.

### A7. Evidence metadata forces fabricated precision

**Where**

- `services/api/jobos_api/career_profile_complete.py`, `EvidenceProvenance.captured_at`

**Current behavior**

Every imported source requires a complete timezone-aware source-capture timestamp, even when the user only knows a date or knows nothing about when it was captured.

**Correction**

Make source capture time optional or precision-aware while retaining an exact system-generated import timestamp.

### A8. User-owned data lacks an explicit erasure path

**Where**

- complete Career Profile and Evidence storage operations
- `docs/public/data-privacy.md`

**Current behavior**

Ordinary removal preserves history and Source Evidence, but the owner has no separately named permanent-delete operation for one source or a full local profile reset.

**Correction**

Keep normal edit/remove reversible and auditable. Add precisely scoped, confirmed erasure for one Evidence object and for a full local Career Profile reset. Synthetic restart, filesystem, and database readback must prove managed local data is gone; documentation must state that user-created exports or external backups are outside JobOS control.

### A9. Document authorship and approval authority are inconsistent

**Where**

- `services/api/jobos_api/document_operations.py`
- editable-document API and MCP operations
- desktop editable-document schema and editor
- artifact approval routes and `document_approve`

**Current behavior**

Agent operations can become active content without honest agent-suggestion authorship, while direct user content can encounter unresolved-suggestion export/publication gates. A connected agent can also approve its own generated résumé, and equivalent approval semantics do not cover every logical resume/cover-letter revision and representation.

User-controlled template locks are **not** a defect: users can lock and unlock selected blocks, and those locks validly protect their choices from agent operations.

**Correction**

Represent autonomous agent insertions, rewrites, and deletions as agent-authored suggestions. Direct user edits are accepted content. Preserve user-controlled locks. Allow the user to explicitly publish/export the deterministic current state after a clear unresolved-suggestion warning. Reserve `user approved` for authenticated user action or an exact explicit grant, and provide coherent approval across intended resume and cover-letter revisions while preserving render/hash/integrity gates.

## B. Required corrections to Issues #55–#58

These are not current staging defects. They are acceptance-contract corrections required before the remaining features are built.

### B1. Trust modes must govern autonomy, not merely tool use

Issue #55 currently describes approval requirements without a complete source-of-intent model.

**Required contract**

- Distinguish direct user edits, authenticated exact user instructions, deterministic imports, autonomous agent changes, and user decisions on exact proposals.
- Ordinary explicit user-directed changes may proceed when intent is cryptographically or transactionally bound to the exact payload.
- Identifier changes, destructive Evidence actions, and loosened privacy/reuse boundaries still require exact confirmation or an explicit grant.
- Proposal Evidence is optional.
- An agent cannot bypass review merely by asserting the user requested the change.

### B2. Evidence-neutral health, selective context, and selective export

The plan and Issue #56 can currently be read to count “missing Evidence” as profile debt and to include current Source Evidence in portable export by default.

**Required contract**

- No health score, task, filter, or generation rule treats absent Evidence as a profile defect.
- Users may share no Career Profile context, selected items/areas, or a broader authorized projection with an agent.
- Accepted user-authored or user-approved content remains usable without Evidence.
- Export supports profile-only, profile-plus-selected-Evidence, and an explicit all-Evidence option; Source Evidence is never silently bundled.
- Sparse profiles and zero-Evidence profiles are first-class acceptance fixtures.

### B3. Migration must preserve agency and uncertainty

Issue #57 must not convert missing data into defaults or treat imported text as accepted merely because a caller labels it exact.

**Required contract**

- Preserve sparse areas and unknown values.
- Keep deterministic mappings separate from agent inference.
- Preserve accepted user-authored content without requiring Evidence.
- Preserve imported proposal provenance through user decisions.
- Never fabricate dates, currency, work status, preference strength, relocation intent, or other semantic defaults.

### B4. Cutover proof must include user-agency behavior

Issue #58 retains its fresh explicit approval gate.

**Required contract**

Before any approved live cutover, prove zero-Evidence and sparse profiles, context-scope controls, accepted unsupported claims, historical inactive-Evidence links, explicit local erasure, and rollback/readback behavior. This audit does not authorize a cutover.

## C. Specification status and later or dormant risks

These are specification or prompt concerns, not current blocking product behavior. The Review Brief repository terminology is corrected; the remaining risks should be corrected before the relevant feature becomes active.

### C1. Review Brief terminology corrected before implementation

`docs/ideas.md` now defines accepted user-authored or user-approved Career Profile information as valid matching context with provenance `user stated`. Supporting Evidence is optional, absent Evidence is not a qualification gap or blocker, and an unsupported agent inference remains a proposal until the user approves it. The proposed Issue #30 body must use the same contract before Review Brief implementation begins.

**Status**

Repository specification corrected by Issue #73. No Review Brief implementation was added.

### C2. Conflict preservation needs future schema guidance

The plan says “preserve both” when a domain permits multiple values, but the audit found no concrete current value rejected solely by that phrase.

**Future correction**

Make parallel or contradictory values representable by default when structurally possible. Document narrow uniqueness invariants explicitly instead of implying a universal canonical truth.

### C3. Dormant document-editor prompt needs an authorship carve-out before activation

`AGENT_SYSTEM_PROMPT` in `packages/docx-editor-core/src/ai/protocol.ts` currently has no runtime consumer. Its anti-fabrication instruction is valid, but a future consumer could interpret it as requiring proof for user-supplied claims.

**Future correction**

Before activating the prompt, distinguish autonomous invention from explicit user content. The agent may warn but must not demand proof, omit, or rewrite an explicit user claim solely because Evidence is absent.

### C4. Complete profile projection must preserve typed user boundaries

The complete profile is not yet projected to agents; this belongs to #55–#57 rather than the current staging tracer. When projection is implemented, the existing “untrusted data” wrapper must not cause user-approved qualifiers and forbidden-use metadata to be discarded.

**Future correction**

Keep profile text non-executable and unable to override tools or policy, while enforcing typed user-approved reuse constraints as data-handling boundaries. Preserve the exact selected context scope on retries and fail closed on unauthorized expansion.

## Guardrails that remain strict

This audit does **not** weaken:

- authentication, authorization, exact grants, and purpose-bounded projections;
- exact user-intent binding for sensitive agent-mediated operations;
- optimistic concurrency, idempotency, and stale-write rejection;
- immutable ordinary revision history and compensating Undo;
- immutable, hash-verified Source Evidence while retained;
- file-path, symlink, media-type, size, encoding, and content-header protections;
- imported profile text being untrusted as executable instructions;
- autonomous agent-inferred, ambiguous, novel, transformed, or conflicting information remaining proposed unless the configured trust mode permits it or the user accepts the exact proposal;
- deterministic turn snapshots and retry/recovery binding;
- user-controlled document locks that protect selected content from agents;
- the fresh approval gate before Issue #58 changes live authority.

These rules protect user control or technical integrity. They do not police what the user is allowed to say.

## Remediation workstreams

Seven independently tracked workstreams can proceed in parallel:

1. **Schema freedom and advisory preferences** — A1, A2, A3, A6, and A7.
2. **Actor-aware trust, imports, and proposal decisions** — A4, A5, and B1.
3. **Selectable context, Evidence-neutral UX, and selective export** — B2, B3, B4, plus semantic policy fixtures.
4. **Document authorship, suggestions, export, and approval** — A9 while preserving user-controlled locks.
5. **Agent prompt semantics and typed reuse boundaries** — C3 and C4 before activation.
6. **User-owned retention and erasure** — A8 and B4 proof requirements.
7. **Review Brief terminology** — C1 specification correction only.

C2 is cross-cutting future schema guidance and belongs in the schema/product-contract work rather than a separate implementation issue.

Every workstream must include focused regression tests and update the relevant plan, contract, product copy, or GitHub acceptance criteria. A future capability is corrected by changing its acceptance contract, not by falsely labeling it a current defect or prematurely activating it.

## Boundary

This audit and every remediation use synthetic data. They do not activate, import, migrate, erase, or cut over a live Career Profile. Issue #58 still requires fresh explicit approval before any live authority change.
