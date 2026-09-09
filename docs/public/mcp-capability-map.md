# JobOS MCP capability map

This is the operating manual for agents connected to the JobOS MCP server. The live MCP `tools/list` response remains the source of truth for tool names, descriptions, and input schemas. This map explains how those tools work together.

Connected clients can also read this document as the MCP resource `jobos://capability-map`.

## Start here

1. Read `jobos://capability-map` before multi-step JobOS work.
2. Inspect the live tool schemas before calling a tool; this map intentionally does not duplicate every argument.
3. Use returned identifiers and revisions exactly. Never guess a `conversation_id`, `job_id`, document ID, Evidence ID, target ID, revision, hash, or browser target.
4. Re-read state after mutations and verify the requested result with the corresponding read tool.
5. Treat unavailable, conflict, review-required, and authorization responses as real product state. Report them plainly instead of bypassing them.

## External clients (ChatGPT and other remote MCP callers)

The OAuth-protected external server exposes **all 45 shared tools plus five external helpers**. It is not a curated subset. Its live schemas omit `turn_id`; `conversation_id` is optional. Internal stdio MCP retains its existing exact active-turn and selected-job checks.

- Ordinary job, Career Profile, and explicit-document operations need no JobOS conversation or active turn. Browser, workspace selection and publication operations transparently resolve a durable external context; responses include its real `conversation_id`.
- `external_session` explicitly resolves that same shared context. It survives API/MCP restarts, starts no internal agent, creates no fake turn, and does not consume any of the five desktop chat slots. The single-owner connector shares its default selection across external clients. Optional explicit IDs must identify an external context, never an internal desktop chat.
- Existing browser and `document_file_*` tools still address the configured desktop. They report `desktop_unavailable` if it is offline. Optional backend capabilities, revision checks and existing Career Profile authority modes remain unchanged.
- Authentication is a separate, opt-in API identity with a unique `JOBOS_EXTERNAL_MCP_TOKEN` (or owner-only `JOBOS_EXTERNAL_MCP_TOKEN_FILE`). An internal MCP credential plus a caller-selected mode/header cannot bypass turn fencing. Public defaults remain loopback. External file handling requires the MCP service and API to share the app-owned artifact root.

### Remote résumé / cover-letter publication

1. Inspect the job and Career Profile. Supply only confirmed career facts.
2. Call `document_generate(job_id, document_key, document_label, markdown)`. Plain text, `#`/`##`/`###` headings and `-` bullets are supported. This creates the source and a matched PDF/DOCX pair server-side in the publication inbox, publishes both by default, then checks their registered hashes and shared source revision. No Mac filesystem access is needed.
3. Inspect the returned `documents` or call `document_list`. Generation also returns opaque `files.md/pdf/docx.file_id` values and checksums. Use `publish=false` to generate without registering the files.
4. To upload existing content instead, use `file_upload(job_id, content, format, encoding)` and pass returned `source_file_id` and `artifact_file_id` to `document_publish` once per format. `encoding` is `text` or `base64`; supported file formats are `txt`, `md`, `json`, `pdf`, `docx`. Use the same source ID for a paired publication.
5. Use `file_read` for inbox files or `artifact_read` for registered artifacts. Both provide bounded chunks and checksums; `artifact_read` revalidates the registered bytes. They do not expose arbitrary local paths or public download URLs.

**Limits:** uploads/generated files are at most 2,000,000 bytes each; reads return at most 65,536 bytes per call. Generation accepts up to 100,000 UTF-8 bytes, uses a bundled font, and explicitly rejects unsupported characters rather than dropping them. This is basic text-based generation, not desktop-template fidelity, rich Markdown, image layout, PDF editing or OCR. PDF/DOCX transfers use base64; text reads are for UTF-8 source files, not binary text extraction. PDF and DOCX have the same content revision, not necessarily identical pagination. Inbox files are immutable and content-addressed; there is no automatic retention/deletion policy. If paired publication stops after the PDF, retry identical generation arguments: deterministic bytes and per-format idempotency keys reuse the first publication.

## Core operating model

- **Conversation scope:** Internal MCP requires the real `conversation_id` and active `turn_id`. External MCP resolves context as described above. One conversation keeps its own selected job and document projection.
- **Job scope:** Inspect or select the relevant job before doing job-specific document work.
- **Optimistic concurrency:** Career Profile and document mutations use revisions or hashes. On conflict, re-read current state, reconcile intent, and retry from the new state.
- **Idempotency:** Reuse the same idempotency key only when retrying the same logical mutation. Use a new key for a new user intent.
- **User authority:** JobOS decides whether a Career Profile edit applies directly or becomes a proposal. The connected agent cannot approve its own proposal, change trust settings, erase Evidence permanently, or reset/restore/delete the profile.
- **Evidence is optional:** User-provided career facts may be recorded without supporting documents. Evidence adds provenance when the user wants it; it is never a prerequisite for a claim or profile record.
- **Publication boundary:** Finished PDF/DOCX publication uses the JobOS-owned inbox. Internal agents write into the directory returned by `document_publication_prepare`; external callers use opaque file IDs and server-side generation.

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

## Workflow: ingest a sourced job without the JobOS browser

1. Gather the listing with your own research tools or user-supplied source text. `job_ingest` saves supplied fields; it does not fetch URLs or need a live desktop browser.
2. Call `job_ingest` with required `company_name`, `title`, `canonical_url`, `location_text`, `description_text`, `application_url`, `listing_source_url`, and `listing_capture_method` (for example `web_extract`, `api`, or `manual`). Text must be nonblank; URLs must be HTTP(S) without embedded credentials. Do not invent missing facts. Supply `full_listing_text` for the source listing and keep commentary in optional `analysis_text`.
3. Include truthful provenance: optional ISO-8601 `listing_captured_at` / `listing_verified_at`, `listing_evidence` JSON object, and 64-character hexadecimal `listing_sha256`. `listing_completeness` defaults to `unknown`; use `partial` for excerpts. Only claim `complete` or a verification timestamp when the source coverage supports it. Omitted capture time records ingestion time, not evidence of a historical fetch. Storage may classify unverified source text as `partial`; it does not become verified merely because it was saved.
4. Read returned `job.job_id` and `created`. The shared `POST /v1/jobs` validation, canonical deduplication and idempotency path is used, with `ingestion_source=external`. Reuse `idempotency_key` only for an identical retry; replay returns the original result, so inspect for current state. New-key reimports refresh the canonical record under provider merge rules without resetting status or replacing a complete listing with a partial one. URL variants recognized by the provider deduplicate; unrelated URLs are not guaranteed to match.
5. If the user wants to consider it, call `job_update_status(job_id, target_status="shortlisted")`, then `job_inspect(job_id)` and verify `status_group="Considering"`. Ingest itself neither shortlists nor selects a job. External callers need no conversation, active turn, browser tab or `external_session` for this flow; internal callers retain their normal scope arguments.

The legacy `job_create_from_browser` tool and its required arguments remain unchanged. Direct API callers that omit `ingestion_source` retain browser attribution. The optional JobHunter adapter records external ingest separately from browser captures and preserves existing source identity when deduplicating. Its existing quality gate additionally requires listing-like text, capture/verification timestamps and evidence with `schema_version=1`, `coverage="complete"`, `end_of_listing_seen=true`, and `verified_text_sha256` matching the full listing before retaining `complete`. Pass those only when actually observed; inspect the returned completeness rather than assuming a caller claim is accepted.

## Workflow: inspect and save a job from the browser

1. Use `browser_tabs_inspect` to identify the intended live tab.
2. Use `browser_snapshot` and its pagination fields until the required listing content is captured.
3. Create or deduplicate the canonical job with `job_create_from_browser`.
4. Link the live tab using `browser_tab_associate`.
5. Select it for the current conversation with `job_select` when subsequent work should use that job.
6. Verify with `job_inspect` or `job_list`.

## Internal workflow: create and publish a résumé or cover letter

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
| `job_ingest` | Saves sourced listing fields and provenance without a JobOS browser; preserves duplicate status. |
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
