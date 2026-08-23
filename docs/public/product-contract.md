# JobOS Product Contract

## Product promise

JobOS is a local-first, persistent workbench for managing opportunities, browsing, creating and reviewing documents, and collaborating with an optional agent. A user should be able to return to the coherent workspace they left without depending on a private backend.

## Primary workbench

The desktop workbench has four structural areas:

1. a compact global workspace bar;
2. job navigation;
3. a flexible Browse, Research, or Review center surface;
4. continuous agent chat when an agent is configured.

Research emphasizes the browser, Review emphasizes the document, and Agent Focus emphasizes conversation. Primary panels remain resizable, collapsible, recoverable, keyboard operable, and persistent. Reset Layout restores presentation defaults; it never deletes jobs, tabs, documents, chat, or other user data.

## JobOS Profiles

The top bar always names the active **JobOS Profile**, which is distinct from the
Career Profile feature inside it. A user may list, create, rename, and switch
profiles. Create has one explicit **Create and switch** action. A new profile is a
blank workspace: jobs, Career Profile data and Evidence, documents, chats,
browser data, layout, theme, avatar, grants, and sessions do not carry over.

Agent connection setup remains available across profiles, while agent access is
profile-local and starts in review mode with no scopes. Switching saves safe
workspace state, blocks on unsaved DOCX work, active/recovering agent turns, or a
browser download, restarts JobOS, and opens only after exact target identity is
confirmed. Failure rolls back without changing or deleting the prior workspace.

## Jobs and browser independence

Exactly one job may be active at a time. Selection changes conversational and organizational context, not browser ownership. Selecting another job must not close tabs, replace pages, end authenticated sessions, discard forms, or create a new conversation.

Manual ordering persists. Calculated ordering modes own their sort and do not pretend to support manual placement. Status mutations from the interface and optional adapters go through the same application boundary and produce the same durable state.

The built-in browser supports ordinary websites and multiple associated or unassociated tabs. Browser sessions and live views remain owned by Electron main. Callers cannot supply arbitrary JavaScript, selectors, or raw IPC.

## Documents and artifacts

JobOS supports local document creation, editing, autosave, reopening, snapshots, restore, preview, export, publication, and download. Local artifact bytes belong to the configured JobOS data directory. Optional render or publication gateways are capabilities, not prerequisites for local document work.

A failed render or publication must preserve the last successful local artifact and return a safe, actionable error. The interface makes synthetic demo documents and jobs unmistakable.

## Continuity

JobOS persists and restores the coherent parts of:

- active job and ordering;
- browser tabs and selected tab;
- selected center surface and document;
- editable document state and snapshots;
- layout preset, panel widths, order, and collapsed state;
- conversation and chronological activity;
- unfinished or waiting work where supported.

State persists after meaningful interaction rather than only on a clean shutdown. A partial restore failure does not erase unrelated recoverable state.

## Human and agent parity

The JobOS API is the product boundary. The interface and MCP adapter translate actions through that boundary rather than mutating databases or files directly. Human and agent actions produce the same durable state and retain their origin where useful.

Approval policies may restrict consequential external actions. Automatic application submission is outside the default capability set.

## Career Profile agency and cutover candidate

The future complete Career Profile treats Evidence as optional support, not permission or proof a user must supply. Accepted user-authored and user-approved information remains usable with zero Evidence. Missing Evidence is not a health deficit, task, filter, or generation gate; conflicts and changed sources may instead produce specific advisory review states.

The user chooses no Career Profile context, selected items or areas, or a broader explicitly authorized projection. A turn keeps that exact scope through retry, recovery, and continuation, and unauthorized expansion fails closed. The authenticated API/MCP consumer projection remains dormant while authority is `staging` and becomes eligible only after the owner-only exact-confirmation cutover operation. Authenticated secondary devices remain excluded from Career Profile unless they are separately and explicitly registered as owner devices; ordinary remote-device authorization does not grant Career Profile access.

Portable Career Profile export will require an explicit choice between profile only, profile plus selected Evidence, and profile plus all Evidence. JobOS never silently bundles Source Evidence. These semantics are repository-owned acceptance requirements and do not activate complete-profile projection, migration, or live authority.

Issue #57 also supplies a journaled, idempotent synthetic migration candidate. Code-owned mappings accept only exact structured facts; inference, ambiguity, conflicts, and conflicting duplicate assertions remain review-only Proposals. Sparse and zero-Evidence profiles are valid candidates. No live authority changes as part of candidate construction.

See the [Career Profile context and export semantic contract](../implementation/career-profile-context-and-export-contract.md) and [migration inventory](../implementation/career-profile-migration-inventory.md) for the detailed boundaries.

See the [agent capability parity matrix](capability-parity.md) for the current UI, API, and MCP mapping.

## Essential states

- **No jobs:** explain the empty state while preserving browser and general workbench access.
- **No selection:** keep general browsing and chat available; explain which actions require a job.
- **Agent not configured/offline:** local work remains usable and the limitation is explicit.
- **Desktop or renderer unavailable:** return a stable capability state and retry guidance.
- **Artifact provider unavailable:** preserve local artifacts and identify only the unavailable optional operation.
- **Document rendering:** keep the last successful artifact visible where possible.
- **Browser/network failure:** offer retry without clearing tabs or workspace state.
- **Partial restore failure:** open recoverable surfaces and explain the affected one.

## Visual and interaction character

JobOS is a focused dark desktop productivity app: precise, calm, compact, and readable rather than an admin dashboard. Thin dividers define regions; lists use rows rather than card piles; violet is reserved for selection and high-value emphasis; motion explains state changes and respects reduced-motion preferences.

## Accessibility

- All controls are keyboard reachable.
- Resize and reorder have keyboard alternatives.
- Icon-only controls have names and tooltips.
- Focus is visible and contrast is sufficient.
- Status is not communicated by color alone.
- Collapsed and expanded states are semantic.
- Activity updates do not overwhelm assistive technology.
- Interface zoom does not make primary controls unreachable.

## Acceptance scenarios

1. **Local first run:** initialize an empty profile, see exactly one synthetic demo job, mutate it, restart without duplication, intentionally delete it without silent reseeding, and restore it only through confirmed reset.
2. **Continuity:** resize panels, select a job, open tabs, edit a document, then restart into the coherent workspace.
3. **Browser independence:** changing jobs leaves unrelated tabs and in-progress sessions intact.
4. **Document trust:** create, edit, autosave, snapshot, export, publish, download, and reopen a local synthetic document with bytes preserved across restart.
5. **Shared state:** user and optional agent status changes appear through the same API-backed interface.
6. **Failure honesty:** unavailable optional providers produce safe versioned errors while local jobs, browsing, and documents remain usable.

## Non-goals for the source-first alpha

- generic CRM or arbitrary dashboard construction;
- automatic application submission;
- required private JobHunter, Hermes, Tailscale, or hosted services;
- signed/notarized public binaries before the source release gates are complete.
