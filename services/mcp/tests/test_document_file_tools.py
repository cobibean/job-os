import json

import httpx
import pytest
from jobos_mcp.jobs import JobOsMcpClient


@pytest.mark.anyio
async def test_document_file_tools_send_bounded_hash_checked_commands():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"state": "completed", "data": {}})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    await client.inspect_document_file(
        "(FAKE)-job-7", "resume", idempotency_key="(FAKE)-inspect-7"
    )
    await client.apply_document_file_operations(
        "(FAKE)-job-7",
        "resume",
        "a" * 64,
        [
            {
                "type": "replace_block_text",
                "blockId": "docx:0",
                "expectedCurrentText": "(FAKE) Alex Morgan",
                "text": "(FAKE) Alex Morgan — edited",
            }
        ],
        idempotency_key="(FAKE)-apply-7",
    )
    with pytest.raises(ValueError, match="SHA-256"):
        await client.apply_document_file_operations(
            "(FAKE)-job-7", "resume", "stale", [{"type": "replace_block_text"}]
        )
    await client.aclose()

    assert [request.url.path for request in requests] == [
        "/v1/browser/commands",
        "/v1/browser/commands",
    ]
    inspect_payload, apply_payload = [json.loads(request.content) for request in requests]
    assert inspect_payload == {
        "command": "document.inspect",
        "arguments": {"job_id": "(FAKE)-job-7", "document_key": "resume"},
        "origin": "mcp",
        "idempotency_key": "(FAKE)-inspect-7",
        "timeout_ms": 10_000,
    }
    assert apply_payload["command"] == "document.apply_operations"
    assert apply_payload["arguments"]["expected_sha256"] == "a" * 64
    assert apply_payload["arguments"]["operations"][0]["text"].endswith("— edited")
