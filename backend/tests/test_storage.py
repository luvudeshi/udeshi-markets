"""Experiment storage. Runs against SQLite always, and against PostgreSQL as well when
``UDESHI_TEST_DATABASE_URL`` names one (CI provides a service container)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from udeshi_api.storage import ExperimentStore, RunRecord, metadata

URLS = ["sqlite+pysqlite:///:memory:"]
if os.environ.get("UDESHI_TEST_DATABASE_URL"):
    URLS.append(os.environ["UDESHI_TEST_DATABASE_URL"])


@pytest.fixture(params=URLS, ids=lambda url: url.split(":", 1)[0])
def store(request: pytest.FixtureRequest) -> Iterator[ExperimentStore]:
    store = ExperimentStore(request.param)
    with store.engine.begin() as connection:  # A clean slate on a shared server.
        for table in reversed(metadata.sorted_tables):
            connection.execute(table.delete())
    yield store
    store.close()


def run(point: int, seed: int, **metrics: float | None) -> RunRecord:
    return RunRecord(
        point=point,
        label=f"exp [population.market_makers={point}]",
        seed=seed,
        parameters=(("population.market_makers", point),),
        metrics=dict(metrics),
        digest=f"{seed:032x}",
        wall_seconds=0.25,
    )


def create(store: ExperimentStore, experiment_id: str = "e1", runs_total: int = 4) -> None:
    store.create(
        experiment_id,
        name="exp",
        label="exp",
        request={"name": "exp", "runs": 2},
        sweep={"population.market_makers": [0, 1]},
        points=2,
        runs_total=runs_total,
    )


def test_an_experiment_round_trips(store: ExperimentStore) -> None:
    create(store)
    record = store.get("e1")
    assert record is not None
    assert (record.status, record.runs_done, record.runs_total, record.points) == (
        "queued",
        0,
        4,
        2,
    )
    assert record.request == {"name": "exp", "runs": 2}
    assert record.sweep == {"population.market_makers": [0, 1]}
    assert record.created_at.tzinfo is not None
    assert (record.started_at, record.finished_at, record.error) == (None, None, None)
    assert [r.id for r in store.all()] == ["e1"]
    assert store.get("missing") is None


def test_status_changes_stamp_their_times(store: ExperimentStore) -> None:
    create(store)
    store.set_status("e1", "running")
    running = store.get("e1")
    assert running is not None
    assert running.started_at is not None
    assert running.finished_at is None
    store.set_status("e1", "failed", error="ValueError: boom")
    failed = store.get("e1")
    assert failed is not None
    assert (failed.status, failed.error) == ("failed", "ValueError: boom")
    assert failed.finished_at is not None
    assert failed.finished_at >= failed.started_at  # type: ignore[operator]
    with pytest.raises(ValueError, match="unknown status"):
        store.set_status("e1", "exploded")


def test_runs_are_stored_in_point_then_seed_order_with_progress(store: ExperimentStore) -> None:
    create(store)
    for point, seed in ((1, 2), (0, 2), (1, 1), (0, 1)):  # The order they finish in.
        store.add_run("e1", run(point, seed, spread_bps=1.5 * seed, sharpe=None))
    stored = store.runs("e1")
    assert [(r.point, r.seed) for r in stored] == [(0, 1), (0, 2), (1, 1), (1, 2)]
    assert stored[0].metrics == {"spread_bps": 1.5, "sharpe": None}  # None survives as null.
    assert stored[2].parameters == (("population.market_makers", 1),)
    assert [r.seed for r in store.runs("e1", point=1)] == [1, 2]
    record = store.get("e1")
    assert record is not None
    assert record.runs_done == 4


def test_large_seeds_fit(store: ExperimentStore) -> None:
    create(store)
    seed = (1 << 62) + 499
    store.add_run("e1", run(0, seed, x=1.0))
    assert store.runs("e1")[0].seed == seed


def test_deleting_an_experiment_deletes_its_runs(store: ExperimentStore) -> None:
    create(store, "e1")
    create(store, "e2")
    store.add_run("e1", run(0, 1, x=1.0))
    store.add_run("e2", run(0, 1, x=2.0))
    assert store.delete("e1")
    assert store.get("e1") is None
    assert store.runs("e1") == []
    assert [r.metrics for r in store.runs("e2")] == [{"x": 2.0}]
    assert not store.delete("e1")


def test_unfinished_experiments_are_failed_after_a_restart(store: ExperimentStore) -> None:
    create(store, "queued")
    create(store, "running")
    create(store, "done")
    store.set_status("running", "running")
    store.set_status("done", "completed")
    assert store.mark_interrupted() == 2
    statuses = {r.id: (r.status, r.error) for r in store.all()}
    assert statuses["done"] == ("completed", None)
    assert statuses["queued"][0] == statuses["running"][0] == "failed"
    assert "stopped before" in (statuses["running"][1] or "")


def test_a_file_database_survives_the_process(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'results.db'}"
    first = ExperimentStore(url)
    create(first)
    first.add_run("e1", run(0, 1, x=3.0))
    first.close()
    second = ExperimentStore(url)
    assert [r.metrics for r in second.runs("e1")] == [{"x": 3.0}]
    second.close()
