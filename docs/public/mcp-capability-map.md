# JobOS MCP capability map

This is the operating manual for agents connected to the JobOS MCP server. The live MCP `tools/list` response remains the source of truth for tool names, descriptions, and input schemas. This map explains how those tools work together.

Connected clients can also read this document as the MCP resource `jobos://capability-map`.

## Start here

1. Read `jobos://capability-map` before multi-step JobOS work.
2. Inspect the live tool schemas before calling a tool; this map intentionally does not duplicate every argument.
3. Use returned identifiers and revisions exactly. Never guess a `conversation_id`, `job_id`, document ID, Evidence ID, target ID, revision, hash, or browser target.
4. Re-read state after mutations and verify the requested result with the corresponding read tool.
5. Treat unavailable, conflict, review-required, and authorization responses as real product state. Report them plainly instead of bypassing them.

## Core operating model

- **Conversation scope:** Browser, workspace, and most document operations require the chat's real `conversation_id`. One conversation keeps its own selected job and document projection.
- **Job scope:** Inspect or select the relevant job before doing job-specific document work.
- **Optimistic concurrency:** Career Profile and document mutations use revisions or hashes. On conflict, re-read current state, reconcile intent, and retry from the new state.
- **Idempotency:** Reuse the same idempotency key only when retrying the same logical mutation. Use a new key for a new user intent.
- **User authority:** JobOS decides whether a Career Profile edit applies directly or becomes a proposal. The connected agent cannot approve its own proposal, change trust settings, erase Evidence permanently, or reset/restore/delete the profile.
- **Evidence is optional:** User-provided career facts may be recorded without supporting documents. Evidence adds provenance when the user wants it; it is never a prerequisite for a claim or profile record.
- **Publication boundary:** Finished PDF/DOCX publication must use the JobOS-owned directory returned by `document_publication_prepare`.

## Workflow: build a Career Profile through conversation

Use this workflow for a blank profile, a sparse profile, or incremental profile improvement.

1. **Orient:** Call `career_profile_get` to learn the authorized projection and current profile revision.
2. **Listen:** Ask for one coherent slice of the user's story in plain language—for example identity and positioning, one role, one project, or what they want next. Do not require a résumé or proof.
3. **Check for overlap:** Call `career_profile_search` using the people, employers, skills, projects, or preferences the user mentioned. This prevents accidental duplicates without loading the entire profile repeatedly.
4. **Structure the facts:** Translate only what the user actually supplied into typed Career Profile items. Keep uncertainty visible; ask a focused follow-up when a required fact is missing.
5. **Save coherently:** Use `career_profile_edit_batch` when the conversation produced multiple related records. Use `career_profile_edit` for one isolated change. Pass the current expected profile revision and optional Evidence IDs.
6. **Verify authority outcome:** Inspect the mutation response, then call `career_profile_changes_list` when proposals or prior agent changes matter. Tell the user which changes applied and which await review.
7. **Continue incrementally:** Re-read or search before the next batch because an accepted edit, another device, or another agent may have advanced the profile revision.

### Optional supporting-document branch

Take this branch only when the user supplies a résumé, portfolio, citation, or supporting document and wants it retained as provenance.

1. Import the user-supplied file with `career_profile_evidence_import`.
2. Use `career_profile_evidence_inspect` to read only the bounded content needed for the current task.
3. Discuss extracted facts with the user rather than silently treating model inference as confirmed biography.
4. Create or update profile items with `career_profile_edit` or `career_profile_edit_batch`; attach the returned Evidence ID where useful.
5. Verify whether the edits applied or became proposals.

**Completion criterion:** the requested profile slice is represented in the current profile or visibly awaiting user review, and the agent has told the user which outcome occurred.

## Workflow: inspect and save a job from the browser

1. Use `browser_tabs_inspect` to identify the intended live tab.
2. Use `browser_snapshot` and its pagination fields until the required listing content is captured.
3. Create or deduplicate the canonical job with `job_create_from_browser`.
4. Link the live tab using `browser_tab_associate`.
5. Select it for the current conversation with `job_select` when subsequent work should use that job.
6. Verify with `job_inspect` or `job_list`.

## Workflow: create and publish a résumé or cover letter

1. Confirm the conversation's active job with `job_inspect` and `job_select` as needed.
2. Inspect relevant Career Profile context with `career_profile_get` or `career_profile_search`.
3. Call `document_publication_prepare` **before generating files**.
4. Write the source and every promised PDF/DOCX into the returned `publication_directory`.
5. Call `document_publish` once for each promised format, using the same source revision for paired outputs.
6. Call `document_list` and confirm every promised format before claiming completion.

**Completion criterion:** every promised artifact appears in `document_list` for the intended job and conversation.

## Workflow: inspect and edit an existing document

Choose one editing surface and keep its concurrency token:

- Use `document_draft_get` → `document_draft_apply` for bounded semantic draft operations, then `document_draft_snapshot` for a durable manual checkpoint.
- Use `document_file_inspect` → `document_file_apply` for typed operations against the canonical DOCX using the returned expected hash.

After editing, re-inspect or list the document. On a revision/hash conflict, read the new state and reconcile instead of replaying stale operations blindly.

## Workflow: browser interaction

1. Inspect tabs with `browser_tabs_inspect` and select or create the intended tab.
2. Navigate with `browser_navigate` when needed.
3. Read a fresh `browser_snapshot` before clicking or typing.
4. Use only opaque targets from the latest snapshot with `browser_click` or `browser_type`.
5. Take another snapshot after each state-changing interaction.

Snapshot targets are short-lived page references, not durable selectors.

## Tool catalog

### Jobs

| Tool | What it does |
|---|---|
| `job_list` | Lists jobs using JobOS filtering and ordering. |
| `job_inspect` | Inspects one normalized JobOS job record. |
| `job_create_from_browser` | Saves a listing inspected from the live JobOS browser through canonical ingest. |
| `job_select` | Selects the current conversation's active job context. |
| `job_reorder` | Replaces the complete manual job order. |
| `job_update_status` | Changes a job status through the shared transition command. |
| `job_update_description` | Replaces a saved job's canonical full listing and refreshes its durable packet. |

### Career Profile

| Tool | What it does |
|---|---|
| `career_profile_get` | Reads the exact Career Profile projection authorized for the connected agent. |
| `career_profile_search` | Searches authorized Profile items and Evidence without loading the whole profile. |
| `career_profile_edit` | Creates, updates, or removes one item under the user's current review mode. |
| `career_profile_edit_batch` | Atomically applies or proposes several related edits under one expected revision. |
| `career_profile_changes_list` | Lists this agent's proposals and directly applied profile revisions. |
| `career_profile_evidence_import` | Optionally imports immutable user-supplied Evidence into the JobOS vault. |
| `career_profile_evidence_inspect` | Reads a bounded segment of Evidence already authorized for this agent. |

### Workspace

| Tool | What it does |
|---|---|
| `workspace_inspect` | Inspects global layout merged with this conversation's job context. |
| `workspace_update` | Updates global layout and this conversation's document projection. |

### Documents

| Tool | What it does |
|---|---|
| `document_list` | Lists trusted registered artifacts for a job. |
| `document_draft_get` | Reads a bounded semantic outline for one editable job document. |
| `document_draft_apply` | Atomically applies allowlisted semantic document operations. |
| `document_draft_snapshot` | Creates a durable manual checkpoint for an editable document. |
| `document_refresh` | Refreshes a job's trusted artifact manifest. |
| `document_render` | Starts the fixed PDF résumé render command for a job source. |
| `document_register` | Registers an opaque facade artifact reference through JobOS. |
| `document_publication_prepare` | Prepares JobOS's supported publication inbox for a conversation and job. |
| `document_publish` | Publishes one finished PDF or DOCX from the prepared inbox. |
| `document_select` | Selects a registered artifact in the shared document workspace. |
| `document_file_inspect` | Inspects canonical DOCX hash, capabilities, and bounded block context. |
| `document_file_apply` | Applies typed operations to the canonical DOCX with a hash conflict check. |

### Browser

| Tool | What it does |
|---|---|
| `browser_tabs_inspect` | Inspects bounded metadata for live desktop browser tabs. |
| `browser_tab_create` | Creates a live tab for an ordinary HTTP(S) URL. |
| `browser_tab_select` | Selects a live browser tab. |
| `browser_tab_associate` | Links a live browser tab to its canonical JobOS job. |
| `browser_tab_close` | Closes a live browser tab. |
| `browser_tabs_reorder` | Replaces the complete live browser-tab order. |
| `browser_navigate` | Navigates a live tab to an ordinary HTTP(S) URL. |
| `browser_back` | Goes back in a live tab. |
| `browser_forward` | Goes forward in a live tab. |
| `browser_reload` | Reloads a live tab. |
| `browser_stop` | Stops loading a live tab. |
| `browser_snapshot` | Reads a bounded page-text segment and returns opaque interaction targets. |
| `browser_click` | Clicks an opaque target from the latest semantic snapshot. |
| `browser_type` | Types bounded text into an opaque snapshot target. |
| `browser_scroll` | Scrolls a live tab by a bounded amount. |

### Agent activity

| Tool | What it does |
|---|---|
| `activity_report` | Appends one concise agent-origin action to the JobOS chronology. |

## Capability boundaries

The MCP surface deliberately omits user-authority operations such as accepting or rejecting Career Profile proposals, changing agent trust, undoing profile history, permanently erasing Evidence, and exporting, restoring, resetting, or deleting the whole profile. Those decisions stay in JobOS's user-facing controls.

A tool being present does not guarantee the current runtime grants it useful data. JobOS may return a truthful unavailable or authorization result when the connected agent, device, conversation, job, or profile projection lacks access.

## Keeping this map current

The registered MCP server and its live `tools/list` response are canonical. Repository tests compare this catalog with the registered tools so additions and removals cannot silently drift from this map.
