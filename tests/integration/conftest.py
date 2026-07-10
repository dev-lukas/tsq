"""Fixtures for integration tests against real TeamSpeak servers.

The servers come from docker/docker-compose.test.yml, normally started by
scripts/run-integration-tests.sh which exports:

    ATSQ_TS3_HOST / ATSQ_TS3_PORT / ATSQ_TS3_PASSWORD
    ATSQ_TS6_HOST / ATSQ_TS6_PORT / ATSQ_TS6_PASSWORD

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

import atsq


@dataclass(frozen=True)
class ServerTarget:
    name: str
    host: str
    port: int
    password: str
    expected_dialect: atsq.Dialect
    #: Host port mapped to the container's file-transfer port (30033);
    #: None when the harness does not publish it.
    ft_port: int | None


@pytest.fixture(scope="session", params=["ts3", "ts6"])
def server(request: pytest.FixtureRequest) -> ServerTarget:
    name = request.param
    prefix = f"ATSQ_{name.upper()}_"
    host = os.environ.get(prefix + "HOST")
    if not host:
        pytest.skip(f"{name} server not configured (set {prefix}HOST/PORT/PASSWORD)")
    ft_port = os.environ.get(prefix + "FT_PORT")
    return ServerTarget(
        name=name,
        host=host,
        port=int(os.environ[prefix + "PORT"]),
        password=os.environ[prefix + "PASSWORD"],
        expected_dialect=atsq.Dialect.TS3 if name == "ts3" else atsq.Dialect.TS6,
        ft_port=int(ft_port) if ft_port else None,
    )


@pytest.fixture(scope="session")
def run_token() -> str:
    """Unique-ish suffix so reruns against a kept-up stack don't collide."""
    return secrets.token_hex(3)


@pytest.fixture
async def client(server: ServerTarget) -> AsyncIterator[atsq.Client]:
    """A fresh connected client on virtual server 1."""
    c = await atsq.connect(
        server.host, server.port, password=server.password, server_id=1
    )
    yield c
    await c.close()
