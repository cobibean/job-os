# JobOS Ideas

A lightweight list of potential product directions. Entries here are ideas, not approved implementation work.

## In-app Update Center

Give official JobOS installations a small “Update available” indicator and a one-click, verified update flow with safe replacement, relaunch, and rollback. Official signed builds should update from an official release channel; self-built or modified source installations should never be silently overwritten and should instead receive release information and source-update guidance.

Current-system reconnaissance and a future architecture proposal live in [`architecture/update-center-reconnaissance.md`](architecture/update-center-reconnaissance.md).

## Bring Your Own or Managed Agent

Make JobOS agent-agnostic so a user can connect an existing Hermes agent, connect an account from another supported provider such as Codex, or subscribe to have JobOS create and manage a new Hermes agent for them. The managed path should include an onboarding flow for shaping the agent and could serve as the primary paid layer around an otherwise open-source application.

## Career Profile and Evidence-Backed Review

Turn applicant context into a visible, user-owned foundation for evaluating opportunities and producing truthful job-search work. Deliver the direction in three parts:

### 1. Applicant Context MVP

Create a focused Career Profile where the user can inspect and manage the trusted facts used during job review: base resume, work experience, projects and accomplishments, skills, target roles, location and work authorization, compensation and work preferences, and the evidence source behind important claims. The profile belongs to the user rather than a particular agent and remains useful when no agent is configured.

### 2. Review Brief

For a selected job, compare the captured listing with the applicant's approved context and save a concise, evidence-backed decision artifact. The brief should show posting liveness, recommendation, requirement-to-evidence matches, gaps, blockers, unanswered questions, a posting-confidence assessment separate from candidate fit, and one recommended next action. It must not silently rewrite documents, change job status, or submit an application.

### 3. Context Expansion Later

After the first review workflow proves the foundation, expand the Career Profile with writing voice, interview stories, portfolio materials, reusable application answers, multiple resume variants, and optional agent-specific instructions. Keep these later capabilities out of the MVP unless the Review Brief establishes a concrete need for them.

The intended relationship is: applicant context provides trusted facts about the user, the job listing provides trusted facts about the opportunity, and the Review Brief provides a traceable comparison of the two.

## Multi-Agent Job Search

Add an in-app job-search flow where users enter preferences such as role, salary, location, and industry, launch a multi-agent search, review the combined results, and choose which opportunities to add to their JobOS job database.
