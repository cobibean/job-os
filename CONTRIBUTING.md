# Contributing to JobOS

Thanks for helping improve JobOS. The project is preparing a source-first public
alpha, so small, focused changes with direct evidence are preferred.

## Before you start

- Read the [architecture overview](docs/public/architecture.md).
- Search existing issues and pull requests.
- For a substantial feature or contract change, open an issue before coding.
- Never include real job records, resumes, cover letters, browser history,
  credentials, local paths, private network details, or session memory.
- Use synthetic examples and reserved domains such as `example.com`.

## Development setup

Use Node.js `26.5.0`, pnpm `10.33.1`, Python `3.11`, and uv `0.11.18`.

```bash
npm install --global pnpm@10.33.1
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
pnpm check
pnpm contracts:check
```

## Pull requests

Keep each pull request narrow and explain:

1. the user-facing problem;
2. the smallest chosen solution;
3. files and contracts changed;
4. commands and real checks run;
5. privacy, migration, and compatibility implications;
6. anything that remains unverified.

Add or update focused tests. For UI changes, include synthetic screenshots and
check keyboard focus, reduced motion, empty/loading/error states, and practical
window sizes. Do not upload screenshots captured from a private profile.

Generated contracts must be regenerated and committed with their source change.
Run `pnpm contracts:check` to prove there is no drift.

## Code style

- Prefer small functions/components and descriptive names.
- Keep data flow direct and architecture proportional to the local single-user
  product.
- Preserve path containment, checksum identity, idempotency, and recoverable
  failure behavior.
- Avoid unrelated rewrites in a focused change.

## Licensing

Unless explicitly stated otherwise, contributions intentionally submitted to
JobOS are provided under the [Apache License 2.0](LICENSE), consistent with
Section 5 of that license. Preserve upstream copyright, license, NOTICE, and
provenance files when modifying derived code.

By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
For vulnerabilities, follow [SECURITY.md](SECURITY.md) instead of filing a
public issue.
