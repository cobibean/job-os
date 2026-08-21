# Career Profile context and export semantic contract

**Status:** Repository-owned dormant contract for Issues #55–#58

**Semantic fixture:** `tests/fixtures/career-profile-semantic-policy.json`

**Activation boundary:** This contract does not activate complete-profile projection, migration, or live authority.

## Product rule

Evidence is optional provenance. Most ordinary user statements will have no Source Evidence, and that absence is normal. An accepted direct-user statement or exact proposal accepted by the user remains usable for matching, generation, export, and publication regardless of whether Evidence exists, is selected, or later becomes unavailable.

An autonomous agent proposal remains unusable until accepted or otherwise permitted by the configured actor-aware trust contract. Evidence does not change that decision. A conflict, uncertainty, or unavailable-source notice may be shown as an advisory; absent Evidence alone must never create a health penalty, required task, filter exclusion, or generation exclusion.

## Agent context choices

A future complete-profile projection must bind exactly one user-authorized scope before dispatch:

1. **No Career Profile context** — project no Career Profile items or areas.
2. **Selected items or areas** — project only the exact item IDs and/or canonical areas the user selected.
3. **Broader authorized projection** — project the wider typed set covered by the explicit grant; this is not implied by connecting an agent or possessing a snapshot ID.

The bound scope descriptor, profile revision, and content hash are immutable for the logical turn. Retry, recovery, continuation, and subagent follow-up reuse that exact scope. Any request to add an item, area, or broader mode outside the bound authorization fails closed before dispatch. Expanding access requires a separately authorized operation and a new turn-bound snapshot.

Profile text remains non-executable untrusted data. Typed user-approved qualifiers, forbidden-use metadata, and privacy/reuse constraints remain enforceable data-handling boundaries inside every allowed scope.

## Export choices

Every portable export request must make one explicit Evidence inclusion choice. There is no implicit Evidence default:

1. **Profile only** — current structured Career Profile and provenance metadata; no Source Evidence bytes.
2. **Profile plus selected Evidence** — profile-only content plus exactly the selected active Source Evidence objects, hashes, and provenance.
3. **Profile plus all Evidence** — profile-only content plus all Source Evidence covered by the export operation; the user must explicitly choose this mode.

Source files are never silently bundled because they are linked, active, or present in the vault. Inactive historical Evidence links may remain in profile provenance, but unavailable bytes cannot be represented as included export files. Export validation must explain that condition without demoting or omitting the accepted item.

## Required semantic acceptance states

Issues #55–#58 must exercise all fixtures below with synthetic data:

| Fixture | Required result |
|---|---|
| Direct user-authored, no Evidence | Accepted and usable; no Evidence deficit |
| Agent-authored proposal accepted by user, no Evidence | Accepted and usable; original agent/proposal provenance preserved |
| Unapproved autonomous agent proposal | Proposed and unusable even if Evidence is present |
| Accepted content with advisory conflict | Usable; conflict remains visible and non-blocking |
| Accepted content with removed/inactive Evidence | Usable; historical unavailable link round-trips |
| Sparse profile/empty areas | Valid migration, context, export, and cutover candidate |
| Zero-Evidence profile | Valid migration, context, export, and cutover candidate |

The machine-readable fixture is a policy oracle rather than a live-user fixture. Its tests protect these semantics independently of generated OpenAPI or TypeScript contract freshness.

## Issue acceptance ownership

- **#55:** actor-aware proposal/direct-edit behavior must not require proposal Evidence and must distinguish exact user authority from an agent assertion.
- **#56:** Evidence-neutral product copy, none/selected/broader context controls, and all three explicit export choices must pass packaged accessibility and product acceptance.
- **#57:** sparse and zero-Evidence migration fixtures must preserve unknown values, accepted Evidence-free content, exact selected scopes, and imported proposal provenance without invented defaults.
- **#58:** the fresh explicit cutover approval gate remains unchanged. Before an approved cutover, installed proof must cover sparse/zero-Evidence profiles, exact scope retention on retry/recovery, failed unauthorized expansion, accepted unsupported claims, selective exports, and restart/readback.

## Non-goals

This issue does not activate complete Career Profile projection, add live agent permissions, execute migration, change current authority, or weaken authentication, structural validation, exact intent binding, revision/hash integrity, privacy boundaries, or the fresh approval required by #58.
