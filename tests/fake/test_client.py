import asyncio

import pytest

from tests.fake.fake_transport import FakeTransport
from tsq.client import Client
from tsq.errors import ConnectionClosedError, QueryError
from tsq.events import Event

OK = b"error id=0 msg=ok"


class TransportFarm:
    """transport_factory that scripts every connection the client makes."""

    def __init__(self, *, fail_first: int = 0, banned_first: int = 0) -> None:
        self.transports: list[FakeTransport] = []
        self._fail_first = fail_first
        self._banned_first = banned_first

    @property
    def connections(self) -> int:
        return len(self.transports)

    def _script(self, transport: FakeTransport) -> None:
        transport.when(b"use ", [OK])
        transport.when(b"servernotifyregister", [OK])
        transport.when(b"whoami", [b"client_id=1 client_unique_identifier=serveradmin", OK])

    async def __call__(self) -> FakeTransport:
        if self._fail_first > 0:
            self._fail_first -= 1
            raise ConnectionClosedError("scripted connect failure")
        transport = FakeTransport()
        if self._banned_first > 0:
            self._banned_first -= 1
            transport.when(
                b"use ",
                [b"error id=3329 msg=connection\\sfailed,\\syou\\sare\\sbanned"],
            )
        else:
            self._script(transport)
        self.transports.append(transport)
        return transport


def make_client(farm: TransportFarm, **kwargs: object) -> Client:
    kwargs.setdefault("keepalive_interval", 0)
    kwargs.setdefault("server_id", 1)
    kwargs.setdefault("register_events", "server")
    return Client(  # type: ignore[arg-type]
        "unused-host", password="unused", transport_factory=farm, **kwargs
    )


async def test_start_runs_use_and_register() -> None:
    farm = TransportFarm()
    client = make_client(farm)
    async with await client.start() as client:
        assert client.connected
    assert farm.transports[0].sent[:2] == [b"use sid=1", b"servernotifyregister event=server"]


async def test_nickname_and_server_port_and_multi_events() -> None:
    transport = FakeTransport()
    transport.when(b"", [OK])  # everything succeeds

    async def factory() -> FakeTransport:
        return transport

    client = Client(
        "unused-host",
        password="unused",
        transport_factory=factory,
        keepalive_interval=0,
        server_port=9987,
        nickname="Ember Bot",
        register_events=["server", ("channel", 0), "textserver"],
    )
    await client.start()
    await client.close()
    assert transport.sent[:5] == [
        b"use port=9987",
        rb"clientupdate client_nickname=Ember\sBot",
        b"servernotifyregister event=server",
        b"servernotifyregister event=channel id=0",
        b"servernotifyregister event=textserver",
    ]


async def test_nickname_collision_does_not_break_connect() -> None:
    transport = FakeTransport()
    transport.when(b"use ", [OK])
    transport.when(
        b"clientupdate", [b"error id=513 msg=nickname\\sis\\salready\\sin\\suse"]
    )

    async def factory() -> FakeTransport:
        return transport

    client = Client(
        "unused-host",
        password="unused",
        transport_factory=factory,
        keepalive_interval=0,
        server_id=1,
        nickname="Taken",
    )
    await client.start()
    assert client.connected  # collision logged, connection kept
    await client.close()


async def test_server_id_and_port_mutually_exclusive() -> None:
    with pytest.raises(ValueError):
        Client("h", password="p", server_id=1, server_port=9987)


async def test_all_events_constant_registers_every_source() -> None:
    from tsq import ALL_EVENTS

    transport = FakeTransport()
    transport.when(b"", [OK])

    async def factory() -> FakeTransport:
        return transport

    client = Client(
        "unused-host",
        password="unused",
        transport_factory=factory,
        keepalive_interval=0,
        register_events=ALL_EVENTS,
    )
    await client.start()
    await client.close()
    registers = [line for line in transport.sent if line.startswith(b"servernotifyregister")]
    assert registers == [
        b"servernotifyregister event=server",
        b"servernotifyregister event=channel id=0",
        b"servernotifyregister event=textserver",
        b"servernotifyregister event=textchannel",
        b"servernotifyregister event=textprivate",
    ]


async def test_start_failure_closes_connection() -> None:
    farm = TransportFarm(banned_first=1)
    client = make_client(farm)
    with pytest.raises(QueryError):
        await client.start()
    assert not client.connected
    assert farm.transports[0].is_closed


async def test_exec_without_connection_raises() -> None:
    client = make_client(TransportFarm())
    with pytest.raises(ConnectionClosedError):
        await client.exec("whoami")


async def test_run_forever_dispatches_events_to_handlers() -> None:
    farm = TransportFarm()
    client = make_client(farm)
    seen: list[tuple[str, str]] = []
    ready = asyncio.Event()

    @client.on("cliententerview")
    async def on_join(event: Event) -> None:
        seen.append(("join", event["clid"]))

    @client.on("*")
    async def on_any(event: Event) -> None:
        seen.append(("any", event.name))

    async def on_ready(c: Client) -> None:
        ready.set()

    task = asyncio.create_task(client.run_forever(on_ready=on_ready))
    await asyncio.wait_for(ready.wait(), timeout=2)
    farm.transports[0].inject(b"notifycliententerview reasonid=0 clid=7")
    await asyncio.sleep(0.05)
    await client.close()
    await asyncio.wait_for(task, timeout=2)
    assert ("join", "7") in seen
    assert ("any", "cliententerview") in seen


async def test_handler_exception_does_not_break_loop() -> None:
    farm = TransportFarm()
    client = make_client(farm)
    seen: list[str] = []
    ready = asyncio.Event()

    @client.on("x")
    async def boom(event: Event) -> None:
        raise RuntimeError("handler bug")

    @client.on("x")
    async def after(event: Event) -> None:
        seen.append(event["n"])

    async def on_ready(c: Client) -> None:
        ready.set()

    task = asyncio.create_task(client.run_forever(on_ready=on_ready))
    await asyncio.wait_for(ready.wait(), timeout=2)
    farm.transports[0].inject(b"notifyx n=1", b"notifyx n=2")
    await asyncio.sleep(0.05)
    await client.close()
    await asyncio.wait_for(task, timeout=2)
    assert seen == ["1", "2"]


async def test_run_forever_reconnects_after_drop() -> None:
    farm = TransportFarm()
    client = make_client(farm)
    readies = 0
    reconnected = asyncio.Event()

    async def on_ready(c: Client) -> None:
        nonlocal readies
        readies += 1
        if readies == 2:
            reconnected.set()

    task = asyncio.create_task(
        client.run_forever(on_ready=on_ready, initial_delay=0.01, max_delay=0.05)
    )
    while farm.connections == 0:
        await asyncio.sleep(0.01)
    farm.transports[0].drop()
    await asyncio.wait_for(reconnected.wait(), timeout=2)
    assert farm.connections == 2
    # the fresh connection re-ran use + servernotifyregister
    assert farm.transports[1].sent[:2] == [b"use sid=1", b"servernotifyregister event=server"]
    await client.close()
    await asyncio.wait_for(task, timeout=2)


async def test_run_forever_backoff_on_connect_failures() -> None:
    farm = TransportFarm(fail_first=3)
    client = make_client(farm)
    ready = asyncio.Event()

    async def on_ready(c: Client) -> None:
        ready.set()

    task = asyncio.create_task(
        client.run_forever(on_ready=on_ready, initial_delay=0.01, max_delay=0.02)
    )
    await asyncio.wait_for(ready.wait(), timeout=2)  # survives 3 scripted failures
    assert farm.connections == 1
    await client.close()
    await asyncio.wait_for(task, timeout=2)


async def test_run_forever_banned_uses_banned_delay() -> None:
    farm = TransportFarm(banned_first=1)
    client = make_client(farm)
    ready = asyncio.Event()

    async def on_ready(c: Client) -> None:
        ready.set()

    task = asyncio.create_task(
        client.run_forever(
            on_ready=on_ready, initial_delay=60.0, max_delay=60.0, banned_delay=0.01
        )
    )
    # The banned error must select banned_delay (0.01s), not initial_delay
    # (60s): ready within the test timeout proves the banned path was taken.
    await asyncio.wait_for(ready.wait(), timeout=5)
    assert farm.connections == 2
    await client.close()
    await asyncio.wait_for(task, timeout=2)


async def test_run_forever_propagates_cancellation() -> None:
    farm = TransportFarm()
    client = make_client(farm)
    ready = asyncio.Event()

    async def on_ready(c: Client) -> None:
        ready.set()

    task = asyncio.create_task(client.run_forever(on_ready=on_ready))
    await asyncio.wait_for(ready.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await client.close()


async def test_close_stops_run_forever_during_backoff() -> None:
    farm = TransportFarm(fail_first=1000)
    client = make_client(farm)
    task = asyncio.create_task(client.run_forever(initial_delay=0.01, max_delay=0.01))
    await asyncio.sleep(0.05)
    await client.close()
    await asyncio.wait_for(task, timeout=2)


async def test_client_delegations_and_props() -> None:
    from tsq.dialect import Dialect

    farm = TransportFarm()
    client = make_client(farm)
    assert client.host == "unused-host"
    await client.start()
    assert client.dialect is Dialect.TS3
    await client.send_keepalive()
    assert b"whoami" in farm.transports[0].sent

    farm.transports[0].inject(b"notifyx a=1")
    event = await client.wait_for_event(timeout=1)
    assert event["a"] == "1"

    farm.transports[0].inject(b"notifyy b=2")
    async for event in client.events():
        assert event["b"] == "2"
        break
    await client.close()


async def test_single_channel_tuple_registration() -> None:
    transport = FakeTransport()
    transport.when(b"", [OK])

    async def factory() -> FakeTransport:
        return transport

    client = Client(
        "h",
        password="p",
        transport_factory=factory,
        keepalive_interval=0,
        register_events=("channel", 0),  # a single pair, not a sequence
    )
    await client.start()
    await client.close()
    assert b"servernotifyregister event=channel id=0" in transport.sent


async def test_connect_function_returns_started_client() -> None:
    from tsq.client import connect

    farm = TransportFarm()
    client = await connect(
        "h", password="p", server_id=1, transport_factory=farm, keepalive_interval=0
    )
    assert client.connected
    assert farm.transports[0].sent[0] == b"use sid=1"
    await client.close()


async def test_connect_function_cleans_up_on_failure() -> None:
    from tsq.client import connect

    farm = TransportFarm(banned_first=1)
    with pytest.raises(QueryError):
        await connect(
            "h", password="p", server_id=1, transport_factory=farm, keepalive_interval=0
        )
    assert farm.transports[0].is_closed


class TestTypedWrappers:
    """Each wrapper renders the exact wire bytes firephenix relies on."""

    async def test_wrapper_wire_bytes(self) -> None:
        transport = FakeTransport()

        async def factory() -> FakeTransport:
            return transport

        client = Client(
            "unused-host",
            password="unused",
            transport_factory=factory,
            keepalive_interval=0,
        )
        await client.start()
        transport.when(b"whoami", [b"client_id=5 client_database_id=2", OK])
        transport.when(b"version", [b"version=3.13.7 build=1 platform=Linux", OK])
        transport.when(b"clientlist", [b"clid=1|clid=2", OK])
        transport.when(b"clientinfo", [b"client_nickname=A client_myteamspeak_id=m1", OK])
        transport.when(b"clientgetdbidfromuid", [b"cluid=u1 cldbid=42", OK])
        transport.when(b"servergroupsbyclientid", [b"name=Guest sgid=8 cldbid=42", OK])
        transport.when(b"channelcreate", [b"cid=60", OK])
        transport.when(b"", [OK])  # catch-all for the no-result commands (first match wins)

        assert (await client.whoami())["client_id"] == "5"
        assert (await client.version())["version"] == "3.13.7"
        assert await client.client_list("uid") == [{"clid": "1"}, {"clid": "2"}]
        assert (await client.client_info(3))["client_myteamspeak_id"] == "m1"
        assert await client.client_dbid_from_uid("u/1=") == "42"
        assert (await client.server_groups_by_client(42))[0]["sgid"] == "8"
        await client.server_group_add_client(13, 42)
        await client.server_group_del_client(13, 42)
        await client.set_client_channel_group(5, 60, 42)
        assert await client.channel_create("My Channel", channel_flag_permanent=1) == "60"
        await client.channel_add_perm(60, "i_channel_needed_join_power", 75)
        await client.channel_client_add_perm(60, 42, "i_channel_join_power", 100)
        await client.channel_move(61, 60)
        await client.send_text_message(5, "Dein Code: 1")
        await client.client_kick(5, reasonmsg="VPN")
        await client.use(1)
        await client.server_notify_register()
        await client.close()

        assert transport.sent == [
            b"whoami",
            b"version",
            b"clientlist -uid",
            b"clientinfo clid=3",
            rb"clientgetdbidfromuid cluid=u\/1=",
            b"servergroupsbyclientid cldbid=42",
            b"servergroupaddclient sgid=13 cldbid=42",
            b"servergroupdelclient sgid=13 cldbid=42",
            b"setclientchannelgroup cgid=5 cid=60 cldbid=42",
            rb"channelcreate channel_name=My\sChannel channel_flag_permanent=1",
            b"channeladdperm cid=60 permsid=i_channel_needed_join_power permvalue=75",
            b"channelclientaddperm cid=60 cldbid=42 permsid=i_channel_join_power permvalue=100",
            b"channelmove cid=61 cpid=60",
            rb"sendtextmessage targetmode=1 target=5 msg=Dein\sCode:\s1",
            rb"clientkick clid=5 reasonid=5 reasonmsg=VPN",
            b"use sid=1",
            b"servernotifyregister event=server",
            b"quit",
        ]
