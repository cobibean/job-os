# JobOS Feature Wishlist Memory - 2026-07-21

## Session summary

Created a deliberately small JobOS feature wishlist from the working MacBook demo. No product code was changed and no feature was approved for implementation during this brainstorm.

## Decisions

- The wishlist stays lean: a short description per idea, without effort estimates, prioritization, or implementation plans.
- Top-level `AGENTS.md` points future agents to the wishlist and states that entries are discussion ideas until Cobi explicitly approves implementation.
- **Next session will plan and then implement “Add a job from the browser.”**

## Wishlist

Source of truth: `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`

Current ideas:

1. Add a job from the browser
2. Job workspace
3. Application checklist
4. Interview mode
5. Freeform panel layout
6. Agent inbox
7. Compare jobs
8. Command palette and keyboard shortcuts
9. Themes

## Files created

- `AGENTS.md`
- `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md`
- `docs/memory/2026-07-21/jobos-feature-brainstorm-memory-2026-07-21.md`

## Verified baseline

- Product baseline before this documentation work: `da45ad5fcd4d64a886f2a8b1067a11e19af1545d` (`Add fresh agent sessions`).
- Local `main` and `origin/main` matched at that commit before the documentation commit.
- The shipped MacBook demo and New session behavior were not modified.

## Next session

1. Read `AGENTS.md`, this memory, and the wishlist.
2. Plan the **Add a job from the browser** feature before editing code.
3. Preserve the working fresh-session boundary and remote browser MCP path.
4. Keep installer/signing/onboarding hardening out of scope.
5. Implement, test, run, and visually verify the real browser → left-side job-list flow.

Expected product behavior:

- From a listing in the embedded browser, click **Add to JobOS**.
- Extract company, role, location, URL, and job description.
- Add the job to the left-side list.
- Link the current tab to the new job.
- Prevent duplicate jobs.
