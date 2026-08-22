# JobOS Ideas

A lightweight list of potential product directions. Entries here are ideas, not approved implementation work.

## In-app Update Center

Give official JobOS installations a small “Update available” indicator and a one-click, verified update flow with safe replacement, relaunch, and rollback. Official signed builds should update from an official release channel; self-built or modified source installations should never be silently overwritten and should instead receive release information and source-update guidance.

Current-system reconnaissance and a future architecture proposal live in [`architecture/update-center-reconnaissance.md`](architecture/update-center-reconnaissance.md).

## Bring Your Own or Managed Agent

Make JobOS agent-agnostic so a user can connect an existing Hermes agent, connect an account from another supported provider such as Codex, or subscribe to have JobOS create and manage a new Hermes agent for them. The managed path should include an onboarding flow for shaping the agent and could serve as the primary paid layer around an otherwise open-source application.

## Career Profile and User-Approved Review

Turn accepted user-authored or user-approved Career Profile information into a visible, user-owned foundation for evaluating opportunities and producing truthful job-search work. User-stated information is valid matching context with provenance `user stated`; supporting Evidence is optional provenance, not permission to use the information. Deliver the direction in three parts:

### 1. Applicant Context MVP

Create a focused Career Profile where the user can inspect and manage the accepted information used during job review: base resume, work experience, projects and accomplishments, skills, target roles, location and work authorization, compensation and work preferences, and optional supporting Evidence. The profile belongs to the user rather than a particular agent and remains useful when no agent is configured or no Evidence is attached.

### 2. Review Brief

For a selected job, compare the captured listing with accepted user-authored or user-approved Career Profile information and save a concise, traceable decision artifact. Map requirements to that accepted information and show supporting Evidence when available, but never require Evidence for a match or report absent Evidence as a qualification gap or blocker. Agent-inferred unsupported matches remain proposals until the user approves them. The brief should also show posting liveness, recommendation, real qualification gaps and blockers, unanswered questions, a posting-confidence assessment separate from candidate fit, and one recommended next action. It must not silently rewrite documents, change job status, or submit an application.

### 3. Context Expansion Later

After the first review workflow proves the foundation, expand the Career Profile with writing voice, interview stories, portfolio materials, reusable application answers, multiple resume variants, and optional agent-specific instructions. Keep these later capabilities out of the MVP unless the Review Brief establishes a concrete need for them.

The intended relationship is: the Career Profile provides accepted user-authored or user-approved information about the user, the captured job listing provides information about the opportunity, and the Review Brief provides a traceable comparison of the two. `User stated` records where accepted applicant information came from; Evidence may add support but is not required for the information to qualify as matching context.

## Multi-Agent Job Search

Add an in-app job-search flow where users enter preferences such as role, salary, location, and industry, launch a multi-agent search, review the combined results, and choose which opportunities to add to their JobOS job database.

## Media Uploads for Agent Context

Let users attach images, PDFs, and DOCX files in JobOS chat. Reuse the app's existing document parsing and rendering capabilities to bridge that media into the active agent session in a form each supported provider can receive, including Hermes, Codex, Claude, and other compatible agents. Each agent turn should make clear which uploaded media and JobOS tools are available for that work.

Use the same foundation to build first-run context: an “Upload your resume” onboarding step and later upload screens for cover letters, supporting documents, and other labeled files. Users can add a short description; the agent infers what it can and uses that guidance to propose where the file belongs in the user-owned Career Profile or JobOS knowledge base.

## Browse Table View

Add a third Browse mode, alongside List and Swipe, that gives people a dense, spreadsheet-inspired table for managing saved jobs the way they might manage a CSV. Let them scroll a broad view of their pipeline and shape it to their own process: choose useful columns, sort and rearrange the view, and apply a personal rank or priority—without turning JobOS into a literal spreadsheet. Keep Browse's existing local focus and explicit Open job handoff so inspecting or organizing the table never changes the active workbench job by surprise.
