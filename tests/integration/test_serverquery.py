"""Integration suite: identical assertions against real TS3 and TS6 servers."""

from __future__ import annotations

import asyncio

import pytest

import tsq
from tests.integration.conftest import ServerTarget

pytestmark = pytest.mark.integration


class TestSession:
    async def test_connect_use_whoami(self, client: tsq.Client, server: ServerTarget) -> None:
        me = await client.whoami()
        assert me["virtualserver_id"] == "1"
        assert me["client_login_name"] == "serveradmin"
        assert int(me["client_id"]) > 0

    async def test_dialect_detection(self, client: tsq.Client, server: ServerTarget) -> None:
        assert client.dialect is server.expected_dialect
        version = (await client.version())["version"]
        if server.name == "ts3":
            assert version.startswith("3.")
        else:
            assert version.startswith("6.")

    async def test_clientlist_contains_self(self, client: tsq.Client) -> None:
        me = await client.whoami()
        rows = await client.client_list("uid")
        own = [row for row in rows if row["clid"] == me["client_id"]]
        assert own, rows
        assert own[0]["client_type"] == "1"  # query client
        assert "client_unique_identifier" in own[0]  # -uid option honoured


class TestEscaping:
    async def test_channel_name_round_trip(
        self, client: tsq.Client, run_token: str
    ) -> None:
        # Space, pipe, slash, backslash. No control chars: both generations
        # sanitize e.g. tabs OUT of channel names server-side (verified),
        # so those are covered by the text-message round-trip instead.
        name = f"tsq it |{run_token}| a/b\\c end"
        cid = await client.channel_create(name, channel_flag_permanent=1)
        rows = await client.exec("channellist")
        names = {row["cid"]: row["channel_name"] for row in rows}
        assert names[cid] == name

    async def test_error_msg_unescaped(self, client: tsq.Client) -> None:
        with pytest.raises(tsq.QueryError) as excinfo:
            await client.exec("thisisnotacommand")
        assert excinfo.value.error_id == 256
        assert "command not found" in str(excinfo.value)


class TestErrors:
    async def test_invalid_server_id(self, server: ServerTarget) -> None:
        c = await tsq.connect(server.host, server.port, password=server.password)
        try:
            with pytest.raises(tsq.QueryError):
                await c.use(999)
        finally:
            await c.close()

    async def test_wrong_password_fails_at_ssh_layer(self, server: ServerTarget) -> None:
        with pytest.raises(Exception):  # noqa: B017 - asyncssh auth error type
            await tsq.connect(server.host, server.port, password="definitely-wrong")

    async def test_connection_usable_after_query_error(self, client: tsq.Client) -> None:
        with pytest.raises(tsq.QueryError):
            await client.exec("thisisnotacommand")
        assert (await client.whoami())["virtualserver_id"] == "1"


class TestEvents:
    async def test_join_and_leave_events_from_second_client(
        self, client: tsq.Client, server: ServerTarget
    ) -> None:
        await client.server_notify_register("server")
        second = await tsq.connect(
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
        assert left["reasonid"] == "8"
        assert left["clid"] == joined_clid

    async def test_text_message_event(
        self, client: tsq.Client, server: ServerTarget
    ) -> None:
        await client.server_notify_register("textserver")
        second = await tsq.connect(
            server.host, server.port, password=server.password, server_id=1
        )
        try:
            payload = "tsq it |pipe| a/b\\c\tend"
            await second.send_text_message(0, payload, targetmode=3)
            while True:
                event = await client.wait_for_event(timeout=10)
                if event.name == "textmessage":
                    break
            assert event["msg"] == payload
        finally:
            await second.close()

    async def test_events_flow_while_commands_run(
        self, client: tsq.Client, server: ServerTarget
    ) -> None:
        """Interleaving: events route correctly while exec() traffic runs."""
        await client.server_notify_register("server")

        async def churn() -> None:
            for _ in range(20):
                await client.whoami()

        second = None
        churn_task = asyncio.create_task(churn())
        try:
            second = await tsq.connect(
                server.host, server.port, password=server.password, server_id=1
            )
            enter = await client.wait_for_event(timeout=10)
            assert enter.name == "cliententerview"
        finally:
            if second is not None:
                await second.close()
            await churn_task  # must complete without desync/timeouts


class TestPipelining:
    async def test_piped_permission_blocks_apply_in_one_command(
        self, client: tsq.Client, run_token: str
    ) -> None:
        cid = await client.channel_create(
            f"tsq pipe {run_token}", channel_flag_permanent=1
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
    def ft(self, client: tsq.Client, server: ServerTarget) -> tsq.FileTransfer:
        if server.ft_port is None:
            pytest.skip(f"{server.name} file-transfer port not configured")
        return tsq.FileTransfer(client, port_override=server.ft_port, timeout=20.0)

    async def test_upload_list_download_delete_round_trip(
        self, client: tsq.Client, ft: tsq.FileTransfer, run_token: str
    ) -> None:
        cid = int(await client.channel_create(
            f"tsq ft {run_token}", channel_flag_permanent=1
        ))
        payload = bytes(range(256)) * 64  # 16 KiB covering every byte value
        await ft.upload(payload, "/tsq-test.bin", cid=cid)

        files = await ft.file_list(cid=cid)
        names = {row["name"]: row for row in files}
        assert "tsq-test.bin" in names
        assert names["tsq-test.bin"]["size"] == str(len(payload))

        assert await ft.download("/tsq-test.bin", cid=cid) == payload

        info = await ft.file_info("/tsq-test.bin", cid=cid)
        assert info["size"] == str(len(payload))

        await ft.delete_file("/tsq-test.bin", cid=cid)
        remaining = await ft.file_list(cid=cid)
        assert all(row["name"] != "tsq-test.bin" for row in remaining)

    async def test_directory_create_and_rename(
        self, client: tsq.Client, ft: tsq.FileTransfer, run_token: str
    ) -> None:
        cid = int(await client.channel_create(
            f"tsq ftdir {run_token}", channel_flag_permanent=1
        ))
        await ft.create_directory("/sub", cid=cid)
        await ft.upload(b"hello tsq", "/sub/a.txt", cid=cid)
        await ft.rename_file("/sub/a.txt", "/sub/b.txt", cid=cid)
        rows = await ft.file_list(cid=cid, path="/sub")
        assert [row["name"] for row in rows] == ["b.txt"]
        assert await ft.download("/sub/b.txt", cid=cid) == b"hello tsq"

    async def test_overwrite_false_surfaces_conflict(
        self, client: tsq.Client, ft: tsq.FileTransfer, run_token: str
    ) -> None:
        cid = int(await client.channel_create(
            f"tsq ftow {run_token}", channel_flag_permanent=1
        ))
        await ft.upload(b"one", "/dup.bin", cid=cid)
        with pytest.raises(tsq.QueryError):
            await ft.upload(b"two", "/dup.bin", cid=cid, overwrite=False)
        # the original file is untouched and the connection stays usable
        assert await ft.download("/dup.bin", cid=cid) == b"one"


class TestFlood:
    async def test_rapid_commands_no_desync(self, client: tsq.Client) -> None:
        """30 back-to-back commands: no hang, no protocol desync.

        The test tolerates FloodError (server policy) but requires the
        connection to stay coherent and usable afterwards.
        """
        flood_errors = 0
        for _ in range(30):
            try:
                me = await client.whoami()
                assert me["virtualserver_id"] == "1"
            except tsq.FloodError:
                flood_errors += 1
                await asyncio.sleep(1)
        assert (await client.whoami())["virtualserver_id"] == "1"
        assert flood_errors <= 5, "allowlisted client should be mostly exempt"
