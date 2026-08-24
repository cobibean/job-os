# Connected Agents Phase 0 acceptance assets

This directory freezes the source-first baseline and the exact Codex redistribution
candidate for issue #111. The JSON files are receipts, not claims that the reserved
full-suite, packaged, installed, remote, or human acceptance runs have occurred.

The executable proof harness and visibly synthetic fixtures live in
`tests/connected_agents/`. `run_phase0_baseline.py` executes the REG-01 command map,
writes only hashes/counts/statuses to `verification-results.json`, and can add a
read-only authenticated installed-Hermes control smoke. No Codex archive, executable,
credential, user database, or raw command output is tracked.

The v31 fixture generator intentionally runs only while production remains on schema
v31. Later migrations must continue restoring the frozen SQL fixture directly; normal
tests do not require regenerating historical evidence from a newer production schema.
The verification receipt likewise checks its recorded immutable Git commit rather than
forcing unrelated future changes to rewrite Phase 0 evidence.

Regenerate the committed receipt with the complete command map and the read-only
installed control check:

```bash
uv run python tests/connected_agents/run_phase0_baseline.py \
  --include-full-gates --installed-smoke
```
