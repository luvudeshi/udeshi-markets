"""``python -m udeshi_api.openapi``, from which the frontend's types are generated."""

from __future__ import annotations

import json

import pytest

from udeshi_api import openapi


def test_the_export_ignores_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The schema must not depend on the shell it runs in. A configured database (here
    unreachable) is never connected to, and invalid settings do not matter."""
    monkeypatch.setenv("UDESHI_DATABASE_URL", "postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    monkeypatch.setenv("UDESHI_MAX_SESSIONS", "-5")
    openapi.main()
    schema = json.loads(capsys.readouterr().out)
    assert "/api/markets/{symbol}/trades" in schema["paths"]
    assert "buyer" in schema["components"]["schemas"]["TradeOut"]["properties"]
