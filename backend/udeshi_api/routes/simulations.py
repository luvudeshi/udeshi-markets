"""Simulation lifecycle: create, list, start / pause / resume / stop / reset, speed and step."""

from fastapi import APIRouter, Response, status

from udeshi_api.deps import Sessions
from udeshi_api.errors import ApiError
from udeshi_api.schemas import SimulationCreate, SimulationOut, SpeedIn, StepIn
from udeshi_markets.simulation import (
    DEFAULT_INSTRUMENTS,
    InstrumentConfig,
    SimulationConfig,
    seconds,
)

router = APIRouter(prefix="/api/simulations", tags=["simulations"])


def _config(body: SimulationCreate) -> SimulationConfig:
    try:
        instruments = (
            DEFAULT_INSTRUMENTS
            if body.instruments is None
            else tuple(
                InstrumentConfig(i.symbol, i.reference_price, i.tick_size) for i in body.instruments
            )
        )
        return SimulationConfig(
            instruments=instruments,
            duration_ns=seconds(body.duration_seconds),
            seed=body.seed,
            order_latency_ns=round(body.order_latency_ms * 1_000_000),
            bar_interval_ns=seconds(body.bar_interval_seconds),
        )
    except ValueError as error:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_configuration", str(error)
        ) from error


@router.get("")
async def list_simulations(sessions: Sessions) -> list[SimulationOut]:
    return [s.describe(active=sessions.is_active(s)) for s in sessions.all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_simulation(body: SimulationCreate, sessions: Sessions) -> SimulationOut:
    session = await sessions.create(_config(body), body.speed)
    return session.describe(active=True)


@router.get("/{simulation_id}")
async def get_simulation(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    return session.describe(active=sessions.is_active(session))


@router.delete("/{simulation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_simulation(simulation_id: str, sessions: Sessions) -> Response:
    await sessions.delete(simulation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{simulation_id}/start")
async def start(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.start()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/pause")
async def pause(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.pause()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/resume")
async def resume(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.resume()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/stop")
async def stop(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    await session.stop()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/reset")
async def reset(simulation_id: str, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    await session.reset()
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/speed")
async def set_speed(simulation_id: str, body: SpeedIn, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.set_speed(body.speed)
    return session.describe(active=sessions.is_active(session))


@router.post("/{simulation_id}/step")
async def step(simulation_id: str, body: StepIn, sessions: Sessions) -> SimulationOut:
    session = sessions.get(simulation_id)
    session.step(body.events)
    return session.describe(active=sessions.is_active(session))
