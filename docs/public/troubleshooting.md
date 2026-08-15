# Troubleshooting

JobOS is pre-release source software. Start with the locked verification path so
version drift is separated from application defects.

## Confirm tool versions

```bash
node --version      # expected v26.5.0
pnpm --version      # expected 10.33.1
python3 --version   # expected Python 3.11.x
uv --version        # CI pins 0.11.18
```

If these differ, align them before debugging generated contracts or native
helpers.

## Install from a clean checkout

```bash
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
```

Do not remove lockfiles or switch to unlocked installs to hide a resolution
failure. Include the failing command and sanitized error in a bug report.

## Native helper build fails on macOS

Install or repair Xcode Command Line Tools, then rerun the exact desktop build:

```bash
xcode-select -p
pnpm --filter @jobos/desktop build
```

Linux CI skips macOS-only Swift helper compilation; a Linux pass is not proof
that native macOS helpers work.

## Generated contracts drift

Run:

```bash
pnpm contracts:generate
pnpm contracts:check
```

If generation changes tracked files, include those outputs in the same pull
request as the API schema change. Do not hand-edit generated clients.

## Desktop opens but services are unavailable

That is currently possible during open-source preparation. The public local
composition and onboarding flow have not landed yet. Do not add private paths or
credentials merely to make a public-source test appear connected. Capture only
safe capability/status text and check the current release-status section in the
README.

## Resetting local development state

Current source-development state may exist at `data/jobos.db` and
`data/jobs.db`; installed macOS
state lives under the user's JobOS Application Support directory. Back up files
before deleting anything. The accepted public reset command will arrive with the
idempotent initializer and is not available yet.

## Filing a useful bug

Include:

- commit SHA;
- operating system and architecture;
- Node, pnpm, Python, and uv versions;
- exact command;
- minimal synthetic reproduction;
- expected and actual behavior;
- sanitized logs with tokens, private paths, jobs, and document contents removed.

Use `SECURITY.md` instead of a public issue when confidentiality matters.
