"""Persistent experiment results: SQLAlchemy Core over PostgreSQL (or SQLite).

Two tables, created at startup:
* ``experiments``: the request as submitted, its status and progress;
* ``runs``: one row per (sweep point, seed), with every scalar metric of the run.

``UDESHI_DATABASE_URL`` selects the database, for example
``postgresql+psycopg://udeshi:udeshi@db:5432/udeshi``. Without it, results live in an in-memory
SQLite database for the lifetime of the process, so the API works with nothing configured.

The calls are synchronous. The API runs them with ``asyncio.to_thread`` so the event loop never
waits on the database, and a lock serialises them (the in-memory SQLite database is a single
shared connection). None of this code touches a simulation.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy.pool import StaticPool

STATUSES = ("queued", "running", "completed", "failed", "cancelled")
UNFINISHED = ("queued", "running")

metadata = MetaData()

experiments = Table(
    "experiments",
    metadata,
    Column("id", String(32), primary_key=True),
    Column("name", String(200), nullable=False),
    Column("label", Text, nullable=False),
    Column("request", JSON, nullable=False),  # The submitted body, as validated.
    Column("sweep", JSON, nullable=True),
    Column("points", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("runs_done", Integer, nullable=False),
    Column("runs_total", Integer, nullable=False),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)

runs = Table(
    "runs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "experiment_id",
        String(32),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("point", Integer, nullable=False),
    Column("label", Text, nullable=False),
    Column("seed", BigInteger, nullable=False),
    Column("parameters", JSON, nullable=False),  # [[setting path, value], ...]
    Column("metrics", JSON, nullable=False),  # {metric: value or null}
    Column("digest", String(64), nullable=False),
    Column("wall_seconds", Float, nullable=False),
    UniqueConstraint("experiment_id", "point", "seed"),
)


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    id: str
    name: str
    label: str
    request: dict[str, Any]
    sweep: dict[str, list[Any]] | None
    points: int
    status: str
    runs_done: int
    runs_total: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class RunRecord:
    point: int
    label: str
    seed: int
    parameters: tuple[tuple[str, object], ...]
    metrics: dict[str, float | None]
    digest: str
    wall_seconds: float


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every stored time is UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ExperimentStore:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or "sqlite+pysqlite:///:memory:"
        self._engine = _engine(self.url)
        self._lock = threading.Lock()
        metadata.create_all(self._engine)

    @property
    def engine(self) -> Engine:
        return self._engine

    def close(self) -> None:
        self._engine.dispose()

    # ---- Experiments -----------------------------------------------------------------------
    def create(
        self,
        experiment_id: str,
        *,
        name: str,
        label: str,
        request: dict[str, Any],
        sweep: dict[str, list[Any]] | None,
        points: int,
        runs_total: int,
    ) -> ExperimentRecord:
        now = datetime.now(UTC)
        with self._lock, self._engine.begin() as connection:
            connection.execute(
                insert(experiments).values(
                    id=experiment_id,
                    name=name,
                    label=label,
                    request=request,
                    sweep=sweep,
                    points=points,
                    status="queued",
                    runs_done=0,
                    runs_total=runs_total,
                    error=None,
                    created_at=now,
                )
            )
        record = self.get(experiment_id)
        assert record is not None
        return record

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        with self._lock, self._engine.connect() as connection:
            row = connection.execute(
                select(experiments).where(experiments.c.id == experiment_id)
            ).one_or_none()
        return None if row is None else _experiment(row._mapping)

    def all(self) -> list[ExperimentRecord]:
        with self._lock, self._engine.connect() as connection:
            rows = connection.execute(
                select(experiments).order_by(experiments.c.created_at, experiments.c.id)
            ).all()
        return [_experiment(row._mapping) for row in rows]

    def set_status(self, experiment_id: str, status: str, *, error: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        now = datetime.now(UTC)
        values: dict[str, Any] = {"status": status}
        if status == "running":
            values["started_at"] = now
        if status in ("completed", "failed", "cancelled"):
            values["finished_at"] = now
        if error is not None:
            values["error"] = error
        with self._lock, self._engine.begin() as connection:
            connection.execute(
                update(experiments).where(experiments.c.id == experiment_id).values(**values)
            )

    def mark_interrupted(self) -> int:
        """Experiments left unfinished by a previous process (it stopped mid-run) are marked
        failed. Returns how many."""
        with self._lock, self._engine.begin() as connection:
            result = connection.execute(
                update(experiments)
                .where(experiments.c.status.in_(UNFINISHED))
                .values(
                    status="failed",
                    error="the server stopped before the experiment finished",
                    finished_at=datetime.now(UTC),
                )
            )
        return int(result.rowcount or 0)

    def delete(self, experiment_id: str) -> bool:
        with self._lock, self._engine.begin() as connection:
            connection.execute(delete(runs).where(runs.c.experiment_id == experiment_id))
            result = connection.execute(
                delete(experiments).where(experiments.c.id == experiment_id)
            )
        return bool(result.rowcount)

    # ---- Runs ------------------------------------------------------------------------------
    def add_run(self, experiment_id: str, run: RunRecord) -> None:
        """Stores one finished run and counts it towards the experiment's progress."""
        with self._lock, self._engine.begin() as connection:
            connection.execute(
                insert(runs).values(
                    experiment_id=experiment_id,
                    point=run.point,
                    label=run.label,
                    seed=run.seed,
                    parameters=[list(pair) for pair in run.parameters],
                    metrics=run.metrics,
                    digest=run.digest,
                    wall_seconds=run.wall_seconds,
                )
            )
            connection.execute(
                update(experiments)
                .where(experiments.c.id == experiment_id)
                .values(runs_done=experiments.c.runs_done + 1)
            )

    def runs(self, experiment_id: str, point: int | None = None) -> list[RunRecord]:
        """Stored runs in (point, seed) order: the order the library reports them in."""
        query = select(runs).where(runs.c.experiment_id == experiment_id)
        if point is not None:
            query = query.where(runs.c.point == point)
        with self._lock, self._engine.connect() as connection:
            rows = connection.execute(query.order_by(runs.c.point, runs.c.seed)).all()
        return [_run(row._mapping) for row in rows]


def _engine(url: str) -> Engine:
    if url.startswith("sqlite") and ":memory:" in url:
        # One connection shared by every thread, or each connection gets its own empty database.
        return create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


def _experiment(row: Any) -> ExperimentRecord:
    return ExperimentRecord(
        id=row["id"],
        name=row["name"],
        label=row["label"],
        request=dict(row["request"]),
        sweep=None if row["sweep"] is None else dict(row["sweep"]),
        points=row["points"],
        status=row["status"],
        runs_done=row["runs_done"],
        runs_total=row["runs_total"],
        error=row["error"],
        created_at=_utc(row["created_at"]) or datetime.now(UTC),
        started_at=_utc(row["started_at"]),
        finished_at=_utc(row["finished_at"]),
    )


def _run(row: Any) -> RunRecord:
    parameters: Sequence[Sequence[object]] = row["parameters"]
    return RunRecord(
        point=row["point"],
        label=row["label"],
        seed=int(row["seed"]),
        parameters=tuple((str(path), value) for path, value in parameters),
        metrics={str(k): (None if v is None else float(v)) for k, v in row["metrics"].items()},
        digest=row["digest"],
        wall_seconds=float(row["wall_seconds"]),
    )
