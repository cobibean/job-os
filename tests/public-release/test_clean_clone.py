from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import xml.etree.ElementTree as ElementTree
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

support_path = Path(__file__).parents[1] / "public_release_support.py"
support_spec = importlib.util.spec_from_file_location("jobos_public_release_support", support_path)
assert support_spec is not None and support_spec.loader is not None
support = importlib.util.module_from_spec(support_spec)
support_spec.loader.exec_module(support)
ApiProcess = support.ApiProcess
redact = support.redact
run_json = support.run_json
clean_runtime = support.clean_runtime

pytestmark = pytest.mark.skipif(
    os.environ.get("JOBOS_CLEAN_CLONE") != "1",
    reason="run through scripts/public-release/smoke-clean-clone.py",
)

EXPECTED_HEALTH = {
    "status": "ready",
    "service": "jobos-api",
    "transport": "local-loopback",
    "agent": "not-configured",
    "artifact_storage": "available",
    "artifact_gateway": "not-configured",
}


def fixture() -> dict[str, str]:
    path = Path(__file__).parent / "fixtures/clean-clone-golden.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert all(isinstance(item, str) for item in value.values())
    return value


def assert_response(response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, (response.status_code, response.text[:500])
    value = response.json()
    assert isinstance(value, dict)
    return value


def mcp_value(result) -> dict[str, Any]:
    assert result.isError is False, str(result.content)[:500]
    assert isinstance(result.structuredContent, dict)
    return result.structuredContent


async def call(session: ClientSession, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    with anyio.fail_after(10):
        return mcp_value(await session.call_tool(name, arguments))


async def assert_optional_error(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    code: str,
    *,
    retryable: bool = True,
) -> None:
    with anyio.fail_after(10):
        result = await session.call_tool(name, arguments)
    assert result.isError is True
    text = " ".join(str(item) for item in result.content)
    assert code in text
    assert f"retryable={str(retryable).lower()}" in text
    assert "correlation_id=" in text


@asynccontextmanager
async def mcp_session(
    root: Path,
    environment: dict[str, str],
    *,
    base_url: str,
    device_token: str,
    mcp_token: str,
    label: str,
):
    mcp_environment = {
        **environment,
        "JOBOS_API_BASE_URL": base_url,
        "JOBOS_DEVICE_TOKEN": device_token,
        "JOBOS_MCP_TOKEN": mcp_token,
    }
    error_path = root / "logs" / f"mcp-{label}.stderr.log"
    try:
        with error_path.open("w", encoding="utf-8") as errors:
            parameters = StdioServerParameters(
                command="uv",
                args=["run", "--frozen", "--no-sync", "jobos-mcp"],
                env=mcp_environment,
            )
            async with (
                stdio_client(parameters, errlog=errors) as (reader, writer),
                ClientSession(reader, writer) as session,
            ):
                with anyio.fail_after(10):
                    await session.initialize()
                    tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} >= {
                    "job_list",
                    "job_inspect",
                    "job_select",
                    "job_update_status",
                    "job_update_description",
                    "workspace_inspect",
                    "workspace_update",
                    "document_draft_get",
                    "document_draft_snapshot",
                    "document_refresh",
                    "document_render",
                    "browser_tabs_inspect",
                }
                yield session
    finally:
        if error_path.exists():
            error_path.write_text(redact(error_path.read_text(encoding="utf-8")), encoding="utf-8")


def read_credentials(config: dict[str, Any], profile: Path) -> tuple[str, str]:
    store = config["credentialStore"]
    assert store["provider"] == "file"
    path = Path(store["path"])
    target = path if path.is_absolute() else profile / path
    metadata = target.lstat()
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert not target.is_symlink()
    value = json.loads(target.read_text(encoding="utf-8"))
    return value["deviceToken"], value["mcpToken"]


def insert_synthetic_text(content: dict[str, Any], text: str) -> None:
    for section in content["content"]:
        if section["attrs"]["semanticRole"] == "cover_letter_body":
            paragraph = section["content"][0]
            paragraph["content"] = [{"type": "text", "text": text}]
            return
    raise AssertionError("blank cover letter did not contain an editable body")


def text_fragments(value: Any) -> list[str]:
    if isinstance(value, dict):
        own = [value["text"]] if isinstance(value.get("text"), str) and value["text"] else []
        return own + text_fragments(value.get("content"))
    if isinstance(value, list):
        return [fragment for item in value for fragment in text_fragments(item)]
    return []


def export_pair(
    root: Path, environment: dict[str, str], document: dict[str, Any]
) -> tuple[Path, Path, dict[str, Any]]:
    input_path = root / "synthetic-document.json"
    docx_path = root / "synthetic-cover-letter.docx"
    pdf_path = root / "synthetic-cover-letter.pdf"
    result_path = root / "synthetic-export-result.json"
    input_path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    electron = Path.cwd() / "apps/desktop/node_modules/.bin/electron"
    assert electron.is_file(), "desktop Electron binary was not installed"
    result = subprocess.run(
        [
            str(electron),
            "scripts/public-release/export-editable-document.mjs",
            "--input",
            str(input_path.resolve()),
            "--docx",
            str(docx_path.resolve()),
            "--pdf",
            str(pdf_path.resolve()),
            "--result",
            str(result_path.resolve()),
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    (root / "logs/document-export.log").write_text(redact(result.stderr), encoding="utf-8")
    assert result.returncode == 0, "production document export failed; see redacted harness log"
    output = json.loads(result_path.read_text(encoding="utf-8"))
    with zipfile.ZipFile(docx_path) as archive:
        assert "word/document.xml" in archive.namelist()
        assert archive.testzip() is None
        document_xml = ElementTree.fromstring(archive.read("word/document.xml"))
        exported_text = "".join(document_xml.itertext())
        expected_fragments = text_fragments(document["content"])
        assert expected_fragments
        assert all(fragment in exported_text for fragment in expected_fragments)
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    for path, key in ((docx_path, "docx"), (pdf_path, "pdf")):
        assert 0 < path.stat().st_size <= 20_000_000
        assert hashlib.sha256(path.read_bytes()).hexdigest() == output[key]["sha256"]
        assert output[key]["filename"] == path.name
    return docx_path, pdf_path, output


@pytest.mark.anyio
async def test_clean_home_golden_path(clean_runtime) -> None:
    root, environment = clean_runtime
    profile = Path(environment["JOBOS_DATA_DIR"])
    config_path = Path(environment["JOBOS_CONFIG_PATH"])
    values = fixture()
    assert not profile.exists()
    assert not config_path.exists()
    assert not any(key.startswith(("HERMES_", "TAILSCALE_", "JOBHUNTER_")) for key in environment)
    probe = subprocess.run(
        [
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "python",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('job_hunter') is None",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert probe.returncode == 0

    initialization = run_json(
        [
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "jobos-init",
            "--data-dir",
            str(profile),
            "--config-path",
            str(config_path),
        ],
        environment=environment,
    )
    assert initialization == {
        "status": "ready",
        "created": True,
        "demoSeeded": True,
        "credentialProvider": "file",
    }
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["mode"] == "local-service"
    assert config["jobProvider"] == "sqlite"
    assert config["artifactProvider"] == "local"
    assert config["agentProvider"] == "offline"
    device_token, mcp_token = read_credentials(config, profile)

    api = ApiProcess(root, environment, device_token)
    document: dict[str, Any]
    workspace_revision: int
    exported: tuple[Path, Path, dict[str, Any]] | None = None
    artifact_bytes: dict[str, bytes] = {}
    try:
        api.start("first")
        with api.client() as client:
            health = assert_response(client.get("/v1/health"))
            assert {key: health[key] for key in EXPECTED_HEALTH} == EXPECTED_HEALTH
            assert assert_response(client.get("/v1/version"))["contract"] == "jobos-api-v1"
            session_state = assert_response(client.get("/v1/device-session"))
            assert session_state["desktop"] == "disconnected"
            assert session_state["transport"] == "local-loopback"
            created_conversation = assert_response(client.post("/v1/conversations"), 201)
            conversation_id = created_conversation["conversation_id"]
            conversations = assert_response(client.get("/v1/conversations"))["conversations"]
            assert [item["conversation_id"] for item in conversations] == [
                "conv_current",
                conversation_id,
            ]
            turn = assert_response(
                client.post(
                    f"/v1/conversations/{conversation_id}/messages",
                    json={
                        "text": "Run the clean-clone acceptance workflow",
                        "idempotency_key": "clean-turn-1",
                    },
                ),
                201,
            )
            turn_id = turn["turn_id"]

        async def wait_for_agentless_turn_to_settle() -> None:
            with anyio.fail_after(5):
                while True:
                    with sqlite3.connect(profile / "state/jobos.db") as connection:
                        row = connection.execute(
                            "SELECT status FROM conversation_turns WHERE turn_id = ?",
                            (turn_id,),
                        ).fetchone()
                    if row is not None and row[0] not in {"queued", "running", "waiting"}:
                        return
                    await anyio.sleep(0.05)

        # The clean-clone runtime intentionally has no agent provider. Let that
        # dispatch settle, then retain its legitimate turn as the trusted MCP
        # scope used by this synthetic acceptance workflow.
        await wait_for_agentless_turn_to_settle()
        with sqlite3.connect(profile / "state/jobos.db") as connection:
            connection.execute(
                "UPDATE conversation_turns SET status = 'running', cancel_requested = 0 "
                "WHERE turn_id = ?",
                (turn_id,),
            )

        async def correlated_call(
            session: ClientSession, name: str, arguments: dict[str, Any]
        ) -> dict[str, Any]:
            return await call(
                session,
                name,
                {**arguments, "conversation_id": conversation_id, "turn_id": turn_id},
            )

        async def correlated_optional_error(
            session: ClientSession,
            name: str,
            arguments: dict[str, Any],
            code: str,
            *,
            retryable: bool = True,
        ) -> None:
            await assert_optional_error(
                session,
                name,
                {**arguments, "conversation_id": conversation_id, "turn_id": turn_id},
                code,
                retryable=retryable,
            )

        async with mcp_session(
            root,
            environment,
            base_url=api.base_url,
            device_token=device_token,
            mcp_token=mcp_token,
            label="first",
        ) as mcp:
            jobs = (
                await correlated_call(mcp, "job_list", {"idempotency_key": "clean-list-1"})
            )["jobs"]
            assert len(jobs) == 1
            demo = jobs[0]
            assert demo["synthetic_demo"] is True
            assert demo["dataset_version"] == "jobos-demo-v1"
            job_id = demo["job_id"]
            inspected = await correlated_call(
                mcp, "job_inspect", {"job_id": job_id, "idempotency_key": "clean-inspect-1"}
            )
            assert inspected["job_id"] == job_id
            selected = await correlated_call(
                mcp,
                "job_select",
                {
                    "conversation_id": conversation_id,
                    "job_id": job_id,
                    "idempotency_key": "clean-select-1",
                },
            )
            assert selected["job_context"]["selected_job_id"] == job_id
            updated_status = await correlated_call(
                mcp,
                "job_update_status",
                {
                    "conversation_id": conversation_id,
                    "job_id": job_id,
                    "target_status": "reviewed",
                    "reason": "Synthetic review",
                    "idempotency_key": "clean-status-1",
                },
            )
            assert updated_status["job"]["status"] == "reviewed"
            updated_description = await correlated_call(
                mcp,
                "job_update_description",
                {
                    "conversation_id": conversation_id,
                    "job_id": job_id,
                    "description_text": values["description"],
                    "source_note": "Synthetic clean-clone fixture",
                    "idempotency_key": "clean-description-1",
                },
            )
            assert updated_description["job"]["description"] == values["description"]
            workspace = await correlated_call(
                mcp,
                "workspace_inspect",
                {
                    "conversation_id": conversation_id,
                    "idempotency_key": "clean-workspace-read-1",
                },
            )
            for response_only in ("repaired_presets", "repaired_browser", "browser_repair_reasons"):
                workspace.pop(response_only, None)
            workspace["browse_query"] = values["workspaceQuery"]
            persisted_workspace = await correlated_call(
                mcp,
                "workspace_update",
                {
                    "conversation_id": conversation_id,
                    "snapshot": workspace,
                    "idempotency_key": "clean-workspace-write-1",
                },
            )
            workspace_revision = persisted_workspace["revision"]

            with api.client() as client:
                history = assert_response(client.get(f"/v1/jobs/{job_id}/history"))["events"]
                assert {event["event_type"] for event in history} >= {
                    "lead_state_changed",
                    "job_description_updated",
                }
                document = assert_response(
                    client.post(
                        f"/v1/jobs/{job_id}/editable-documents",
                        json={
                            "mode": "blank",
                            "document_key": "cover_letter",
                            "idempotency_key": "clean-document-create-1",
                        },
                    ),
                    201,
                )
                insert_synthetic_text(document["content"], values["documentText"])
                document = assert_response(
                    client.put(
                        f"/v1/editable-documents/{document['document_id']}",
                        json={
                            "base_revision": document["revision"],
                            "content": document["content"],
                            "settings": document["settings"],
                            "comments": document["comments"],
                            "idempotency_key": "clean-document-save-1",
                        },
                    )
                )
            draft = await correlated_call(
                mcp,
                "document_draft_get",
                {
                    "job_id": job_id,
                    "document_key": "cover_letter",
                    "conversation_id": conversation_id,
                    "idempotency_key": "clean-document-read-1",
                },
            )
            assert any(block["text"] == values["documentText"] for block in draft["outline"])
            snapshot = await correlated_call(
                mcp,
                "document_draft_snapshot",
                {
                    "job_id": job_id,
                    "document_id": document["document_id"],
                    "conversation_id": conversation_id,
                    "label": values["snapshotLabel"],
                    "idempotency_key": "clean-document-snapshot-1",
                },
            )
            assert snapshot["document_revision"] == document["revision"]

            if os.environ["JOBOS_CLEAN_CLONE_PLATFORM"] == "complete":
                exported = export_pair(root, environment, document)
                docx_path, pdf_path, checksums = exported
                docx_bytes, pdf_bytes = docx_path.read_bytes(), pdf_path.read_bytes()
                with api.client() as client:
                    published = assert_response(
                        client.post(
                            f"/v1/editable-documents/{document['document_id']}/publish",
                            json={
                                "expected_revision": document["revision"],
                                "docx_filename": docx_path.name,
                                "docx_base64": base64.b64encode(docx_bytes).decode("ascii"),
                                "docx_sha256": checksums["docx"]["sha256"],
                                "pdf_filename": pdf_path.name,
                                "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
                                "pdf_sha256": checksums["pdf"]["sha256"],
                                "idempotency_key": "clean-document-publish-1",
                            },
                        )
                    )
                    assert published["published_revision"] == document["revision"]
                    artifacts = assert_response(client.get(f"/v1/jobs/{job_id}/artifacts"))[
                        "artifacts"
                    ]
                    assert len(artifacts) == 2
                    expected_by_type = {
                        (
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ): docx_bytes,
                        "application/pdf": pdf_bytes,
                    }
                    for artifact in artifacts:
                        downloaded = client.get(f"/v1/artifacts/{artifact['artifact_id']}/download")
                        assert downloaded.status_code == 200
                        expected = expected_by_type[artifact["media_type"]]
                        assert downloaded.content == expected
                        artifact_bytes[artifact["artifact_id"]] = expected

            await correlated_optional_error(
                mcp,
                "browser_tabs_inspect",
                {"conversation_id": conversation_id, "timeout_ms": 500},
                "desktop_unavailable",
            )
            await correlated_optional_error(
                mcp,
                "document_refresh",
                {
                    "job_id": job_id,
                    "conversation_id": conversation_id,
                    "idempotency_key": "clean-refresh-1",
                },
                "artifact_provider_unavailable",
            )
            await correlated_optional_error(
                mcp,
                "document_render",
                {
                    "job_id": job_id,
                    "source_id": "synthetic-source",
                    "conversation_id": conversation_id,
                    "idempotency_key": "clean-render-1",
                },
                "renderer_unavailable",
            )
        with api.client() as client:
            health = assert_response(client.get("/v1/health"))
            assert {key: health[key] for key in EXPECTED_HEALTH} == EXPECTED_HEALTH
    finally:
        api.stop()

    repeated = run_json(
        [
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "jobos-init",
            "--data-dir",
            str(profile),
            "--config-path",
            str(config_path),
        ],
        environment=environment,
    )
    assert repeated == {
        "status": "ready",
        "created": False,
        "demoSeeded": False,
        "credentialProvider": "file",
    }

    try:
        api.start("restart")
        await wait_for_agentless_turn_to_settle()
        with sqlite3.connect(profile / "state/jobos.db") as connection:
            connection.execute(
                "UPDATE conversation_turns SET status = 'running', cancel_requested = 0 "
                "WHERE turn_id = ?",
                (turn_id,),
            )
        async with mcp_session(
            root,
            environment,
            base_url=api.base_url,
            device_token=device_token,
            mcp_token=mcp_token,
            label="restart",
        ) as mcp:
            jobs = (
                await correlated_call(mcp, "job_list", {"idempotency_key": "clean-list-2"})
            )["jobs"]
            assert len(jobs) == 1
            assert jobs[0]["job_id"] == job_id
            assert jobs[0]["status"] == "reviewed"
            inspected = await correlated_call(
                mcp, "job_inspect", {"job_id": job_id, "idempotency_key": "clean-inspect-2"}
            )
            assert inspected["description"] == values["description"]
            workspace = await correlated_call(
                mcp,
                "workspace_inspect",
                {
                    "conversation_id": conversation_id,
                    "idempotency_key": "clean-workspace-read-2",
                },
            )
            assert workspace["revision"] == workspace_revision
            assert workspace["browse_query"] == values["workspaceQuery"]
            with api.client() as client:
                saved = assert_response(
                    client.get(f"/v1/editable-documents/{document['document_id']}")
                )
                assert saved["revision"] == document["revision"]
                snapshots = assert_response(
                    client.get(f"/v1/editable-documents/{document['document_id']}/snapshots")
                )["snapshots"]
                assert any(item["label"] == values["snapshotLabel"] for item in snapshots)
                artifacts = assert_response(client.get(f"/v1/jobs/{job_id}/artifacts"))["artifacts"]
                assert len(artifacts) == (2 if exported else 0)
                for artifact in artifacts:
                    downloaded = client.get(f"/v1/artifacts/{artifact['artifact_id']}/download")
                    assert downloaded.content == artifact_bytes[artifact["artifact_id"]]
                removed = client.request(
                    "DELETE",
                    f"/v1/jobs/{job_id}/demo",
                    json={"origin": "user", "idempotency_key": "clean-remove-demo-1"},
                )
                assert removed.status_code == 200
                assert assert_response(client.get("/v1/jobs"))["jobs"] == []
                assert_response(
                    client.get(f"/v1/editable-documents/{document['document_id']}"), 404
                )
    finally:
        api.stop()

    after_delete = run_json(
        [
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "jobos-init",
            "--data-dir",
            str(profile),
            "--config-path",
            str(config_path),
        ],
        environment=environment,
    )
    assert after_delete["created"] is False
    assert after_delete["demoSeeded"] is False
    try:
        api.start("after-delete")
        with api.client() as client:
            assert assert_response(client.get("/v1/jobs"))["jobs"] == []
            documents = assert_response(client.get(f"/v1/jobs/{job_id}/editable-documents"), 404)
            assert documents["detail"] == "Job not found"
    finally:
        api.stop()
