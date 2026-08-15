# Data and Privacy

JobOS is designed for one person working with local job-search records and
documents. Protecting those files from accidental publication is a primary
product boundary.

## Current status

The repository is still in open-source preparation. Current source contains
operator-specific defaults and private project memory that must be removed from
the publication candidate and rewritten history before launch. The public alpha
must not ship until the privacy and history gates pass.

## Public-alpha data model

The accepted public composition will store:

- configuration without secrets;
- canonical jobs and job history in a local SQLite database;
- workbench/UI state in a separate local SQLite database;
- generated and edited artifacts under a configured local artifact root;
- logs under the user's application-data directory;
- credentials in the platform credential provider, with a documented restrictive
  source-development fallback.

A fresh profile will contain one unmistakably synthetic demo job. No real jobs,
people, companies, resumes, cover letters, browser history, or conversations may
be included in source, fixtures, documentation, or launch media.

## Data locations

During current source development, API workbench state defaults to
`data/jobos.db` unless explicitly configured. Installed macOS runtime files use
the user's JobOS Application Support directory. Later initialization work will
make the complete layout configurable and document exact backup/reset behavior.

Do not commit runtime databases, logs, exports, backups, support bundles,
credentials, `.env` files, local runtime configuration, or `.DS_Store` files.

## Network behavior

The public default will bind to local loopback and will not require Tailscale,
Hermes, JobHunter, a second machine, or a private network. Optional integrations
must identify themselves as configured/unconfigured and must not silently become
public defaults.

## Diagnostics and errors

Public diagnostics should expose only safe versions, selected non-secret mode,
and capability states. Tokens, environment values, absolute private paths, raw
exceptions, upstream response bodies, and document contents must not appear in
renderer state, screenshots, logs intended for sharing, or public error payloads.

## Backup, reset, and uninstall

The current source build has no supported one-command backup or reset flow. Use
this conservative procedure until the clean-home lifecycle is implemented:

1. Stop the desktop application and local API before copying or moving data.
2. For source development, copy `data/jobos.db`. For an installed private macOS
   build, copy the entire JobOS Application Support directory, inspect
   `service/runtime.json`, and also copy every configured `state_db_path`,
   `job_hunter_db_path`, and `artifact_roots` location. Those absolute paths can
   point outside Application Support.
3. Verify that every backup exists and is non-empty before editing, resetting,
   or uninstalling anything.

File backups do not include credentials stored in macOS Keychain. Plan to
reconfigure those credentials after a restore. Do not export tokens into the
backup, documentation, a support bundle, or a public issue.

There is no supported public reset command yet. If a source developer must start
fresh, move the stopped runtime data aside rather than deleting it, then restart.
That is a manual recovery technique, not accepted public onboarding.

Removing the application or source checkout does not intentionally remove the
separate Application Support/runtime data. Remove that data separately only
after making and checking a backup. Future public initialization will document
exact directories, confirmation prompts, synthetic-demo persistence, and a
tested restore path.

These behaviors are not considered accepted until the clean-home and restart
suite proves them.

## Reporting privacy issues

Follow `SECURITY.md`. Never attach private source data to a public issue; create a
minimal reproduction with synthetic content instead.
