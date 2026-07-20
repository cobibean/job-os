# JobOS V1 Workbench Contract

## Status

Locked product and UI contract for V1 implementation.

This contract translates the approved product vision and visual direction into observable behavior. It defines what the V1 experience must do and feel like without choosing the application framework, browser engine, persistence library, or deployment model.

## Sources of Truth

- Product vision: [`../brainstorming/v2-brainstorm-doc.md`](../brainstorming/v2-brainstorm-doc.md)
- Locked visual direction: [`../../design/references/jobos-v1-locked-direction.png`](../../design/references/jobos-v1-locked-direction.png)
- Technical architecture: [`../../architecture/v1-technical-architecture.md`](../../architecture/v1-technical-architecture.md)
- Implementation plan: [`../implementation/v1-implementation-plan.md`](../implementation/v1-implementation-plan.md)

The visual reference is binding for composition, hierarchy, density, theme, and overall character. Its sample names, resume content, timestamps, exact dimensions, and generated icon drawings are illustrative rather than literal implementation requirements.

If the visual reference and this contract appear to conflict, this contract governs behavior and the reference governs visual intent. Material changes to either require an explicit product decision.

## Product Promise

> JobOS is a beautiful, persistent workbench shared by me and my job-hunter agent.

V1 succeeds when I can return to the exact workspace I left, choose a job, inspect its live listing, ask the agent to tailor my resume, observe its work, review the real rendered result, iterate in chat, and save the approved artifact without leaving JobOS.

## V1 Golden Path

1. I select a job in the navigator.
2. I open or continue using its live listing in the browser.
3. I ask the agent to tailor my resume for the selected role.
4. JobOS shows each meaningful agent action as it occurs.
5. The tailored resume becomes available in the document workspace.
6. I inspect the rendered artifact and request changes in chat.
7. The agent updates the source, refreshes the artifact, and saves the approved version to the job.

This path is the primary acceptance journey. Features that do not strengthen it are secondary in V1.

## Primary Screen Contract

JobOS opens directly into the last active workbench. It does not open on a dashboard, briefing, analytics page, or generic welcome screen when restorable state exists.

The default desktop composition has four structural areas:

1. A compact global workspace bar.
2. Job navigation on the left.
3. A flexible browser or document workspace in the center.
4. Continuous agent chat on the right.

The center may include a narrow contextual rail for job requirements while reviewing a resume. That rail belongs to the active center workspace and is not a fourth permanent product surface.

At a 1440 × 1024 reference viewport:

- Job navigation begins near 260–300 px wide.
- Agent chat begins near 360–400 px wide.
- The center consumes the remaining width and remains the dominant work surface in Research and Review layouts.
- Minimum widths must protect legibility rather than allowing panels to collapse into broken UI.

These values are starting proportions, not fixed pixel requirements.

## Global Workspace Bar

The global bar provides only workspace-level controls:

- Research layout
- Review layout
- Agent Focus layout
- Reset Layout
- restrained access to application-level settings when needed

The selected layout is visually clear without becoming a large segmented control. Reset Layout restores the selected preset's default panel order, sizes, and collapsed state. It does not clear jobs, browser tabs, chat, documents, or other product data.

## Layout Contract

### Required V1 capabilities

- Every primary panel is resizable with a visible pointer target.
- Every primary panel is collapsible and recoverable.
- Panel widths, order, and collapsed state persist.
- The three layout presets are always recoverable.
- Reset Layout is available without entering settings.
- Panel reordering is restrained and explicit, not a freeform dashboard canvas.

### Presets

#### Research

The browser is dominant. Job navigation and chat remain available.

#### Review

The rendered document is dominant. A narrow job-requirements context may appear beside it. Job navigation and chat remain available.

#### Agent Focus

Chat becomes dominant while job and source context stay immediately reachable.

### Resizing behavior

- Dragging a divider updates the layout continuously.
- The cursor communicates resize direction.
- A panel stops at a usable minimum width.
- A collapsed panel leaves a clear affordance for reopening it.
- Resizing does not reload browser content, reset chat, or change the selected job.
- User-adjusted dimensions are remembered per preset.

### Reordering behavior

- Primary panels may be reordered through an intentional drag interaction.
- Reordering changes presentation only; it does not change job context or content state.
- A clear insertion preview shows where the panel will land.
- Reset Layout restores the preset's canonical order.

## Job Navigation Contract

### Row content

Each compact job row shows:

- company
- role
- specific underlying status
- a restrained activity or next-action indicator only when useful

Rows are part of one coherent list. They are not individual cards.

### Selection

- Exactly one job may be the active context at a time.
- Selecting a job updates the active context used by chat and related document actions.
- Selecting a job does not close, reload, or navigate browser tabs.
- Selecting a job does not start a new conversation.
- The current selection remains visually obvious in all ordering modes.

### Ordering

V1 supports:

- Manual
- Recently Opened
- Recently Added
- Alphabetical
- Status

Manual mode supports drag-and-drop ordering. The custom order persists.

Calculated modes own their ordering. Dragging is unavailable in those modes so the interface never implies that an automatic sort can also preserve arbitrary manual placement.

### Status editing

The user can change a job's specific status directly from the navigator. Agent status changes appear through the same interface and persist through the same application API.

The existing agent values remain canonical in V1:

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

The interface groups them as:

| UI group | Agent values |
| --- | --- |
| Inbox | `discovered`, `scored`, `reviewed` |
| Considering | `shortlisted`, `apply_now`, `maybe`, `stretch` |
| Applied | `applied` |
| Interviewing | `interviewing` |
| Closed | `closed` |
| Inactive | `skipped`, `archived` |

The specific value may appear on the row while the group determines status-based ordering and organization.

## Browser Contract

The built-in browser behaves like a general browser, not a restricted job viewer.

### Required behavior

- Create, select, close, and reorder tabs.
- Navigate arbitrary ordinary websites, including Google and Gmail.
- Preserve authenticated browser sessions.
- Preserve in-progress navigation and form state to the extent supported by the browser engine.
- Allow job-associated and unassociated tabs to coexist.
- Optionally associate a tab with a job without making that association a navigation boundary.
- Expose browser actions to the agent through the JobOS API and MCP adapter.

### Context boundary

Changing the active job must never automatically:

- close a tab
- replace the current page
- end an authenticated session
- discard an in-progress form
- force an unrelated tab to become job-associated

The active job is conversational and organizational context, not a browser sandbox.

## Agent Chat Contract

### Conversation model

- JobOS V1 has one continuous job-hunter conversation.
- Changing jobs does not create, archive, or switch conversations.
- The active job context is visible in a lightweight control.
- The user can change or clear the active context without losing the conversation.
- The agent retains broader search context while using the active job as the immediate reference.

### Activity trace

Every meaningful agent tool action produces one chronological event in the conversation.

Each event is compact by default and includes:

- a plain-language action label
- working, completed, failed, or waiting state
- a disclosure affordance

Expanded details may include:

- tool name
- command or API operation
- file or artifact involved
- browser action
- concise result or error

If the agent performs fifteen actions, the interface represents fifteen events. JobOS must not replace the trace with an indefinite spinner or an unsupported summary.

The trace uses progressive disclosure. Full commands, payloads, and logs are not expanded by default.

### Response and control

- Tool events appear in execution order before the corresponding agent response.
- The user can continue typing while the agent works.
- Waiting-for-user states clearly identify the decision or approval needed.
- Errors remain visible and actionable rather than disappearing into the transcript.
- Consequential external actions may require explicit approval even when the agent has the technical capability to perform them.

## Document Preview Contract

V1 is agent-edited and human-reviewed. It is not a document editor.

### Required behavior

- Open the actual generated PDF or DOCX-derived preview associated with a job.
- Render the artifact with faithful pagination, spacing, line breaks, and layout.
- Refresh after the agent changes the underlying source.
- Preserve the current page and zoom where practical during refresh.
- Make the artifact association with the selected job clear.
- Make the newest successfully rendered version obvious.

### Required actions

- Open in Default App
- Reveal in Finder
- Export or Download
- Refresh Preview
- Zoom in and out

### Editing boundary

The user requests content changes through chat. The agent edits the underlying source and produces a new render. V1 does not include inline text editing, block editing, selection toolbars, or tracked changes.

A failed render must preserve access to the last successful artifact and explain that the latest version is unavailable.

## Workspace State Contract

Continuity is a product feature, not an implementation detail.

JobOS persists and restores:

- active job
- job ordering mode and manual order
- browser tabs, selected tab, and session state
- open document, page, and zoom when practical
- continuous chat and activity trace
- active layout preset
- panel order
- panel widths
- collapsed panels
- unfinished agent work and waiting states

State changes should persist after meaningful interaction rather than relying only on a clean application shutdown.

On restart, JobOS restores the last coherent state. If part of the state cannot be restored, the rest of the workbench still opens and the unavailable surface explains what happened without clearing recoverable data.

## Human and Agent Parity Contract

> Anything the user can do in JobOS, the agent can do through the same product boundary.

The JobOS application API is that boundary.

- The UI uses the JobOS API.
- The MCP server wraps the same JobOS API.
- MCP tools translate agent intent into ordinary API operations.
- State-changing actions do not bypass the API through direct database or filesystem mutations.
- Human and agent actions produce the same durable state and visible results.
- Action origin is retained so the interface can distinguish user and agent activity when useful.

Parity applies to jobs, statuses, browser operations, documents, workspace context, and other exposed V1 capabilities. Approval policies may constrain consequential actions without requiring a separate agent-only product surface.

## Visual Contract

### Overall character

The approved direction is a premium dark desktop productivity app:

- Codex-like persistent agent workbench
- Linear-like precision and restrained density
- Notion-like document calm
- ChatGPT-like conversational familiarity
- Cursor-like adaptable panel behavior

The experience should feel native to focused work, not like an admin dashboard or themed website.

### Color and surfaces

- Near-black graphite is the primary application surface.
- Adjacent panels use restrained charcoal variation rather than visibly separate cards.
- Warm white is used for primary text.
- Cool gray is used for secondary text, dividers, and inactive controls.
- Violet is reserved for selection, active layout state, and rare high-value emphasis.
- Green may communicate successful completion but should remain quiet.
- The white rendered document is the dominant contrast in Review mode.
- Gradients, glow effects, and glassmorphism are excluded.

Exact color tokens will be chosen during implementation and reviewed against the locked reference.

### Typography

- Product UI uses a highly legible system-oriented sans serif.
- Body text targets a 14–16 px readable baseline.
- Hierarchy relies on size, weight, spacing, and contrast rather than decorative treatment.
- The rendered document keeps its own artifact typography.
- The application uses no more than two interface font families.

### Density and shape

- The interface is compact but breathable.
- Thin dividers define major regions.
- Lists use row separation rather than individual containers.
- Corner radii are modest and consistent.
- Shadows are nearly absent except where a paper artifact needs physical separation.
- Large cards, nested cards, pills everywhere, and ornamental containers are excluded.

### Iconography

The selected revision locks a mature system-icon direction:

- one coherent outline family
- approximately 1.5 px optical stroke weight
- 14–18 px default size
- monochrome neutral color by default
- violet only for selected or active controls
- semantic icons rather than decorative symbols
- consistent chevrons, status marks, and toolbar actions
- text labels omitted only when the icon is standard and has an accessible name

Excluded icon treatments:

- colorful square letter tiles for every job
- decorative sparkles
- emoji
- mixed filled and outline families
- inconsistent stroke weights
- novelty metaphors
- unnecessary icons beside already-clear labels

### Motion

Motion should explain state change:

- panel resizing tracks the pointer directly
- collapse and expand preserve spatial orientation
- layout preset changes animate briefly without spectacle
- new activity events enter without shifting the conversation unpredictably
- document refresh communicates replacement without flashing the entire workspace

Reduced-motion preferences must be respected.

## Essential Product States

### No jobs

The navigator explains that no opportunities are available and provides one clear path to add or sync them. The rest of the workbench remains usable.

### No job selected

The browser and general chat remain available. Job-specific document actions explain that a job must be selected.

### Agent working

The conversation shows live chronological activity. Other panels remain usable.

### Agent waiting

The required user decision is visually distinct, specific, and actionable.

### Document rendering

The last successful artifact remains visible while the new version renders whenever possible.

### Browser or network failure

The affected browser surface explains the failure and offers retry without clearing tabs or workspace state.

### Partial restore failure

Recoverable panels open normally. The affected surface explains what could not be restored and does not silently reset unrelated state.

## Accessibility Contract

- All controls are keyboard reachable.
- Panel resize and reorder operations have keyboard alternatives.
- Icon-only controls have accessible names and tooltips.
- Focus is visible against the dark theme.
- Text and interactive states meet appropriate contrast requirements.
- Status is not communicated by color alone.
- Collapsed and expanded states are exposed semantically.
- Activity updates are announced without overwhelming assistive technology.
- Zooming the interface does not make primary controls unreachable.

## V1 Acceptance Scenarios

### Resume-tailoring journey

Given ten existing jobs, the user can select one, open its listing, ask the agent for a tailored resume, observe agent actions, review the actual artifact, request a revision, and save the approved version to the job.

### Continuity

After changing panel sizes, selecting a job, opening browser tabs, viewing a resume, and chatting with the agent, closing and reopening JobOS restores the coherent workbench.

### Browser independence

Changing the selected job does not close or navigate an unrelated Google or Gmail tab and does not discard an in-progress browser session.

### Layout adaptability

The user can resize and collapse panels, switch among all three presets, reorder primary panels, and restore the current preset with Reset Layout.

### Shared state

A user status change is visible to the agent through the API, and an agent status change appears in the same UI without a separate synchronization workflow.

### Activity visibility

Multiple tool calls appear as distinct chronological events that are concise when collapsed and useful when expanded.

### Artifact trust

The resume preview reflects the real exported artifact, and a render failure does not silently replace or remove the last successful version.

## Explicit V1 Non-Goals

- whole-search dashboard
- morning briefing
- search analytics
- Kanban pipeline
- built-in resume editor
- separate chats per job
- generic CRM workflows
- arbitrary dashboard construction
- redesigned status data model
- agent administration dashboard
- automatic application submission without an explicit approval boundary

## Implementation Decisions

The major implementation choices are defined in the linked technical architecture. This product contract remains framework-independent and continues to govern observable behavior.

Phase 0 of the implementation plan must verify the live Mac Mini and Hermes runtime before code is scaffolded. Runtime discoveries may change an Adapter or Implementation behind an established Interface; they must not silently reduce the product requirements in this contract.

These decisions should be made against this contract. They must not reduce the required continuity, browser freedom, artifact fidelity, layout adaptability, or human-agent parity without reopening the relevant product decision.

## Definition of Done

V1 is not done when the interface merely resembles the mockup or when isolated components work.

V1 is done when the golden path works end to end, the workbench survives real use and reopening, the agent and user operate the same durable system, and the resulting experience matches the locked visual direction closely enough to feel intentional, calm, and trustworthy.
