import json

import httpx
import pytest
from jobos_mcp.jobs import JobOsMcpClient
from jobos_mcp.profile_views import (
    AUTO_COMPACT_BUDGET,
    LARGE_SELECTION_NOTE,
    compact_item,
    existing_evidence_ids,
    index_item,
    keep_unsaid_fields,
    leaves_fields_unsaid,
    list_items,
    shape_edit_result,
    shape_projection,
    shape_search,
)
from jobos_mcp.server import create_server

SCOPE = {
    "conversation_id": "conv_career_profile_views",
    "turn_id": "turn_career_profile_views_0001",
}


def _item(item_id: str, value: dict, *, evidence_ids=(), area="my_career", created_at="1"):
    return {
        "item_id": item_id,
        "area": area,
        "value": value,
        "review_status": "accepted",
        "evidence_ids": list(evidence_ids),
        "provenance": {"method": "agent_edit", "source_label": None},
        "item_revision": 3,
        "actor_principal": "agent:synthetic",
        "created_at": created_at,
        "updated_at": "2",
    }


def _preview() -> dict:
    return {
        "agent_id": "synthetic-agent",
        "profile_revision": 7,
        "scope": {"agent_id": "synthetic-agent", "mode": "broader", "selected_item_ids": []},
        "content_hash": "0" * 64,
        "projection": {
            "profile_revision": 7,
            "authority_state": "cutover",
            "items": [
                _item(
                    "cpi_syntheticskill000001",
                    {"kind": "skill", "name": "Synthetic Python"},
                    evidence_ids=["cpe_syntheticsource0001"],
                ),
                _item(
                    "cpi_syntheticcustom00001",
                    {"kind": "custom", "label": "Synthetic brief", "text": "Read first"},
                ),
                _item(
                    "cpi_syntheticroles000001",
                    {"kind": "target_roles", "roles": ["Synthetic role"]},
                    area="what_im_looking_for",
                ),
            ],
            "source_evidence": [
                {
                    "evidence_id": "cpe_syntheticsource0001",
                    "original_filename": "synthetic.txt",
                    "media_type": "text/plain",
                    "byte_count": 9,
                    "sha256": "1" * 64,
                    "active": True,
                }
            ],
        },
    }


def _client(handler) -> JobOsMcpClient:
    return JobOsMcpClient(
        base_url="http://jobos.test",
        device_token="test-device-token",
        mcp_token="test-mcp-trusted-token",
        agent_id="synthetic-agent",
        agent_token="test-career-profile-agent-token",
        transport=httpx.MockTransport(handler),
    )


def test_compact_item_keeps_identity_value_and_evidence_links_only():
    item = _item(
        "cpi_syntheticskill000001",
        {"kind": "skill", "name": "Synthetic Python"},
        evidence_ids=["cpe_syntheticsource0001"],
    )

    assert compact_item(item) == {
        "item_id": "cpi_syntheticskill000001",
        "value": {"kind": "skill", "name": "Synthetic Python"},
        "evidence_ids": ["cpe_syntheticsource0001"],
    }
    assert compact_item({**item, "review_status": "proposed"})["review_status"] == "proposed"


def test_compact_projection_drops_bookkeeping_and_counts_what_it_returns():
    shaped = shape_projection(_preview(), view="compact", kinds=None)

    assert shaped["view"] == "compact"
    assert shaped["profile_revision"] == 7
    assert shaped["scope"] == {"mode": "broader"}
    assert shaped["total_items"] == 3 and shaped["returned_items"] == 3
    assert all(
        set(item) == {"item_id", "value", "evidence_ids"} for item in shaped["projection"]["items"]
    )
    assert shaped["projection"]["source_evidence"] == [
        {
            "evidence_id": "cpe_syntheticsource0001",
            "original_filename": "synthetic.txt",
            "media_type": "text/plain",
        }
    ]


def test_kind_filter_returns_only_those_items_and_no_evidence():
    shaped = shape_projection(_preview(), view="compact", kinds=["custom"])

    assert [item["item_id"] for item in shaped["projection"]["items"]] == [
        "cpi_syntheticcustom00001"
    ]
    assert shaped["projection"]["source_evidence"] == []
    assert shaped["total_items"] == 3 and shaped["returned_items"] == 1


def test_index_names_items_without_their_content_and_counts_each_kind():
    long_statement = "Synthetic statement " * 20
    indexed = index_item(
        _item("cpi_syntheticclaim000001", {"kind": "claim", "statement": long_statement})
    )
    shaped = shape_projection(_preview(), view="index")

    assert indexed["kind"] == "claim"
    assert len(indexed["title"]) == 120 and indexed["title"].endswith("…")
    assert shaped["view"] == "index"
    assert shaped["projection"]["items"] == [
        {"item_id": "cpi_syntheticskill000001", "kind": "skill", "title": "Synthetic Python"},
        {"item_id": "cpi_syntheticcustom00001", "kind": "custom", "title": "Synthetic brief"},
        {"item_id": "cpi_syntheticroles000001", "kind": "target_roles", "title": ""},
    ]
    assert shaped["kind_counts"] == {"custom": 1, "skill": 1, "target_roles": 1}
    assert "note" not in shaped


def test_item_id_filter_returns_exactly_the_named_items():
    shaped = shape_projection(
        _preview(), view="compact", item_ids=["cpi_syntheticroles000001", "cpi_missing0000000001"]
    )

    assert [item["item_id"] for item in shaped["projection"]["items"]] == [
        "cpi_syntheticroles000001"
    ]
    assert shaped["projection"]["source_evidence"] == []
    assert shaped["returned_items"] == 1


def test_auto_view_is_compact_when_it_fits_and_the_index_when_it_does_not():
    small = shape_projection(_preview(), view="auto")
    large_preview = _preview()
    large_preview["projection"]["items"].append(
        _item(
            "cpi_syntheticlarge000001",
            {"kind": "custom", "label": "Synthetic large", "text": "x" * AUTO_COMPACT_BUDGET},
        )
    )
    large = shape_projection(large_preview, view="auto")
    narrowed = shape_projection(large_preview, view="auto", kinds=["skill"])
    found = shape_search(
        {"profile_revision": 7, "items": large_preview["projection"]["items"]}, view="auto"
    )

    assert small["view"] == "compact" and "note" not in small
    assert large["view"] == "index" and large["note"] == LARGE_SELECTION_NOTE
    assert large["kind_counts"] == {"custom": 2, "skill": 1, "target_roles": 1}
    assert "x" * 100 not in str(large)
    assert narrowed["view"] == "compact" and narrowed["returned_items"] == 1
    assert found["view"] == "index" and found["note"] == LARGE_SELECTION_NOTE


def test_full_view_without_a_filter_is_the_exact_api_projection():
    preview = _preview()

    assert shape_projection(preview, view="full", kinds=None) is preview
    filtered = shape_projection(preview, view="full", kinds=["skill"])
    assert filtered["projection"]["items"] == [preview["projection"]["items"][0]]
    assert filtered["content_hash"] == preview["content_hash"]


def test_list_items_applies_search_filters_without_a_keyword():
    by_area = list_items(
        _preview(),
        view="compact",
        kinds=None,
        areas=["what_im_looking_for"],
        review_statuses=None,
        has_evidence=None,
        limit=25,
    )
    with_evidence = list_items(
        _preview(),
        view="full",
        kinds=None,
        areas=None,
        review_statuses=["accepted"],
        has_evidence=True,
        limit=25,
    )
    bounded = list_items(
        _preview(),
        view="compact",
        kinds=None,
        areas=None,
        review_statuses=None,
        has_evidence=None,
        limit=1,
    )

    assert [item["item_id"] for item in by_area["items"]] == ["cpi_syntheticroles000001"]
    assert by_area["source_evidence"] == [] and by_area["total_evidence"] == 0
    assert [item["item_id"] for item in with_evidence["items"]] == ["cpi_syntheticskill000001"]
    assert with_evidence["items"][0]["item_revision"] == 3
    assert len(bounded["items"]) == 1 and bounded["total_items"] == 3


def test_existing_evidence_ids_reads_only_visible_items():
    assert existing_evidence_ids(_preview(), "cpi_syntheticskill000001") == [
        "cpe_syntheticsource0001"
    ]
    assert existing_evidence_ids(_preview(), "cpi_syntheticcustom00001") == []
    assert existing_evidence_ids(_preview(), "cpi_syntheticmissing0001") is None
    assert existing_evidence_ids(_preview(), None) is None


def test_edit_result_reports_the_outcome_without_the_profile():
    profile = _preview()["projection"]
    profile["items"].append(
        _item(
            "cpi_syntheticskill000002",
            {"kind": "skill", "name": "Synthetic Go", "level": None},
            created_at="9",
        )
    )

    created = shape_edit_result(
        {"outcome": "applied", "profile": profile, "proposal": None},
        operation="item.create",
        target_id=None,
        value={"kind": "skill", "name": "Synthetic Go"},
    )
    updated = shape_edit_result(
        {"outcome": "applied", "profile": profile, "proposal": None},
        operation="item.update",
        target_id="cpi_syntheticskill000001",
        value={"kind": "skill", "name": "Synthetic Python"},
    )
    proposed = shape_edit_result(
        {
            "outcome": "proposal",
            "profile": profile,
            "proposal": {
                "proposal_id": "cpp_syntheticproposal01",
                "review_reason": "Removing Career Profile information always needs your approval.",
                "target_id": "cpi_syntheticskill000001",
                "status": "pending",
                "before": profile["items"][0],
                "after": None,
            },
        },
        operation="item.remove",
        target_id="cpi_syntheticskill000001",
        value=None,
    )

    assert created == {
        "outcome": "applied",
        "operation": "item.create",
        "profile_revision": 7,
        "target_id": "cpi_syntheticskill000002",
    }
    assert updated["target_id"] == "cpi_syntheticskill000001"
    assert proposed == {
        "outcome": "proposal",
        "operation": "item.remove",
        "profile_revision": 7,
        "target_id": "cpi_syntheticskill000001",
        "proposal": {
            "proposal_id": "cpp_syntheticproposal01",
            "review_reason": "Removing Career Profile information always needs your approval.",
            "status": "pending",
        },
    }


@pytest.mark.anyio
async def test_career_profile_get_is_compact_by_default_and_offers_index_and_exact_views():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_preview())

    client = _client(handler)
    server = create_server(client)

    _, compact = await server.call_tool("career_profile_get", SCOPE)
    _, filtered = await server.call_tool("career_profile_get", SCOPE | {"kinds": ["custom"]})
    _, named = await server.call_tool(
        "career_profile_get", SCOPE | {"item_ids": ["cpi_syntheticskill000001"]}
    )
    _, index = await server.call_tool("career_profile_get", SCOPE | {"view": "index"})
    _, full = await server.call_tool("career_profile_get", SCOPE | {"view": "full"})
    await client.aclose()

    assert compact["view"] == "compact" and compact["returned_items"] == 3
    assert "provenance" not in json.dumps(compact)
    assert [item["value"]["kind"] for item in filtered["projection"]["items"]] == ["custom"]
    assert [item["item_id"] for item in named["projection"]["items"]] == [
        "cpi_syntheticskill000001"
    ]
    assert index["view"] == "index" and "value" not in index["projection"]["items"][0]
    assert full == _preview()
    assert {request.url.path for request in requests} == {"/v1/career-profile/consumer-projection"}


@pytest.mark.anyio
async def test_career_profile_search_lists_without_a_keyword_and_searches_with_one():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/agent-search"):
            return httpx.Response(
                200,
                json={
                    "profile_revision": 7,
                    "items": [_preview()["projection"]["items"][0]],
                    "source_evidence": [],
                    "total_items": 1,
                    "total_evidence": 0,
                },
            )
        return httpx.Response(200, json=_preview())

    client = _client(handler)
    server = create_server(client)

    _, listed = await server.call_tool("career_profile_search", SCOPE | {"kinds": ["custom"]})
    _, found = await server.call_tool("career_profile_search", SCOPE | {"query": "Python"})
    _, exact = await server.call_tool(
        "career_profile_search", SCOPE | {"query": "Python", "view": "full"}
    )
    await client.aclose()

    assert [item["item_id"] for item in listed["items"]] == ["cpi_syntheticcustom00001"]
    assert listed["total_items"] == 1
    assert found["view"] == "compact" and found["total_items"] == 1
    assert set(found["items"][0]) == {"item_id", "value", "evidence_ids"}
    assert exact["items"][0]["item_revision"] == 3
    assert [request.url.path for request in requests] == [
        "/v1/career-profile/consumer-projection",
        "/v1/career-profile/agent-search",
        "/v1/career-profile/agent-search",
    ]


@pytest.mark.anyio
async def test_career_profile_update_keeps_evidence_links_unless_told_to_remove_them():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_preview())
        return httpx.Response(
            200,
            json={"outcome": "applied", "profile": _preview()["projection"], "proposal": None},
        )

    client = _client(handler)
    server = create_server(client)
    update = {
        "expected_profile_revision": 7,
        "operation": "item.update",
        "target_id": "cpi_syntheticskill000001",
        "reason": "Synthetic wording change",
        "value": {"kind": "skill", "name": "Synthetic Python 3"},
    }

    _, kept = await server.call_tool(
        "career_profile_edit", SCOPE | update | {"idempotency_key": "synthetic-keep-evidence-01"}
    )
    _, cleared = await server.call_tool(
        "career_profile_edit",
        SCOPE | update | {"evidence_ids": [], "idempotency_key": "synthetic-clear-evidence-1"},
    )
    await client.aclose()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/career-profile/consumer-projection"),
        ("POST", "/v1/career-profile/agent-edits"),
        ("POST", "/v1/career-profile/agent-edits"),
    ]
    assert json.loads(requests[1].content)["evidence_ids"] == ["cpe_syntheticsource0001"]
    assert json.loads(requests[2].content)["evidence_ids"] == []
    assert kept == {
        "outcome": "applied",
        "operation": "item.update",
        "profile_revision": 7,
        "target_id": "cpi_syntheticskill000001",
    }
    assert "profile" not in cleared


@pytest.mark.anyio
async def test_career_profile_batch_fills_only_omitted_evidence_links_on_updates():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_preview())
        return httpx.Response(200, json={"profile_revision": 8, "results": []})

    client = _client(handler)
    server = create_server(client)
    create = {
        "operation": "item.create",
        "reason": "Synthetic new skill",
        "value": {"kind": "skill", "name": "Synthetic Go"},
    }

    await server.call_tool(
        "career_profile_edit_batch",
        SCOPE
        | {
            "expected_profile_revision": 7,
            "edits": [create],
            "idempotency_key": "synthetic-batch-create-01",
        },
    )
    await server.call_tool(
        "career_profile_edit_batch",
        SCOPE
        | {
            "expected_profile_revision": 7,
            "edits": [
                create,
                {
                    "operation": "item.update",
                    "target_id": "cpi_syntheticskill000001",
                    "reason": "Synthetic wording change",
                    "value": {"kind": "skill", "name": "Synthetic Python 3"},
                },
                {
                    "operation": "item.update",
                    "target_id": "cpi_syntheticcustom00001",
                    "reason": "Synthetic explicit removal",
                    "value": {"kind": "custom", "label": "Synthetic brief", "text": "Updated"},
                    "evidence_ids": [],
                },
                {
                    "operation": "item.remove",
                    "target_id": "cpi_syntheticroles000001",
                    "reason": "Synthetic removal",
                },
            ],
            "idempotency_key": "synthetic-batch-update-01",
        },
    )
    await client.aclose()

    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/career-profile/agent-edits/batch"),
        ("GET", "/v1/career-profile/consumer-projection"),
        ("POST", "/v1/career-profile/agent-edits/batch"),
    ]
    assert "evidence_ids" not in json.loads(requests[0].content)["edits"][0]
    sent = json.loads(requests[2].content)["edits"]
    assert "evidence_ids" not in sent[0]
    assert sent[1]["evidence_ids"] == ["cpe_syntheticsource0001"]
    assert sent[2]["evidence_ids"] == []
    assert "evidence_ids" not in sent[3]


def _claim_preview() -> dict:
    preview = _preview()
    preview["projection"]["items"].append(
        _item(
            "cpi_syntheticclaim000001",
            {
                "kind": "claim",
                "statement": "Synthetic statement",
                "qualifiers": ["Synthetic point"],
                "forbidden_uses": [],
                "notes": "Synthetic history",
            },
            evidence_ids=["cpe_syntheticsource0001"],
        )
    )
    return preview


def test_compact_view_leaves_out_claim_notes_and_says_they_exist():
    preview = _claim_preview()
    claim = preview["projection"]["items"][-1]

    compact = compact_item(claim)
    full = shape_projection(preview, view="full", kinds=["claim"])

    assert "notes" not in compact["value"] and compact["has_notes"] is True
    assert compact["value"]["statement"] == "Synthetic statement"
    assert claim["value"]["notes"] == "Synthetic history"
    assert full["projection"]["items"][0]["value"]["notes"] == "Synthetic history"
    assert "has_notes" not in compact_item(preview["projection"]["items"][0])


def test_an_update_keeps_claim_notes_and_evidence_it_did_not_mention():
    preview = _claim_preview()
    rewrite = {
        "operation": "item.update",
        "target_id": "cpi_syntheticclaim000001",
        "reason": "Synthetic rewording",
        "value": {"kind": "claim", "statement": "Synthetic statement, reworded"},
    }
    explicit = {
        **rewrite,
        "value": {**rewrite["value"], "notes": None},
        "evidence_ids": [],
    }
    skill = {
        "operation": "item.update",
        "target_id": "cpi_syntheticskill000001",
        "reason": "Synthetic rewording",
        "value": {"kind": "skill", "name": "Synthetic Python 3"},
        "evidence_ids": [],
    }

    kept = keep_unsaid_fields(rewrite, preview)

    assert leaves_fields_unsaid(rewrite) and not leaves_fields_unsaid(explicit)
    assert not leaves_fields_unsaid(skill)
    assert not leaves_fields_unsaid({**rewrite, "operation": "item.create"})
    assert kept["value"]["notes"] == "Synthetic history"
    assert kept["evidence_ids"] == ["cpe_syntheticsource0001"]
    assert "notes" not in rewrite["value"]
    assert keep_unsaid_fields(explicit, preview) is explicit
    assert keep_unsaid_fields(skill, preview) is skill


@pytest.mark.anyio
async def test_career_profile_edit_keeps_claim_notes_unless_told_to_remove_them():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=_claim_preview())
        return httpx.Response(
            200,
            json={
                "outcome": "applied",
                "profile": _claim_preview()["projection"],
                "proposal": None,
            },
        )

    client = _client(handler)
    server = create_server(client)
    update = {
        "expected_profile_revision": 7,
        "operation": "item.update",
        "target_id": "cpi_syntheticclaim000001",
        "reason": "Synthetic rewording",
        "evidence_ids": ["cpe_syntheticsource0001"],
    }

    await server.call_tool(
        "career_profile_edit",
        SCOPE
        | update
        | {
            "value": {"kind": "claim", "statement": "Synthetic statement, reworded"},
            "idempotency_key": "synthetic-keep-notes-0001",
        },
    )
    await server.call_tool(
        "career_profile_edit",
        SCOPE
        | update
        | {
            "value": {"kind": "claim", "statement": "Synthetic statement", "notes": None},
            "idempotency_key": "synthetic-clear-notes-001",
        },
    )
    await client.aclose()

    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/v1/career-profile/consumer-projection"),
        ("POST", "/v1/career-profile/agent-edits"),
        ("POST", "/v1/career-profile/agent-edits"),
    ]
    assert json.loads(requests[1].content)["value"]["notes"] == "Synthetic history"
    assert json.loads(requests[2].content)["value"]["notes"] is None
