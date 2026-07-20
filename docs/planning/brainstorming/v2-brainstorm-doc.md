# JobOS V1 Product Vision

## Status

Product vision locked through direct discussion. The V1 visual direction and implementation-facing experience contract are also locked.

- Locked visual direction: [`../../design/references/jobos-v1-locked-direction.png`](../../design/references/jobos-v1-locked-direction.png)
- V1 workbench contract: [`../specs/v1-workbench-contract.md`](../specs/v1-workbench-contract.md)

This document narrows the ambitious original brainstorm into a focused, buildable V1.

JobOS V1 should prioritize a beautiful, fluid, trustworthy experience over feature breadth. The goal is not to build the entire job-search operating system immediately. The goal is to make one important workflow exceptional.

## Product Definition

> JobOS is a beautiful, persistent workbench shared by me and my job-hunter agent.

The job-hunter agent remains the main driver of the search. JobOS gives me visibility into its work, a direct way to guide it, and shared surfaces where either of us can act.

JobOS V1 is not a dashboard that I visit. It is a workspace that I return to. Reopening it should feel like reopening a browser with the full session still waiting exactly where I left it.

## V1 Golden Path

The first complete workflow is deliberately simple:

1. Select a job from the job navigator.
2. Open the real job listing in the built-in browser.
3. Ask the agent in chat to tailor my resume for the role.
4. See the agent's actions as it works.
5. Preview the real tailored resume beside the listing.
6. Give the agent feedback in chat and review each updated result.
7. Save the approved resume version to that job.

If JobOS makes this workflow fast, clear, and delightful, V1 succeeds.

## The Five V1 Surfaces

### 1. Job Navigation

A compact, Linear- and Codex-like list of opportunities.

Each row should remain restrained and useful, showing:

- company
- role
- current status
- a subtle activity or next-action indicator when useful

The navigator should support these ordering modes:

- Manual
- Recently opened
- Recently added
- Alphabetical
- Status

Manual mode supports drag-and-drop ordering, and the custom order persists across sessions. Calculated sorting modes determine their own order so dragging does not create ambiguous behavior.

Selecting a job focuses the workbench on that opportunity. It does not destroy or close unrelated browser tabs, documents, or general work.

### 2. Built-In Browser

The browser should feel like a real browser, not a restricted job-listing viewer.

I should be able to:

- open and navigate job listings
- use Google, Gmail, and other ordinary sites
- keep multiple tabs open
- preserve authenticated sessions and in-progress forms
- move between jobs without losing browser state
- allow the agent to use the same browser capabilities I can use

Browser tabs may be associated with a job, another job, or no job at all. Selecting a job changes the active work context; it must never end a browser session or close unrelated work.

### 3. Agent Chat

JobOS has one continuous conversation with the job-hunter agent.

Switching jobs does not create or switch conversations. The selected job becomes the active context for the same ongoing agent, while the agent retains the broader context of the search.

The active job context should be visible but lightweight, and I should be able to clear or change it without disrupting the conversation.

Agent actions should appear as a compact chronological activity trace. If the agent performs fifteen actions before responding, the interface should represent all fifteen actions. Each event should be concise by default and expandable to reveal relevant details such as the tool used, command run, file changed, browser action, or result.

This visibility is the desired standard, but it should not dominate the initial build at the expense of the core workflow.

### 4. Document Preview

The central workspace should support document viewing alongside browser content rather than adding a permanent fourth column.

V1 resumes are agent-edited and human-reviewed. JobOS is not a document editor.

The interaction is:

- I request a change in chat.
- The agent edits the underlying resume source.
- The preview refreshes.
- I inspect the rendered result.
- I approve it or request another change.

The preview must display the real output artifact, including its actual pagination, spacing, and layout. A simplified recreation is not sufficient because the reviewed artifact should match what an employer receives.

Useful escape hatches include:

- Open in Default App
- Reveal in Finder
- Export or Download
- Refresh Preview

V1 does not include a word processor, block editor, selection toolbar, or tracked-changes system.

### 5. Workspace State

Continuity is a first-class product feature.

When JobOS reopens, it should restore:

- the selected job
- browser windows or tabs and their state
- the open document
- the continuous chat
- panel order, dimensions, and collapsed state
- the current layout preset
- unfinished work and relevant context

The desired feeling is the familiar experience of returning to a browser session that is exactly where I left it.

## Workbench Layout

The default composition is a three-part workbench:

```text
Job Navigation | Browser or Document | Agent Chat
```

Panel resizing and collapsing are non-negotiable V1 capabilities.

The workbench should also support three purpose-driven layout presets to prove that the interface can adapt without becoming an unrestricted dashboard builder:

### Research

Job navigation, a browser-dominant center, and agent chat.

### Review

Job navigation, a document-dominant center, and agent chat.

### Agent Focus

Compact navigation and center content with chat given the most space.

The app remembers adjustments to panel widths and ordering. A clear Reset Layout action restores the intended arrangement for the current preset.

Panel ordering should be flexible but restrained. The three presets, remembered dimensions, draggable panel order, and reset behavior should come before any fully arbitrary workspace construction system.

## Job Status Vocabulary

The existing job-hunter agent uses these values:

```text
discovered
scored
reviewed
shortlisted
apply_now
maybe
stretch
skipped
applied
interviewing
closed
archived
```

V1 should preserve those values for compatibility while presenting a calmer six-group model in the interface:

### Inbox

- discovered
- scored
- reviewed

### Considering

- shortlisted
- apply_now
- maybe
- stretch

### Applied

- applied

### Interviewing

- interviewing

### Closed

- closed

### Inactive

- skipped
- archived

The compact job row may still show the specific underlying label, while status ordering groups related jobs under the six understandable categories.

Both I and the agent can change a job's status through the same underlying system. Long-term, processing state, pursuit decision, application stage, and archival state may become separate properties, but that cleanup is not required for V1.

## Human and Agent Parity

> Anything I can do in JobOS, the agent should be able to do too.

This should not require a separate agent dashboard, hidden control plane, or duplicate set of workflows. The human interface and agent interface operate on the same jobs, documents, browser state, statuses, and workspace context.

Consequential external actions may still require explicit approval. Parity of capability does not remove human control or trust boundaries.

## Integration Model

JobOS will own a stable application API.

- The JobOS interface uses the application API.
- An MCP server wraps the same application API for the job-hunter agent.
- The MCP layer translates agent tool calls into ordinary JobOS API operations.
- The agent does not need a separate data model or agent-only administration system.
- Human and agent actions should produce the same durable state and visible results.

```text
JobOS UI ──────┐
               ├── JobOS Application API ── Jobs, browser, documents, chat, workspace state
Job Hunter ─ MCP Server ┘
```

The API is the product boundary. MCP is the agent-facing adapter around it.

The exact framework, transport details, authentication model, browser implementation, and deployment topology remain implementation decisions. They should be selected after the V1 product experience is locked and the existing job-hunter integration points are inspected.

## Experience Standard

The visual character should combine:

- the persistent agent workbench feeling of Codex Desktop
- the precision and restrained density of Linear
- the calm document experience of Notion
- the conversational familiarity of ChatGPT
- the flexible panel behavior of Cursor and modern IDEs

The result should feel minimal, intentional, responsive, and native to focused work. Beauty and ease of use are part of V1 functionality, not polish to add later.

The emotional outcome is:

> I can see what my agent sees, understand what it is doing, step in whenever I want, and return later without losing my place.

## Explicitly Deferred

V1 does not need:

- a whole-search dashboard
- a morning briefing
- analytics or strategy reporting
- a Kanban board
- a built-in resume editor
- separate conversations for every job
- a complex CRM workflow
- arbitrary dashboard or workspace construction
- a redesigned job-status data model
- a generic agent administration surface
- the full end-state described in the original brainstorm

These may become valuable after the core workbench feels excellent and the golden path works end to end.

## V1 Success Standard

V1 is successful when I can reopen JobOS, find my work exactly where I left it, choose an opportunity, inspect the live listing, direct my agent to tailor a resume, watch enough of its work to trust what is happening, review the real output, iterate conversationally, and save the approved artifact without leaving the workbench.

The app does not need many features. It needs this experience to feel exceptionally good.
