# JobOS Agent + Multi-Document Incident Root-Cause Report

Date: 2026-07-24

Status: root causes identified; no fixes applied in this investigation

Repository inspected: `d1775d2` on `main`; the installed app exhibited the incident, but its exact embedded source commit is not exposed in runtime metadata.

## User-visible bugs

### BUG-1 — Agent starts offline after installing/restarting JobOS

**Observed behavior**

The Agent Chat surface showed **Agent offline** on both the Mac mini and the packaged MacBook build. It recovered only after starting a **New session** and sending a message.

**Root cause: confirmed**

JobOS performs only a one-shot Hermes gateway connection during API startup:

- `ConversationService.start()` suppresses any gateway startup exception and continues running (`services/api/jobos_api/conversations.py:72-76`).
- The observed JobOS API process started at **20:59:28**, while the Hermes dashboard did not start until **21:09:20**.
- Once that first connection attempt fails, JobOS has no background retry loop. It remains offline until a user action forces another gateway operation.
- Existing-session recovery also failed repeatedly with `Hermes session isolation could not be verified`, as recorded in `~/Library/Application Support/JobOS/logs/api.error.log`.
- **New session** clears the stored attachment/recovery state, and the next send forces a fresh `session.create`, explaining why that exact workaround recovered the agent.

**Contributing lifecycle defect: confirmed**

The gateway event consumer can die permanently:

- `_consume_gateway_events()` catches every non-cancellation exception and silently returns (`services/api/jobos_api/conversations.py:385-460`).
- There is no supervisor/restart loop.
- `reset()` happens to replace that task (`conversations.py:181-192`), so **New session** can revive event delivery even though it is presented as a conversation action rather than a runtime repair action.

**Disposition**

This is not a renderer-only status bug. The renderer is faithfully displaying stale/offline backend state caused by a brittle gateway lifecycle.

---

### BUG-2 — Resume exists in the document viewer, but the cover letter cannot be cycled to

**Observed behavior**

The agent said it created a resume and cover letter. The resume appeared, but the document switcher acted as if no cover letter existed.

**Root cause A: confirmed — the first turn created a cover-letter source file but did not publish/register a cover-letter artifact**

The viewer intentionally renders only rows in JobOS's `document_artifacts` registry; arbitrary files in the Job Hunter workspace are not viewer documents.

Runtime chronology for job `f9d7558efa7faf713cbaa0e6`:

- The long JobOS turn began at **21:46:23**.
- It invoked `mcp__jobos__document_render` once at **21:53:11**. That registered/rendered the resume.
- It approved and selected a document at **21:54:40–21:54:45**, then claimed completion at **21:55:51**.
- The cover letter was not registered in `document_artifacts` until a later follow-up invoked `mcp__jobos__document_refresh` at **21:58:10**.

Therefore the original UI was correct about the registry it had: at the time the agent claimed both documents were ready, only the resume was a registered viewer artifact.

The integration makes this easy to get wrong: the embedded JobOS MCP exposes `document_list`, `document_refresh`, `document_render`, and `document_register`, but not the Job Hunter `publish-artifact` operation that turns an arbitrary newly created file into a manifest-backed artifact. `document_register` can only import something already published in that manifest. The agent therefore fell through to a fragile split workflow: create files in the workspace, render/register the resume, then assume the cover letter was viewer-ready.

**Root cause B: confirmed — the live UI refresh contract listens only for `document.render`**

- `AgentPanel` calls `onArtifactRendered` only for completed activity whose `detail.command === 'document.render'` (`apps/desktop/src/renderer/components/AgentPanel.tsx:132-149`).
- The corrective follow-up used `document.refresh` and `document.select`, not `document.render`.
- `DocumentWorkspace` loads and refreshes artifacts on mount/job change or a manual refresh click; it does not subscribe to general document registry mutations (`DocumentWorkspace.tsx:205-241`, `286-301`).

This means even after the backend discovered the cover letter, an already-mounted viewer could remain stale until remounted or manually refreshed.

**What is not broken**

The multi-document identity/cycling logic itself works when both documents are present in the registry. Existing focused tests for resume + cover-letter identity and cycling pass.

---

### BUG-3 — Final agent response disappears and is replaced by `[protected path]`

**Observed behavior**

The final assistant response streamed visibly while being typed. At completion, the response disappeared and the UI showed `[protected path]`.

**Root cause: confirmed and deterministically reproduced**

This is an over-broad redaction bug, not a write denial and not a Hermes refusal.

- `redaction.py` classifies paths containing `.hermes`, `.ssh`, `mcp-tokens`, `auth.json`, or `.env` as credential paths (`services/api/jobos_api/redaction.py:32-73`).
- If any such substring appears anywhere in a prose string, the redactor replaces the **entire string** with `[protected path]` rather than masking only the path.
- The Hermes adapter applies that redactor to `message.complete` (`services/api/jobos_api/hermes_adapter.py:605-625`).
- The renderer deliberately replaces accumulated `message.delta` text with the terminal `message.complete` text (`apps/desktop/src/renderer/hooks/useAgentConversation.ts:313-317`).

Deterministic reproduction:

1. Stream ordinary message deltas containing a split `.hermes/...` output path. The text appears normally because no individual delta contains the complete protected pattern.
2. Send the complete response as `message.complete`.
3. The API normalizes the final summary to `[protected path]`.
4. The renderer replaces the previously visible streamed message with that literal.

The guardrail therefore destroys the user's final answer in this one-user local app. It does not protect a write operation; it only damages presentation/persistence.

---

### BUG-4 — Follow-up message appears unanswered and Agent Chat reports offline

**Observed behavior**

After the document turn, the user followed up that the cover letter was missing. JobOS showed no useful reply and presented the agent as offline.

**Root cause A: confirmed — the agent actually received and completed the follow-up**

The Job Hunter runtime log proves this was not simply an absent agent:

- The follow-up JobOS turn started at **21:56:51** in Hermes session `20260724_214622_ae344b`.
- It called `document_list`, then `document_refresh` at **21:58:10**, then `document_select` at **21:58:16**.
- Hermes completed a final response at **21:58:50**.

So the backend agent did work and reply; JobOS failed to deliver/project that result reliably.

**Root cause B: confirmed — SQLite connections are leaked until garbage collection, and the event consumer dies silently on persistence failure**

Both JobOS's state store and the Job Hunter storage layer use `with sqlite3.connect(...) as connection` as if the context manager closes the connection. Python's SQLite context manager commits/rolls back but does **not** close the connection.

Evidence:

- JobOS has **44** `with sqlite3.connect(...)` call sites in `state_store.py`.
- The live API process had **82 open file descriptors** to `data/jobos.db` and **118 total descriptors** against a macOS soft limit of **256**.
- During the incident, API requests for jobs, workspace, artifacts, and device state repeatedly failed with `sqlite3.OperationalError: unable to open database file`.
- A deterministic harness with garbage collection disabled leaked one descriptor per read and failed on call **253** with the same error.
- `_consume_gateway_events()` silently exits on any such exception and is not restarted (`conversations.py:457-460`). Once dead, Hermes can finish normally while JobOS records/displays no completion or later connection recovery.

This explains the mismatch between the Job Hunter log (follow-up completed) and the UI (no answer/stale offline state). It also explains why **New session** is an accidental recovery mechanism: reset recreates the event-consumer task.

**Root cause C: confirmed — offline errors collapse distinct failures into one generic message**

`safe_error_summary()` maps transport, session-isolation, persistence-adjacent attachment, and other connection failures to the same user-facing copy: **Agent connection unavailable. Retry when the agent is online.** The UI therefore reports “offline” even when the actual failure is stale recovery state or a dead event-delivery path.

---

## Shared root-cause map

The four bugs are best explained by **three** underlying defects:

1. **Brittle gateway/session lifecycle**
   - One-shot startup connection.
   - No autonomous reconnect.
   - Stored-session isolation recovery can remain wedged.
   - New session accidentally acts as a runtime reset.
   - Causes BUG-1 and contributes to BUG-4.

2. **Unsafely managed SQLite connection lifetime + unsupervised event consumer**
   - Repeated API polling leaks DB descriptors until garbage collection.
   - Resource exhaustion produces intermittent database failures.
   - A single persistence exception permanently kills agent-event consumption.
   - Strongly causes BUG-4 and can make document refresh/state APIs unreliable during BUG-2.

3. **Incomplete document/public-output contracts**
   - The agent can claim both files are UI-ready before both are registered artifacts.
   - The live viewer only reacts to `document.render`, not all registry-changing document commands.
   - Prose redaction replaces entire terminal messages that mention a protected path.
   - Causes BUG-2 and BUG-3.

## Recommended repair order

1. **Close every SQLite connection deterministically and supervise/restart the gateway event consumer.** This is the broad reliability fix and should land first.
2. **Add autonomous gateway reconnect with backoff and make recovery status explicit.** Remove the need for New session as a runtime repair button.
3. **Narrow/redesign path redaction for this local one-user product.** Never replace an entire assistant answer because it contains a local path.
4. **Make document publication atomic and UI-reactive.** A turn should not claim a resume/cover-letter packet is ready until every promised document is registered; every registry mutation should notify/refresh the viewer.
5. **Replace generic “agent offline” copy with specific states** such as dashboard unavailable, session recovery blocked, event stream failed, or retrying.

## Verification performed

Read-only investigation only; no fixes or runtime restarts were performed.

- Correlated installed API, Hermes dashboard, Job Hunter, and JobOS database evidence.
- Reproduced full-response redaction to `[protected path]` through the actual adapter.
- Reproduced SQLite descriptor exhaustion at the real macOS `RLIMIT_NOFILE=256` boundary.
- Ran focused existing API/renderer tests for gateway reconnection, session isolation, agent projection, redaction, and multi-document identity; they passed, confirming the missing cases are lifecycle/integration gaps rather than already-covered regressions.
- Preserved the unrelated modified file `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` without reading or editing it.
