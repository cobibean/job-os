import json

import httpx
import pytest
from jobos_mcp.jobs import JobOsMcpClient
from jobos_mcp.server import create_server


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
        transport=httpx.MockTransport(handler),
    )

    await client.list_jobs(sort="status", query="builder")
    await client.inspect_job("job-1")
    await client.select_job("job-1")
    await client.reorder_jobs(["job-1", "job-2"])
    await client.update_status("job-1", "reviewed", reason="Agent review")
    await client.aclose()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/jobs"),
        ("GET", "/v1/jobs/job-1"),
        ("PUT", "/v1/workspace/jobs/selection"),
        ("PUT", "/v1/jobs/order"),
        ("PUT", "/v1/jobs/job-1/status"),
    ]
    assert all(
        request.headers["authorization"] == "Bearer test-device-token"
        for request in requests
    )
    assert json.loads(requests[2].content) == {"job_id": "job-1", "origin": "mcp"}
    assert json.loads(requests[4].content) == {
        "target_status": "reviewed",
        "origin": "mcp",
        "reason": "Agent review",
    }


@pytest.mark.anyio
async def test_mcp_server_exposes_only_the_five_phase_two_job_tools():
    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )

    server = create_server(client)
    tools = await server.list_tools()
    await client.aclose()

    assert [tool.name for tool in tools] == [
        "job_list",
        "job_inspect",
        "job_select",
        "job_reorder",
        "job_update_status",
    ]
