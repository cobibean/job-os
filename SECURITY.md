# Security Policy

## Supported versions

JobOS is currently a pre-release source project. Security fixes are applied to
the latest `main` branch; no public binary release is supported yet.

## Reporting a vulnerability

Please do not open a public issue for a suspected vulnerability or include
secrets, private documents, tokens, local paths, or exploit details in public
comments.

Before public launch, report privately to the repository owner through the
contact options on the GitHub repository profile. GitHub private vulnerability
reporting will become the preferred channel when it is enabled at publication.

Include only what is necessary to reproduce the issue:

- affected commit or version;
- impacted component;
- minimal reproduction using synthetic data;
- expected and actual behavior;
- likely impact;
- suggested mitigation, if known.

You should receive an acknowledgement within seven days. Timelines for a fix or
disclosure depend on severity and reproducibility. Please allow a reasonable
remediation window before public disclosure.

## Scope

Especially relevant reports include:

- exposure of local documents, job records, credentials, or conversation data;
- authentication or capability-routing bypasses;
- path traversal, unsafe archive handling, or symlink escapes;
- secret leakage through logs, diagnostics, renderer state, or error responses;
- supply-chain or packaged-notice integrity problems.

JobOS is designed as a local, single-user application—not a public multi-tenant
service. Reports should be evaluated against that deployment boundary while
still protecting the user's files and credentials.
