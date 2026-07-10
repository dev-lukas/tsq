"""TeamSpeak file transfer (icons, avatars, channel files).

File transfer is a two-step protocol: an ``ftinitupload``/``ftinitdownload``
query command hands out a one-time ``ftkey`` plus the file-transfer port
(default 30033), then a plain TCP connection to that port sends the raw key
followed by the file bytes (upload) or receives exactly ``size`` bytes
(download). No TLS is involved on either generation.

Example::

    ft = tsq.FileTransfer(client)
    await ft.upload(icon_bytes, "/icon_3735928559")          # cid=0 = icons
    data = await ft.download("/avatar_...", cid=0)
    rows = await ft.file_list(cid=42, path="/")
"""

from __future__ import annotations

import asyncio
import itertools
import zlib
from typing import TYPE_CHECKING, Any

from tsq.errors import ConnectionClosedError, QueryError, QueryTimeoutError

if TYPE_CHECKING:
    from tsq.client import Client

__all__ = ["FileTransfer"]

#: ``ftgetfilelist`` on an empty directory answers with this error id.
_EMPTY_RESULT_SET = 1281


class FileTransfer:
    """File operations bound to a connected :class:`~tsq.client.Client`.

    The data channel connects to ``host`` (default: the client's host) and
    the port advertised by the server. Pass ``port_override`` when the
    advertised port is not reachable as-is - e.g. docker port mappings,
    where the container-internal 30033 maps to an ephemeral host port.
    """

    def __init__(
        self,
        client: Client,
        *,
        host: str | None = None,
        port_override: int | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._host = host or client.host
        self._port_override = port_override
        self._timeout = timeout
        self._ftfid = itertools.count(1)

    # -- transfers -----------------------------------------------------------

    async def upload(
        self,
        data: bytes,
        name: str,
        cid: int | str = 0,
        *,
        channel_password: str = "",
        overwrite: bool = True,
        resume: bool = False,
    ) -> None:
        """Upload *data* as file *name* into channel *cid* (0 = server files/icons)."""
        row = await self._init(
            "ftinitupload",
            name=_normalize(name),
            cid=cid,
            cpw=channel_password,
            size=len(data),
            overwrite=overwrite,
            resume=resume,
        )
        seekpos = int(row.get("seekpos", "0"))
        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await self._open_data_channel(row)
                try:
                    writer.write(row["ftkey"].encode("ascii"))
                    writer.write(data[seekpos:])
                    await writer.drain()
                    if writer.can_write_eof():
                        writer.write_eof()
                    # The server closes the socket once it accepted all bytes.
                    await reader.read()
                finally:
                    writer.close()
                    await writer.wait_closed()
        except TimeoutError as err:
            raise QueryTimeoutError(f"file upload {name!r} timed out") from err

    async def download(
        self,
        name: str,
        cid: int | str = 0,
        *,
        channel_password: str = "",
    ) -> bytes:
        """Download file *name* from channel *cid* and return its bytes."""
        row = await self._init(
            "ftinitdownload",
            name=_normalize(name),
            cid=cid,
            cpw=channel_password,
            seekpos=0,
        )
        size = int(row["size"])
        try:
            async with asyncio.timeout(self._timeout):
                reader, writer = await self._open_data_channel(row)
                try:
                    writer.write(row["ftkey"].encode("ascii"))
                    await writer.drain()
                    try:
                        return await reader.readexactly(size)
                    except asyncio.IncompleteReadError as err:
                        raise ConnectionClosedError(
                            f"file download {name!r} ended after "
                            f"{len(err.partial)}/{size} bytes"
                        ) from err
                finally:
                    writer.close()
                    await writer.wait_closed()
        except TimeoutError as err:
            raise QueryTimeoutError(f"file download {name!r} timed out") from err

    # -- icons ---------------------------------------------------------------
    # Server icons live as ``/icon_<crc32>`` files in cid 0; the crc32 of the
    # image bytes doubles as the icon id referenced by ``i_icon_id`` perms.

    async def upload_icon(self, data: bytes) -> int:
        """Upload an icon and return its icon id (crc32 of the bytes)."""
        icon_id = zlib.crc32(data)
        await self.upload(data, f"/icon_{icon_id}", cid=0)
        return icon_id

    async def download_icon(self, icon_id: int) -> bytes:
        return await self.download(f"/icon_{icon_id}", cid=0)

    async def delete_icon(self, icon_id: int) -> None:
        await self.delete_file(f"/icon_{icon_id}", cid=0)

    # -- listing / management -------------------------------------------------

    async def file_list(
        self, cid: int | str = 0, path: str = "/", channel_password: str = ""
    ) -> list[dict[str, str]]:
        try:
            return await self._client.exec(
                "ftgetfilelist", cid=cid, cpw=channel_password, path=path
            )
        except QueryError as err:
            # Both generations report an empty directory as error 1281
            # ("database empty result set") instead of zero rows.
            if err.error_id == _EMPTY_RESULT_SET:
                return []
            raise

    async def file_info(
        self, name: str, cid: int | str = 0, channel_password: str = ""
    ) -> dict[str, str]:
        rows = await self._client.exec(
            "ftgetfileinfo", cid=cid, cpw=channel_password, name=_normalize(name)
        )
        return rows[0]

    async def delete_file(
        self, name: str, cid: int | str = 0, channel_password: str = ""
    ) -> None:
        await self._client.exec(
            "ftdeletefile", cid=cid, cpw=channel_password, name=_normalize(name)
        )

    async def create_directory(
        self, dirname: str, cid: int | str = 0, channel_password: str = ""
    ) -> None:
        await self._client.exec(
            "ftcreatedir", cid=cid, cpw=channel_password, dirname=_normalize(dirname)
        )

    async def rename_file(
        self,
        oldname: str,
        newname: str,
        cid: int | str = 0,
        channel_password: str = "",
    ) -> None:
        await self._client.exec(
            "ftrenamefile",
            cid=cid,
            cpw=channel_password,
            oldname=_normalize(oldname),
            newname=_normalize(newname),
        )

    # -- internals -------------------------------------------------------------

    async def _init(self, cmd: str, **params: Any) -> dict[str, str]:
        rows = await self._client.exec(cmd, clientftfid=next(self._ftfid), **params)
        row = rows[0] if rows else {}
        if "ftkey" not in row:
            # Init failures arrive as status/msg in the row, not as an error line.
            raise QueryError(
                int(row.get("status", "-1")), row.get("msg", f"{cmd} failed"), row
            )
        return row

    async def _open_data_channel(
        self, row: dict[str, str]
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        port = self._port_override or int(row["port"])
        return await asyncio.open_connection(self._host, port)


def _normalize(name: str) -> str:
    return name if name.startswith("/") else f"/{name}"
