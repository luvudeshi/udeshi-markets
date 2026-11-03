"""Liveness endpoint."""

from fastapi import APIRouter

from udeshi_api.schemas import Health
from udeshi_markets import __version__

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> Health:
    return Health(version=__version__)
