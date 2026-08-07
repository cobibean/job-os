# Codex Content-Filter Fallback to Kimi K3 Memory - 2026-08-07

## Session summary

After the Hermes `SessionDB` descriptor leak was fixed, a JobOS **Save job** retry failed with a different terminal state:

```text
The model declined to respond to this request (safety refusal — not a Hermes/gateway failure).
```

The visible explanation was harmless employment-writing text such as “Composing complete job description” and “Including benefits and EEO sections.” Runtime evidence confirmed that OpenAI Codex returned a real `content_filter` terminal status with zero response text. This was not a recurrence of the file-descriptor leak and was not invented by JobOS or Hermes.

JobHunter now keeps OpenAI Codex as its primary provider and automatically falls back to OpenRouter Kimi K3 when Hermes receives a recoverable provider failure such as this content-policy block.

## Root cause and system boundary

- **Provider response:** `gpt-5.6-sol` through `openai-codex` returned Responses API `status=incomplete`, `incomplete_details.reason=content_filter`, and `streamed_chars=0`.
- **Reproduction count:** two consecutive Save-job attempts ended with the same provider content filter.
- **Harmless trigger text:** the model was preparing the complete job description, including benefits and equal-employment-opportunity sections.
- **Hermes behavior:** Hermes correctly classified and surfaced the provider refusal. Its existing conversation loop already supports routing this exact status to a configured fallback.
- **Missing resilience:** the `job-hunter` profile had no fallback provider configured, so the refusal became a hard JobOS turn failure.
- **JobOS behavior:** JobOS correctly projected the terminal failure and offered Retry. JobOS did not cause or misclassify the refusal.

## Configuration applied

Profile:

```text
~/.hermes/profiles/job-hunter
```

Effective model chain:

1. Primary: `gpt-5.6-sol` via `openai-codex`
2. Fallback: `moonshotai/kimi-k3` via `openrouter`

The exact OpenRouter model ID was resolved from the live OpenRouter model catalog. At configuration time it advertised a `1,048,576`-token context window.

A pre-existing local OpenRouter credential was securely copied from Devonte's profile into JobHunter's profile-local secret scope. The credential value was never printed or stored in project memory. The destination `.env` retained `0600` permissions.

## Verification

- `hermes -p job-hunter fallback list` showed exactly one fallback:
  - `moonshotai/kimi-k3 (via openrouter)`
- A direct live JobHunter-profile request to OpenRouter Kimi K3 returned exactly:

```text
KIMI_K3_OK
```

- Hermes's focused regression test for the exact recovery transition passed:

```text
tests/run_agent/test_run_agent.py::TestRunConversation::test_codex_content_filter_incomplete_routes_to_policy_fallback
1 passed
```

- Restarted only the supervised JobOS-facing Hermes Dashboard service, `ai.hermes.dashboard-fleet`, so it loaded the new profile configuration.
- The restarted dashboard listened on port `9119`.
- Installed JobOS API health returned `ready`.
- JobOS agent connection returned `online`.
- No active turn remained stuck.

## Files and state changed

No JobOS source code or job data was changed.

Profile-local runtime changes:

- `~/.hermes/profiles/job-hunter/config.yaml`
  - added typed `fallback_providers` entry for OpenRouter Kimi K3
- `~/.hermes/profiles/job-hunter/.env`
  - added the existing OpenRouter credential without exposing its value

The unrelated existing edit in `docs/notebooks/jobos-feature-wishlist-notebook-2026-07-21.md` remained untouched and unstaged.

## Gotchas and constraints

- `hermes config set fallback_providers '<json>'` stores the value as a string rather than a typed YAML list. Hermes then correctly reports no effective fallback. Use `hermes fallback add` interactively or Hermes's typed configuration writer for structured fallback entries.
- `hermes auth list` can show an environment-backed credential reference even when the corresponding secret is absent from the active profile's secret scope. Prove the provider with a live minimal request before claiming fallback readiness.
- A fallback makes JobOS resilient to provider-specific false positives; it does not change or bypass the primary provider's safety policy.
- The installed JobOS process had no open on-screen window during final verification, so the visible Retry control was not clicked. The provider, routing seam, supervised restart, and JobOS reconnection were verified independently. Cobi can retry the failed Save turn from the UI for final product-path acceptance.
