# Agent Turn Output Ordering Installed Acceptance Memory - 2026-07-24

## Accepted source and candidate

- Source commit: `5b1124703b3645127819d7f0b126f709fde63baf`.
- `origin/main` was verified at that exact commit before packaging.
- Packaging used the clean detached worktree `/Users/jacobilangemm/DEV/dependencies/job-os-turn-order-release`.
- Clean-candidate `pnpm check` passed: 207 desktop tests, 339 Python tests with one expected skip, lint, type checks, production build, and packaged-renderer verification.

## Mac mini installation proof

- Packaged app: `release/desktop/mac-arm64/JobOS.app`.
- Package ZIP: `JobOS-0.1.0-arm64.zip`.
- Package size: `143,883,358` bytes.
- Package SHA-256: `da4be39c5d0bf9277c368d15642ab386882b4508579c76cc42991e028370e92e`.
- ZIP integrity, deep strict code signature, arm64 Electron executable, and arm64 Keychain helper passed.
- Previous Mini install was backed up before replacement.
- Installed target: `/Users/jacobilangemm/Applications/JobOS.app`.
- Installed `app.asar`, Keychain helper, and executable hashes matched the packaged candidate exactly.
- The running process was verified from the exact installed path.

## Installed visual acceptance

The exact installed app launched against the authoritative Mini runtime and visibly showed:

- green **Mac Mini connected** state;
- one collapsed **Agent activity** disclosure with `25 actions completed`;
- that activity group before exactly one final rich-text assistant response;
- readable Markdown bullets and inline code;
- preserved historical terminal rows below the completed turn;
- an enabled composer and visible **New session** control;
- no overlap, clipping, or obvious layout regression at the installed 1440×960 window.

Screenshot evidence is stored in the Devonte profile cache as `screenshots/jobos-turn-order-installed-initial-20260724.png`.

Interactive background clicks reached the exact Electron control coordinates but Electron ignored them. Foreground-control permission was requested and timed out, so active/waiting/stopping, detached-scroll, reset, and Retry transitions were not re-exercised through the installed GUI. Those states remain covered by the 207-test desktop suite and focused component/projection tests. Do not describe them as newly observed installed-GUI states.

## MacBook updater

- Bundle: `JobOS-MacBook-Turn-Ordering-Update-2026-07-24.zip`.
- Bundle size: `143,493,013` bytes.
- Bundle SHA-256: `7d080868b816de76a413f5930f1b089bdac9a7202047b7865466b8726557fb31`.
- The updater preserves the existing MacBook runtime configuration and Keychain credential.
- Outer ZIP integrity, updater shell syntax/executable bit, nested ZIP integrity, nested deep signature, arm64 app/helper, and the nested package SHA-256 all passed after clean extraction.
- Direct Taildrop initially failed while the MacBook was offline.
- After the MacBook returned online, the verified updater was successfully sent to `jacobis-macbook-pro:` and a local receipt recorded the exact filename, size, and SHA-256.
- The temporary sender watchdog was removed after successful delivery.

## Rollback and limitations

- Mini rollback copy: `/Users/jacobilangemm/.hermes/profiles/devonte/cache/jobos-backups/JobOS-before-turn-order-20260724-170720.app`.
- The app is ad-hoc signed and private-demo scoped; it is not notarized or public-distribution ready.
- MacBook installation confirmation remains pending until Cobi accepts the Taildrop, unzips it, and runs `Update JobOS.command`.
