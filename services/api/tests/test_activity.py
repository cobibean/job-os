import pytest
from jobos_api.activity import ActivityNormalizer
from jobos_api.redaction import redact_detail, sanitize_text, sanitize_user_text


@pytest.mark.parametrize(
    ("frame", "expected_label"),
    [
        ({"type": "tool.start", "tool_id": "1", "name": "shell"}, "Running command"),
        ({"type": "tool.start", "tool_id": "2", "name": "read_file"}, "Reading file"),
        ({"type": "tool.start", "tool_id": "3", "name": "browser.open"}, "Using browser"),
    ],
)
def test_tool_names_receive_plain_language_labels(frame, expected_label):
    event = ActivityNormalizer().normalize(frame)
    assert event.summary == expected_label


def test_progress_and_completion_update_one_activity_identity():
    normalizer = ActivityNormalizer()
    started = normalizer.normalize({"type": "tool.start", "tool_id": "tool-1", "name": "shell"})
    progress = normalizer.normalize(
        {"type": "tool.progress", "tool_id": "tool-1", "message": "working"}
    )
    complete = normalizer.normalize(
        {"type": "tool.complete", "tool_id": "tool-1", "status": "complete"}
    )

    assert {started.activity_id, progress.activity_id, complete.activity_id} == {"tool-1"}
    assert [started.state, progress.state, complete.state] == ["working", "working", "completed"]


def test_tool_output_risk_is_advisory_and_does_not_pause_a_completed_mcp_call():
    normalizer = ActivityNormalizer()
    normalizer.normalize(
        {"type": "tool.start", "tool_id": "tool-1", "name": "mcp_jobos_job_inspect"}
    )
    normalizer.normalize(
        {
            "type": "tool.complete",
            "tool_id": "tool-1",
            "name": "mcp_jobos_job_inspect",
            "status": "complete",
        }
    )

    advisory = normalizer.normalize(
        {
            "type": "tool.output_risk",
            "tool_id": "tool-1",
            "name": "mcp_jobos_job_inspect",
            "risk": "high",
            "findings": ["prompt_injection"],
            "redacted": False,
        }
    )

    assert advisory.state == "completed"
    assert advisory.detail["risk"] == "high"


def test_fifteen_unique_tools_remain_fifteen_ordered_actions():
    normalizer = ActivityNormalizer()
    events = []
    for index in range(15):
        tool_id = f"tool-{index}"
        events.extend(
            [
                normalizer.normalize({"type": "tool.start", "tool_id": tool_id, "name": "shell"}),
                normalizer.normalize(
                    {"type": "tool.progress", "tool_id": tool_id, "message": "working"}
                ),
                normalizer.normalize(
                    {"type": "tool.complete", "tool_id": tool_id, "name": "shell"}
                ),
            ]
        )

    identities = [event.activity_id for event in events]
    assert list(dict.fromkeys(identities)) == [f"tool-{index}" for index in range(15)]
    assert all(identities.count(f"tool-{index}") == 3 for index in range(15))


def test_redaction_bounds_content_and_removes_nested_secrets_and_home_paths():
    detail = redact_detail(
        {
            "command": "curl -H '" + "Authorization: Bearer " + "test-placeholder' https://example.test",
            "cookie": "session=top-secret-value",
            "nested": {"api_key": "top-secret-value", "result": "x" * 10_000},
            "path": "/Users/example/.hermes/profiles/example-adapter/.env",
            "url": "https://example.test/file?X-Amz-Signature=top-secret-value",
            "other_path": "/etc/passwd",
        }
    )
    serialized = str(detail).lower()
    assert "top-secret-value" not in serialized
    assert ".hermes" not in serialized
    assert "/etc/passwd" not in serialized
    assert detail["redacted"] is True
    assert len(serialized) < 3000


def test_public_text_sanitizer_redacts_credentials_and_preserves_normal_prose():
    normal = "Compare the API design with the product requirements."
    secret = "Please use api_key=sk-live-super-secret-value for this request"

    assert sanitize_text(normal) == normal
    sanitized = sanitize_text(secret)
    assert sanitized == "Please use [redacted] for this request"
    assert "sk-live-super-secret-value" not in sanitized


def test_public_text_sanitizer_redacts_arbitrary_posix_paths_without_mangling_urls():
    for path in (
        "/tmp",
        "/private.db",
        "/tmp/jobos/private.db",
        "/var/lib/jobos/state.sqlite3",
        "/opt/jobos/config/runtime.json",
        "/srv/jobos/artifacts/resume.pdf",
        "/mnt/custom-volume/jobos/internal.log",
    ):
        sanitized = sanitize_text(f"Operation failed at {path}; retry later")
        assert path not in sanitized
        assert "[protected path]" in sanitized

    public = (
        "See https://example.com/docs/public/errors and compare the API design "
        "and/or retry guidance."
    )
    assert sanitize_text(public) == public


def test_public_text_sanitizer_redacts_bounded_unlabeled_credentials_conservatively():
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvYm9zIFVzZXIifQ."
        "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    opaque = "bQ7_vR2fG9mK4pL8sN1xC6zW3dH0jT5uY-aE2iO7qP9"

    assert sanitize_text(f"JWT failed: {jwt}") == "JWT failed: [redacted]"
    assert sanitize_text(f"Credential failed: {opaque}") == "Credential failed: [redacted]"

    ordinary_values = (
        "550e8400-e29b-41d4-a716-446655440000",
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "job_01ARZ3NDEKTSV4RRFFQ69G5FAV",
    )
    for value in ordinary_values:
        assert sanitize_text(f"Identifier {value}") == f"Identifier {value}"

    job_url = "https://example.com/jobs/job_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert sanitize_text(job_url) == job_url


def test_public_text_sanitizer_redacts_standalone_token_shapes_without_prefix_prose():
    raw_token = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"

    assert sanitize_text(f"Use {raw_token} only for this step.") == (
        "Use [redacted] only for this step."
    )
    assert sanitize_text("Discuss the sk- token prefix without a credential.") == (
        "Discuss the sk- token prefix without a credential."
    )
    assert len(sanitize_text("x" * 10_000)) <= 1001


def test_standalone_basic_credentials_are_redacted_without_an_authorization_header():
    raw = "Basic dXNlcjpwYXNz"

    assert sanitize_user_text(f"Use {raw} for the request") == ("Use [redacted] for the request")
    assert raw not in str(redact_detail({"message": f"transport rejected {raw}"}))
