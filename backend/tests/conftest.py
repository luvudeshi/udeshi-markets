"""Shared fixtures for the API tests. (A hung test fails the run after pytest's
``faulthandler_timeout``, configured in pyproject.toml.)"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from udeshi_api.main import create_app
from udeshi_api.settings import Settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app(Settings(tick_seconds=0.01))) as test_client:
        yield test_client
