import json

import httpx
import pytest
from jobos_mcp.jobs import JobOsMcpClient
from jobos_mcp.server import create_server


@pytest.mark.anyio
async def test_career_profile_client_submits_exact_agent_edit_contract():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "outcome": "proposal",
                "profile": {"profile_revision": 4, "items": []},
                "proposal": {"proposal_id": "cpp_fixturefixture1234"},
            },
        )

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        agent_id="job-hunter",
        agent_token="test-career-profile-agent-token",
        transport=httpx.MockTransport(handler),
    )

    result = await client.edit_career_profile(
        expected_profile_revision=4,
        operation="item.create",
        reason="The user described this skill in the current conversation",
        value={"kind": "skill", "name": "Product strategy"},
        evidence_ids=[],
        idempotency_key="career-profile-edit-0001",
    )
    await client.aclose()

    assert result["outcome"] == "proposal"
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/career-profile/agent-edits"),
    ]
    assert all(
        request.headers["authorization"] == "Bearer test-mcp-trusted-token" for request in requests
    )
    assert all(
        request.headers["x-jobos-mcp-token"] == "test-mcp-trusted-token" for request in requests
    )
    assert all(request.headers["x-jobos-agent-id"] == "job-hunter" for request in requests)
    assert all(
        request.headers["x-jobos-agent-token"] == "test-career-profile-agent-token"
        for request in requests
    )
    assert json.loads(requests[0].content) == {
        "expected_profile_revision": 4,
        "idempotency_key": "career-profile-edit-0001",
        "operation": "item.create",
        "target_id": None,
        "reason": "The user described this skill in the current conversation",
        "value": {"kind": "skill", "name": "Product strategy"},
        "evidence_ids": [],
    }


@pytest.mark.anyio
async def test_career_profile_get_reads_only_the_authorized_consumer_projection():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "scope": {"mode": "selected"},
                "projection": {"authority_state": "cutover", "items": []},
            },
        )

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        agent_id="job-hunter",
        agent_token="test-career-profile-agent-token",
        transport=httpx.MockTransport(handler),
    )

    result = await client.get_career_profile_projection()
    await client.aclose()

    assert result["projection"]["authority_state"] == "cutover"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/career-profile/consumer-projection")
    ]
    assert requests[0].headers["x-jobos-agent-id"] == "job-hunter"


@pytest.mark.anyio
async def test_career_profile_mcp_tools_keep_review_decisions_user_owned():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "outcome": "proposal",
                "profile": {"profile_revision": 0, "items": []},
                "proposal": {"proposal_id": "cpp_fixturefixture1234"},
            },
        )

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    server = create_server(client)

    _, edit = await server.call_tool(
        "career_profile_edit",
        {
            "expected_profile_revision": 0,
            "operation": "item.create",
            "reason": "Remember a user-authored skill",
            "value": {"kind": "skill", "name": "Python"},
            "evidence_ids": [],
            "idempotency_key": "career-profile-tool-edit-0001",
        },
    )
    tools = {tool.name for tool in await server.list_tools()}
    await client.aclose()

    assert isinstance(edit, dict) and edit["outcome"] == "proposal"
    assert "career_profile_get" in tools
    assert "career_profile_edit" in tools
    assert "career_profile_proposal_accept" not in tools
    assert "career_profile_trust_mode_update" not in tools
    assert [request.url.path for request in requests] == [
        "/v1/career-profile/agent-edits",
    ]
