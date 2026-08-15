# JobOS open-source readiness Phase 0–1 sprint memory — 2026-08-15

## Publication classification

**Private implementation memory.** This file belongs in the current private
`docs/memory/**` tree for continuity during the open-source sprint. Phase 5 must
archive this tree privately, checksum the archive, and remove it—including this
file—from the publication candidate before any repository visibility change.

## Session summary

Cobi started the JobOS open-source readiness implementation sprint after first
recovering and confirming the product/publication direction. Phase 0 established
permanent baseline and red-gate tests. Phase 1 established the Apache-2.0 project
identity, public repository entry points, dependency-license checks, and packaged
legal materials.

Both phases were implemented in isolated worktrees, reviewed independently,
merged through pull requests, and verified again on `main`. The canonical JobOS
checkout's two pre-existing edits were left untouched throughout:

- `services/api/jobos_api/jobs.py`
- `services/api/tests/test_jobs_contract.py`

The repository remains private. No history rewrite, visibility change, binary
publication, production deployment, force-push, or branch deletion occurred.

## Publication decisions locked before implementation

1. **License:** Apache License 2.0. The intent is a permissive,
   commercial-friendly license with an explicit patent grant.
2. **JobHunter boundary:** JobHunter remains private. Public JobOS owns an adapter
   interface and will include a synthetic/local implementation rather than the
   private package.
3. **First run:** initialize a real local workspace and preload exactly one
   clearly labeled synthetic demo job. There will not be a separate fake demo
   workspace, and deleting the demo job must remain persistent.
4. **Repository history:** sanitize and rewrite the existing repository's full
   history rather than simply toggling the current private history public. The
   destructive cutover requires a verified backup, exact privacy inventory,
   isolated rewrite rehearsal, rollback proof, and Cobi's fresh explicit approval
   immediately before replacing shared history or changing visibility.
5. **Launch media:** a short silent README GIF plus polished screenshots using
   synthetic data. No narrated launch video is currently planned.

The core product boundary remains: open-source the reusable JobOS workbench, not
Cobi's personal installation, data, memory, conversations, credentials, browser
profile, private network, or machine-specific configuration.

## Phase 0 — permanent baseline and red gates

Phase 0 shipped through pull request `#1`. Its initial merge commit was
`379bda45d91f7e53c2d04d44bf5059677ec3cd25`; the accepted Phase 1 base after
Phase 0 follow-up hardening was
`e4b1b499024295962313936616f3d08bbe5a6e1f`.

Phase 0 added:

- a public-composition smoke test;
- tracked-source scanning for direct private `job_hunter` imports;
- scanning for operator/private-network defaults;
- prohibited tracked-path classification covering private memory, databases,
  logs, credentials, backups, exports, `.env` files, and `.DS_Store`;
- a checksum-pinned publication manifest for every tracked binary asset;
- tracked provenance for synthetic DOCX fixtures;
- default pytest collection for all public-release gates.

The normal suite carries exactly three strict expected failures:

1. direct JobHunter imports;
2. operator/private-network defaults;
3. private tracked paths and operating-system metadata.

The explicit `--runxfail` acceptance command proved that those exact three gates
are genuinely red rather than accidentally skipped. The baseline counts are
snapshots, not permanent contracts: the initial scan found two direct JobHunter
imports, fourteen operator/private-default locations, and roughly fifty-five
tracked path violations dominated by `docs/memory/**` plus `.DS_Store` files.

Phase 0 verification included a green full `pnpm check`, 360 desktop tests, the
full Python suite, focused native DOCX tests, Ruff, diff checks, pull-request CI,
and separate post-merge `main` CI run `31888883260`.

## Phase 1 — identity, documentation, and legal gates

Phase 1 shipped through pull request `#2` and merged to `main` at
`a1e1c04800afce9bdfae1b67c243dd4acc6b8764`.

Phase 1 added:

- root Apache-2.0 `LICENSE` and `NOTICE`;
- preserved `LICENSE`, `NOTICE`, and pinned `UPSTREAM.md` provenance for both
  GenOffice-derived DOCX packages;
- prominent JobOS modification notices in the opening lines of all 121 tracked
  adapted source, test, and script files;
- `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, GitHub issue
  forms, and the pull-request template;
- public architecture, data/privacy, troubleshooting, and release-process docs;
- Apache-2.0 repository metadata while retaining `"private": true` solely as
  protection against accidental package-registry publication;
- dynamic discovery of every tracked Node package manifest and every uv Python
  workspace member for direct-dependency license verification;
- nine required legal/provenance resources in the Electron package and a verifier
  that checks the files inside a real unpacked `.app`;
- conservative backup/reset/uninstall guidance covering Application Support,
  configured database and artifact paths outside that directory, and credentials
  that remain in macOS Keychain rather than file backups.

The README is intentionally conservative. It says JobOS is a source-first,
pre-release alpha; there is no supported public binary; a clean checkout does not
yet provide accepted first-run onboarding; local mutable jobs and the synthetic
demo arrive in later phases; optional integrations are not public defaults.

## Phase 1 verification and review

Final evidence:

- focused public-release suite: `13 passed, 3 xfailed`;
- public-identity suite after the last documentation correction: `8 passed`;
- full desktop suite: 50 files / 360 tests passed;
- full Python/public suite: `401 passed, 2 skipped, 3 xfailed`;
- lint, TypeScript checks, Python checks, production build, and generated-contract
  drift checks passed;
- dependency inventory resolved 28 direct Node dependencies and six direct Python
  dependencies with no unknown license category;
- a real unpacked macOS arm64 app built successfully without signing or
  publication;
- all nine legal/provenance resources were present in the real `.app`;
- added-line scans found no absolute user home, private-network IP, obvious secret
  assignment, or Cobi-specific resume/cover-letter data;
- independent final Codex review reported no blocker, high, or medium findings;
- CodeRabbit and pull-request CI passed;
- separate post-merge `main` CI run `31904253556` passed on the pinned Node 26.5.0
  toolchain.

Local verification used Node 22.23.1 and therefore emitted the expected engine
warning. GitHub CI supplied the authoritative pinned Node 26.5.0 proof.

## Late asynchronous audit disposition

Two read-only subagent audits were launched while Phase 1 was still being edited
and returned after the phase had merged. Their reports describe the draft state
at `e4b1b49`, so findings must be separated into **resolved during Phase 1** and
**publication follow-ups**.

Resolved before merge:

- missing Apache §4(b) change notices: fixed across all 121 adapted files;
- incomplete direct dependency table entries: corrected;
- missing root/package legal resources in Electron packaging: added and verified
  in a real `.app`;
- static dependency-manifest discovery: replaced with dynamic tracked-workspace
  discovery plus regression tests;
- misleading clean-clone/binary/readiness language: replaced with explicit
  source-alpha limitations;
- vague backup/reset/uninstall guidance: replaced with current conservative
  procedures and Keychain caveats.

Still worth explicit legal/release review before publication:

- decide whether to add a machine-readable file-by-file upstream provenance map
  with upstream paths/blob hashes and authored-vs-modified classification; this is
  stronger traceability than Apache §4(b)'s required per-file change notices;
- decide whether copied GenOffice notices need a JobOS addendum explaining stale
  upstream-only tooling/font references while preserving upstream attribution;
- consider deterministic generation of complete redistribution notices from the
  resolved production graph, including obligations for bundled transitive code;
- confirm the legal copyright-holder wording and whether `author: "Cobi"` should
  remain before publication;
- pin and inventory build-system requirements such as Hatchling;
- configure a verified private vulnerability-reporting route before enabling a
  public security surface.

These follow-ups do not reopen Phase 1's accepted implementation scope, but they
must not be forgotten or represented as already completed.

## Remaining plan

- **Phase 2:** built-in mutable SQLite jobs and private JobHunter behind an
  optional adapter.
- **Phase 3:** idempotent local initialization and exactly one synthetic demo job.
- **Later phases:** local artifact ownership, public-safe defaults, archive/remove
  private tracked memory and `.DS_Store`, clean-clone acceptance, synthetic launch
  media, complete release scanning, and the isolated history rewrite rehearsal.
- **Final cutover:** stop button-ready and request Cobi's fresh approval before any
  destructive history replacement or repository visibility change.

The three Phase 0 xfails remain the fastest truth signal for current readiness.
Phase 0 and Phase 1 are complete; JobOS itself is not yet ready to become public.
