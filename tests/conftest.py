from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import types
from typing import Callable, Iterator

import pytest
from fastapi.testclient import TestClient


def _install_pythonjsonlogger_stub() -> None:
    if importlib.util.find_spec("pythonjsonlogger") is not None:
        return

    pythonjsonlogger = types.ModuleType("pythonjsonlogger")
    jsonlogger_mod = types.ModuleType("pythonjsonlogger.jsonlogger")

    class JsonFormatter(logging.Formatter):
        pass

    jsonlogger_mod.JsonFormatter = JsonFormatter
    pythonjsonlogger.jsonlogger = jsonlogger_mod
    sys.modules["pythonjsonlogger"] = pythonjsonlogger
    sys.modules["pythonjsonlogger.jsonlogger"] = jsonlogger_mod


def _install_slowapi_stub() -> None:
    if importlib.util.find_spec("slowapi") is not None:
        return

    slowapi = types.ModuleType("slowapi")

    class Limiter:
        def __init__(self, *args, **kwargs):
            pass

        def limit(self, _rule: str):
            def decorator(fn):
                return fn

            return decorator

    slowapi.Limiter = Limiter
    slowapi._rate_limit_exceeded_handler = lambda *args, **kwargs: None
    slowapi_util = types.ModuleType("slowapi.util")
    slowapi_util.get_remote_address = lambda request: "127.0.0.1"
    slowapi_errors = types.ModuleType("slowapi.errors")

    class RateLimitExceeded(Exception):
        pass

    slowapi_errors.RateLimitExceeded = RateLimitExceeded
    sys.modules["slowapi"] = slowapi
    sys.modules["slowapi.util"] = slowapi_util
    sys.modules["slowapi.errors"] = slowapi_errors


def _install_multipart_stub() -> None:
    if importlib.util.find_spec("multipart") is not None:
        return

    multipart_pkg = types.ModuleType("multipart")
    multipart_pkg.__version__ = "0.0-test"
    multipart_sub = types.ModuleType("multipart.multipart")
    multipart_sub.parse_options_header = lambda value: (value, {})
    sys.modules["multipart"] = multipart_pkg
    sys.modules["multipart.multipart"] = multipart_sub


_install_pythonjsonlogger_stub()
_install_slowapi_stub()
_install_multipart_stub()


@pytest.fixture()
def app_client_factory(monkeypatch, tmp_path) -> Iterator[Callable[..., tuple[TestClient, object]]]:
    clients: list[TestClient] = []

    def _factory(
        *,
        free_games_per_day: int = 5,
        trial_days: int = 14,
        api_key: str | None = None,
        db_name: str = "app.db",
    ) -> tuple[TestClient, object]:
        db_path = tmp_path / db_name
        monkeypatch.setenv("ENVIRONMENT", "development")
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
        monkeypatch.setenv("BACKEND_AUTH_SECRET", "backend-test-secret")
        monkeypatch.setenv("APPLE_BUNDLE_ID", "com.llmchesscoach.test")
        monkeypatch.setenv("APPLE_TEST_IDENTITY_SECRET", "apple-test-secret")
        monkeypatch.setenv("APPSTORE_TEST_SHARED_SECRET", "app-store-test-secret")
        monkeypatch.setenv("APPSTORE_PRODUCT_ID_30_GAMES", "com.llmchesscoach.games30")
        monkeypatch.setenv("FREE_GAMES_PER_DAY", str(free_games_per_day))
        monkeypatch.setenv("TRIAL_DAYS", str(trial_days))
        monkeypatch.delenv("REDIS_URL", raising=False)
        if api_key is None:
            monkeypatch.delenv("API_KEY", raising=False)
        else:
            monkeypatch.setenv("API_KEY", api_key)

        import live_sessions
        import api_server

        importlib.reload(live_sessions)
        reloaded_api_server = importlib.reload(api_server)
        client = TestClient(reloaded_api_server.app)
        clients.append(client)
        return client, reloaded_api_server

    yield _factory

    for client in reversed(clients):
        client.close()
