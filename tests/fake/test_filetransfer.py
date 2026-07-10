import asyncio
from collections.abc import AsyncIterator

import pytest

from tests.fake.fake_transport import FakeTransport
from tsq.client import Client
from tsq.errors import ConnectionClosedError, QueryError
from tsq.filetransfer import FileTransfer

OK = b"error id=0 msg=ok"
UPLOAD_KEY = "u" * 32
DOWNLOAD_KEY = "d" * 32


class FakeFtServer:
    """Speaks the data-channel side: 32-byte ftkey, then raw bytes."""

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}
        self.downloads: dict[str, bytes] = {}
        self.short_read_keys: set[str] = set()
        self.port = 0

    async def __aenter__(self) -> "FakeFtServer":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: object) -> None:
        self._server.close()
        await self._server.wait_closed()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        key = (await reader.readexactly(32)).decode("ascii")
        if key in self.downloads:
            payload = self.downloads[key]
            if key in self.short_read_keys:
                payload = payload[: len(payload) // 2]
            writer.write(payload)
            await writer.drain()
        else:
            self.uploads[key] = await reader.read()
        writer.close()


@pytest.fixture
async def ft_server() -> AsyncIterator[FakeFtServer]:
    async with FakeFtServer() as server:
        yield server


@pytest.fixture
async def setup(ft_server: FakeFtServer) -> AsyncIterator[tuple[FileTransfer, FakeTransport]]:
    transport = FakeTransport()

    async def factory() -> FakeTransport:
        return transport

    client = Client(
        "127.0.0.1", password="unused", transport_factory=factory, keepalive_interval=0
    )
    await client.start()
    ft = FileTransfer(client, port_override=ft_server.port, timeout=5.0)
    yield ft, transport
    await client.close()


async def test_upload_sends_key_and_payload(
    setup: tuple[FileTransfer, FakeTransport], ft_server: FakeFtServer
) -> None:
    ft, transport = setup
    transport.when(
        b"ftinitupload",
        [f"clientftfid=1 serverftfid=9 ftkey={UPLOAD_KEY} port=30033 seekpos=0".encode(), OK],
    )
    payload = bytes(range(256)) * 4
    await ft.upload(payload, "icon_123", cid=0)
    assert ft_server.uploads[UPLOAD_KEY] == payload
    assert transport.sent[0] == (
        rb"ftinitupload clientftfid=1 name=\/icon_123 cid=0 cpw= size=1024 "
        rb"overwrite=1 resume=0"
    )


async def test_upload_honours_seekpos_for_resume(
    setup: tuple[FileTransfer, FakeTransport], ft_server: FakeFtServer
) -> None:
    ft, transport = setup
    transport.when(
        b"ftinitupload",
        [f"clientftfid=1 ftkey={UPLOAD_KEY} port=30033 seekpos=100".encode(), OK],
    )
    payload = bytes(1024)
    await ft.upload(payload, "/f.bin", resume=True)
    assert ft_server.uploads[UPLOAD_KEY] == payload[100:]


async def test_download_returns_exact_payload(
    setup: tuple[FileTransfer, FakeTransport], ft_server: FakeFtServer
) -> None:
    ft, transport = setup
    payload = b"\x00\x01binary|data with spaces\n\r\t" * 16
    ft_server.downloads[DOWNLOAD_KEY] = payload
    transport.when(
        b"ftinitdownload",
        [
            f"clientftfid=1 ftkey={DOWNLOAD_KEY} port=30033 size={len(payload)}".encode(),
            OK,
        ],
    )
    assert await ft.download("f.bin", cid=7) == payload
    assert transport.sent[0] == (
        rb"ftinitdownload clientftfid=1 name=\/f.bin cid=7 cpw= seekpos=0"
    )


async def test_short_download_raises(
    setup: tuple[FileTransfer, FakeTransport], ft_server: FakeFtServer
) -> None:
    ft, transport = setup
    payload = bytes(512)
    ft_server.downloads[DOWNLOAD_KEY] = payload
    ft_server.short_read_keys.add(DOWNLOAD_KEY)
    transport.when(
        b"ftinitdownload",
        [f"clientftfid=1 ftkey={DOWNLOAD_KEY} port=30033 size=512".encode(), OK],
    )
    with pytest.raises(ConnectionClosedError):
        await ft.download("/f.bin")


async def test_init_failure_row_raises_query_error(
    setup: tuple[FileTransfer, FakeTransport],
) -> None:
    ft, transport = setup
    transport.when(
        b"ftinitupload",
        [b"clientftfid=1 status=2050 msg=file\\salready\\sexists", OK],
    )
    with pytest.raises(QueryError) as excinfo:
        await ft.upload(b"x", "/f.bin", overwrite=False)
    assert excinfo.value.error_id == 2050
    assert "already exists" in str(excinfo.value)


async def test_empty_directory_lists_as_empty(
    setup: tuple[FileTransfer, FakeTransport],
) -> None:
    ft, transport = setup
    transport.when(
        b"ftgetfilelist", [b"error id=1281 msg=database\\sempty\\sresult\\sset"]
    )
    assert await ft.file_list(cid=7) == []


async def test_management_commands_render(
    setup: tuple[FileTransfer, FakeTransport],
) -> None:
    ft, transport = setup
    transport.when(b"ftgetfilelist", [b"cid=7 path=\\/ name=f.bin size=3 type=1", OK])
    transport.when(b"", [OK])  # catch-all for the mutations
    rows = await ft.file_list(cid=7)
    assert rows[0]["name"] == "f.bin"
    await ft.delete_file("f.bin", cid=7)
    await ft.create_directory("sub", cid=7)
    await ft.rename_file("/sub/a", "/sub/b", cid=7)
    assert transport.sent[1:] == [
        rb"ftdeletefile cid=7 cpw= name=\/f.bin",
        rb"ftcreatedir cid=7 cpw= dirname=\/sub",
        rb"ftrenamefile cid=7 cpw= oldname=\/sub\/a newname=\/sub\/b",
    ]
