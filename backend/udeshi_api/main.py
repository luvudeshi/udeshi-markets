"""Application factory. Run with: ``uvicorn udeshi_api.main:create_app --factory``."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from udeshi_api.errors import install_error_handlers
from udeshi_api.routes import agents, health, markets, simulations, stream
from udeshi_api.sessions import SessionManager
from udeshi_api.settings import Settings
from udeshi_markets import __version__


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    manager = SessionManager(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logging.getLogger("udeshi_api").info("Udeshi Markets API %s starting", __version__)
        yield
        await manager.shutdown()

    app = FastAPI(
        title="Udeshi Markets API",
        version=__version__,
        description="Simulated electronic exchange: market data, simulation control, "
        "manual order entry and live WebSocket streams.",
        lifespan=lifespan,
    )
    app.state.sessions = manager
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    for router in (
        health.router,
        simulations.router,
        markets.router,
        agents.router,
        stream.router,
    ):
        app.include_router(router)
    return app
