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


@pytest.mark.anyio
async def test_career_profile_agent_operating_tools_map_to_scoped_api_contracts():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/agent-search"):
            return httpx.Response(200, json={"profile_revision": 4, "items": []})
        if request.url.path.endswith("/batch"):
            return httpx.Response(200, json={"profile_revision": 6, "results": []})
        if request.url.path.endswith("/agent-changes"):
            return httpx.Response(
                200,
                json={"profile_revision": 6, "proposals": [], "applied_revisions": []},
            )
        if "/agent-evidence/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "evidence": {"evidence_id": "cpe_fixturefixture1234"},
                    "content_base64": "c3ludGhldGlj",
                },
            )
        if request.url.path.endswith("/agent-evidence"):
            return httpx.Response(
                201,
                json={
                    "profile_revision": 7,
                    "evidence": {"evidence_id": "cpe_fixturefixture1234"},
                },
            )
        raise AssertionError(request.url)

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        agent_id="job-hunter",
        agent_token="test-career-profile-agent-token",
        transport=httpx.MockTransport(handler),
    )
    server = create_server(client)

    await server.call_tool(
        "career_profile_search",
        {
            "query": "Python",
            "kinds": ["skill"],
            "areas": ["my_career"],
            "review_statuses": ["accepted"],
            "has_evidence": False,
            "limit": 20,
        },
    )
    await server.call_tool(
        "career_profile_edit_batch",
        {
            "expected_profile_revision": 4,
            "edits": [
                {
                    "operation": "item.create",
                    "reason": "The user described this skill",
                    "value": {"kind": "skill", "name": "Python"},
                    "evidence_ids": [],
                }
            ],
            "idempotency_key": "career-profile-batch-0001",
        },
    )
    await server.call_tool(
        "career_profile_changes_list",
        {"status": "all", "limit": 20},
    )
    await server.call_tool(
        "career_profile_evidence_import",
        {
            "expected_profile_revision": 6,
            "original_filename": "synthetic.txt",
            "media_type": "text/plain",
            "source_kind": "supporting_document",
            "source_label": "Synthetic notes",
            "content_base64": "c3ludGhldGlj",
            "extractions": [],
            "idempotency_key": "career-profile-evidence-0001",
        },
    )
    await server.call_tool(
        "career_profile_evidence_inspect",
        {
            "evidence_id": "cpe_fixturefixture1234",
            "byte_start": 0,
            "byte_length": 1000,
        },
    )
    tools = {tool.name for tool in await server.list_tools()}
    await client.aclose()

    assert {
        "career_profile_search",
        "career_profile_edit_batch",
        "career_profile_changes_list",
        "career_profile_evidence_import",
        "career_profile_evidence_inspect",
    } <= tools
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/career-profile/agent-search"),
        ("POST", "/v1/career-profile/agent-edits/batch"),
        ("GET", "/v1/career-profile/agent-changes"),
        ("POST", "/v1/career-profile/agent-evidence"),
        ("GET", "/v1/career-profile/agent-evidence/cpe_fixturefixture1234"),
    ]
    assert requests[0].url.params.get_list("kinds") == ["skill"]
    assert requests[0].url.params.get_list("areas") == ["my_career"]
    assert json.loads(requests[1].content)["edits"][0]["evidence_ids"] == []
    imported = json.loads(requests[3].content)
    assert imported["provenance"] == {
        "source_kind": "supporting_document",
        "source_label": "Synthetic notes",
        "method": "agent_import",
    }
    assert all(request.headers["x-jobos-agent-id"] == "job-hunter" for request in requests)
