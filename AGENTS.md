# JobOS Contributor Guidance

## Product boundary

JobOS is a source-first, local-first desktop workbench. Public defaults must run on loopback with local SQLite storage, local artifacts, and no private network or external agent requirement.

- Keep optional integrations behind explicit adapters and validated configuration.
- Never make JobHunter, Hermes, Tailscale, a particular computer, or an operator-specific path a public startup requirement.
- Do not commit real job records, documents, credentials, runtime databases, logs, exports, support bundles, or private deployment instructions.
- Use only synthetic fixtures that are listed in `tests/public-release/synthetic-fixtures.json`.
- Preserve stable, capability-based errors when optional integrations are unavailable.

## Local checkout and worktrees

- Treat `~/DEV/dependencies/job-os` as the canonical local checkout. Keep it on a clean `main` branch that tracks `origin/main`.
- Before starting repository work, fetch `origin` and fast-forward local `main` with `git merge --ff-only origin/main`. Do not treat a stale local `main` as the starting point.
- Small solo changes may be made directly in the canonical checkout when no concurrent work or review isolation requires a separate branch directory.
- Create a worktree only for genuinely concurrent or isolated work. Start it from the synchronized local `main`, not as a workaround for a stale canonical checkout.
- After a worktree branch is merged, verify the remote result, preserve any unrelated changes, and remove the clean worktree. Do not leave `main` checked out in a task-named worktree.

## Code quality

- Prefer small components, focused interfaces, direct data flow, and readable names.
- Preserve path containment, symlink resistance, checksums, atomic writes, migration locking, and redaction at filesystem and API boundaries.
- Regenerate OpenAPI and TypeScript contracts whenever public schemas change.
- Keep source-first and packaged-app claims truthful; packaging support is not implied by source portability.

## Verification

Before opening a pull request, run:

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
```

For public-boundary work, also run the clean-clone and expected-red gates documented in `docs/public/release-process.md`.

## Document artifacts

- A submission-ready resume or cover letter is a matched PDF/DOCX pair generated from the same revision.
- Agent publication must use the app-owned inbox returned by `document_publication_prepare`; do not add workspace, profile-cache, or operator-defined path fallbacks.
- Verify both files and their checksums before describing publication as complete.
- Real user documents are never Git fixtures or public-release evidence.
