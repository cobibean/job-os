from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .editable_documents import (
    ApplyOperationsRequest,
    DeleteBlock,
    DocumentComment,
    DocumentSettings,
    InsertBlockAfter,
    MoveBlockAfter,
    ReplaceBlockText,
    SetBlockRole,
    plain_text,
    validate_content,
)


def _index(
    root: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], int]]:
    result: dict[str, tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], int]] = {}

    def walk(node: dict[str, Any]) -> None:
        children = node.get("content", [])
        if not isinstance(children, list):
            return
        for position, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            block_id = (child.get("attrs") or {}).get("jobosId")
            if isinstance(block_id, str):
                result[block_id] = (child, node, children, position)
            walk(child)

    walk(root)
    return result


def _locked(node: dict[str, Any]) -> bool:
    if bool((node.get("attrs") or {}).get("locked")):
        return True

    def walk(value: dict[str, Any]) -> bool:
        for mark in value.get("marks", []):
            if mark.get("type") == "jobosField" and bool((mark.get("attrs") or {}).get("locked")):
                return True
        return any(walk(child) for child in value.get("content", []) if isinstance(child, dict))

    return walk(node)


def _target_locked(root: dict[str, Any], target_id: str) -> bool:
    def walk(node: dict[str, Any], inherited: bool) -> bool | None:
        current_locked = inherited or bool((node.get("attrs") or {}).get("locked"))
        if (node.get("attrs") or {}).get("jobosId") == target_id:
            return current_locked or _locked(node)
        for child in node.get("content", []):
            if isinstance(child, dict):
                result = walk(child, current_locked)
                if result is not None:
                    return result
        return None

    return bool(walk(root, False))


def _plain_content(text: str, marks: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    return (
        []
        if not text
        else [{"type": "text", "text": text, **({"marks": deepcopy(marks)} if marks else {})}]
    )


def apply_operations(
    document: dict[str, object], command: ApplyOperationsRequest
) -> tuple[dict[str, Any], list[str], list[dict[str, str]]]:
    content = deepcopy(document["content"])
    if not isinstance(content, dict):
        raise ValueError("editable document content is invalid")
    changed: list[str] = []
    changes: list[dict[str, str]] = []

    def suggestion(kind: str, **extra: str) -> dict[str, str]:
        return {
            "suggestionId": f"sug_{uuid4()}",
            "kind": kind,
            "author": "jobhunter",
            "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            **extra,
        }

    def suggested_text(
        text: str,
        kind: str,
        suggestion_id: str,
        marks: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not text:
            return []
        retained = [
            deepcopy(mark) for mark in (marks or []) if mark.get("type") != "suggestion"
        ]
        retained.append(
            {
                "type": "suggestion",
                "attrs": {
                    "suggestionId": suggestion_id,
                    "kind": kind,
                    "author": "jobhunter",
                    "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                },
            }
        )
        return _plain_content(text, retained)

    for operation in command.operations:
        index = _index(content)
        kind = operation.type
        target_id = getattr(operation, "block_id", None) or getattr(
            operation, "after_block_id", None
        )
        target = index.get(target_id)
        if target is None:
            raise ValueError(f"operation target does not exist: {target_id}")
        node, parent_node, parent, position = target
        if _target_locked(content, str(target_id)):
            raise ValueError(f"operation target is locked: {target_id}")

        if kind == "replace_block_text":
            assert isinstance(operation, ReplaceBlockText)
            if node.get("type") not in {"paragraph", "heading"}:
                raise ValueError("replace_block_text targets only paragraphs or headings")
            before = plain_text(node)
            if before != operation.expected_text:
                raise ValueError(f"expected text does not match block {operation.block_id}")
            first_text = next(
                (child for child in node.get("content", []) if child.get("type") == "text"), None
            )
            marks = first_text.get("marks") if first_text else None
            suggestion_id = suggestion("insert")["suggestionId"]
            node["content"] = [
                *suggested_text(before, "delete", suggestion_id, marks),
                *suggested_text(operation.replacement_text, "insert", suggestion_id, marks),
            ]
            changed.append(operation.block_id)
            changes.append(
                {
                    "block_id": operation.block_id,
                    "before": before[:500],
                    "after": operation.replacement_text[:500],
                }
            )
        elif kind == "insert_block_after":
            assert isinstance(operation, InsertBlockAfter)
            new_id = f"node_{uuid4()}"
            inserted_attrs = {
                "jobosId": new_id,
                "semanticRole": operation.semantic_role,
                "locked": False,
                "origin": "jobhunter",
                "structuralSuggestion": suggestion("insert"),
            }
            if operation.node_type == "listItem":
                if parent_node.get("type") not in {"bulletList", "orderedList"}:
                    raise ValueError("list items may be inserted only after another list item")
                inserted = {
                    "type": "listItem",
                    "attrs": inserted_attrs,
                    "content": [
                        {
                            "type": "paragraph",
                            "attrs": {
                                **inserted_attrs,
                                "jobosId": f"node_{uuid4()}",
                            },
                            "content": _plain_content(operation.text),
                        }
                    ],
                }
            else:
                if parent_node.get("type") not in {
                    "jobosSection",
                    "blockquote",
                    "listItem",
                    "tableCell",
                    "tableHeader",
                }:
                    raise ValueError("paragraph cannot be inserted in this container")
                inserted = {
                    "type": "paragraph",
                    "attrs": inserted_attrs,
                    "content": _plain_content(operation.text),
                }
            parent.insert(position + 1, inserted)
            changed.append(new_id)
            changes.append({"block_id": new_id, "before": "", "after": operation.text[:500]})
        elif kind == "delete_block":
            assert isinstance(operation, DeleteBlock)
            before = plain_text(node)
            if before != operation.expected_text:
                raise ValueError(f"expected text does not match block {operation.block_id}")
            if (
                parent_node.get("type")
                in {
                    "doc",
                    "jobosSection",
                    "blockquote",
                    "bulletList",
                    "orderedList",
                    "table",
                    "tableCell",
                    "tableHeader",
                }
                and len(parent) <= 1
            ):
                raise ValueError("delete would empty a required container")
            if parent_node.get("type") == "listItem" and position == 0:
                raise ValueError("a list item's leading paragraph cannot be deleted")
            node["attrs"]["structuralSuggestion"] = suggestion("delete")
            changed.append(operation.block_id)
            changes.append({"block_id": operation.block_id, "before": before[:500], "after": ""})
        elif kind == "move_block_after":
            assert isinstance(operation, MoveBlockAfter)
            if operation.block_id == operation.after_block_id:
                raise ValueError("a block cannot move after itself")
            destination = index.get(operation.after_block_id)
            source = index.get(operation.block_id)
            if destination is None or source is None:
                raise ValueError("move target does not exist")
            source_node, source_parent_node, source_parent, source_position = source
            _, destination_parent_node, destination_parent, _ = destination
            if (
                source_parent_node is not destination_parent_node
                or source_parent is not destination_parent
            ):
                raise ValueError("blocks may move only within the same container")
            if _target_locked(content, operation.block_id) or _target_locked(
                content, operation.after_block_id
            ):
                raise ValueError("move target is locked")
            source_node["attrs"]["structuralSuggestion"] = suggestion(
                "move", afterBlockId=operation.after_block_id
            )
            changed.append(operation.block_id)
            text = plain_text(source_node)[:500]
            changes.append({"block_id": operation.block_id, "before": text, "after": text})
        else:
            assert isinstance(operation, SetBlockRole)
            before = str((node.get("attrs") or {}).get("semanticRole") or "")
            node["attrs"]["structuralSuggestion"] = suggestion(
                "set_role", semanticRole=operation.semantic_role
            )
            changed.append(operation.block_id)
            changes.append(
                {"block_id": operation.block_id, "before": before, "after": operation.semantic_role}
            )

    validate_content(
        content,
        DocumentSettings.model_validate(document["settings"]),
        [DocumentComment.model_validate(comment) for comment in document["comments"]],  # type: ignore[union-attr]
    )
    return content, list(dict.fromkeys(changed)), changes


def semantic_outline(document: dict[str, object]) -> list[dict[str, object]]:
    content = document["content"]
    result: list[dict[str, object]] = []

    def walk(node: dict[str, Any], section_id: str | None) -> None:
        attrs = node.get("attrs") or {}
        block_id = attrs.get("jobosId")
        next_section = block_id if node.get("type") == "jobosSection" else section_id
        if isinstance(block_id, str):
            result.append(
                {
                    "block_id": block_id,
                    "parent_section_id": section_id,
                    "node_type": node["type"],
                    "semantic_role": attrs.get("semanticRole"),
                    "locked": bool(attrs.get("locked")) or _locked(node),
                    "text": plain_text(node)[:2000],
                }
            )
        for child in node.get("content", []):
            if isinstance(child, dict):
                walk(child, next_section)

    if isinstance(content, dict):
        walk(content, None)
    return result


def unresolved_suggestion_count(content: dict[str, Any]) -> int:
    ids: set[str] = set()

    def walk(node: dict[str, Any]) -> None:
        structural = (node.get("attrs") or {}).get("structuralSuggestion")
        if isinstance(structural, dict) and isinstance(structural.get("suggestionId"), str):
            ids.add(structural["suggestionId"])
        for mark in node.get("marks", []):
            suggestion_id = (
                (mark.get("attrs") or {}).get("suggestionId")
                if mark.get("type") == "suggestion"
                else None
            )
            if isinstance(suggestion_id, str):
                ids.add(suggestion_id)
        for child in node.get("content", []):
            if isinstance(child, dict):
                walk(child)

    walk(content)
    return len(ids)
