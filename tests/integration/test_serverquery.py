"""Integration suite: identical assertions against real TS3 and TS6 servers."""

from __future__ import annotations

import asyncio

import pytest

import atsq
from tests.integration.conftest import ServerTarget

pytestmark = pytest.mark.integration


class TestSession:
    async def test_connect_use_whoami(self, client: atsq.Client, server: ServerTarget) -> None:
        me = await client.whoami()
        assert me["virtualserver_id"] == "1"
        assert me["client_login_name"] == "serveradmin"
        assert int(me["client_id"]) > 0

    async def test_dialect_detection(self, client: atsq.Client, server: ServerTarget) -> None:
        assert client.dialect is server.expected_dialect
        version = (await client.version())["version"]
        if server.name == "ts3":
            assert version.startswith("3.")
        else:
            assert version.startswith("6.")

    async def test_clientlist_contains_self(self, client: atsq.Client) -> None:
        me = await client.whoami()
        rows = await client.client_list("uid")
        own = [row for row in rows if row["clid"] == me["client_id"]]
        assert own, rows
        assert own[0]["client_type"] == atsq.ClientType.QUERY
        assert "client_unique_identifier" in own[0]  # -uid option honoured

    async def test_client_info_chain(self, client: atsq.Client) -> None:
        """client_info -> client_dbid_from_uid -> server_groups_by_client."""
        me = await client.whoami()
        info = await client.client_info(me["client_id"])
        assert info["client_nickname"] == me["client_nickname"]
        assert "client_myteamspeak_id" in info  # the firephenix identity field

        dbid = await client.client_dbid_from_uid(me["client_unique_identifier"])
        assert dbid == me["client_database_id"]

        groups = await client.server_groups_by_client(dbid)
        assert any(row["name"] == "Admin Server Query" for row in groups)

    async def test_send_keepalive(self, client: atsq.Client) -> None:
        await client.send_keepalive()
        assert (await client.whoami())["virtualserver_id"] == "1"

    async def test_greeting_exposed(self, client: atsq.Client) -> None:
        greeting = client.connection.greeting
        assert greeting[0] == b"TS3"  # literally, on BOTH generations
        assert len(greeting) == 2


class TestEscaping:
    async def test_channel_name_round_trip(
        self, client: atsq.Client, run_token: str
    ) -> None:
        # Space, pipe, slash, backslash. No control chars: both generations
        # sanitize e.g. tabs OUT of channel names server-side (verified),
        # so those are covered by the text-message round-trip instead.
        name = f"atsq it |{run_token}| a/b\\c end"
        cid = await client.channel_create(name, channel_flag_permanent=1)
        rows = await client.exec("channellist")
        names = {row["cid"]: row["channel_name"] for row in rows}
        assert names[cid] == name

    async def test_error_msg_unescaped(self, client: atsq.Client) -> None:
        with pytest.raises(atsq.QueryError) as excinfo:
            await client.exec("thisisnotacommand")
        assert excinfo.value.error_id == 256
        assert "command not found" in str(excinfo.value)


class TestChannelWrappers:
    async def test_channel_perm_and_move_wrappers(
        self, client: atsq.Client, run_token: str
    ) -> None:
        parent = await client.channel_create(
            f"atsq wrap parent {run_token}", channel_flag_permanent=1
        )
        child = await client.channel_create(
            f"atsq wrap child {run_token}", channel_flag_permanent=1
        )
        await client.channel_add_perm(parent, "i_channel_needed_join_power", 42)
        perms = await client.exec("channelpermlist", "permsid", cid=parent)
        assert {
            key: row[key]
            for row in perms
            if row["permsid"] == "i_channel_needed_join_power"
            for key in ("permsid", "permvalue")
        } == {"permsid": "i_channel_needed_join_power", "permvalue": "42"}
        await client.channel_move(child, parent)
        rows = await client.exec("channellist")
        moved = next(row for row in rows if row["cid"] == child)
        assert moved["pid"] == parent


class TestQueryClientContracts:
    """Live contracts for the group/kick wrappers.

    A query client's database id cannot be added to groups or kicked -
    both generations answer with the same error ids (512 invalid clientID,
    516 invalid client type). Pinning these documents the live behaviour
    the firephenix bot relies on error-handling-wise.
    """

    async def test_group_wrappers_reject_query_dbid(self, client: atsq.Client) -> None:
        me = await client.whoami()
        dbid = me["client_database_id"]
        # (expected error ids taken from the recorded probe transcripts)
        for call, expected in (
            (client.server_group_add_client(7, dbid), 512),
            (client.server_group_del_client(7, dbid), 2563),
            (client.set_client_channel_group(5, 1, dbid), 512),
            (client.channel_client_add_perm(1, dbid, "i_channel_join_power", 1), 512),
        ):
            with pytest.raises(atsq.QueryError) as excinfo:
                await call
            assert excinfo.value.error_id == expected, str(excinfo.value)

    async def test_client_kick_rejects_query_client(self, client: atsq.Client) -> None:
        me = await client.whoami()
        with pytest.raises(atsq.QueryError) as excinfo:
            await client.client_kick(me["client_id"], reasonmsg="atsq test")
        assert excinfo.value.error_id == 516


class TestTransportLifecycle:
    async def test_close_idempotent_and_io_after_close_raises(
        self, server: ServerTarget
    ) -> None:
        from atsq.transport import SshTransport

        transport = await SshTransport.connect(
            server.host, server.port, username="serveradmin", password=server.password
        )
        assert not transport.is_closed
        assert await transport.read_line() == b"TS3"
        await transport.read_line()  # welcome line - drain the whole greeting
        await transport.close()
        await transport.close()  # idempotent
        assert transport.is_closed
        with pytest.raises(atsq.ConnectionClosedError):
            await transport.send_line(b"whoami")
        with pytest.raises(atsq.ConnectionClosedError):
            await transport.read_line()  # buffer empty -> closed error


class TestErrors:
    async def test_invalid_server_id(self, server: ServerTarget) -> None:
        c = await atsq.connect(server.host, server.port, password=server.password)
        try:
            with pytest.raises(atsq.QueryError):
                await c.use(999)
        finally:
            await c.close()

    async def test_wrong_password_fails_at_ssh_layer(self, server: ServerTarget) -> None:
        with pytest.raises(Exception):  # noqa: B017 - asyncssh auth error type
            await atsq.connect(server.host, server.port, password="definitely-wrong")

    async def test_connection_usable_after_query_error(self, client: atsq.Client) -> None:
        with pytest.raises(atsq.QueryError):
            await client.exec("thisisnotacommand")
        assert (await client.whoami())["virtualserver_id"] == "1"


class TestEvents:
    async def test_join_and_leave_events_from_second_client(
        self, client: atsq.Client, server: ServerTarget
    ) -> None:
        await client.server_notify_register("server")
        second = await atsq.connect(
            server.host, server.port, password=server.password, server_id=1
        )
        try:
            enter = await client.wait_for_event(timeout=10)
            assert enter.name == "cliententerview"
            assert enter["reasonid"] == "0"
            assert enter["client_type"] == "1"
            joined_clid = enter["clid"]
        finally:
            # close() sends `quit` - required on TS6, where a bare SSH
            # disconnect produces no leftview at all (docs/dialects.md).
            await second.close()
        left = await client.wait_for_event(timeout=10)
        assert left.name == "clientleftview"
        assert left["reasonid"] == atsq.ReasonId.QUIT
        assert left["reasonid"] in atsq.LEAVE_REASONS
        assert left["clid"] == joined_clid

    async def test_text_message_event(
        self, client: atsq.Client, server: ServerTarget
    ) -> None:
        await client.server_notify_register("textserver")
        second = await atsq.connect(
            server.host, server.port, password=server.password, server_id=1
        )
        try:
            payload = "atsq it |pipe| a/b\\c\tend"
            await second.send_text_message(0, payload, targetmode=3)
            while True:
                event = await client.wait_for_event(timeout=10)
                if event.name == "textmessage":
                    break
            assert event["msg"] == payload
        finally:
            await second.close()

    async def test_events_iterator(self, client: atsq.Client, server: ServerTarget) -> None:
        await client.server_notify_register("textserver")
        await client.send_text_message(0, "iterate me", targetmode=3)
        async for event in client.events():
            if event.name == "textmessage":
                assert event["msg"] == "iterate me"
                break

    async def test_run_forever_dispatch_and_live_reconnect(
        self, server: ServerTarget, run_token: str
    ) -> None:
        """The full bot loop against a live server, including a reconnect.

        `exec("quit")` makes the server close the connection - run_forever
        must recover, re-run use/servernotifyregister, and keep dispatching.
        """
        client = atsq.Client(
            server.host,
            server.port,
            password=server.password,
            server_id=1,
            register_events="server",
            nickname=f"aatsq rf {run_token}",
        )
        readies = 0
        reconnected = asyncio.Event()
        joined = asyncio.Event()

        @client.on("cliententerview")
        async def on_join(event: atsq.Event) -> None:
            joined.set()

        async def on_ready(c: atsq.Client) -> None:
            nonlocal readies
            readies += 1
            if readies == 2:
                reconnected.set()

        task = asyncio.create_task(
            client.run_forever(on_ready=on_ready, initial_delay=0.5, max_delay=2.0)
        )
        try:
            for _ in range(100):
                if readies >= 1:
                    break
                await asyncio.sleep(0.1)
            assert readies >= 1, "run_forever never became ready"

            # a second client joining reaches the @on handler
            second = await atsq.connect(
                server.host, server.port, password=server.password, server_id=1
            )
            await second.close()
            await asyncio.wait_for(joined.wait(), timeout=10)

            # server-side disconnect -> automatic reconnect
            import contextlib

            with contextlib.suppress(atsq.TsqError):
                await client.exec("quit")
            await asyncio.wait_for(reconnected.wait(), timeout=15)
            assert (await client.whoami())["virtualserver_id"] == "1"
        finally:
            await client.close()
            await asyncio.wait_for(task, timeout=5)

    async def test_events_flow_while_commands_run(
        self, client: atsq.Client, server: ServerTarget
    ) -> None:
        """Interleaving: events route correctly while exec() traffic runs."""
        await client.server_notify_register("server")

        async def churn() -> None:
            for _ in range(20):
                await client.whoami()

        second = None
        churn_task = asyncio.create_task(churn())
        try:
            second = await atsq.connect(
                server.host, server.port, password=server.password, server_id=1
            )
            enter = await client.wait_for_event(timeout=10)
            assert enter.name == "cliententerview"
        finally:
            if second is not None:
                await second.close()
            await churn_task  # must complete without desync/timeouts


class TestClientOptions:
    async def test_nickname_and_multi_event_registration(
        self, server: ServerTarget, run_token: str
    ) -> None:
        nick = f"atsq {run_token}"
        c = await atsq.connect(
            server.host,
            server.port,
            password=server.password,
            server_id=1,
            nickname=nick,
            register_events=atsq.ALL_EVENTS,
        )
        try:
            assert (await c.whoami())["client_nickname"] == nick
            # textserver registration active: our own message comes back
            await c.send_text_message(0, "hello", targetmode=3)
            event = await c.wait_for_event(timeout=10)
            assert event.name == "textmessage"
            assert event["msg"] == "hello"
        finally:
            await c.close()

    async def test_select_server_by_voice_port(self, server: ServerTarget) -> None:
        c = await atsq.connect(
            server.host, server.port, password=server.password, server_port=9987
        )
        try:
            assert (await c.whoami())["virtualserver_id"] == "1"
        finally:
            await c.close()


class TestSnapshots:
    async def test_snapshot_create_and_deploy_round_trip(
        self, client: atsq.Client
    ) -> None:
        """Snapshots need no special payload handling - plain exec works."""
        rows = await client.exec("serversnapshotcreate")
        snapshot = rows[0]
        assert snapshot["version"] == "3"
        assert len(snapshot["data"]) > 100
        await client.exec(
            "serversnapshotdeploy", version=snapshot["version"], data=snapshot["data"]
        )
        # Deploy recreates the virtual server and deselects the session
        # (whoami reports virtualserver_id=0) on both generations - callers
        # must re-`use` afterwards.
        assert (await client.whoami())["virtualserver_id"] == "0"
        await client.use(1)
        assert (await client.whoami())["virtualserver_id"] == "1"


class TestPipelining:
    async def test_piped_permission_blocks_apply_in_one_command(
        self, client: atsq.Client, run_token: str
    ) -> None:
        cid = await client.channel_create(
            f"atsq pipe {run_token}", channel_flag_permanent=1
        )
        await client.exec(
            "channeladdperm",
            cid=cid,
            blocks=[
                {"permsid": "i_channel_needed_join_power", "permvalue": 75},
                {"permsid": "i_channel_needed_subscribe_power", "permvalue": 60},
            ],
        )
        rows = await client.exec("channelpermlist", "permsid", cid=cid)
        perms = {row["permsid"]: row["permvalue"] for row in rows}
        assert perms["i_channel_needed_join_power"] == "75"
        assert perms["i_channel_needed_subscribe_power"] == "60"


class TestFileTransfer:
    @pytest.fixture
    def ft(self, client: atsq.Client, server: ServerTarget) -> atsq.FileTransfer:
        if server.ft_port is None:
            pytest.skip(f"{server.name} file-transfer port not configured")
        return atsq.FileTransfer(client, port_override=server.ft_port, timeout=20.0)

    async def test_upload_list_download_delete_round_trip(
        self, client: atsq.Client, ft: atsq.FileTransfer, run_token: str
    ) -> None:
        cid = int(await client.channel_create(
            f"atsq ft {run_token}", channel_flag_permanent=1
        ))
        payload = bytes(range(256)) * 64  # 16 KiB covering every byte value
        await ft.upload(payload, "/atsq-test.bin", cid=cid)

        files = await ft.file_list(cid=cid)
        names = {row["name"]: row for row in files}
        assert "atsq-test.bin" in names
        assert names["atsq-test.bin"]["size"] == str(len(payload))

        assert await ft.download("/atsq-test.bin", cid=cid) == payload

        info = await ft.file_info("/atsq-test.bin", cid=cid)
        assert info["size"] == str(len(payload))

        await ft.delete_file("/atsq-test.bin", cid=cid)
        remaining = await ft.file_list(cid=cid)
        assert all(row["name"] != "atsq-test.bin" for row in remaining)

    async def test_directory_create_and_rename(
        self, client: atsq.Client, ft: atsq.FileTransfer, run_token: str
    ) -> None:
        cid = int(await client.channel_create(
            f"atsq ftdir {run_token}", channel_flag_permanent=1
        ))
        await ft.create_directory("/sub", cid=cid)
        await ft.upload(b"hello atsq", "/sub/a.txt", cid=cid)
        await ft.rename_file("/sub/a.txt", "/sub/b.txt", cid=cid)
        rows = await ft.file_list(cid=cid, path="/sub")
        assert [row["name"] for row in rows] == ["b.txt"]
        assert await ft.download("/sub/b.txt", cid=cid) == b"hello atsq"

    async def test_icon_round_trip(self, ft: atsq.FileTransfer) -> None:
        import zlib

        icon = b"\x89PNG aatsq fake icon " + bytes(range(64))
        icon_id = await ft.upload_icon(icon)
        assert icon_id == zlib.crc32(icon)
        assert await ft.download_icon(icon_id) == icon
        # icons are addressed as /icon_<id> but the server files them under
        # the /icons directory of cid 0 (both generations)
        listed = await ft.file_list(cid=0, path="/icons")
        assert any(row["name"] == f"icon_{icon_id}" for row in listed)
        await ft.delete_icon(icon_id)
        remaining = await ft.file_list(cid=0, path="/icons")
        assert all(row["name"] != f"icon_{icon_id}" for row in remaining)

    async def test_overwrite_false_surfaces_conflict(
        self, client: atsq.Client, ft: atsq.FileTransfer, run_token: str
    ) -> None:
        cid = int(await client.channel_create(
            f"atsq ftow {run_token}", channel_flag_permanent=1
        ))
        await ft.upload(b"one", "/dup.bin", cid=cid)
        with pytest.raises(atsq.QueryError):
            await ft.upload(b"two", "/dup.bin", cid=cid, overwrite=False)
        # the original file is untouched and the connection stays usable
        assert await ft.download("/dup.bin", cid=cid) == b"one"


class TestFlood:
    async def test_rapid_commands_no_desync(self, client: atsq.Client) -> None:
        """30 back-to-back commands: no hang, no protocol desync.

        The test tolerates FloodError (server policy) but requires the
        connection to stay coherent and usable afterwards.
        """
        flood_errors = 0
        for _ in range(30):
            try:
                me = await client.whoami()
                assert me["virtualserver_id"] == "1"
            except atsq.FloodError:
                flood_errors += 1
                await asyncio.sleep(1)
        assert (await client.whoami())["virtualserver_id"] == "1"
        assert flood_errors <= 5, "allowlisted client should be mostly exempt"
