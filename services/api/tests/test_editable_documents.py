import base64
from copy import deepcopy
from hashlib import sha256

import pytest
from jobos_api.document_operations import apply_operations
from jobos_api.editable_documents import (
    ApplyOperationsRequest,
    CreateEditableDocumentRequest,
    CreateExternalImportRequest,
    DocumentComment,
    DocumentImportReport,
    DocumentSettings,
    ReplaceFromDocxRequest,
    SaveEditableDocumentRequest,
    blank_content,
    validate_content,
)
from jobos_api.state_store import (
    EditableDocumentConflict,
    IdempotencyConflict,
    JobOsStateStore,
)
from pydantic import TypeAdapter, ValidationError


def _paragraphs(content):
    return [section["content"][0] for section in content["content"]]


@pytest.mark.parametrize("document_key", ["resume", "cover_letter", "references"])
def test_blank_templates_are_bounded_valid_and_have_unique_stable_ids(document_key):
    content = blank_content(document_key)

    validate_content(content, DocumentSettings(), [])
    ids = [
        node["attrs"]["jobosId"]
        for section in content["content"]
        for node in (section, *section["content"])
    ]

    assert len(ids) == len(set(ids))
    assert all(identifier.startswith("node_") for identifier in ids)


def test_python_validator_rejects_non_iso_and_timezone_less_timestamps():
    with pytest.raises(ValidationError):
        DocumentComment(
            comment_id="comment_ABCDEFGHIJKLMNOPQRSTUVWX",
            block_id="node_00000000-0000-4000-8000-000000000001",
            author="user",
            body="Check this",
            created_at="not-a-date",
        )
    with pytest.raises(ValidationError):
        DocumentImportReport(
            source_filename="resume.docx",
            imported_at="2026-08-07T12:00:00",
            issues=[],
        )
    for invalid_timestamp in (
        "2026-08-07T00:00:00+01:00",
        "2026-08-07 00:00:00+00:00",
        "2026-08-07T00:00:00.1234567Z",
    ):
        with pytest.raises(ValidationError):
            DocumentImportReport(
                source_filename="resume.docx",
                imported_at=invalid_timestamp,
                issues=[],
            )

    content = blank_content("resume")
    content["content"][0]["content"][0]["content"] = [
        {
            "type": "text",
            "text": "Suggested",
            "marks": [
                {
                    "type": "suggestion",
                    "attrs": {
                        "suggestionId": "suggestion_ABCDEFGHIJKLMNOPQRSTUVWX",
                        "kind": "insert",
                        "author": "Reviewer",
                        "createdAt": "yesterday",
                    },
                }
            ],
        }
    ]
    with pytest.raises(ValueError, match="invalid suggestion"):
        validate_content(content, DocumentSettings(), [])


def test_python_validator_matches_renderer_alignment_and_text_style_contract():
    content = blank_content("resume")
    paragraph = content["content"][1]["content"][0]
    paragraph["attrs"]["textAlign"] = "center"
    paragraph["content"] = [
        {
            "type": "text",
            "text": "Styled summary",
            "marks": [
                {
                    "type": "textStyle",
                    "attrs": {
                        "fontFamily": "Georgia",
                        "fontSize": "12pt",
                        "lineHeight": "1.5",
                        "color": "#112233",
                        "backgroundColor": "#fff59d",
                    },
                }
            ],
        }
    ]
    validate_content(content, DocumentSettings(), [])

    malformed = deepcopy(content)
    malformed["content"][1]["content"][0]["content"][0]["marks"][0]["attrs"][
        "fontSize"
    ] = "huge"
    with pytest.raises(ValueError, match="font size"):
        validate_content(malformed, DocumentSettings(), [])


def test_save_contract_rejects_unknown_fields_duplicate_ids_and_unsafe_links():
    content = blank_content("references")
    duplicate = deepcopy(content)
    duplicate["content"][1]["attrs"]["jobosId"] = duplicate["content"][0]["attrs"][
        "jobosId"
    ]
    with pytest.raises(ValueError, match="unique"):
        validate_content(duplicate, DocumentSettings(), [])

    unknown = deepcopy(content)
    unknown["content"][1]["unexpected"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        SaveEditableDocumentRequest(
            base_revision=1,
            content=unknown,
            settings=DocumentSettings(),
            comments=[],
            idempotency_key="save-1",
        )

    unsafe = deepcopy(content)
    unsafe["content"][1]["content"][0]["content"] = [
        {
            "type": "text",
            "text": "click",
            "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
        }
    ]
    with pytest.raises(ValueError, match="unsafe link"):
        validate_content(unsafe, DocumentSettings(), [])


def test_create_and_replace_import_contracts_are_discriminated_snake_case_and_bounded():
    content = blank_content("references")
    settings = DocumentSettings().model_dump(mode="json")
    report = {"source_filename": "references.docx", "imported_at": None, "issues": []}
    source = b"PK\x03\x04fixture"
    external = {
        "mode": "import_external_docx",
        "document_key": "references",
        "source_filename": "references.docx",
        "source_base64": base64.b64encode(source).decode("ascii"),
        "source_sha256": sha256(source).hexdigest(),
        "content": content,
        "settings": settings,
        "import_report": report,
        "idempotency_key": "external-contract",
    }

    parsed = TypeAdapter(CreateEditableDocumentRequest).validate_python(external)
    assert isinstance(parsed, CreateExternalImportRequest)
    assert ReplaceFromDocxRequest.model_validate(
        {
            "base_revision": 1,
            "source": external,
            "idempotency_key": "replace-contract",
        }
    ).source.mode == "import_external_docx"

    for invalid in (
        {**external, "sourceFilename": "camel-case.docx"},
        {**external, "source_filename": "..\\escape.docx"},
        {**external, "source_sha256": "0" * 64},
    ):
        with pytest.raises(ValidationError):
            TypeAdapter(CreateEditableDocumentRequest).validate_python(invalid)

    oversized = b"PK" + b"x" * 19_999_999
    with pytest.raises(ValidationError, match="20 MB"):
        CreateExternalImportRequest.model_validate(
            {
                **external,
                "source_base64": base64.b64encode(oversized).decode("ascii"),
                "source_sha256": sha256(oversized).hexdigest(),
            }
        )


def test_all_five_agent_operations_apply_as_one_valid_batch():
    content = blank_content("resume")
    summary, experience, education, skills = _paragraphs(content)[1:]
    experience_section = next(
        section for section in content["content"] if experience in section["content"]
    )
    education_section = next(
        section for section in content["content"] if education in section["content"]
    )
    skills_section = next(section for section in content["content"] if skills in section["content"])
    document = {
        "content": content,
        "settings": DocumentSettings().model_dump(mode="json"),
        "comments": [],
    }
    command = ApplyOperationsRequest.model_validate(
        {
            "base_revision": 1,
            "origin": "mcp",
            "idempotency_key": "ops-all-five",
            "operations": [
                {
                    "type": "replace_block_text",
                    "block_id": summary["attrs"]["jobosId"],
                    "expected_text": "",
                    "replacement_text": "Product builder",
                },
                {
                    "type": "insert_block_after",
                    "after_block_id": summary["attrs"]["jobosId"],
                    "node_type": "paragraph",
                    "semantic_role": "summary",
                    "text": "Agent-native systems",
                },
                {
                    "type": "delete_block",
                    "block_id": experience_section["attrs"]["jobosId"],
                    "expected_text": "",
                },
                {
                    "type": "move_block_after",
                    "block_id": education_section["attrs"]["jobosId"],
                    "after_block_id": skills_section["attrs"]["jobosId"],
                },
                {
                    "type": "set_block_role",
                    "block_id": skills["attrs"]["jobosId"],
                    "semantic_role": "custom",
                },
            ],
        }
    )

    updated, changed_ids, changes = apply_operations(document, command)

    validate_content(updated, DocumentSettings(), [])
    assert len(changed_ids) == 5
    assert len(changes) == 5
    assert "Product builder" in str(updated)
    assert experience_section["attrs"]["jobosId"] not in str(updated)


def test_operation_validation_is_atomic_and_locked_ancestors_fail_closed():
    content = blank_content("resume")
    before = deepcopy(content)
    contact_paragraph = content["content"][0]["content"][0]
    command = ApplyOperationsRequest.model_validate(
        {
            "base_revision": 1,
            "origin": "mcp",
            "idempotency_key": "ops-locked",
            "operations": [
                {
                    "type": "replace_block_text",
                    "block_id": contact_paragraph["attrs"]["jobosId"],
                    "expected_text": "",
                    "replacement_text": "should fail",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="locked"):
        apply_operations(
            {"content": content, "settings": DocumentSettings().model_dump(), "comments": []},
            command,
        )
    assert content == before


def test_state_store_crud_conflict_snapshot_restore_and_atomic_agent_snapshot(tmp_path):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    content = blank_content("references")
    settings = DocumentSettings().model_dump(mode="json")
    created = store.create_editable_document(
        job_id="job-1",
        document_key="references",
        document_label="References",
        content=content,
        settings=settings,
        comments=[],
        import_report={"source_filename": None, "imported_at": None, "issues": []},
    )

    assert created["revision"] == 1
    assert store.get_job_editable_document("job-1", "references") == created
    saved = store.save_editable_document(
        str(created["document_id"]),
        expected_revision=1,
        content=content,
        settings=settings,
        comments=[],
    )
    assert saved["revision"] == 2
    with pytest.raises(EditableDocumentConflict) as conflict:
        store.save_editable_document(
            str(created["document_id"]),
            expected_revision=1,
            content=content,
            settings=settings,
            comments=[],
        )
    assert conflict.value.current["revision"] == 2

    checkpoint = store.create_editable_snapshot(
        str(created["document_id"]),
        expected_revision=2,
        reason="manual",
        actor="user",
        label="Before edits",
    )
    mutated = deepcopy(content)
    mutated["content"][1]["content"][0]["content"] = [
        {"type": "text", "text": "Reference One"}
    ]
    agent_saved, snapshot_id = store.save_agent_document_operation(
        str(created["document_id"]), expected_revision=2, content=mutated
    )
    assert agent_saved["revision"] == 3
    assert snapshot_id.startswith("dsnap_")
    restored = store.restore_editable_snapshot(
        str(created["document_id"]),
        str(checkpoint["snapshot_id"]),
        expected_revision=3,
    )
    assert restored["revision"] == 4
    assert restored["content"] == content
    reasons = [
        item["reason"]
        for item in store.list_editable_snapshots(str(created["document_id"]))
    ]
    assert reasons == ["before_restore", "before_agent_edit", "manual"]


def test_atomic_editable_mutation_rolls_back_failed_settlement_and_replays(tmp_path, monkeypatch):
    store = JobOsStateStore(tmp_path / "jobos.db")
    store.initialize()
    content = blank_content("resume")
    settings = DocumentSettings().model_dump(mode="json")

    def mutation(connection):
        row = store.create_editable_document(
            job_id="job-atomic",
            document_key="resume",
            document_label="Resume",
            content=content,
            settings=settings,
            comments=[],
            import_report={"source_filename": None, "imported_at": None, "issues": []},
            connection=connection,
        )
        return {
            "document_id": row["document_id"],
            "job_id": row["job_id"],
            "revision": row["revision"],
        }

    original_settlement = store._settle_editable_mutation

    def fail_settlement(*_args, **_kwargs):
        raise RuntimeError("injected settlement failure")

    monkeypatch.setattr(store, "_settle_editable_mutation", fail_settlement)
    with pytest.raises(RuntimeError, match="injected settlement failure"):
        store.execute_editable_mutation(
            event_type="user_action",
            origin="user",
            actor_id="device-test",
            target_resource="jobs/job-atomic/editable-documents",
            command_name="document.editor.create",
            idempotency_key="atomic-create",
            request_hash="hash-one",
            detail={"label": "Create draft"},
            mutation=mutation,
            job_id="job-atomic",
        )
    assert store.get_job_editable_document("job-atomic", "resume") is None
    assert store.mutation_result(
        actor_id="device-test",
        target_resource="jobs/job-atomic/editable-documents",
        command_name="document.editor.create",
        idempotency_key="atomic-create",
        request_hash="hash-one",
    ) is None

    monkeypatch.setattr(store, "_settle_editable_mutation", original_settlement)
    result, replayed = store.execute_editable_mutation(
        event_type="user_action",
        origin="user",
        actor_id="device-test",
        target_resource="jobs/job-atomic/editable-documents",
        command_name="document.editor.create",
        idempotency_key="atomic-create",
        request_hash="hash-one",
        detail={"label": "Create draft"},
        mutation=mutation,
        job_id="job-atomic",
    )
    assert replayed is False
    assert result["revision"] == 1

    replay, replayed = store.execute_editable_mutation(
        event_type="user_action",
        origin="user",
        actor_id="device-test",
        target_resource="jobs/job-atomic/editable-documents",
        command_name="document.editor.create",
        idempotency_key="atomic-create",
        request_hash="hash-one",
        detail={"label": "Create draft"},
        mutation=lambda _connection: pytest.fail("replay must not execute mutation"),
        job_id="job-atomic",
    )
    assert replayed is True
    assert replay == result

    with pytest.raises(IdempotencyConflict):
        store.execute_editable_mutation(
            event_type="user_action",
            origin="user",
            actor_id="device-test",
            target_resource="jobs/job-atomic/editable-documents",
            command_name="document.editor.create",
            idempotency_key="atomic-create",
            request_hash="hash-two",
            detail={"label": "Different request"},
            mutation=lambda _connection: pytest.fail("conflict must not execute mutation"),
            job_id="job-atomic",
        )
