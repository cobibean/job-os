import base64
import json
import os
import re
import subprocess
from pathlib import Path

import httpx
import jobos_mcp.server as server_module
import pytest
from jobos_api.redaction import sanitize_text
from jobos_mcp.jobs import JobOsMcpClient, JobOsMcpError, _safe_error_message
from jobos_mcp.server import (
    _document_artifact_root,
    _existing_document_publication_workspace,
    _prepare_document_publication_workspace,
    _read_document_input,
    _read_publication_input,
    create_server,
)
from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[import-not-found]


def test_local_mcp_credentials_load_from_device_scoped_keychain(monkeypatch):
    calls = []

    def find_password(arguments, **kwargs):
        calls.append((arguments, kwargs))
        service = arguments[arguments.index("-s") + 1]
        value = "(FAKE)-device-token" if service.endswith("device-token") else "(FAKE)-mcp-token"
        return subprocess.CompletedProcess(arguments, 0, stdout=value, stderr="")

    monkeypatch.delenv("JOBOS_DEVICE_TOKEN", raising=False)
    monkeypatch.delenv("JOBOS_MCP_TOKEN", raising=False)
    monkeypatch.setenv("JOBOS_DEVICE_ID", "(FAKE)-device")
    monkeypatch.setattr(server_module.sys, "platform", "darwin")
    monkeypatch.setattr(server_module.subprocess, "run", find_password)

    assert server_module.local_device_token() == "(FAKE)-device-token"
    assert server_module.local_mcp_token() == "(FAKE)-mcp-token"
    assert all("(FAKE)-device" in arguments for arguments, _kwargs in calls)


@pytest.mark.parametrize("value", ["../job", "job/other", "job\\other", "job\nother", ""])
def test_mcp_path_segments_reject_traversal_and_control_characters(value):
    with pytest.raises(ValueError, match="Invalid job ID"):
        JobOsMcpClient._segment(value, "job ID")


def test_mcp_path_segments_are_url_encoded_and_document_keys_are_allowlisted():
    assert JobOsMcpClient._segment("job:123", "job ID") == "job%3A123"
    assert JobOsMcpClient._document_key("references") == "references"
    with pytest.raises(ValueError, match="document key"):
        JobOsMcpClient._document_key("resume/../../secrets")


@pytest.mark.anyio
async def test_mcp_maps_versioned_api_errors_to_bounded_safe_errors():
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={
                "error_schema": "jobos-error-v1",
                "code": "desktop_unavailable",
                "message": "Open JobOS and retry.",
                "retryable": True,
                "correlation_id": "corr_test_123",
                "detail": "/Users/example/private.db",
            },
        )

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(JobOsMcpError) as raised:
        await client.list_jobs()
    await client.aclose()

    assert raised.value.code == "desktop_unavailable"
    assert raised.value.retryable is True
    assert raised.value.correlation_id == "corr_test_123"
    assert str(raised.value) == (
        "desktop_unavailable: Open JobOS and retry. (retryable=true, correlation_id=corr_test_123)"
    )
    assert "/Users" not in str(raised.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected_code", "expected_message"),
    [
        (
            {
                "error_schema": "jobos-error-v1",
                "code": "repository_unavailable",
                "message": (
                    "Database failed at /var/lib/jobos/private/state.db "
                    "with token=private-secret-value"
                ),
                "retryable": True,
                "correlation_id": "corr_safe_123",
                "detail": {"path": "/opt/jobos/secret.json"},
            },
            "repository_unavailable",
            "Database failed at [protected path] with [redacted]",
        ),
        (
            {
                "code": "unsafe_internal_code",
                "message": "Leaked /srv/jobos/private/error.log",
                "retryable": False,
                "correlation_id": "corr_unsafe_123",
            },
            "http_503",
            "JobOS API request failed",
        ),
        (
            {
                "error_schema": "jobos-error-v1",
                "code": "repository_unavailable",
                "message": (
                    "Rejected eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvYm9zIFVzZXIifQ."
                    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
                ),
                "retryable": True,
                "correlation_id": "corr_jwt_123",
            },
            "repository_unavailable",
            "Rejected [redacted]",
        ),
        (
            {
                "error_schema": "jobos-error-v1",
                "code": "repository_unavailable",
                "message": "Rejected bQ7_vR2fG9mK4pL8sN1xC6zW3dH0jT5uY-aE2iO7qP9 at /tmp",
                "retryable": True,
                "correlation_id": "corr_opaque_123",
            },
            "repository_unavailable",
            "Rejected [redacted] at [protected path]",
        ),
    ],
)
async def test_mcp_requires_the_versioned_envelope_and_sanitizes_messages(
    payload, expected_code, expected_message
):
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json=payload)

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(JobOsMcpError) as raised:
        await client.list_jobs()
    await client.aclose()

    assert raised.value.code == expected_code
    assert raised.value.message == expected_message
    assert "/var/" not in str(raised.value)
    assert "/opt/" not in str(raised.value)
    assert "/srv/" not in str(raised.value)
    assert "private-secret-value" not in str(raised.value)


def test_mcp_error_sanitizer_preserves_urls_prose_and_ordinary_identifiers():
    safe = (
        "See https://example.com/jobs/job_01ARZ3NDEKTSV4RRFFQ69G5FAV and/or retry "
        "with 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    )

    assert _safe_error_message(safe) == safe


@pytest.mark.parametrize(
    ("category", "raw", "expected"),
    [
        ("authorization_code", "authorization_code=short-secret", "[redacted]"),
        ("relative ssh path", ".ssh/id_rsa", "[protected path]"),
        ("relative Hermes path", ".hermes/auth.json", "[protected path]"),
        (
            "signed URL",
            "https://files.example.com/resume.pdf?X-Amz-Signature=short-secret",
            "[protected signed URL]",
        ),
        ("authorization header", "Authorization: Bearer header-secret", "[redacted]"),
        ("standard token", "token=standard-secret", "[redacted]"),
        (
            "JWT",
            "Rejected eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvYm9zIFVzZXIifQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
            "Rejected [redacted]",
        ),
        (
            "opaque token",
            "Rejected bQ7_vR2fG9mK4pL8sN1xC6zW3dH0jT5uY-aE2iO7qP9",
            "Rejected [redacted]",
        ),
        (
            "arbitrary absolute path",
            "Failure at /custom/private/data.db",
            "Failure at [protected path]",
        ),
        (
            "absolute path with spaces",
            "Failure at /custom/Jane Doe/JobOS/private.db; retry later",
            "Failure at [protected path]; retry later",
        ),
        (
            "absolute path with spaces and trailing prose",
            "Failure at /custom/Jane Doe/JobOS/private.db but safe connector prose follows",
            "Failure at [protected path] but safe connector prose follows",
        ),
        (
            "absolute path with a multi-word final component",
            "Failure at /custom/Jane Doe/Resume Final Draft.pdf; retry later",
            "Failure at [protected path]; retry later",
        ),
    ],
)
def test_mcp_error_sanitizer_has_api_parity_for_every_sensitive_category(category, raw, expected):
    assert _safe_error_message(raw) == expected, category
    assert sanitize_text(raw) == expected, category


@pytest.mark.anyio
async def test_job_tools_use_only_the_authenticated_jobos_http_contract():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"jobs": []})
        if request.method == "GET":
            return httpx.Response(200, json={"job_id": "job-1"})
        return httpx.Response(200, json={"event_id": len(requests)})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    client.scope_turn("conv_test", "turn_contract_test_0001")

    await client.list_jobs(sort="status", query="builder")
    await client.inspect_job("job-1")
    await client.create_job(
        company_name="Northstar Labs",
        title="Applied AI Product Builder",
        canonical_url="https://jobs.example.com/northstar/applied-ai-builder",
        location_text="United States · Remote",
        description_text="Build useful agent workflows.",
        application_url="https://jobs.example.com/northstar/applied-ai-builder/apply",
        idempotency_key="create-1",
    )
    await client.select_job("job-1", idempotency_key="select-1")
    await client.reorder_jobs(["job-1", "job-2"], idempotency_key="order-1")
    await client.update_status(
        "job-1", "reviewed", reason="Agent review", idempotency_key="status-1"
    )
    await client.update_description(
        "job-1",
        "Full canonical listing text.",
        source_note="Supplied by the user",
        idempotency_key="description-1",
    )
    await client.aclose()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/jobs"),
        ("GET", "/v1/jobs/job-1"),
        ("POST", "/v1/jobs"),
        ("PUT", "/v1/conversations/conv_test/workspace/job"),
        ("PUT", "/v1/jobs/order"),
        ("PUT", "/v1/jobs/job-1/status"),
        ("PUT", "/v1/jobs/job-1/description"),
    ]
    assert all(
        request.headers["authorization"] == "Bearer test-mcp-trusted-token" for request in requests
    )
    assert json.loads(requests[2].content) == {
        "company_name": "Northstar Labs",
        "title": "Applied AI Product Builder",
        "canonical_url": "https://jobs.example.com/northstar/applied-ai-builder",
        "location_text": "United States · Remote",
        "description_text": "Build useful agent workflows.",
        "application_url": "https://jobs.example.com/northstar/applied-ai-builder/apply",
        "origin": "mcp",
        "idempotency_key": "create-1",
    }
    assert json.loads(requests[3].content) == {
        "job_id": "job-1",
        "origin": "mcp",
        "idempotency_key": "select-1",
    }
    assert json.loads(requests[5].content) == {
        "target_status": "reviewed",
        "origin": "mcp",
        "reason": "Agent review",
        "idempotency_key": "status-1",
    }
    assert json.loads(requests[6].content) == {
        "description_text": "Full canonical listing text.",
        "source": "mcp_agent",
        "provenance": "Supplied by the user",
        "origin": "mcp",
        "idempotency_key": "description-1",
    }


@pytest.mark.anyio
async def test_document_publish_sends_bounded_bytes_through_the_authenticated_api():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"job_id": "job-1", "artifacts": []})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    await client.publish_document(
        "job-1",
        "cover_letter",
        "Cover Letter",
        "cover-letter.md",
        b"Dear team",
        "cover-letter.docx",
        b"PK\x03\x04fixture",
        idempotency_key="publish-1",
    )
    await client.aclose()

    assert len(requests) == 1
    request = requests[0]
    assert (request.method, request.url.path) == (
        "POST",
        "/v1/jobs/job-1/artifacts/publish",
    )
    assert request.headers["x-jobos-mcp-token"] == "test-mcp-trusted-token"
    payload = json.loads(request.content)
    assert payload == {
        "document_key": "cover_letter",
        "document_label": "Cover Letter",
        "source_filename": "cover-letter.md",
        "source_base64": base64.b64encode(b"Dear team").decode("ascii"),
        "artifact_filename": "cover-letter.docx",
        "artifact_base64": base64.b64encode(b"PK\x03\x04fixture").decode("ascii"),
        "origin": "mcp",
        "idempotency_key": "publish-1",
    }


def test_document_publish_input_is_limited_to_explicit_roots(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    artifact = allowed / "letter.docx"
    artifact.write_bytes(b"PK\x03\x04fixture")
    outside = tmp_path / "outside.docx"
    outside.write_bytes(b"PK\x03\x04private")

    filename, content = _read_document_input(
        str(artifact), roots=(allowed,), maximum=100, suffixes={".docx"}
    )
    assert (filename, content) == ("letter.docx", b"PK\x03\x04fixture")
    with pytest.raises(ValueError, match="outside"):
        _read_document_input(str(outside), roots=(allowed,), maximum=100, suffixes={".docx"})


def test_document_publish_input_rejects_relative_path_with_cwd_inside_allowed_root(
    tmp_path, monkeypatch
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    artifact = allowed / "letter.docx"
    artifact.write_bytes(b"PK\x03\x04fixture")
    monkeypatch.chdir(allowed)

    with pytest.raises(ValueError, match="must be absolute"):
        _read_document_input(artifact.name, roots=(allowed,), maximum=100, suffixes={".docx"})


def test_document_input_descriptor_read_cannot_leak_post_open_symlink_bytes(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    nested = allowed / "nested"
    nested.mkdir(parents=True)
    original = b"inside document bytes"
    outside_bytes = b"outside private bytes"
    target = nested / "letter.docx"
    target.write_bytes(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / target.name).write_bytes(outside_bytes)
    held = allowed / "held-nested"
    real_open = os.open
    swapped = False

    def racing_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == nested.name and dir_fd is not None and not swapped:
            swapped = True
            nested.rename(held)
            nested.symlink_to(outside, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(server_module.os, "open", racing_open)
    filename, content = _read_document_input(
        str(target), roots=(allowed,), maximum=100, suffixes={".docx"}
    )

    assert swapped
    assert filename == target.name
    assert content == original
    assert content != outside_bytes


def test_document_input_rejects_same_inode_same_size_in_place_mutation(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = allowed / "resume.pdf"
    original = b"A" * 256
    replacement = b"B" * len(original)
    target.write_bytes(original)
    identity = target.stat()
    real_read = os.read
    mutated = False

    def racing_read(descriptor, size):
        nonlocal mutated
        chunk = real_read(descriptor, size)
        if chunk and not mutated:
            mutated = True
            target.write_bytes(replacement)
        return chunk

    monkeypatch.setattr(server_module.os, "read", racing_read)
    with pytest.raises(ValueError, match="changed during access"):
        _read_document_input(str(target), roots=(allowed,), maximum=1_024, suffixes={".pdf"})

    changed = target.stat()
    assert mutated
    assert changed.st_ino == identity.st_ino
    assert changed.st_size == identity.st_size


def test_document_artifact_root_uses_local_config_and_never_cwd_or_hermes(tmp_path, monkeypatch):
    working = tmp_path / "working"
    working.mkdir()
    hermes = tmp_path / ".hermes/profiles/job-hunter/cache/documents"
    hermes.mkdir(parents=True)
    monkeypatch.chdir(working)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("JOBOS_CONFIG_PATH", str(tmp_path / "missing-config.json"))
    monkeypatch.setenv("JOBOS_DOCUMENT_ROOTS", str(hermes))

    with pytest.raises(RuntimeError, match="valid JobOS local config"):
        _document_artifact_root()

    artifacts = tmp_path / "application-data/artifacts"
    artifacts.mkdir(parents=True)
    config = tmp_path / "application-data/config.json"
    config.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "paths": {"artifacts": "artifacts"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JOBOS_CONFIG_PATH", str(config))
    assert _document_artifact_root() == artifacts.resolve()
    assert _document_artifact_root() != working.resolve()
    assert _document_artifact_root() != hermes.resolve()


def test_publication_workspace_is_app_owned_session_scoped_and_stable(tmp_path):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()

    first = _prepare_document_publication_workspace(
        "conv_alpha", "job:123", artifact_root=artifact_root
    )
    repeated = _prepare_document_publication_workspace(
        "conv_alpha", "job:123", artifact_root=artifact_root
    )
    other_session = _prepare_document_publication_workspace(
        "conv_beta", "job:123", artifact_root=artifact_root
    )

    assert first == repeated
    assert first.parent.parent == artifact_root / "publication-inbox"
    assert first != other_session
    assert first.is_dir()
    assert first.stat().st_mode & 0o777 == 0o700
    assert "job:123" not in str(first)


def test_publication_workspace_rejects_symlinked_inbox(tmp_path):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (artifact_root / "publication-inbox").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symbolic links"):
        _prepare_document_publication_workspace("conv_alpha", "job-1", artifact_root=artifact_root)


def test_publication_workspace_rejects_parent_replaced_by_symlink(tmp_path):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    workspace = _prepare_document_publication_workspace(
        "conv_alpha", "job-1", artifact_root=artifact_root
    )
    conversation_directory = workspace.parent
    held = conversation_directory.with_name(f"{conversation_directory.name}-held")
    conversation_directory.rename(held)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / workspace.name).mkdir()
    conversation_directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="symbolic links"):
        _existing_document_publication_workspace("conv_alpha", "job-1", artifact_root=artifact_root)


def test_publication_read_cannot_follow_parent_swapped_after_workspace_check(tmp_path, monkeypatch):
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    workspace = _prepare_document_publication_workspace(
        "conv_alpha", "job-1", artifact_root=artifact_root
    )
    target = workspace / "resume.docx"
    target.write_bytes(b"inside document")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / target.name).write_bytes(b"outside private bytes")
    held = workspace.parent.with_name(f"{workspace.parent.name}-held")
    original_check = server_module._existing_document_publication_workspace

    def check_then_swap(*args, **kwargs):
        checked = original_check(*args, **kwargs)
        checked.parent.rename(held)
        checked.parent.symlink_to(outside, target_is_directory=True)
        return checked

    monkeypatch.setattr(server_module, "_existing_document_publication_workspace", check_then_swap)
    with pytest.raises(ValueError, match="symbolic link|inaccessible component"):
        _read_publication_input(
            str(target),
            conversation_id="conv_alpha",
            job_id="job-1",
            artifact_root=artifact_root,
            maximum=100,
            suffixes={".docx"},
        )


@pytest.mark.anyio
async def test_publication_prepare_and_publish_use_only_the_app_owned_inbox(tmp_path):
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"job_id": "job-1", "artifacts": []})

    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    server = create_server(client, artifact_root=artifact_root)

    _, prepared = await server.call_tool(
        "document_publication_prepare",
        {
            "conversation_id": "conv_alpha",
            "turn_id": "turn_publication_test_0001",
            "job_id": "job-1",
        },
    )
    assert isinstance(prepared, dict)
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/jobs/job-1"
    assert requests[0].url.params["conversation_id"] == "conv_alpha"
    assert requests[0].url.params["turn_id"] == "turn_publication_test_0001"
    workspace = Path(prepared["publication_directory"])
    source = workspace / "resume.md"
    artifact = workspace / "resume.docx"
    source.write_text("Resume source", encoding="utf-8")
    artifact.write_bytes(b"PK\x03\x04fixture")

    _, published = await server.call_tool(
        "document_publish",
        {
            "conversation_id": "conv_alpha",
            "turn_id": "turn_publication_test_0001",
            "job_id": "job-1",
            "document_key": "resume",
            "document_label": "Resume",
            "source_path": str(source),
            "artifact_path": str(artifact),
            "idempotency_key": "publish-1",
        },
    )
    assert isinstance(published, dict)
    assert len(requests) == 2

    outside = tmp_path / "outside.docx"
    outside.write_bytes(b"PK\x03\x04private")
    with pytest.raises(ToolError, match="document_publication_prepare"):
        await server.call_tool(
            "document_publish",
            {
                "conversation_id": "conv_alpha",
                "turn_id": "turn_publication_test_0001",
                "job_id": "job-1",
                "document_key": "resume",
                "document_label": "Resume",
                "source_path": str(source),
                "artifact_path": str(outside),
            },
        )
    assert len(requests) == 2
    await client.aclose()


@pytest.mark.anyio
async def test_mcp_server_exposes_public_v1_parity_tools_while_retaining_job_tools():
    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )

    server = create_server(client)
    tools = await server.list_tools()
    await client.aclose()

    assert [tool.name for tool in tools] == [
        "job_list",
        "job_inspect",
        "job_create_from_browser",
        "job_select",
        "job_reorder",
        "job_update_status",
        "job_update_description",
        "career_profile_edit",
        "career_profile_get",
        "career_profile_search",
        "career_profile_edit_batch",
        "career_profile_changes_list",
        "career_profile_evidence_import",
        "career_profile_evidence_inspect",
        "workspace_inspect",
        "workspace_update",
        "document_list",
        "document_draft_get",
        "document_draft_apply",
        "document_draft_snapshot",
        "document_refresh",
        "document_render",
        "document_register",
        "document_publication_prepare",
        "document_publish",
        "document_select",
        "document_file_inspect",
        "document_file_apply",
        "browser_tabs_inspect",
        "browser_tab_create",
        "browser_tab_select",
        "browser_tab_associate",
        "browser_tab_close",
        "browser_tabs_reorder",
        "browser_navigate",
        "browser_back",
        "browser_forward",
        "browser_reload",
        "browser_stop",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "activity_report",
    ]
    tools_by_name = {tool.name: tool for tool in tools}
    assert "before generating a resume or cover letter" in (
        tools_by_name["document_publication_prepare"].description or ""
    )
    assert "Other filesystem paths are never read" in (
        tools_by_name["document_publish"].description or ""
    )
    for tool in tools:
        conversation = tool.inputSchema["properties"]["conversation_id"]
        turn = tool.inputSchema["properties"]["turn_id"]
        assert "conversation_id" in tool.inputSchema["required"]
        assert "turn_id" in tool.inputSchema["required"]
        assert conversation["maxLength"] == 133
        assert conversation["pattern"] == "^conv_[A-Za-z0-9_-]{1,128}$"
        assert turn["maxLength"] == 205
        assert turn["pattern"] == "^turn_[A-Za-z0-9_-]{8,200}$"
    snapshot_schema = tools_by_name["browser_snapshot"].inputSchema
    assert "text_start" not in snapshot_schema["required"]
    assert "text_length" not in snapshot_schema["required"]
    assert "include_targets" not in snapshot_schema["required"]
    assert snapshot_schema["properties"]["text_start"]["default"] == 0
    assert snapshot_schema["properties"]["text_length"]["default"] == 12_000
    assert snapshot_schema["properties"]["include_targets"]["default"] is True
    create_schema = tools_by_name["browser_tab_create"].inputSchema
    assert create_schema["properties"]["activate"]["default"] is True


@pytest.mark.anyio
async def test_mcp_server_exposes_capability_map_and_keeps_catalog_in_sync():
    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    server = create_server(client)

    tools = await server.list_tools()
    resources = await server.list_resources()
    contents = list(await server.read_resource("jobos://capability-map"))
    await client.aclose()

    assert "jobos://capability-map" in server.instructions
    assert "supporting Evidence is optional" in server.instructions
    assert [str(resource.uri) for resource in resources] == ["jobos://capability-map"]
    assert len(contents) == 1
    capability_map = contents[0].content
    assert "## Workflow: build a Career Profile through conversation" in capability_map
    assert "Evidence is optional" in capability_map

    documented_tools = set(re.findall(r"^\| `([a-z0-9_]+)` \|", capability_map, re.MULTILINE))
    assert documented_tools == {tool.name for tool in tools}

    repository_map = (
        Path(__file__).resolve().parents[3] / "docs/public/mcp-capability-map.md"
    ).read_text(encoding="utf-8")
    assert capability_map == repository_map


@pytest.mark.anyio
async def test_save_current_tab_contract_pages_oversized_listing_then_creates_and_associates():
    listing_text = "Applied Systems AI Product Manager\n" + "J" * 48_000
    revision = "a" * 64
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        if request.url.path == "/v1/browser/commands" and body["command"] == "page.snapshot":
            start = body["arguments"]["text_start"]
            length = body["arguments"]["text_length"]
            text = listing_text[start : start + length]
            return httpx.Response(
                200,
                json={
                    "state": "completed",
                    "outcome": "page.snapshot",
                    "data": {
                        "tab_id": "live-applied-systems-tab",
                        "url": "https://www.linkedin.com/jobs/view/4431837844/",
                        "title": "AI Product Manager",
                        "text": text,
                        "requested_text_start": start,
                        "text_start": start,
                        "text_length": len(text),
                        "next_text_start": (
                            start + len(text) if start + len(text) < len(listing_text) else None
                        ),
                        "total_text_length": len(listing_text),
                        "has_more": start + len(text) < len(listing_text),
                        "page_revision": revision,
                        "targets": [],
                    },
                },
            )
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json={"job_id": "job-applied-systems", "created": True})
        return httpx.Response(200, json={"state": "completed", "outcome": "tab.associate"})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    server = create_server(client)
    captured = []
    start = 0
    while True:
        content, snapshot = await server.call_tool(
            "browser_snapshot",
            {
                "conversation_id": "conv_save_current_tab",
                "turn_id": "turn_save_current_tab_0001",
                "tab_id": "live-applied-systems-tab",
                "text_start": start,
                "text_length": 12_000,
                "include_targets": False,
            },
        )
        assert sum(len(item.text) for item in content if hasattr(item, "text")) < 25_000
        assert snapshot["data"]["page_revision"] == revision
        captured.append(snapshot["data"]["text"])
        if not snapshot["data"]["has_more"]:
            break
        start = snapshot["data"]["next_text_start"]

    _, created = await server.call_tool(
        "job_create_from_browser",
        {
            "conversation_id": "conv_save_current_tab",
            "turn_id": "turn_save_current_tab_0001",
            "company_name": "Applied Systems",
            "title": "AI Product Manager",
            "canonical_url": "https://www.linkedin.com/jobs/view/4431837844/",
            "location_text": "Not specified",
            "description_text": "".join(captured),
            "application_url": "https://www.linkedin.com/jobs/view/4431837844/",
        },
    )
    await server.call_tool(
        "browser_tab_associate",
        {
            "conversation_id": "conv_save_current_tab",
            "turn_id": "turn_save_current_tab_0001",
            "tab_id": "live-applied-systems-tab",
            "job_id": created["job_id"],
        },
    )
    await client.aclose()

    assert "".join(captured) == listing_text
    assert created == {"job_id": "job-applied-systems", "created": True}
    create_body = json.loads(requests[-2].content)
    associate_body = json.loads(requests[-1].content)
    assert create_body["company_name"] == "Applied Systems"
    assert create_body["title"] == "AI Product Manager"
    assert associate_body["arguments"] == {
        "tab_id": "live-applied-systems-tab",
        "job_id": "job-applied-systems",
    }


@pytest.mark.anyio
async def test_parity_mutations_are_thin_authenticated_api_calls_with_idempotency():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/v1/workspace":
            return httpx.Response(200, json={"revision": 3, "active_center_surface": "browser"})
        return httpx.Response(200, json={"state": "completed"})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    await client.browser_command(
        "conv_browser_test",
        "element.click",
        {"tab_id": "tab-1", "target_id": "t_3"},
        idempotency_key="click-1",
    )
    await client.render_document("job-1", "resume-main", idempotency_key="render-1")
    await client.approve_document("job-1", "art_1234567890abcdef", idempotency_key="approve-1")
    await client.report_activity("Reviewed listing", "completed", idempotency_key="activity-1")
    await client.aclose()

    assert [(item.method, item.url.path) for item in requests] == [
        ("POST", "/v1/browser/commands"),
        ("POST", "/v1/jobs/job-1/artifacts/render"),
        ("POST", "/v1/jobs/job-1/artifacts/art_1234567890abcdef/approve"),
        ("POST", "/v1/activity"),
    ]
    assert [json.loads(item.content)["idempotency_key"] for item in requests] == [
        "click-1",
        "render-1",
        "approve-1",
        "activity-1",
    ]


@pytest.mark.anyio
async def test_document_select_reads_workspace_silently_then_emits_one_shared_mutation():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/workspace/jobs":
            return httpx.Response(
                200,
                json={
                    "selected_job_id": "job-1",
                    "sort_mode": "manual",
                    "manual_order": [],
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"artifacts": [{"artifact_id": "art_1234567890abcdef", "job_id": "job-1"}]},
            )
        return httpx.Response(
            200,
            json={
                "job_context": {
                    "selected_job_id": "job-1",
                    "active_artifact_id": "art_1234567890abcdef",
                    "active_artifact_page": 1,
                    "active_artifact_zoom": 1.0,
                }
            },
        )

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    client.scope_turn("conv_test", "turn_document_test_0001")
    await client.select_document("art_1234567890abcdef", idempotency_key="select-document-1")
    await client.aclose()

    assert [(item.method, item.url.path) for item in requests] == [
        ("GET", "/v1/workspace/jobs"),
        ("GET", "/v1/jobs/job-1/artifacts"),
        ("PUT", "/v1/conversations/conv_test/workspace/document"),
    ]
    assert requests[0].url.params["conversation_id"] == "conv_test"
    assert requests[1].url.params["conversation_id"] == "conv_test"
    assert json.loads(requests[2].content) == {
        "active_artifact_id": "art_1234567890abcdef",
        "active_artifact_page": 1,
        "active_artifact_zoom": 1.0,
        "origin": "mcp",
        "idempotency_key": "select-document-1",
    }


@pytest.mark.anyio
async def test_document_draft_tools_are_bounded_owned_authenticated_api_calls():
    requests = []
    document_id = "edoc_123456789012345678901234"

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/editable-documents"):
            return httpx.Response(
                200,
                json={
                    "documents": [
                        {
                            "document_id": document_id,
                            "job_id": "job-1",
                            "document_key": "references",
                            "document_label": "References",
                            "revision": 3,
                            "source_artifact_id": None,
                            "published_revision": None,
                            "created_at": "2026-08-07",
                            "updated_at": "2026-08-07",
                        }
                    ]
                },
            )
        if "editable-document-outlines" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "document_id": document_id,
                    "document_key": "references",
                    "revision": 3,
                    "outline": [],
                },
            )
        return httpx.Response(200, json={"document_id": document_id})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    outline = await client.get_document_draft("job-1", "references", idempotency_key="draft-get-1")
    await client.apply_document_draft(
        "job-1",
        document_id,
        3,
        [
            {
                "type": "replace_block_text",
                "block_id": "node_12345678-1234-4123-8123-123456789012",
                "expected_text": "Before",
                "replacement_text": "After",
            }
        ],
        idempotency_key="draft-apply-1",
    )
    await client.snapshot_document_draft(
        "job-1", document_id, "Agent checkpoint", idempotency_key="draft-snapshot-1"
    )
    await client.aclose()

    assert "content" not in outline
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/jobs/job-1/editable-document-outlines/references"),
        ("GET", "/v1/jobs/job-1/editable-documents"),
        ("POST", f"/v1/editable-documents/{document_id}/operations"),
        ("GET", "/v1/jobs/job-1/editable-documents"),
        ("POST", f"/v1/editable-documents/{document_id}/snapshots"),
    ]
    assert requests[0].url.params["origin"] == "mcp"
    assert json.loads(requests[2].content)["origin"] == "mcp"
    assert json.loads(requests[4].content) == {
        "base_revision": 3,
        "reason": "manual",
        "label": "Agent checkpoint",
        "origin": "mcp",
        "idempotency_key": "draft-snapshot-1",
    }


@pytest.mark.anyio
async def test_document_draft_mutations_reject_cross_job_document_ids_before_posting():
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"documents": []})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ValueError, match="not owned"):
        await client.apply_document_draft(
            "job-wrong",
            "edoc_123456789012345678901234",
            1,
            [],
            idempotency_key="cross-job-1",
        )
    await client.aclose()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/jobs/job-wrong/editable-documents")
    ]
