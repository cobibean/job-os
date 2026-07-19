# JobOS V1 Brainstorm

## Status

Early product brainstorm. This is intentionally ambitious and should be narrowed before implementation.

## What We Learned

- The existing Job Hunter CLI is real and useful. It can run discovery, generate search queries, manage leads, produce reports, recommend resume angles, render resumes, and run recurring monitoring.
- The static job pages and resume review pages were worthwhile experiments, but they are outputs rather than a unified product experience.
- The existing data layer is mature enough to provide a strong foundation for JobOS.
- Operational data is currently fragmented across the database, queue files, application packets, generated reports, resume artifacts, and session history.
- The job ingest being stale is acceptable for now. When it resumes, new information should flow into one coherent operational system.
- The previous Resume OS focused too heavily on being a resume editor, was unattractive, and did not work reliably. It may have been intentionally deleted during migration.
- Resume editing can still be valuable as one feature inside JobOS, especially during application preparation, but it should not define the whole product.
- JobOS should be agent-native. The exact agent interface—CLI, API, MCP, or a combination—should be selected after the product features and operating model are defined.

## Product Definition

> JobOS is the command center where I run my entire job search with an agent—from finding opportunities through getting an offer.

The product should always answer five questions:

1. What should I do today?
2. Which opportunities deserve my time?
3. Where does every opportunity currently stand?
4. What is the agent doing, and what does it need from me?
5. Is my search strategy actually working?

JobOS is not a collection of generated pages, a prettier spreadsheet, a resume editor, or a chatbot with job links. It is a unified operating system for the search.

## Ideal User Experience

### Home: What Matters Today

Opening JobOS should immediately reveal:

- new high-quality opportunities awaiting review
- applications ready for approval
- follow-ups that are due
- roles that may close soon
- current search health and recent results
- work the agent is performing
- decisions or information the agent needs from me

The home screen should feel like an intelligent daily briefing rather than a conventional analytics dashboard.

### Radar: Everything Entering the Search

Radar is the opportunity inbox. It combines:

- automatically discovered jobs
- jobs added manually
- opportunities found during company research
- referrals or recruiter messages
- openings at saved target companies
- future opportunities worth watching

Each opportunity should show enough information to make a quick decision:

- company and title
- remote and location status
- compensation
- posting age and freshness
- recommendation and confidence
- why it may be worth pursuing
- biggest concern
- whether the posting has been verified as live

Every opportunity should eventually receive a decision:

- pursue
- investigate
- maybe later
- watch
- skip

### Opportunity Workspace: One Home for Every Job

Each opportunity should have a single living workspace containing:

#### The job

- clean job description
- source and application link
- date found and last verified date
- remote, compensation, and closing information
- changes since discovery

#### The decision

- apply now, maybe, stretch, or skip
- confidence in the recommendation
- why the job appears winnable or unwinnable
- company quality and career leverage
- likely interview risks
- urgency and expected application effort

#### My fit

- strongest fit thesis
- relevant experience and projects
- claims that can safely be used
- gaps that should not be hidden
- likely employer objections
- most believable positioning angle
- recommended resume direction

#### The work

- captured application requirements
- resume status
- application questions
- cover letter or outreach when relevant
- blockers
- checklist and next action
- whether the next action belongs to me or the agent

#### The history

- discovery
- research and decisions
- materials created
- approvals
- submission
- follow-ups
- interviews
- final outcome

### Pipeline: Where Everything Stands

The primary pipeline could be:

```text
New
→ Reviewing
→ Pursuing
→ Application Building
→ Ready for Approval
→ Applied
→ Recruiter Conversation
→ Interviewing
→ Offer
→ Closed
```

The same pipeline should be understandable as a board, list, timeline, or focused view such as:

- waiting on me
- waiting on someone else
- agent working
- blocked
- stale

Every opportunity should show a next action, not only a status.

### Application Studio: Turning a Good Job Into a Submission

The application experience should make progress visible:

```text
Job verified
Fit thesis approved
Requirements captured
Resume selected
Resume tailored
Questions answered
Artifacts checked
Ready to submit
```

It should clearly show:

- what the application requires
- what is complete
- what the agent is preparing
- what needs human judgment
- risky claims requiring approval
- whether the materials are internally consistent
- whether the packet is ready for the specific application form

Resume editing belongs inside this experience. The ideal interaction is reviewing a small set of recommended changes for a specific role, comparing them to the base resume, accepting or rejecting them, previewing the final artifact, and retaining a record of exactly what was submitted.

### My Story: Trusted Career Context

JobOS should maintain a human-readable source for:

- experience
- projects
- skills
- accomplishments and verified metrics
- dates
- preferred positioning
- technical truth boundaries
- claims requiring qualification or approval
- statements that should never be made

The system should make this easy to review without turning career context maintenance into a separate knowledge-management project.

### Search Operations: Is the Machine Working?

JobOS should provide visibility into:

- when search last ran
- sources checked and failures encountered
- roles discovered
- duplicates removed
- hard-filter failures
- opportunities awaiting review
- target companies that have not been checked recently
- stale or closed postings
- areas where the search is producing too much noise or missing coverage

The search should explain why an opportunity was included or excluded rather than behaving like a black box.

### Agent Desk: What the Agent Is Doing

Agent-native should not mean attaching a generic chat window. The agent should be a visible operator inside the product.

The user should be able to see:

#### Working now

- company research
- job verification
- fit analysis
- resume comparison
- application preparation

#### Waiting for me

- high-risk claim approval
- positioning choices
- personal application answers
- submission authorization
- relocation or compensation decisions

#### Recently completed

- jobs scored
- obvious mismatches archived
- opportunity workspaces created
- postings updated
- application materials prepared

Every meaningful agent action should be visible, attributable, understandable, and reversible when appropriate.

## Agent-Native Operating Model

The user and agent should operate the same jobs, applications, tasks, resumes, and decisions.

The user should be able to give outcome-oriented instructions such as:

- Research these opportunities.
- Prepare the strongest one for application.
- Explain why this job ranks below another.
- Tailor my resume but show every change.
- Apply this preference to future searches.
- Follow up on applications that have been quiet for seven days.
- Show me what is blocking submissions.
- Find more jobs like the one that produced an interview.

Those instructions should create visible work and durable results inside JobOS rather than returning an isolated wall of prose.

### Possible autonomy levels

#### Observe

The agent researches, evaluates, and suggests without changing records.

#### Assist

The agent organizes opportunities, drafts materials, and prepares actions while the user approves consequential changes.

#### Operate

The agent performs routine work automatically, such as ingest, deduplication, freshness checks, scoring, obvious hard-filter archiving, first drafts, and follow-up reminders.

#### Submission boundary

Application submission remains separately controlled. Legal, identity, salary, demographic, and other consequential answers should have clear approval boundaries.

## Outcomes and Learning

Over time, JobOS should help answer:

- which role categories produce interviews
- which companies and sources respond
- which positioning and resume variants perform best
- where applications stall
- whether the search is too broad or narrow
- how score relates to actual outcomes
- which evidence creates traction
- whether application timing affects response

The purpose is not to celebrate how many jobs were discovered. The purpose is to learn what increases the chance of a strong offer.

## What JobOS Should Not Become

JobOS should not become:

- a static report generator
- a generic CRM with job labels
- a resume editor with a job tab
- a chatbot that happens to remember links
- a vanity analytics dashboard
- an auto-apply spam machine
- an overcomplicated project-management tool
- a graveyard of old job descriptions
- a system that hides agent behavior
- a polished interface sitting on fragmented truth

## Emotional Standard

JobOS should make the search feel controlled rather than chaotic, selective rather than desperate, transparent rather than mysterious, and focused on winning rather than collecting jobs.

The desired feeling is:

> I know exactly where everything stands. The agent is working. Nothing good is slipping through the cracks. The important applications are moving forward. I know what I need to do next.

## Working Product Thesis

> JobOS is an agent-native command center that finds, evaluates, prepares, tracks, and learns from job opportunities while keeping the human in control of consequential decisions.

CLI, API, MCP, resume editing, browser assistance, and analytics are supporting capabilities. They are not the product itself.
