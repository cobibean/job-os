"""Synthetic end-to-end external MCP → real API/SQLite/publication tests."""

import base64
import io
import sqlite3
from hashlib import sha256

import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from jobos_api.app import create_app
from jobos_api.settings import Settings
from jobos_mcp.external import create_external_server
from jobos_mcp.jobs import JobOsMcpClient
from jobos_mcp.remote_files import MAX_TRANSFER, decode_upload, generate_pair, put_file, read_file
from jobos_mcp.server import create_server

EXTERNAL = "test-external-mcp-token"
INTERNAL = "test-internal-mcp-token"
DEVICE = "test-device-token-value"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api(tmp_path):
    settings = Settings(
        device_token=DEVICE,
        mcp_token=INTERNAL,
        external_mcp_token=EXTERNAL,
        state_db_path=tmp_path / "state.db",
    )
    app = create_app(settings)
    with TestClient(app) as api:
        yield settings, app, api


@pytest.mark.anyio
async def test_complete_catalog_and_context_free_reads(api):
    settings, app, _ = api
    client = JobOsMcpClient(
        base_url="http://test",
        device_token="unused",
        mcp_token=EXTERNAL,
        external=True,
        transport=httpx.ASGITransport(app=app),
    )
    try:
        internal = create_server(client)
        external = create_external_server(
            client, artifact_root=settings.resolved_local_artifact_root()
        )
        original = {tool.name for tool in await internal.list_tools()}
        exposed = {tool.name: tool for tool in await external.list_tools()}
        assert len(original) == 45
        assert original <= exposed.keys()
        assert len(exposed) == 50
        for tool in exposed.values():
            assert "turn_id" not in tool.inputSchema.get("properties", {})
            assert "conversation_id" not in tool.inputSchema.get("required", [])
        result = await external._tool_manager.call_tool("job_list", {})
        assert "jobs" in result
        with sqlite3.connect(settings.state_db_path) as connection:
            assert (
                connection.execute(
                    "SELECT count(*) FROM conversations "
                    "WHERE owner_device_id = 'jobos-external-mcp'"
                ).fetchone()[0]
                == 0
            )
    finally:
        await client.aclose()


def test_external_auth_cannot_be_selected_by_internal_header(api):
    settings, _, api = api
    for token in (DEVICE, INTERNAL):
        result = api.get(
            "/v1/jobs",
            headers={
                "Authorization": f"Bearer {token}",
                "X-JobOS-MCP-Token": INTERNAL,
                "X-JobOS-External": "true",
            },
            params={"origin": "mcp"},
        )
        assert result.status_code in {403, 422}
    external_headers = {"Authorization": f"Bearer {EXTERNAL}", "X-JobOS-MCP-Token": EXTERNAL}
    assert (
        api.get("/v1/jobs", headers=external_headers, params={"origin": "mcp"}).status_code == 200
    )
    assert (
        api.get(
            "/v1/jobs", headers=external_headers, params={"turn_id": "turn_fake12345"}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/v1/external-mcp/session",
            headers={"Authorization": f"Bearer {INTERNAL}", "X-JobOS-MCP-Token": INTERNAL},
        ).status_code
        == 403
    )
    first = api.post("/v1/external-mcp/session", headers=external_headers).json()
    mixed_headers = {**external_headers, "Authorization": f"bEaReR {EXTERNAL}"}
    assert (
        api.get(
            "/v1/jobs", headers=mixed_headers, params={"conversation_id": "conv_unknown"}
        ).status_code
        == 404
    )
    assert (
        api.get("/v1/jobs", headers=mixed_headers, params={"turn_id": "turn_fake12345"}).status_code
        == 422
    )
    second = api.post("/v1/external-mcp/session", headers=external_headers).json()
    assert first == second
    assert first["active_turn"] is None
    assert (
        api.get(
            "/v1/jobs", headers=external_headers, params={"conversation_id": "conv_unknown"}
        ).status_code
        == 404
    )
    with sqlite3.connect(settings.state_db_path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM conversation_turns WHERE conversation_id = ?",
                (first["conversation_id"],),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.anyio
@pytest.mark.parametrize("document_key", ["resume", "cover_letter", "references"])
async def test_remote_generate_publish_read_and_select_without_active_turn(
    api, monkeypatch, document_key
):
    settings, app, _ = api
    client = JobOsMcpClient(
        base_url="http://test",
        device_token="unused",
        mcp_token=EXTERNAL,
        external=True,
        transport=httpx.ASGITransport(app=app),
    )
    server = create_external_server(client, artifact_root=settings.resolved_local_artifact_root())

    async def tool(name, **kwargs):
        return await server._tool_manager.call_tool(name, kwargs)

    try:
        created = await tool(
            "job_create_from_browser",
            company_name="(FAKE) Example Company",
            title="(FAKE) Engineer",
            canonical_url="https://example.com/jobs/synthetic",
            location_text="Remote",
            description_text="Synthetic role for tests",
            application_url="https://example.com/apply/synthetic",
        )
        job_id = created["job"]["job_id"]
        selection = await tool("job_select", job_id=job_id)
        conversation_id = selection["conversation_id"]
        inspected = await tool("workspace_inspect")
        assert inspected["selected_job_id"] == job_id
        content = "# (FAKE) Candidate\n## Experience\n- Built synthetic systems\n## Skills\nPython"
        publish_document = client.publish_document

        async def fail_second(*args, **kwargs):
            if args[5].endswith(".docx"):
                raise RuntimeError("synthetic interruption after PDF publication")
            return await publish_document(*args, **kwargs)

        monkeypatch.setattr(client, "publish_document", fail_second)
        from mcp.server.fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="synthetic interruption"):
            await tool(
                "document_generate",
                job_id=job_id,
                document_key=document_key,
                document_label="Synthetic resume",
                markdown=content,
            )
        assert len((await tool("document_list", job_id=job_id))["artifacts"]) == 1
        monkeypatch.setattr(client, "publish_document", publish_document)
        result = await tool(
            "document_generate",
            job_id=job_id,
            document_key=document_key,
            document_label="Synthetic resume",
            markdown=content,
        )
        assert result["published"] is True
        assert result["conversation_id"] == conversation_id
        assert len(result["publications"]) == 2
        files = result["files"]
        for suffix in ("pdf", "docx"):
            read = await tool("file_read", job_id=job_id, file_id=files[suffix]["file_id"])
            data = base64.b64decode(read["content"])
            assert sha256(data).hexdigest() == files[suffix]["sha256"]
            if suffix == "pdf":
                assert data.startswith(b"%PDF-")
            else:
                assert "(FAKE) Candidate" in [p.text for p in Document(io.BytesIO(data)).paragraphs]
        artifacts = result["documents"]["artifacts"]
        assert len(artifacts) == 2
        assert len({a["source_revision"] for a in artifacts}) == 1
        for artifact in artifacts:
            chunk = await tool(
                "artifact_read", artifact_id=artifact["artifact_id"], byte_length=100
            )
            assert len(base64.b64decode(chunk["content"])) == 100
            assert chunk["sha256"] == artifact["sha256"]
        replay = await tool(
            "document_generate",
            job_id=job_id,
            document_key=document_key,
            document_label="Synthetic resume",
            markdown=content,
        )
        assert len(replay["documents"]["artifacts"]) == 2
        assert replay["files"] == files
        assert all(a["document_label"] == "Synthetic resume" for a in artifacts)
        second = await tool(
            "document_generate",
            job_id=job_id,
            document_key=document_key,
            document_label="Synthetic alternative",
            markdown=content + "\nAlternative version.",
        )
        reread = await tool("document_list", job_id=job_id)
        assert len(reread["artifacts"]) == 4
        assert {a["artifact_id"] for a in artifacts} <= {
            a["artifact_id"] for a in reread["artifacts"]
        }
        assert {a["document_label"] for a in reread["artifacts"]} == {
            "Synthetic resume",
            "Synthetic alternative",
        }
        assert second["published"] is True
        assert reread["approved_artifact_id"] == replay["documents"]["approved_artifact_id"]
        upload = await tool("file_upload", job_id=job_id, content="synthetic source", format="txt")
        read = await tool(
            "file_read", job_id=job_id, file_id=upload["file_id"], encoding="text", byte_length=9
        )
        assert read["content"] == "synthetic"
        assert read["has_more"] is True
    finally:
        await client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("label", ["", "   ", "x" * 81])
async def test_publication_validation_is_actionable_over_mcp(api, label):
    from mcp.server.fastmcp.exceptions import ToolError

    settings, app, _ = api
    client = JobOsMcpClient(
        base_url="http://test",
        device_token="unused",
        mcp_token=EXTERNAL,
        external=True,
        transport=httpx.ASGITransport(app=app),
    )
    server = create_external_server(client, artifact_root=settings.resolved_local_artifact_root())
    try:
        created = await server._tool_manager.call_tool(
            "job_ingest",
            {
                "company_name": "(FAKE) Example Company",
                "title": "(FAKE) Engineer",
                "canonical_url": "https://example.com/jobs/synthetic",
                "location_text": "Remote",
                "description_text": "Synthetic role",
                "application_url": "https://example.com/apply/synthetic",
                "listing_source_url": "https://example.com/jobs/synthetic",
                "listing_capture_method": "web_extract",
            },
        )
        with pytest.raises(ToolError, match="document_label: expected a nonblank label"):
            await server._tool_manager.call_tool(
                "document_generate",
                {
                    "job_id": created["job"]["job_id"],
                    "document_key": "references",
                    "document_label": label,
                    "markdown": "Synthetic document.",
                    "publish": True,
                },
            )
    finally:
        await client.aclose()


def test_publication_validation_does_not_echo_input(api):
    _, _, client = api
    secret = "synthetic-private-document-content"
    response = client.post(
        "/v1/jobs/synthetic/artifacts/publish",
        headers={"Authorization": f"Bearer {EXTERNAL}", "X-JobOS-MCP-Token": EXTERNAL},
        json={"document_key": secret, "document_label": {"private": secret}, secret: secret},
    )
    assert response.status_code == 422
    assert (
        "document_key: expected resume, cover_letter, or references" in response.json()["message"]
    )
    assert secret not in response.text


def test_file_bounds_tampering_and_symlinks(tmp_path):
    for content, encoding in [("!notbase64", "base64"), ("x" * (MAX_TRANSFER + 1), "text")]:
        with pytest.raises(ValueError):
            decode_upload(content, encoding)
    file = put_file(tmp_path, "conv_synthetic", "fake-job", "txt", b"synthetic")
    assert read_file(tmp_path, "conv_synthetic", "fake-job", file["file_id"]) == b"synthetic"
    with pytest.raises(ValueError):
        read_file(tmp_path, "conv_synthetic", "fake-job", "../../etc/passwd")
    stored = next(tmp_path.rglob(file["file_id"]))
    stored.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        read_file(tmp_path, "conv_synthetic", "fake-job", file["file_id"])
    stored.unlink()
    stored.symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ValueError):
        read_file(tmp_path, "conv_synthetic", "fake-job", file["file_id"])


def test_external_context_is_durable_and_does_not_consume_chat_slots(tmp_path):
    from jobos_api.state_store import JobOsStateStore

    settings = Settings(
        device_token=DEVICE,
        mcp_token=INTERNAL,
        external_mcp_token=EXTERNAL,
        state_db_path=tmp_path / "state.db",
    )
    headers = {"Authorization": f"Bearer {EXTERNAL}", "X-JobOS-MCP-Token": EXTERNAL}
    with TestClient(create_app(settings)) as api:
        store = JobOsStateStore(settings.state_db_path)
        early = api.post("/v1/external-mcp/session", headers=headers).json()
        while len(store.list_active_conversations()) < 5:
            store.create_conversation(actor_id="primary-device")
        store.archive_conversation(
            store.list_active_conversations()[1]["conversation_id"], actor_id="primary-device"
        )
        store.create_conversation(actor_id="primary-device")
        response = api.post("/v1/external-mcp/session", headers=headers)
        assert response.json() == early
        assert response.status_code == 200
        context = response.json()
        assert len(store.list_active_conversations()) == 5
        assert len(store.list_active_conversations(include_external=True)) == 6
    with TestClient(create_app(settings)) as api:
        assert api.post("/v1/external-mcp/session", headers=headers).json() == context
        browser = api.post(
            "/v1/browser/commands",
            headers=headers,
            params={"conversation_id": context["conversation_id"]},
            json={
                "command": "tabs.inspect",
                "arguments": {},
                "origin": "mcp",
                "conversation_id": context["conversation_id"],
                "idempotency_key": "synthetic-browser",
                "timeout_ms": 1000,
            },
        )
        assert browser.status_code == 503
        assert browser.json()["code"] == "desktop_unavailable"


def test_document_generation_is_deterministic_and_rejects_unsupported_text(monkeypatch, tmp_path):
    from reportlab import rl_config

    # System font search order differs across macOS and Linux CI.
    monkeypatch.setattr(rl_config, "TTFSearchPath", [str(tmp_path)])
    text = "# (FAKE) Candidate\n- Synthetic résumé experience"
    assert generate_pair(text) == generate_pair(text)
    with pytest.raises(ValueError, match="unsupported"):
        generate_pair("Unsupported emoji 🦄")
