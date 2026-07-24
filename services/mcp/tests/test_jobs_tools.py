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
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )

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
        ("PUT", "/v1/workspace/jobs/selection"),
        ("PUT", "/v1/jobs/order"),
        ("PUT", "/v1/jobs/job-1/status"),
        ("PUT", "/v1/jobs/job-1/description"),
    ]
    assert all(
        request.headers["authorization"] == "Bearer test-device-token" for request in requests
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
        "source": "jobhunter_agent",
        "provenance": "Supplied by the user",
        "origin": "mcp",
        "idempotency_key": "description-1",
    }


@pytest.mark.anyio
async def test_mcp_server_exposes_phase_seven_parity_tools_while_retaining_job_tools():
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
        "workspace_inspect",
        "workspace_update",
        "document_list",
        "document_refresh",
        "document_render",
        "document_register",
        "document_approve",
        "document_select",
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
        "element.click",
        {"tab_id": "tab-1", "target_id": "t_3"},
        idempotency_key="click-1",
    )
    await client.render_document("job-1", "resume-main", idempotency_key="render-1")
    await client.approve_document(
        "job-1", "art_1234567890abcdef", idempotency_key="approve-1"
    )
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
        if request.url.path == "/v1/workspace":
            return httpx.Response(200, json={
                "revision": 3,
                "selected_preset": "research",
                "layouts": {
                    name: {
                        "order": ["jobs", "center", "agent"],
                        "widths": {"jobs": 280, "center": 720, "agent": 360},
                        "collapsed": [],
                    }
                    for name in ("research", "review", "agent-focus")
                },
                "selected_job_id": "job-1", "active_center_surface": "browser",
                "browser_tabs": [], "active_browser_tab_id": None,
                "active_artifact_id": None, "active_artifact_page": 1, "active_artifact_zoom": 1.0,
                "repaired_presets": [], "repaired_browser": False,
                "browser_repair_reasons": ["dropped_tabs"],
            })
        if request.method == "GET":
            return httpx.Response(200, json={"artifacts": [
                {"artifact_id": "art_1234567890abcdef", "job_id": "job-1"}
            ]})
        return httpx.Response(200, json={"revision": 4, "active_center_surface": "document"})

    client = JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        transport=httpx.MockTransport(handler),
    )
    await client.select_document("art_1234567890abcdef", idempotency_key="select-document-1")
    await client.aclose()

    assert [(item.method, item.url.path) for item in requests] == [
        ("GET", "/v1/workspace"),
        ("GET", "/v1/jobs/job-1/artifacts"),
        ("PUT", "/v1/workspace"),
    ]
    assert requests[0].url.query == b""
    assert requests[1].url.query == b""
    assert json.loads(requests[2].content) == {
        "revision": 3,
        "selected_preset": "research",
        "layouts": {
            name: {
                "order": ["jobs", "center", "agent"],
                "widths": {"jobs": 280, "center": 720, "agent": 360},
                "collapsed": [],
            }
            for name in ("research", "review", "agent-focus")
        },
        "selected_job_id": "job-1",
        "active_center_surface": "document",
        "browser_tabs": [],
        "active_browser_tab_id": None,
        "active_artifact_id": "art_1234567890abcdef",
        "active_artifact_page": 1,
        "active_artifact_zoom": 1.0,
        "origin": "mcp",
        "idempotency_key": "select-document-1",
    }
