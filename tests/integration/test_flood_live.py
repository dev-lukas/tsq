"""Live flood-protection tests against a NON-allowlisted TS3 server.

The regular ts3/ts6 services allowlist every source IP, so they never
answer 524 - this suite uses the ``ts3strict`` compose service (no
allowlist mount), where connections from the docker gateway are subject to
the real flood limits. The 524 wire format is identical on TS6
(probe-verified), so one strict server suffices.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import atsq

pytestmark = pytest.mark.integration


def _target() -> tuple[str, int, str]:
    host = os.environ.get("ATSQ_TS3STRICT_HOST")
    if not host:
        pytest.skip("strict server not configured (set ATSQ_TS3STRICT_HOST/PORT/PASSWORD)")
    return host, int(os.environ["ATSQ_TS3STRICT_PORT"]), os.environ["ATSQ_TS3STRICT_PASSWORD"]


async def test_flood_error_surfaces_with_retry_hint() -> None:
    host, port, password = _target()
    client = await atsq.connect(host, port, password=password, server_id=1, flood_retries=0)
    try:
        flood: atsq.FloodError | None = None
        for _ in range(60):
            try:
                await client.whoami()
            except atsq.FloodError as err:
                flood = err
                break
        assert flood is not None, "no 524 within 60 rapid commands on the strict server"
        assert flood.retry_after >= 1.0  # live hint: 'please wait N seconds'
        # the connection survives a 524 once the requested time has passed
        await asyncio.sleep(flood.retry_after + 0.2)
        assert (await client.whoami())["virtualserver_id"] == "1"
    finally:
        await client.close()


async def test_auto_retry_absorbs_flood_transparently() -> None:
    host, port, password = _target()
    client = await atsq.connect(host, port, password=password, server_id=1, flood_retries=5)
    try:
        # Way past the flood threshold (~10 cmds / 3s): every call must
        # still succeed because exec() waits and retries internally.
        for _ in range(30):
            assert (await client.whoami())["virtualserver_id"] == "1"
    finally:
        await client.close()
