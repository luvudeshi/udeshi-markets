"""Service settings: validation and the environment variables they are read from."""

from __future__ import annotations

from typing import Any

import pytest

from udeshi_api.settings import MAX_BOOK_DEPTH, Settings


def test_defaults_are_valid() -> None:
    settings = Settings()
    assert settings.stream_book_depth == 15
    assert settings.cors_origins == ("http://localhost:3000", "http://127.0.0.1:3000")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("tick_seconds", 0.0009, r"tick_seconds must lie in \[0.001, 5\]"),
        ("tick_seconds", 5.01, r"tick_seconds must lie in \[0.001, 5\]"),
        ("max_sessions", 0, "max_sessions must be at least 1"),
        ("max_events_per_tick", 0, "max_events_per_tick must be at least 1"),
        ("stream_queue_size", 0, "stream_queue_size must be at least 1"),
        ("stream_book_depth", 0, r"stream_book_depth must lie in \[1, 200\]"),
        ("stream_book_depth", -1, r"stream_book_depth must lie in \[1, 200\]"),
        ("stream_book_depth", MAX_BOOK_DEPTH + 1, r"stream_book_depth must lie in \[1, 200\]"),
        ("max_bars_per_simulation", 0, "max_bars_per_simulation must be at least 1"),
        ("trade_history_limit", 0, "trade_history_limit must be at least 1"),
    ],
)
def test_invalid_settings_are_rejected(field: str, value: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tick_seconds", 0.001),
        ("tick_seconds", 5.0),
        ("stream_book_depth", 1),
        ("stream_book_depth", MAX_BOOK_DEPTH),
    ],
)
def test_boundary_settings_are_accepted(field: str, value: Any) -> None:
    assert getattr(Settings(**{field: value}), field) == value


def test_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "UDESHI_TICK_SECONDS": "0.2",
        "UDESHI_MAX_SESSIONS": "3",
        "UDESHI_MAX_EVENTS_PER_TICK": "1234",
        "UDESHI_STREAM_QUEUE_SIZE": "64",
        "UDESHI_STREAM_BOOK_DEPTH": "7",
        "UDESHI_MAX_BARS": "9999",
        "UDESHI_TRADE_HISTORY": "500",
        "UDESHI_CORS_ORIGINS": " http://a.test , ,http://b.test ",
    }.items():
        monkeypatch.setenv(name, value)
    assert Settings.from_environment() == Settings(
        tick_seconds=0.2,
        max_sessions=3,
        max_events_per_tick=1234,
        stream_queue_size=64,
        stream_book_depth=7,
        max_bars_per_simulation=9999,
        trade_history_limit=500,
        cors_origins=("http://a.test", "http://b.test"),
    )


def test_unset_variables_keep_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "UDESHI_TICK_SECONDS",
        "UDESHI_MAX_SESSIONS",
        "UDESHI_MAX_EVENTS_PER_TICK",
        "UDESHI_STREAM_QUEUE_SIZE",
        "UDESHI_STREAM_BOOK_DEPTH",
        "UDESHI_MAX_BARS",
        "UDESHI_TRADE_HISTORY",
        "UDESHI_CORS_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)
    assert Settings.from_environment() == Settings()


@pytest.mark.parametrize(
    ("name", "value", "error"),
    [
        ("UDESHI_STREAM_BOOK_DEPTH", "-1", "stream_book_depth must lie in"),
        ("UDESHI_TICK_SECONDS", "10", "tick_seconds must lie in"),
        ("UDESHI_MAX_SESSIONS", "many", "invalid literal for int"),
    ],
)
def test_invalid_environment_values_fail_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, error: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=error):
        Settings.from_environment()
