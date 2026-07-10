"""Fixtures for integration tests against real TeamSpeak servers.

The servers come from docker/docker-compose.test.yml, normally started by
scripts/run-integration-tests.sh which exports:

    TSQ_TS3_HOST / TSQ_TS3_PORT / TSQ_TS3_PASSWORD
    TSQ_TS6_HOST / TSQ_TS6_PORT / TSQ_TS6_PASSWORD

The same suite runs against both generations via the parametrized ``server``
fixture; a generation whose env vars are missing is skipped, so a partial
stack degrades gracefully instead of failing the run.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

import tsq


@dataclass(frozen=True)
class ServerTarget:
    name: str
    host: str
    port: int
    password: str
    expected_dialect: tsq.Dialect


@pytest.fixture(scope="session", params=["ts3", "ts6"])
def server(request: pytest.FixtureRequest) -> ServerTarget:
    name = request.param
    prefix = f"TSQ_{name.upper()}_"
    host = os.environ.get(prefix + "HOST")
    if not host:
        pytest.skip(f"{name} server not configured (set {prefix}HOST/PORT/PASSWORD)")
    return ServerTarget(
        name=name,
        host=host,
        port=int(os.environ[prefix + "PORT"]),
        password=os.environ[prefix + "PASSWORD"],
        expected_dialect=tsq.Dialect.TS3 if name == "ts3" else tsq.Dialect.TS6,
    )


@pytest.fixture(scope="session")
def run_token() -> str:
    """Unique-ish suffix so reruns against a kept-up stack don't collide."""
    return secrets.token_hex(3)


@pytest.fixture
async def client(server: ServerTarget) -> AsyncIterator[tsq.Client]:
    """A fresh connected client on virtual server 1."""
    c = await tsq.connect(
        server.host, server.port, password=server.password, server_id=1
    )
    yield c
    await c.close()
