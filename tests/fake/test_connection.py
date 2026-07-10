import asyncio

import pytest

from tests.fake.fake_transport import FakeTransport
from tsq.connection import RawConnection
from tsq.dialect import Dialect
from tsq.errors import (
    ConnectionClosedError,
    FloodError,
    QueryError,
    QueryTimeoutError,
)

OK = b"error id=0 msg=ok"


def make_conn(transport: FakeTransport, **kwargs: object) -> RawConnection:
    kwargs.setdefault("keepalive_interval", 0)  # keepalive off unless a test wants it
    return RawConnection(transport, **kwargs)  # type: ignore[arg-type]


async def test_greeting_consumed_and_dialect_sniffed() -> None:
    transport = FakeTransport()
    async with make_conn(transport) as conn:
        assert conn.dialect is Dialect.TS3
        assert conn.greeting[0] == b"TS3"
        assert len(conn.greeting) == 2


async def test_exec_returns_parsed_rows() -> None:
    transport = FakeTransport()
    transport.when(b"clientlist", [b"clid=1 client_nickname=A|clid=2 client_nickname=B\\sC", OK])
    async with make_conn(transport) as conn:
        rows = await conn.exec("clientlist")
    assert rows == [
        {"clid": "1", "client_nickname": "A"},
        {"clid": "2", "client_nickname": "B C"},
    ]
    assert transport.sent == [b"clientlist", b"quit"]  # quit is sent by close()


async def test_exec_empty_response_is_empty_list() -> None:
    transport = FakeTransport()
    transport.when(b"use", [OK])
    async with make_conn(transport) as conn:
        assert await conn.exec("use", sid=1) == []


async def test_query_error_raised_with_server_msg() -> None:
    transport = FakeTransport()
    transport.when(b"use", [b"error id=1024 msg=invalid\\sserverID"])
    async with make_conn(transport) as conn:
        with pytest.raises(QueryError) as excinfo:
            await conn.exec("use", sid=99)
    assert excinfo.value.error_id == 1024
    assert "invalid serverID" in str(excinfo.value)


async def test_flood_error_type() -> None:
    transport = FakeTransport()
    transport.when(b"whoami", [b"error id=524 msg=client\\sis\\sflooding"])
    async with make_conn(transport) as conn:
        with pytest.raises(FloodError):
            await conn.exec("whoami")


async def test_event_interleaved_mid_response_does_not_corrupt() -> None:
    transport = FakeTransport()
    transport.when(
        b"clientlist",
        [
            b"clid=1 client_nickname=A",
            b"notifycliententerview cfid=0 ctid=1 reasonid=0 clid=7 client_type=0",
            OK,
        ],
    )
    async with make_conn(transport) as conn:
        rows = await conn.exec("clientlist")
        event = await conn.wait_for_event(timeout=1)
    assert rows == [{"clid": "1", "client_nickname": "A"}]
    assert event.name == "cliententerview"
    assert event["clid"] == "7"
    assert event.get("reasonid") == "0"


async def test_unsolicited_events_flow_without_commands() -> None:
    transport = FakeTransport()
    async with make_conn(transport) as conn:
        transport.inject(b"notifyclientleftview reasonid=8 clid=5")
        event = await conn.wait_for_event(timeout=1)
    assert event.name == "clientleftview"
    assert event["reasonid"] == "8"


async def test_concurrent_execs_serialize_in_order() -> None:
    transport = FakeTransport()
    transport.when(b"cmd_a", [b"answer=a", OK])
    transport.when(b"cmd_b", [b"answer=b", OK])
    async with make_conn(transport) as conn:
        result_a, result_b = await asyncio.gather(conn.exec("cmd_a"), conn.exec("cmd_b"))
    assert result_a == [{"answer": "a"}]
    assert result_b == [{"answer": "b"}]
    assert transport.sent == [b"cmd_a", b"cmd_b", b"quit"]  # quit is sent by close()


async def test_command_timeout_closes_connection() -> None:
    transport = FakeTransport()
    # No responder for "whoami": the response never arrives.
    conn = make_conn(transport, command_timeout=0.05)
    async with conn:
        with pytest.raises(QueryTimeoutError):
            await conn.exec("whoami")
        assert conn.closed
        with pytest.raises(ConnectionClosedError):
            await conn.exec("whoami")


async def test_eof_fails_pending_command() -> None:
    transport = FakeTransport()
    transport.auto_respond = False
    async with make_conn(transport) as conn:
        task = asyncio.create_task(conn.exec("clientlist"))
        await asyncio.sleep(0)  # let the command go out
        transport.drop()
        with pytest.raises(ConnectionClosedError):
            await task
        assert conn.closed


async def test_eof_wakes_event_waiters() -> None:
    transport = FakeTransport()
    async with make_conn(transport) as conn:
        waiter = asyncio.create_task(conn.wait_for_event())
        await asyncio.sleep(0)
        transport.drop()
        with pytest.raises(ConnectionClosedError):
            await waiter


async def test_wait_for_event_timeout() -> None:
    transport = FakeTransport()
    async with make_conn(transport) as conn:
        with pytest.raises(QueryTimeoutError):
            await conn.wait_for_event(timeout=0.05)


async def test_events_iterator_ends_on_close() -> None:
    transport = FakeTransport()
    seen: list[str] = []
    async with make_conn(transport) as conn:
        transport.inject(b"notifyclientleftview reasonid=8 clid=1")

        async def consume() -> None:
            async for event in conn.events():
                seen.append(event.name)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        transport.drop()
        await asyncio.wait_for(task, timeout=1)
    assert seen == ["clientleftview"]


async def test_event_queue_overflow_drops_oldest() -> None:
    transport = FakeTransport()
    async with make_conn(transport, event_queue_size=2) as conn:
        transport.inject(
            b"notifyx a=1",
            b"notifyx a=2",
            b"notifyx a=3",
        )
        await asyncio.sleep(0.05)  # let the recv loop process all three
        first = await conn.wait_for_event(timeout=1)
        second = await conn.wait_for_event(timeout=1)
    assert (first["a"], second["a"]) == ("2", "3")


async def test_keepalive_fires_when_idle() -> None:
    transport = FakeTransport()
    transport.when(b"whoami", [b"client_id=1", OK])
    async with make_conn(transport, keepalive_interval=0.05) as conn:
        assert not conn.closed
        await asyncio.sleep(0.2)
        assert any(line == b"whoami" for line in transport.sent)
        assert not conn.closed


async def test_keepalive_suppressed_by_activity() -> None:
    transport = FakeTransport()
    transport.when(b"cmd", [OK])
    transport.when(b"whoami", [b"client_id=1", OK])
    async with make_conn(transport, keepalive_interval=0.2) as conn:
        for _ in range(6):
            await conn.exec("cmd")
            await asyncio.sleep(0.05)
    assert b"whoami" not in transport.sent


async def test_close_is_idempotent_and_stops_everything() -> None:
    transport = FakeTransport()
    conn = make_conn(transport)
    await conn.start()
    await conn.close()
    await conn.close()
    assert conn.closed
    with pytest.raises(ConnectionClosedError):
        await conn.exec("whoami")
    with pytest.raises(ConnectionClosedError):
        await conn.wait_for_event(timeout=1)


async def test_close_sends_quit() -> None:
    # TS6 emits no notifyclientleftview for query clients that silently drop
    # the connection; a clean `quit` produces one on both generations, so
    # close() must send it (fire-and-forget) before tearing down.
    transport = FakeTransport()
    async with make_conn(transport):
        pass
    assert transport.sent == [b"quit"]


async def test_close_swallows_send_failure() -> None:
    transport = FakeTransport()
    conn = make_conn(transport)
    await conn.start()
    transport.drop()
    await asyncio.sleep(0.05)  # recv loop notices EOF first
    await conn.close()  # must not raise even though quit cannot be sent
    assert conn.closed


async def test_ts6_style_greeting_sniffs_ts6() -> None:
    # Probe finding: TS6 also greets with a literal "TS3" first line; only
    # the welcome line differs ("TeamSpeak" instead of "TeamSpeak 3").
    transport = FakeTransport(
        greeting=[
            b"TS3",
            (
                b'Welcome to the TeamSpeak ServerQuery interface, type "help" '
                b'for a list of commands and "help <command>" for information '
                b"on a specific command."
            ),
        ]
    )
    async with make_conn(transport) as conn:
        assert conn.dialect is Dialect.TS6
