"""Participants of a simulation (the active one by default) and the news published to them."""

from typing import Annotated

from fastapi import APIRouter, Query

from udeshi_api.deps import Sessions
from udeshi_api.routes.markets import SimulationId
from udeshi_api.schemas import AgentDetailOut, AgentOut, NewsOut

router = APIRouter(prefix="/api", tags=["agents"])


@router.get("/agents")
async def list_agents(sessions: Sessions, simulation_id: SimulationId = None) -> list[AgentOut]:
    """Every participant (agents, the API user and the market environment), in id order, with
    cash, positions and P&L at the current marks."""
    return sessions.resolve(simulation_id).agents()


@router.get("/agents/{agent_id}")
async def get_agent(
    agent_id: int, sessions: Sessions, simulation_id: SimulationId = None
) -> AgentDetailOut:
    """One participant in detail: positions, open orders, recent orders, statistics and its
    strategy's own measures."""
    return sessions.resolve(simulation_id).agent(agent_id)


@router.get("/news")
async def list_news(
    sessions: Sessions,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 50,
    simulation_id: SimulationId = None,
) -> list[NewsOut]:
    """Published news, oldest first (unannounced shocks never appear here)."""
    return sessions.resolve(simulation_id).news(limit)
