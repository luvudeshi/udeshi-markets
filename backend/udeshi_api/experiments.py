"""Running experiments for the API.

Every (sweep point, seed) is one job for a pool of worker processes (spawned, not forked), so
experiments never compete with the live markets for the event loop. The service awaits the jobs
with ``asyncio.wrap_future`` and stores each run as it finishes, so progress is visible and a
crash loses only unfinished runs. A failed run fails the experiment, with its error recorded
and logged. Cancelling stops every job not yet started; a job already running finishes in its
worker and is discarded.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import secrets
from concurrent.futures import Future, ProcessPoolExecutor

from fastapi import status

from udeshi_api.configs import market_configs
from udeshi_api.errors import ApiError, not_found
from udeshi_api.experiment_schemas import ExperimentIn
from udeshi_api.storage import ExperimentRecord, ExperimentStore, RunRecord
from udeshi_markets.agents import market_simulation
from udeshi_markets.analytics import RunAnalytics
from udeshi_markets.experiments import (
    ExperimentResult,
    ExperimentSpec,
    RunResult,
    run_once,
    sweep,
)
from udeshi_markets.simulation import DEFAULT_INSTRUMENTS, InstrumentConfig, seconds

logger = logging.getLogger("udeshi_api.experiments")


def build_specs(body: ExperimentIn) -> list[ExperimentSpec]:
    """The experiment's points, in sweep order. Every point is built once (agents and
    environment included) so that an invalid setting is rejected here, not in a worker."""
    try:
        instruments = (
            DEFAULT_INSTRUMENTS
            if body.instruments is None
            else tuple(
                InstrumentConfig(i.symbol, i.reference_price, i.tick_size) for i in body.instruments
            )
        )
        population, environment = market_configs(
            body.agents, body.market, {i.symbol for i in instruments}
        )
        assert population is not None  # Experiments always have agents.
        spec = ExperimentSpec(
            name=body.name,
            instruments=instruments,
            duration_ns=seconds(body.duration_seconds),
            order_latency_ns=round(body.order_latency_ms * 1_000_000),
            initial_cash=body.initial_cash,
            population=population,
            environment=environment,
            runs=body.runs,
            base_seed=body.base_seed,
            sample_interval_ns=seconds(body.sample_interval_seconds),
            markout_ns=seconds(body.markout_seconds),
        )
        points = sweep(spec, {path: list(values) for path, values in (body.sweep or {}).items()})
        for point in points:
            market_simulation(
                point.simulation_config(point.base_seed),
                point.population,
                point.environment_config(),
            )
    except (ValueError, TypeError) as error:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_configuration", str(error)
        ) from error
    return points


def results_from(record: ExperimentRecord, runs: list[RunRecord]) -> list[ExperimentResult]:
    """The stored runs as library results, one per point, so the library's own summaries,
    comparisons and CSV export apply. Only scalar metrics are stored, not the per-run detail."""
    specs = build_specs(ExperimentIn.model_validate(record.request))
    results = []
    for point, spec in enumerate(specs):
        empty = RunAnalytics((), (), (), spec.sample_interval_ns, spec.markout_ns)
        stored = tuple(
            RunResult(
                experiment=run.label,
                seed=run.seed,
                parameters=run.parameters,
                metrics=run.metrics,
                analytics=empty,
                digest=run.digest,
                wall_seconds=run.wall_seconds,
            )
            for run in runs
            if run.point == point
        )
        results.append(ExperimentResult(spec, stored))
    return results


class ExperimentService:
    def __init__(self, store: ExperimentStore, workers: int) -> None:
        self.store = store
        self.workers = workers
        self._pool: ProcessPoolExecutor | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        interrupted = await asyncio.to_thread(self.store.mark_interrupted)
        if interrupted:
            logger.warning("%d experiments were interrupted by a restart", interrupted)

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await asyncio.to_thread(pool.shutdown, True, cancel_futures=True)
        await asyncio.to_thread(self.store.close)

    # ---- Requests ------------------------------------------------------------------------------
    async def submit(self, body: ExperimentIn) -> ExperimentRecord:
        specs = build_specs(body)
        experiment_id = secrets.token_hex(6)
        record = await asyncio.to_thread(
            self.store.create,
            experiment_id,
            name=body.name,
            label=specs[0].name,
            request=body.model_dump(mode="json"),
            sweep=None if body.sweep is None else {k: list(v) for k, v in body.sweep.items()},
            points=len(specs),
            runs_total=sum(spec.runs for spec in specs),
        )
        task = asyncio.create_task(
            self._run(experiment_id, specs), name=f"experiment-{experiment_id}"
        )
        self._tasks[experiment_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(experiment_id, None))
        logger.info("experiment %s queued: %d runs", experiment_id, record.runs_total)
        return record

    async def get(self, experiment_id: str) -> ExperimentRecord:
        record = await asyncio.to_thread(self.store.get, experiment_id)
        if record is None:
            raise not_found("unknown_experiment", f"no experiment with id '{experiment_id}'")
        return record

    async def all(self) -> list[ExperimentRecord]:
        return await asyncio.to_thread(self.store.all)

    async def runs(self, experiment_id: str) -> list[RunRecord]:
        await self.get(experiment_id)
        return await asyncio.to_thread(self.store.runs, experiment_id)

    async def results(self, experiment_id: str) -> tuple[ExperimentRecord, list[ExperimentResult]]:
        record = await self.get(experiment_id)
        runs = await asyncio.to_thread(self.store.runs, experiment_id)
        return record, results_from(record, runs)

    async def cancel(self, experiment_id: str) -> ExperimentRecord:
        await self.get(experiment_id)
        task = self._tasks.get(experiment_id)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return await self.get(experiment_id)

    async def delete(self, experiment_id: str) -> None:
        await self.cancel(experiment_id)
        await asyncio.to_thread(self.store.delete, experiment_id)

    # ---- Execution -----------------------------------------------------------------------------
    def _executor(self) -> ProcessPoolExecutor:
        if self._pool is None:
            context = multiprocessing.get_context("spawn")
            self._pool = ProcessPoolExecutor(max_workers=self.workers, mp_context=context)
        return self._pool

    async def _run(self, experiment_id: str, specs: list[ExperimentSpec]) -> None:
        store = self.store
        pending: dict[asyncio.Future[RunResult], tuple[int, ExperimentSpec]] = {}
        try:
            await asyncio.to_thread(store.set_status, experiment_id, "running")
            pool = self._executor()
            for point, spec in enumerate(specs):
                for seed in spec.seeds():
                    job: Future[RunResult] = pool.submit(run_once, spec, seed)
                    pending[asyncio.wrap_future(job)] = (point, spec)
            while pending:
                done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for future in done:
                    point, spec = pending.pop(future)
                    result = future.result()  # A run's exception surfaces here.
                    run = RunRecord(
                        point=point,
                        label=spec.label,
                        seed=result.seed,
                        parameters=result.parameters,
                        metrics=result.metrics,
                        digest=result.digest,
                        wall_seconds=result.wall_seconds,
                    )
                    await asyncio.to_thread(store.add_run, experiment_id, run)
            await asyncio.to_thread(store.set_status, experiment_id, "completed")
            logger.info("experiment %s completed", experiment_id)
        except asyncio.CancelledError:
            _cancel_all(pending)
            await asyncio.shield(asyncio.to_thread(store.set_status, experiment_id, "cancelled"))
            logger.info("experiment %s cancelled", experiment_id)
            raise
        except Exception as error:
            _cancel_all(pending)
            logger.exception("experiment %s failed", experiment_id)
            await asyncio.to_thread(
                store.set_status, experiment_id, "failed", error=f"{type(error).__name__}: {error}"
            )


def _cancel_all(pending: dict[asyncio.Future[RunResult], tuple[int, ExperimentSpec]]) -> None:
    for future in pending:
        future.cancel()  # Cancels the pool job too, if it has not started.
