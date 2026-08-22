# Career Profile migration inventory

**Status:** Issue #57 public-safe candidate inventory. This document describes legacy source shapes only; it contains no live values, private paths, or derived user facts.

## Authority boundary

Issue #57 builds and proves a staging candidate. It does not build a real migration bundle, activate a live profile, modify a legacy workspace, or switch authority. Before cutover, legacy sources remain authoritative. After the separately approved atomic cutover, JobOS is the sole Career Profile authority.

There is no dual-write, shadow authority, bidirectional synchronization, or compatibility-file projection. Rollback is intentionally unrehearsed.

## Source classes and code-owned mapping

`career_profile_migration.MAPPING_POLICY` owns classification. A bundle supplies a known mapping key and value; it cannot supply or override a deterministic/inference label. Unknown mapping keys fail validation. Duplicate deterministic assertions for one profile-wide singleton mapping with different canonical values are code-forced to conflict Proposals; repeatable rows remain independent records.

| Legacy source shape | Typed JobOS target | Code mapping | Disposition |
|---|---|---|---|
| Candidate identity/contact fields | `IdentityValue` | `canonical.identity` | Deterministic exact fact |
| Education ledger row | `EducationValue` | `canonical.education` | Deterministic exact fact |
| Named skill row | `SkillValue` | `canonical.skill` | Deterministic exact fact |
| Positioning/headline prose | `PositioningValue` | `canonical.positioning` | Inference-required Proposal |
| Experience ledger row | `ExperienceValue` | `canonical.experience` | Deterministic exact repeatable fact |
| Project inventory row | `ProjectValue` | `canonical.project` | Deterministic exact fact |
| Claims/accomplishment registry row | `ClaimValue` | `canonical.claim` | Deterministic only when already structured and user-authored/approved |
| Search role list | `TargetRolesValue` | `search.target_roles` | Deterministic exact preference |
| Compensation fields | `CompensationValue` | `search.compensation` | Deterministic only for explicitly present fields |
| Location/relocation fields | `LocationPreferenceValue` | `search.location` | Deterministic only for explicitly present fields |
| Work-arrangement fields | `WorkArrangementProfileValue` | `search.work_arrangement` | Deterministic only for explicitly present fields |
| Industry list | `IndustryPreferencesValue` | `search.industries` | Deterministic exact preference |
| Priority row | `PriorityValue` | `search.priority` | Deterministic exact preference |
| Dealbreaker row | `DealbreakerValue` | `search.dealbreaker` | Deterministic exact constraint |
| Source-document extracted statement | matching typed value | `source.extracted` | Inference-required Proposal |
| Ambiguous source statement | matching typed value | `source.ambiguous` | Inference-required Proposal |
| Conflicting source statement | matching typed value | `source.conflict` | Conflict Proposal |
| Product-specific extension | `CustomValue` | no generic caller mapping | Must first receive an explicit code-owned mapping |

Unknown values are represented by absent facts or absent optional fields. The migration never invents dates, currency, compensation period, current-work status, preference strength, relocation intent, work arrangement, or Evidence.

## Source Evidence

Résumé, portfolio, supporting-document, and citation bytes become immutable managed Source Evidence. Each managed copy records opaque ID, original display filename, media type, optional captured date, import timestamp, source kind/label, byte count, and SHA-256. Installation is descriptor-relative, no-follow, atomic, hash-verified, and idempotent. Profile values never contain source bytes or server paths.

## Required consumers switched together

The release candidate provides one authenticated least-privilege JobOS projection for the required post-cutover Job Hunter flows. Read-only inspection identified the concrete legacy entry points that Issue #58 must switch together:

| Consumer | Current legacy entry point | Cutover source |
|---|---|---|
| Identity and candidate-document preflight | `src/job_hunter/candidate_identity.py` (`find_candidate_profile`, `load_candidate_identity`, and document validation) discovers and reads `resume/canonical/candidate-profile.yaml`. | Identity values from the authenticated JobOS projection. |
| Résumé source selection and publication | `src/job_hunter/facade.py` resolves the standard source under `resume/variants/standard.md`; `src/job_hunter/resume_generator/service.py` renders that source. Alignment artifacts reference `resume/canonical/*` evidence. | Career facts and managed Source Evidence from the projection; generated documents remain Job Hunter outputs. |
| Search-query generation and calibration | `src/job_hunter/search_queries/generator.py` builds calibration from workspace instructions, search seeds, rubric text, and packaged query profiles. | User-owned target roles, location, arrangement, industries, priorities, and dealbreakers from the projection; operational source/query mechanics stay configured locally. |
| Similarity and scoring calibration | `src/job_hunter/similarity_examples.py` loads ideal/strong-example profiles and `src/job_hunter/similarity_features.py` loads similarity rules. | Career/search constraints from the projection; generic scoring rules and example mechanics remain outside Career Profile authority. |
| Agent prompt/context assembly | Workspace `AGENTS.md` and canonical résumé files currently supply reusable career facts to agent workflows. | Exact `none`, `selected`, or `broader` JobOS projection supplied to the connected agent. |

Each consumer reads the exact persisted `none`, `selected`, or `broader` grant through the JobOS API/MCP projection. The projection is dormant in `staging` and fail-closed until the exact-confirmation authority operation persists `cutover`. Operational query mechanics, runtime configuration, job/application state, generated documents, and browser/tool state remain outside the authority switch.

## Writer inventory and fences

| Writer shape | Fence in Issue #57 candidate |
|---|---|
| One-time JobOS migration command | Mechanical: requires a fresh staging profile; it is refused after cutover with a stable fenced error. |
| JobOS-side legacy import/sync adapters | None exist in the candidate. Any future adapter must use the authenticated Career Profile API and the same authority check. |
| Connected-agent ordinary edits | Mechanical: authenticated agent principal, user-owned edit mode, revision/idempotency checks, and exact granted projection. Work arrangement leaves the tracer boundary only after cutover. |
| Legacy canonical résumé/profile file writers outside JobOS | Verified-contractual: redirect workflows that edit `resume/canonical/*` or `resume/variants/standard.md` during Issue #58; post-cutover edits must not influence JobOS or agent behavior. |
| Legacy search-preference writers outside JobOS | Verified-contractual: remove or redirect authority writes during Issue #58; operational query mechanics remain out of scope. |
| Ad hoc source-document edits outside JobOS | Contractual: imported managed bytes remain immutable; changing an external original never rewrites imported Source Evidence. |

JobOS does not add OS file locks or mutate external workspaces. External writer removal and the “legacy edit has no effect” proof belong to the separately approved live cutover.

## Candidate fixtures

- `career-profile-migration-full.json`: all three areas, multiple Source Evidence objects, accepted exact facts, and inference Proposals.
- `career-profile-migration-sparse.json`: empty areas and unknown values remain absent.
- `career-profile-migration-zero-evidence.json`: accepted user-authored content with no Source Evidence.
- `career-profile-migration-conflict.json`: conflicting duplicate deterministic assertions are forced into review-only Proposals.

Every user-like fixture value is visibly `(FAKE)` and each fixture is registered in `tests/public-release/synthetic-fixtures.json`.
