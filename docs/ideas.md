# JobOS Ideas

A lightweight list of potential product directions. Entries here are ideas, not approved implementation work.

## In-app Update Center

Give official JobOS installations a small “Update available” indicator and a one-click, verified update flow with safe replacement, relaunch, and rollback. Official signed builds should update from an official release channel; self-built or modified source installations should never be silently overwritten and should instead receive release information and source-update guidance.

Current-system reconnaissance and a future architecture proposal live in [`architecture/update-center-reconnaissance.md`](architecture/update-center-reconnaissance.md).

## Bring Your Own or Managed Agent

Make JobOS agent-agnostic so a user can connect an existing Hermes agent, connect an account from another supported provider such as Codex, or subscribe to have JobOS create and manage a new Hermes agent for them. The managed path should include an onboarding flow for shaping the agent and could serve as the primary paid layer around an otherwise open-source application.

## Agent Context Workspace

Add a dedicated view where users can inspect and manage the personal context their agent uses for job-search work, including their base resume, skills, work experience, selected projects, and other relevant background. This should make the agent's knowledge visible and editable rather than hiding it behind the integration.

## Multi-Agent Job Search

Add an in-app job-search flow where users enter preferences such as role, salary, location, and industry, launch a multi-agent search, review the combined results, and choose which opportunities to add to their JobOS job database.
