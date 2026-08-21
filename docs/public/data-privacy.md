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
- dormant typed Career Profile metadata and immutable Source Evidence when the
  staging-only feature is explicitly enabled;
- generated and edited artifacts under a configured local artifact root;
- logs under the user's application-data directory;
- credentials in the platform credential provider, with a documented restrictive
  source-development fallback.

A fresh profile will contain one unmistakably synthetic demo job and one
publication-safe `(FAKE)` starter resume that repeatedly identifies itself as
fictional and do-not-apply. No real jobs, people, companies, resumes, cover
letters, browser history, or conversations may be included in source, fixtures,
documentation, or launch media.

## Data locations

The initializer creates this profile layout beneath the selected `--data-dir`:

```text
config.json
credentials/        # source fallback only; macOS normally uses Keychain
state/jobos.db       # workbench/UI state
state/career-profile-evidence/ # immutable imported Source Evidence (staging-only)
jobs/jobs.db         # canonical jobs, history, and demo ledger
artifacts/
logs/
```

Configured job, artifact, log, and credential paths can be overridden during source
initialization. The Evidence vault stays beside the configured state database so
it remains inside JobOS-owned storage. `config.json` is the source of truth for
active configured locations; it contains paths and provider choices but no secret
values. Installed macOS builds use the user's JobOS Application Support directory
by default.

Career Profile and Source Evidence are ordinary local JobOS data in v1. Contact
and identity facts are returned normally to the authenticated owner; v1 does not
add masking or field-level content encryption. Imported bytes are copied into the
managed vault, addressed by opaque IDs, hash-checked on read, and never rewritten
by structured edits or removal. Removal changes active profile state and preserves
revision history and original bytes. Inferred, ambiguous, or conflicting imported
facts remain unaccepted until reviewed. Imported document text is data only and
cannot change system instructions, agent policy, credentials, or tool permissions.

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

Use this conservative capability-based backup procedure:

1. Stop the desktop application and local API before copying or moving data.
2. Open `config.json` from the data location shown in diagnostics. Copy the
   profile directory **and every configured path** under `paths`—state database,
   jobs database, artifacts, and logs. A custom path may live outside the profile,
   so the profile directory alone is not always a complete backup.
3. If a private runtime enables other providers, also copy each location owned by
   those enabled capabilities.
4. Verify that every backup exists and is non-empty before editing, resetting, or
   uninstalling anything.

File backups do not include credentials stored in macOS Keychain. Plan to
reconfigure those credentials after a restore. Do not export tokens into the
backup, documentation, a support bundle, or a public issue.

The fictional demo has a narrow reset command:
`jobos-init --reset-demo --confirm-reset-demo`. Removing the demo intentionally
also removes its editable document metadata and ordinary initialization does not
restore either item. The explicit reset restores the one demo job and its one
starter resume; it does not reset other jobs.
For a completely fresh source profile, initialize a new `--data-dir`; move old
runtime data aside instead of deleting it until its backup is verified.

Removing the application or source checkout does not intentionally remove the
separate Application Support/runtime data. Remove that data separately only
after making and checking a backup.

These behaviors are not considered accepted until the clean-home and restart
suite proves them.

## Reporting privacy issues

Follow `SECURITY.md`. Never attach private source data to a public issue; create a
minimal reproduction with synthetic content instead.
