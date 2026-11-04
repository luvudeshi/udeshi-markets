"""Experiments through the API: real worker processes, real storage, real simulations."""

from __future__ import annotations

import csv
import io
import os
import time
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from udeshi_api.experiment_schemas import ExperimentIn
from udeshi_api.experiments import ExperimentService, build_specs
from udeshi_api.main import create_app
from udeshi_api.settings import Settings
from udeshi_api.storage import ExperimentStore
from udeshi_markets.experiments import run_experiment, write_runs_csv

SMALL: dict[str, Any] = {
    "name": "small",
    "duration_seconds": 20,
    "runs": 2,
    "agents": {
        "random_traders": 4,
        "market_makers": 2,
        "momentum_traders": 1,
        "fundamental_traders": 2,
        "pairs_traders": 1,
    },
}


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    """One app (and so one worker pool) for the module: spawning workers takes a moment. On
    PostgreSQL when ``UDESHI_TEST_DATABASE_URL`` names one, else in-memory SQLite."""
    settings = Settings(
        tick_seconds=0.01,
        experiment_workers=2,
        database_url=os.environ.get("UDESHI_TEST_DATABASE_URL"),
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def submit(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post("/api/experiments", json=body)
    assert response.status_code == 202, response.text
    payload: dict[str, Any] = response.json()
    return payload


def wait(client: TestClient, experiment_id: str, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        detail: dict[str, Any] = client.get(f"/api/experiments/{experiment_id}").json()
        if detail["status"] not in ("queued", "running"):
            return detail
        assert time.monotonic() < deadline, f"experiment still {detail['status']}"
        time.sleep(0.05)


def test_an_experiment_runs_in_the_background_and_is_summarized(client: TestClient) -> None:
    created = submit(client, **SMALL)
    assert created["status"] in ("queued", "running")
    assert (created["points"], created["runs_total"]) == (1, 2)
    detail = wait(client, created["id"])
    assert (detail["status"], detail["runs_done"], detail["error"]) == ("completed", 2, None)
    assert detail["started_at"] is not None
    assert detail["finished_at"] is not None
    [point] = detail["results"]
    assert (point["point"], point["label"], point["runs"]) == (0, "small", 2)
    summary = {s["metric"]: s for s in point["summary"]}
    for metric in ("spread_bps", "volume", "effective_spread_bps", "market_maker.pnl"):
        assert summary[metric]["count"] == 2
        assert summary[metric]["mean"] is not None
    assert summary["volume"]["ci_low"] <= summary["volume"]["mean"] <= summary["volume"]["ci_high"]
    assert created["id"] in [e["id"] for e in client.get("/api/experiments").json()]


def test_stored_runs_match_the_library_exactly(client: TestClient) -> None:
    """Worker processes and storage change nothing: the same seeds give the same digests and
    metrics as running the library directly, and the CSV export is the library's own."""
    experiment = wait(client, submit(client, **SMALL)["id"])
    runs = client.get(f"/api/experiments/{experiment['id']}/runs").json()
    [spec] = build_specs(ExperimentIn.model_validate(SMALL))
    direct = run_experiment(spec)
    assert [r["seed"] for r in runs] == [1, 2]
    assert [r["digest"] for r in runs] == [r.digest for r in direct.runs]
    assert [r["metrics"] for r in runs] == [dict(r.metrics) for r in direct.runs]
    exported = client.get(f"/api/experiments/{experiment['id']}/runs.csv")
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    stream = io.StringIO()
    write_runs_csv([direct], stream)

    def without_timing(text: str) -> list[list[str]]:
        return [row[:-1] for row in csv.reader(io.StringIO(text))]  # wall_seconds is last.

    assert without_timing(exported.text) == without_timing(stream.getvalue())
    summary = client.get(f"/api/experiments/{experiment['id']}/summary.csv").text.splitlines()
    assert summary[0].startswith("experiment,metric,count,mean,stdev,stderr,ci_low,ci_high")


def test_a_sweep_has_one_point_per_value_and_points_can_be_compared(client: TestClient) -> None:
    body = {**SMALL, "name": "makers", "sweep": {"population.market_makers": [0, 4]}}
    detail = wait(client, submit(client, **body)["id"])
    assert detail["status"] == "completed"
    assert [p["label"] for p in detail["results"]] == [
        "makers [population.market_makers=0]",
        "makers [population.market_makers=4]",
    ]
    assert [p["parameters"] for p in detail["results"]] == [
        {"population.market_makers": 0},
        {"population.market_makers": 4},
    ]
    response = client.get(
        "/api/experiments/compare",
        params={"a": detail["id"], "b": detail["id"], "point_a": 0, "point_b": 1},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["a"]["label"].endswith("=0]")
    by_metric = {m["metric"]: m for m in result["metrics"]}
    # Only metrics both sides define are compared: point 0 has no market makers at all.
    assert "market_maker.pnl" not in by_metric
    assert "random.pnl" in by_metric
    volume = by_metric["volume"]
    assert volume["difference"] == pytest.approx(volume["mean_b"] - volume["mean_a"])
    exported = client.get(
        "/api/experiments/compare.csv",
        params={"a": detail["id"], "b": detail["id"], "point_b": 1},
    )
    assert exported.text.splitlines()[0].startswith("metric,count_a,mean_a,count_b")
    bad = client.get(
        "/api/experiments/compare", params={"a": detail["id"], "b": detail["id"], "point_b": 9}
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "invalid_point")


def test_cancelling_stops_the_runs_not_yet_started(client: TestClient) -> None:
    created = submit(client, **{**SMALL, "name": "long", "runs": 200, "duration_seconds": 60})
    cancelled = client.post(f"/api/experiments/{created['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["runs_done"] < 200
    time.sleep(0.3)  # Runs already in a worker finish there and are discarded.
    later = client.get(f"/api/experiments/{created['id']}").json()
    assert later["status"] == "cancelled"
    assert later["runs_done"] == cancelled["runs_done"]


def test_deleting_an_experiment(client: TestClient) -> None:
    experiment = wait(client, submit(client, **SMALL)["id"])
    assert client.delete(f"/api/experiments/{experiment['id']}").status_code == 204
    missing = client.get(f"/api/experiments/{experiment['id']}")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "unknown_experiment")
    assert client.delete(f"/api/experiments/{experiment['id']}").status_code == 404


@pytest.mark.parametrize(
    ("body", "code", "message"),
    [
        ({"runs": 501}, "validation_error", ""),
        ({"duration_seconds": 86_401}, "validation_error", ""),
        ({"duration_seconds": "NaN"}, "validation_error", ""),
        ({"sweep": {"population.market_makers": list(range(51))}}, "validation_error", "51"),
        (
            {"runs": 50, "sweep": {"population.random_traders": list(range(41))}},
            "validation_error",
            "2050",
        ),
        ({"runs": 500, "duration_seconds": 86_400}, "validation_error", "simulated time"),
        ({"sweep": {"population.wizards": [1]}}, "invalid_configuration", "wizards"),
        ({"sweep": {"population.market_makers": [2.5]}}, "invalid_configuration", ""),
        ({"sweep": {"population.market_makers": []}}, "validation_error", "at least one"),
        (
            {"agents": {"pairs_traders": 1}, "market": {"pairs": []}},
            "invalid_configuration",
            "pair",
        ),
    ],
)
def test_experiments_are_validated_before_anything_runs(
    client: TestClient, body: dict[str, Any], code: str, message: str
) -> None:
    before = len(client.get("/api/experiments").json())
    response = client.post("/api/experiments", json={**SMALL, **body})
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert message in str(error)
    assert len(client.get("/api/experiments").json()) == before


def test_results_persist_across_restarts(tmp_path: Path) -> None:
    url = f"sqlite+pysqlite:///{tmp_path / 'experiments.db'}"
    settings = Settings(tick_seconds=0.01, experiment_workers=1, database_url=url)
    with TestClient(create_app(settings)) as first:
        experiment = wait(first, submit(first, **SMALL)["id"])
        # Left unfinished when the server stops: marked failed on the next start.
        unfinished = submit(first, **{**SMALL, "runs": 300, "duration_seconds": 30})
    with TestClient(create_app(settings)) as second:
        restored = second.get(f"/api/experiments/{experiment['id']}").json()
        assert (restored["status"], restored["runs_done"]) == ("completed", 2)
        assert restored["results"][0]["summary"]
        interrupted = second.get(f"/api/experiments/{unfinished['id']}").json()
        assert interrupted["status"] in ("cancelled", "failed")


class _FailingPool:
    """Runs jobs in-process; the second one fails, as a crashed run would."""

    def __init__(self) -> None:
        self.jobs = 0

    def submit(self, fn: Any, *args: Any) -> Future[Any]:
        self.jobs += 1
        future: Future[Any] = Future()
        if self.jobs == 2:
            future.set_exception(RuntimeError("worker blew up"))
        else:
            future.set_result(fn(*args))
        return future


@pytest.mark.anyio
async def test_a_failed_run_fails_the_experiment_with_its_error() -> None:
    pool = _FailingPool()

    class Service(ExperimentService):
        def _executor(self) -> Any:
            return pool

    service = Service(ExperimentStore(), workers=1)
    record = await service.submit(ExperimentIn.model_validate({**SMALL, "runs": 3}))
    await service._tasks[record.id]
    failed = await service.get(record.id)
    assert failed.status == "failed"
    assert failed.error == "RuntimeError: worker blew up"
    assert failed.finished_at is not None
    assert failed.runs_done <= 2  # What finished before the failure is kept.
