"""Agent-facing shapes for Career Profile reads and edit results.

The API returns complete item records. An agent mostly needs each item's identity and
value, so these views leave out per-item revision bookkeeping, let an agent read part of a
large profile, and keep the default read small enough for one tool response. The API
contract itself is unchanged.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Literal

ProfileView = Literal["auto", "index", "compact", "full"]

MAX_LISTED_ITEMS = 100
# "auto" returns compact records up to this many characters of JSON, then falls back to
# the index so the default read never overflows an agent's tool-result budget.
AUTO_COMPACT_BUDGET = 60_000
INDEX_TITLE_LENGTH = 120
LARGE_SELECTION_NOTE = (
    "This selection is too large for one compact response. Choose from the index, then "
    "read with kinds or item_ids."
)
_TITLE_FIELDS = (
    "label",
    "name",
    "headline",
    "organization",
    "institution",
    "professional_name",
    "statement",
)


def _records(container: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(container, dict):
        return []
    return [record for record in container.get(key) or [] if isinstance(record, dict)]


def _item_kind(item: dict[str, Any]) -> str | None:
    value = item.get("value")
    return value.get("kind") if isinstance(value, dict) else None


def compact_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep an item's identity, value, and Evidence links."""
    compact: dict[str, Any] = {
        "item_id": item.get("item_id"),
        "value": item.get("value"),
        "evidence_ids": list(item.get("evidence_ids") or []),
    }
    review_status = item.get("review_status")
    if review_status not in (None, "accepted"):
        compact["review_status"] = review_status
    return compact


def index_item(item: dict[str, Any]) -> dict[str, Any]:
    """Name an item without its content, so an agent can choose what to read."""
    value = item.get("value") if isinstance(item.get("value"), dict) else {}
    title = next(
        (
            " ".join(str(value[field]).split())
            for field in _TITLE_FIELDS
            if isinstance(value.get(field), str) and value[field].strip()
        ),
        "",
    )
    if len(title) > INDEX_TITLE_LENGTH:
        title = title[: INDEX_TITLE_LENGTH - 1].rstrip() + "…"
    return {"item_id": item.get("item_id"), "kind": value.get("kind"), "title": title}


def compact_evidence(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: source[key]
        for key in ("evidence_id", "original_filename", "media_type")
        if key in source
    }


def _select(
    items: list[dict[str, Any]], kinds: list[str] | None, item_ids: list[str] | None
) -> list[dict[str, Any]]:
    if kinds:
        wanted_kinds = set(kinds)
        items = [item for item in items if _item_kind(item) in wanted_kinds]
    if item_ids:
        wanted_ids = set(item_ids)
        items = [item for item in items if item.get("item_id") in wanted_ids]
    return items


def _resolve_view(
    view: ProfileView, items: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> tuple[Literal["index", "compact", "full"], str | None]:
    if view != "auto":
        return view, None
    compact = {
        "items": [compact_item(item) for item in items],
        "source_evidence": [compact_evidence(source) for source in evidence],
    }
    if len(json.dumps(compact, ensure_ascii=False)) <= AUTO_COMPACT_BUDGET:
        return "compact", None
    return "index", LARGE_SELECTION_NOTE


def _shown(
    items: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    view: Literal["index", "compact", "full"],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if view == "full":
        return items, evidence
    shape = index_item if view == "index" else compact_item
    return [shape(item) for item in items], [compact_evidence(source) for source in evidence]


def shape_projection(
    preview: dict[str, Any],
    *,
    view: ProfileView,
    kinds: list[str] | None = None,
    item_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Shape the authorized projection for one agent read."""
    filtered = bool(kinds or item_ids)
    if view == "full" and not filtered:
        return preview

    projection = preview.get("projection") if isinstance(preview.get("projection"), dict) else {}
    all_items = _records(projection, "items")
    items = _select(all_items, kinds, item_ids)
    # Evidence is not an item, so a filtered read asks for items only.
    evidence = [] if filtered else _records(projection, "source_evidence")

    if view == "full":
        return {
            **preview,
            "projection": {**projection, "items": items, "source_evidence": evidence},
        }

    resolved, note = _resolve_view(view, items, evidence)
    shown_items, shown_evidence = _shown(items, evidence, resolved)
    scope = preview.get("scope") if isinstance(preview.get("scope"), dict) else {}
    shaped: dict[str, Any] = {
        "view": resolved,
        "profile_revision": preview.get("profile_revision", projection.get("profile_revision")),
        "scope": {"mode": scope.get("mode")},
        "projection": {
            "profile_revision": projection.get("profile_revision"),
            "authority_state": projection.get("authority_state"),
            "items": shown_items,
            "source_evidence": shown_evidence,
        },
        "total_items": len(all_items),
        "returned_items": len(items),
    }
    if resolved == "index":
        shaped["kind_counts"] = dict(
            sorted(Counter(str(_item_kind(item)) for item in all_items).items())
        )
    if note is not None:
        shaped["note"] = note
    return shaped


def shape_search(result: dict[str, Any], *, view: ProfileView) -> dict[str, Any]:
    """Shape an API search result without changing which records matched."""
    if view == "full":
        return result
    items = _records(result, "items")
    evidence = _records(result, "source_evidence")
    resolved, note = _resolve_view(view, items, evidence)
    shown_items, shown_evidence = _shown(items, evidence, resolved)
    shaped = {**result, "view": resolved, "items": shown_items, "source_evidence": shown_evidence}
    if note is not None:
        shaped["note"] = note
    return shaped


def list_items(
    preview: dict[str, Any],
    *,
    view: ProfileView,
    kinds: list[str] | None,
    areas: list[str] | None,
    review_statuses: list[str] | None,
    has_evidence: bool | None,
    limit: int,
) -> dict[str, Any]:
    """List authorized items without a keyword, using the same filters as search."""
    projection = preview.get("projection") if isinstance(preview.get("projection"), dict) else {}
    area_filter = set(areas or [])
    status_filter = set(review_statuses or [])

    matched = []
    for item in _select(_records(projection, "items"), kinds, None):
        if area_filter and item.get("area") not in area_filter:
            continue
        if status_filter and item.get("review_status") not in status_filter:
            continue
        if has_evidence is not None and bool(item.get("evidence_ids")) != has_evidence:
            continue
        matched.append(item)

    bounded = matched[: max(1, min(limit, MAX_LISTED_ITEMS))]
    evidence: list[dict[str, Any]] = []
    if not kinds and (not area_filter or "my_evidence" in area_filter):
        evidence = _records(projection, "source_evidence")

    resolved, note = _resolve_view(view, bounded, evidence)
    shown_items, shown_evidence = _shown(bounded, evidence, resolved)
    listed: dict[str, Any] = {
        "view": resolved,
        "profile_revision": preview.get("profile_revision", projection.get("profile_revision")),
        "items": shown_items,
        "source_evidence": shown_evidence,
        "total_items": len(matched),
        "total_evidence": len(evidence),
    }
    if note is not None:
        listed["note"] = note
    return listed


def existing_evidence_ids(preview: dict[str, Any], target_id: str | None) -> list[str] | None:
    """Return an authorized item's current Evidence links, or None when it is not visible."""
    if target_id is None:
        return None
    for item in _records(preview.get("projection"), "items"):
        if item.get("item_id") == target_id:
            return list(item.get("evidence_ids") or [])
    return None


def _created_item_id(profile: dict[str, Any], value: dict[str, Any] | None) -> str | None:
    """Find the item a direct create just saved by matching the submitted value."""
    if not value:
        return None
    submitted = {key: field for key, field in value.items() if field is not None}
    matches = [
        item
        for item in _records(profile, "items")
        if isinstance(item.get("value"), dict)
        and all(item["value"].get(key) == field for key, field in submitted.items())
    ]
    if not matches:
        return None
    newest = max(matches, key=lambda item: str(item.get("created_at") or ""))
    return newest.get("item_id")


def shape_edit_result(
    result: dict[str, Any],
    *,
    operation: str,
    target_id: str | None,
    value: dict[str, Any] | None,
) -> dict[str, Any]:
    """Report one edit's outcome without echoing the whole profile back."""
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    proposal = result.get("proposal") if isinstance(result.get("proposal"), dict) else None
    shaped: dict[str, Any] = {
        "outcome": result.get("outcome"),
        "operation": operation,
        "profile_revision": profile.get("profile_revision"),
    }
    if proposal is not None:
        shaped["target_id"] = proposal.get("target_id", target_id)
        shaped["proposal"] = {
            key: proposal[key]
            for key in ("proposal_id", "review_reason", "status")
            if key in proposal
        }
    elif operation == "item.create":
        shaped["target_id"] = _created_item_id(profile, value)
    else:
        shaped["target_id"] = target_id
    return shaped
