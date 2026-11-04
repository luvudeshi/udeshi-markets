"""Experiments: many seeded runs of a market (and sweeps over its settings), in the background.

Results are persistent (see udeshi_api.storage). Summaries, comparisons and CSV files come from
the library's own functions (udeshi_markets.experiments), applied to the stored runs.
"""

import io
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request, Response, status

from udeshi_api.errors import ApiError
from udeshi_api.experiment_schemas import (
    ComparedOut,
    ComparisonOut,
    ComparisonResultOut,
    ExperimentDetailOut,
    ExperimentIn,
    ExperimentOut,
    ExperimentPointOut,
    ExperimentStatus,
    MetricSummaryOut,
    RunOut,
)
from udeshi_api.experiments import ExperimentService
from udeshi_api.storage import ExperimentRecord, RunRecord
from udeshi_markets.experiments import (
    Comparison,
    ExperimentResult,
    compare,
    write_comparison_csv,
    write_runs_csv,
    write_summary_csv,
)

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


async def get_experiments(request: Request) -> ExperimentService:
    service = getattr(request.app.state, "experiments", None)
    if not isinstance(service, ExperimentService):
        raise ApiError(500, "internal_error", "experiment service not configured")
    return service


Experiments = Annotated[ExperimentService, Depends(get_experiments)]


def _out(record: ExperimentRecord) -> ExperimentOut:
    return ExperimentOut(
        id=record.id,
        name=record.name,
        status=cast(ExperimentStatus, record.status),
        points=record.points,
        runs_done=record.runs_done,
        runs_total=record.runs_total,
        error=record.error,
        created_at=record.created_at.isoformat(),
        started_at=None if record.started_at is None else record.started_at.isoformat(),
        finished_at=None if record.finished_at is None else record.finished_at.isoformat(),
        request=ExperimentIn.model_validate(record.request),
    )


def _parameters(pairs: tuple[tuple[str, object], ...]) -> dict[str, float | int]:
    return {path: value for path, value in pairs if isinstance(value, int | float)}


def _csv(text: str, filename: str) -> Response:
    return Response(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _point(results: list[ExperimentResult], experiment_id: str, point: int) -> ExperimentResult:
    if not 0 <= point < len(results):
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_point",
            f"experiment {experiment_id} has points 0 to {len(results) - 1}",
        )
    return results[point]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_experiment(body: ExperimentIn, service: Experiments) -> ExperimentOut:
    """Queues the experiment and returns at once; poll it for progress and results."""
    return _out(await service.submit(body))


@router.get("")
async def list_experiments(service: Experiments) -> list[ExperimentOut]:
    return [_out(record) for record in await service.all()]


@router.get("/compare")
async def compare_experiments(
    service: Experiments,
    a: Annotated[str, Query(description="First experiment id.")],
    b: Annotated[str, Query(description="Second experiment id.")],
    point_a: Annotated[int, Query(ge=0)] = 0,
    point_b: Annotated[int, Query(ge=0)] = 0,
) -> ComparisonResultOut:
    """Metric by metric: both means and Welch's t-test of their difference."""
    first, second, comparisons = await _compare(service, a, b, point_a, point_b)
    return ComparisonResultOut(
        a=ComparedOut(experiment=a, point=point_a, label=first.spec.label),
        b=ComparedOut(experiment=b, point=point_b, label=second.spec.label),
        metrics=[ComparisonOut.model_validate(c, from_attributes=True) for c in comparisons],
    )


@router.get("/compare.csv")
async def compare_experiments_csv(
    service: Experiments,
    a: str,
    b: str,
    point_a: Annotated[int, Query(ge=0)] = 0,
    point_b: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    _, _, comparisons = await _compare(service, a, b, point_a, point_b)
    stream = io.StringIO()
    write_comparison_csv(comparisons, stream)
    return _csv(stream.getvalue(), f"compare-{a}-{b}.csv")


async def _compare(
    service: ExperimentService, a: str, b: str, point_a: int, point_b: int
) -> tuple[ExperimentResult, ExperimentResult, list[Comparison]]:
    _, results_a = await service.results(a)
    _, results_b = await service.results(b)
    first = _point(results_a, a, point_a)
    second = _point(results_b, b, point_b)
    return first, second, compare(first, second)


@router.get("/{experiment_id}")
async def get_experiment(experiment_id: str, service: Experiments) -> ExperimentDetailOut:
    """Status, progress and, per point, a summary of every metric over the runs stored so far."""
    record, results = await service.results(experiment_id)
    points = []
    for point, result in enumerate(results):
        points.append(
            ExperimentPointOut(
                point=point,
                label=result.spec.label,
                parameters=_parameters(result.spec.parameters),
                runs=len(result.runs),
                summary=[
                    MetricSummaryOut.model_validate(summary, from_attributes=True)
                    for summary in result.summary().values()
                ],
            )
        )
    return ExperimentDetailOut(**_out(record).model_dump(), results=points)


@router.get("/{experiment_id}/runs")
async def list_runs(experiment_id: str, service: Experiments) -> list[RunOut]:
    return [_run_out(run) for run in await service.runs(experiment_id)]


def _run_out(run: RunRecord) -> RunOut:
    return RunOut(
        point=run.point,
        label=run.label,
        seed=run.seed,
        parameters=_parameters(run.parameters),
        metrics=run.metrics,
        digest=run.digest,
        wall_seconds=run.wall_seconds,
    )


@router.get("/{experiment_id}/runs.csv")
async def runs_csv(experiment_id: str, service: Experiments) -> Response:
    _, results = await service.results(experiment_id)
    stream = io.StringIO()
    write_runs_csv(results, stream)
    return _csv(stream.getvalue(), f"{experiment_id}-runs.csv")


@router.get("/{experiment_id}/summary.csv")
async def summary_csv(experiment_id: str, service: Experiments) -> Response:
    _, results = await service.results(experiment_id)
    stream = io.StringIO()
    write_summary_csv(results, stream)
    return _csv(stream.getvalue(), f"{experiment_id}-summary.csv")


@router.post("/{experiment_id}/cancel")
async def cancel_experiment(experiment_id: str, service: Experiments) -> ExperimentOut:
    """Stops every run not yet started. Finished experiments are left as they are."""
    return _out(await service.cancel(experiment_id))


@router.delete("/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(experiment_id: str, service: Experiments) -> Response:
    await service.get(experiment_id)
    await service.delete(experiment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
