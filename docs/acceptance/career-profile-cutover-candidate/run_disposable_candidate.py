from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.career_profile_migration import (
    CareerProfileMigrationBundle,
    CareerProfileMigrationService,
)
from jobos_api.settings import Settings
from jobos_api.state_store import JobOsStateStore
from jobos_mcp.jobs import JobOsMcpClient

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "services/api/tests/fixtures/career-profile-migration-full.json"
OUTPUT = Path(__file__).resolve().parent
DEVICE_TOKEN = "(FAKE)-disposable-device-token"
MCP_TOKEN = "(FAKE)-disposable-mcp-token"
AGENT_TOKEN = "(FAKE)-disposable-agent-token"


def runtime_settings(database: Path) -> Settings:
    return Settings(
        device_id="primary-device",
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        state_db_path=database,
        career_profile_enabled=True,
        career_profile_agent_id="job-hunter",
        career_profile_agent_display_name="(FAKE) Job Hunter",
        career_profile_agent_token=AGENT_TOKEN,
    )


def user_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEVICE_TOKEN}"}


def agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {MCP_TOKEN}",
        "X-JobOS-MCP-Token": MCP_TOKEN,
        "X-JobOS-Agent-Id": "job-hunter",
        "X-JobOS-Agent-Token": AGENT_TOKEN,
    }


async def mcp_read(app) -> dict[str, object]:
    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token=DEVICE_TOKEN,
        mcp_token=MCP_TOKEN,
        agent_id="job-hunter",
        agent_token=AGENT_TOKEN,
        transport=httpx.ASGITransport(app=app),
    )
    try:
        return await client.get_career_profile_projection()
    finally:
        await client.aclose()


def main() -> None:
    temporary = Path(tempfile.mkdtemp(prefix="jobos-issue-57-disposable-"))
    try:
        database = temporary / "jobos.db"
        evidence_root = temporary / "career-profile-evidence"
        JobOsStateStore(database).initialize(owner_device_id="primary-device")
        service = CareerProfileMigrationService(database, evidence_root)
        service.complete.initialize()
        bundle = CareerProfileMigrationBundle.model_validate_json(FIXTURE.read_text())
        report = service.run(bundle)
        replay = service.run(bundle)
        if replay != report:
            raise RuntimeError("idempotent migration replay changed the report")

        app = create_app(runtime_settings(database))
        transcript: dict[str, object] = {
            "fixture": str(FIXTURE.relative_to(ROOT)),
            "temporary_root": "[DISPOSABLE ROOT REDACTED]",
            "migration_replay_equal": True,
            "pre_activation_authority": report.authority_state,
        }
        with TestClient(app) as api:
            profile = api.get("/v1/career-profile", headers=user_headers())
            profile.raise_for_status()
            accepted = profile.json()["items"]
            selected_item_id = accepted[0]["item_id"]
            scope = api.put(
                "/v1/career-profile/agents/job-hunter/context",
                headers=user_headers(),
                json={
                    "expected_profile_revision": report.profile_revision,
                    "expected_authority_epoch": 0,
                    "idempotency_key": "acceptance-scope-0001",
                    "mode": "selected",
                    "selected_item_ids": [selected_item_id],
                    "selected_areas": [],
                },
            )
            scope.raise_for_status()
            dormant = api.get(
                "/v1/career-profile/consumer-projection",
                headers=agent_headers(),
            )
            transcript["dormant_projection_status"] = dormant.status_code
            activation = api.post(
                "/v1/career-profile/authority/activate",
                headers=user_headers(),
                json={
                    "expected_profile_revision": report.profile_revision,
                    "expected_authority_epoch": 0,
                    "idempotency_key": "acceptance-activation-0001",
                    "confirmation": "CUT OVER CAREER PROFILE AUTHORITY",
                },
            )
            activation.raise_for_status()
            transcript["disposable_activation"] = activation.json()
            projection = api.get(
                "/v1/career-profile/consumer-projection",
                headers=agent_headers(),
            )
            projection.raise_for_status()
            projected_items = projection.json()["projection"]["items"]
            if [item["item_id"] for item in projected_items] != [selected_item_id]:
                raise RuntimeError("selected projection expanded beyond the exact grant")
            if projection.json()["projection"]["source_evidence"]:
                raise RuntimeError("selected projection expanded to Source Evidence")
            transcript["api_projection"] = {
                "mode": projection.json()["scope"]["mode"],
                "item_count": len(projected_items),
                "authority_state": projection.json()["projection"]["authority_state"],
            }
            try:
                service.run(bundle)
            except RuntimeError as error:
                transcript["legacy_writer_refusal"] = str(error)
            else:
                raise RuntimeError("legacy writer did not fail closed")

        restarted = create_app(runtime_settings(database))
        with TestClient(restarted) as api:
            readback = api.get("/v1/career-profile/authority", headers=user_headers())
            readback.raise_for_status()
            transcript["restart_authority"] = readback.json()
            transcript["mcp_projection"] = asyncio.run(mcp_read(restarted))

        (OUTPUT / "migration-report.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
        )
        (OUTPUT / "sanitized-transcript.json").write_text(
            json.dumps(transcript, indent=2, sort_keys=True) + "\n"
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
