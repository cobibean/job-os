from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from threading import Barrier

import pytest
from jobos_api.career_profile import (
    WORK_ARRANGEMENT_NAMESPACE,
    CareerProfileIdempotencyConflict,
    CareerProfileRevisionConflict,
    CareerProfileSnapshotForbidden,
    CareerProfileSnapshotIntegrityError,
    CareerProfileSnapshotRequest,
    CareerProfileStore,
    WorkArrangementMutation,
    WorkArrangementRestore,
)
from jobos_api.state_store import JobOsStateStore


def initialized_store(path: Path) -> CareerProfileStore:
    JobOsStateStore(path).initialize(owner_device_id="device-primary")
    store = CareerProfileStore(path)
    store.initialize()
    return store


def mutation(
    *,
    revision: int,
    key: str,
    mode: str = "remote",
    strength: str = "strong_preference",
    note: str | None = "(FAKE) Prefer remote-first teams",
) -> WorkArrangementMutation:
    return WorkArrangementMutation.model_validate(
        {
            "expected_profile_revision": revision,
            "idempotency_key": key,
            "value": {"mode": mode, "strength": strength, "note": note},
        }
    )


def canonical_hash(snapshot) -> str:
    payload = {
        "profile_revision": snapshot.profile_revision,
        "scopes": snapshot.scopes,
        "projection": snapshot.projection.model_dump(mode="json"),
    }
    return sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def test_fresh_store_initializes_and_persists_typed_work_arrangement(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    store = initialized_store(database)

    assert store.current_work_arrangement().model_dump(mode="json") == {
        "profile_revision": 0,
        "record": None,
    }

    saved = store.set_work_arrangement(
        principal="device:device-primary",
        command=mutation(revision=0, key="create-work-arrangement"),
    )
    restarted = CareerProfileStore(database).current_work_arrangement()

    assert saved == restarted
    assert restarted.profile_revision == 1
    assert restarted.record is not None
    assert restarted.record.namespace == WORK_ARRANGEMENT_NAMESPACE
    assert restarted.record.value.mode == "remote"
    assert restarted.record.item_revision == 1
    assert restarted.record.actor_principal == "device:device-primary"


@pytest.mark.parametrize("mode", ["remote", "hybrid", "onsite", "flexible"])
@pytest.mark.parametrize(
    "strength", ["requirement", "strong_preference", "preference", "dealbreaker"]
)
def test_every_work_arrangement_mode_strength_combination_round_trips(
    tmp_path: Path, mode: str, strength: str
) -> None:
    store = initialized_store(tmp_path / f"{mode}-{strength}.db")
    note = "x" * 1000
    saved = store.set_work_arrangement(
        principal="device:primary",
        command=mutation(
            revision=0,
            key=f"round-trip-{mode}-{strength}",
            mode=mode,
            strength=strength,
            note=note,
        ),
    )
    assert saved.record is not None
    assert saved.record.value.model_dump(mode="json") == {
        "mode": mode,
        "strength": strength,
        "note": note,
    }
    restarted = CareerProfileStore(tmp_path / f"{mode}-{strength}.db")
    assert restarted.current_work_arrangement() == saved


def test_mutation_is_one_immutable_revision_and_replay_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    store = initialized_store(database)
    command = mutation(revision=0, key="one-command-replay")

    first = store.set_work_arrangement(principal="device:primary", command=command)
    replay = store.set_work_arrangement(principal="device:primary", command=command)
    history = store.work_arrangement_history()

    assert replay == first
    assert history.profile_revision == 1
    assert len(history.revisions) == 1
    revision = history.revisions[0]
    assert revision.actor_principal == "device:primary"
    assert revision.base_profile_revision == 0
    assert revision.item_revision == 1
    assert revision.operation == "set"
    assert revision.changed_fields == ["mode", "strength", "note"]

    with pytest.raises(CareerProfileIdempotencyConflict):
        store.set_work_arrangement(
            principal="device:primary",
            command=mutation(
                revision=1,
                key="one-command-replay",
                mode="hybrid",
            ),
        )

    assert len(store.work_arrangement_history().revisions) == 1


def test_stale_expected_revision_fails_closed_without_partial_write(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    store = initialized_store(database)
    original = store.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=0, key="initial-revision"),
    )

    with pytest.raises(CareerProfileRevisionConflict) as error:
        store.set_work_arrangement(
            principal="device:secondary",
            command=mutation(revision=0, key="stale-revision", mode="onsite"),
        )

    assert error.value.current_revision == 1
    assert store.current_work_arrangement() == original
    assert len(store.work_arrangement_history().revisions) == 1
    with sqlite3.connect(database) as connection:
        idempotency_count = connection.execute(
            "SELECT COUNT(*) FROM career_profile_idempotency"
        ).fetchone()
        assert idempotency_count == (1,)


def test_current_read_never_combines_head_and_record_from_different_revisions(
    tmp_path: Path,
) -> None:
    database = tmp_path / "jobos.db"
    reader = initialized_store(database)
    writer = CareerProfileStore(database)
    reader.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=0, key="atomic-read-original", mode="remote"),
    )
    def write_revisions() -> None:
        for revision in range(1, 41):
            writer.set_work_arrangement(
                principal="device:primary",
                command=mutation(
                    revision=revision,
                    key=f"atomic-read-{revision + 1}",
                    mode="onsite" if revision % 2 else "remote",
                ),
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        writing = executor.submit(write_revisions)
        while not writing.done():
            observed = reader.current_work_arrangement()
            assert observed.record is not None
            assert observed.record.profile_revision == observed.profile_revision
            assert observed.record.item_revision == observed.profile_revision
        writing.result()

    observed = reader.current_work_arrangement()
    assert observed.profile_revision == 41
    assert observed.record is not None
    assert observed.record.item_revision == 41


def test_concurrent_writers_from_same_revision_allow_exactly_one_commit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "jobos.db"
    initialized_store(database)
    barrier = Barrier(2)

    def write(key: str, mode: str) -> str:
        barrier.wait()
        try:
            CareerProfileStore(database).set_work_arrangement(
                principal=f"device:{key}",
                command=mutation(revision=0, key=key, mode=mode),
            )
        except CareerProfileRevisionConflict:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda item: write(*item),
                (("writer-one", "remote"), ("writer-two", "hybrid")),
            )
        )

    assert sorted(results) == ["committed", "conflict"]
    current = CareerProfileStore(database).current_work_arrangement()
    assert current.profile_revision == 1
    assert len(CareerProfileStore(database).work_arrangement_history().revisions) == 1


def test_restore_is_a_compensating_revision_that_preserves_history(tmp_path: Path) -> None:
    store = initialized_store(tmp_path / "jobos.db")
    first = store.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=0, key="revision-one", mode="remote"),
    )
    assert first.record is not None
    second = store.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=1, key="revision-two", mode="hybrid"),
    )
    assert second.record is not None

    restored = store.restore_work_arrangement(
        principal="device:primary",
        command=WorkArrangementRestore(
            expected_profile_revision=2,
            idempotency_key="restore-revision-one",
            target_profile_revision=1,
        ),
    )
    history = store.work_arrangement_history()

    assert restored.profile_revision == 3
    assert restored.record is not None
    assert restored.record.value == first.record.value
    assert restored.record.item_revision == 3
    assert [item.profile_revision for item in history.revisions] == [3, 2, 1]
    assert history.revisions[0].operation == "restore"
    assert history.revisions[0].restored_from_profile_revision == 1
    assert history.revisions[0].base_profile_revision == 2
    assert history.revisions[1].value.mode == "hybrid"


def test_snapshot_is_bounded_immutable_hashed_and_principal_bound(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    store = initialized_store(database)
    store.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=0, key="snapshot-source", mode="remote"),
    )

    snapshot = store.create_snapshot(
        principal="device:primary",
        request=CareerProfileSnapshotRequest(),
    )
    store.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=1, key="after-snapshot", mode="onsite"),
    )
    resolved = CareerProfileStore(database).get_snapshot(
        snapshot.snapshot_id,
        principal="device:primary",
    )

    assert snapshot.snapshot_id.startswith("cps_")
    assert resolved == snapshot
    assert resolved.profile_revision == 1
    assert resolved.scopes == [WORK_ARRANGEMENT_NAMESPACE]
    assert resolved.projection.work_arrangement is not None
    assert resolved.projection.work_arrangement.value.mode == "remote"
    assert resolved.content_hash == canonical_hash(resolved)
    current = store.current_work_arrangement()
    assert current.record is not None
    assert current.record.value.mode == "onsite"

    with pytest.raises(CareerProfileSnapshotForbidden):
        store.get_snapshot(snapshot.snapshot_id, principal="device:secondary")

    with sqlite3.connect(database) as connection:
        projection = json.loads(
            connection.execute(
                "SELECT projection_json FROM career_profile_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            ).fetchone()[0]
        )
        projection["work_arrangement"]["value"]["mode"] = "onsite"
        connection.execute(
            "UPDATE career_profile_snapshots SET projection_json = ? WHERE snapshot_id = ?",
            (json.dumps(projection), snapshot.snapshot_id),
        )

    with pytest.raises(CareerProfileSnapshotIntegrityError):
        store.get_snapshot(snapshot.snapshot_id, principal="device:primary")


def test_audit_events_record_transitions_without_copying_profile_values(tmp_path: Path) -> None:
    database = tmp_path / "jobos.db"
    store = initialized_store(database)
    sensitive_sentinel = "(FAKE) private preference details"
    store.set_work_arrangement(
        principal="device:primary",
        command=mutation(revision=0, key="redacted-audit", note=sensitive_sentinel),
    )
    store.create_snapshot(principal="device:primary", request=CareerProfileSnapshotRequest())

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT action, profile_revision, base_profile_revision,
                   affected_fields_json, revision_id
            FROM career_profile_audit_events ORDER BY audit_id
            """
        ).fetchall()

    assert [row[0] for row in rows] == ["work_arrangement.set", "snapshot.create"]
    assert rows[0][1:3] == (1, 0)
    assert json.loads(rows[0][3]) == ["mode", "strength", "note"]
    assert rows[0][4].startswith("cpv_")
    assert sensitive_sentinel not in json.dumps(rows)
