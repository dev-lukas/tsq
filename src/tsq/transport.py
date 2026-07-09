"""Transports: how raw protocol lines reach the server.

The only real transport is SSH (the single line-protocol interface TS6 still
offers; opt-in on TS3 ≥ 3.3). The :class:`Transport` protocol is the seam
used by the test suite to drive :class:`~tsq.connection.RawConnection`
without any network.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

import asyncssh

from tsq.errors import ConnectionClosedError

if TYPE_CHECKING:
    from types import TracebackType

__all__ = ["SshTransport", "Transport"]

#: ServerQuery line terminator (newline first, then carriage return).
LINE_TERMINATOR = b"\n\r"

_READ_CHUNK = 4096


@runtime_checkable
class Transport(Protocol):
    """One established, bidirectional line stream to a query interface."""

    async def read_line(self) -> bytes:
        """Return the next line without its terminator. Blocks until one
        arrives; raises :class:`ConnectionClosedError` on EOF."""
        ...

    async def send_line(self, data: bytes) -> None:
        """Send one line; the terminator is appended here."""
        ...

    async def close(self) -> None: ...

    @property
    def is_closed(self) -> bool: ...


class SshTransport:
    """SSH query transport using :mod:`asyncssh`.

    Authentication happens at the SSH layer: the ServerQuery login name and
    password double as SSH credentials (there is no in-band ``login``
    command on the SSH interface). After auth a shell session carries the
    plain ServerQuery line protocol.
    """

    def __init__(
        self,
        conn: asyncssh.SSHClientConnection,
        stdin: asyncssh.SSHWriter[bytes],
        stdout: asyncssh.SSHReader[bytes],
    ) -> None:
        self._conn = conn
        self._stdin = stdin
        self._stdout = stdout
        self._buffer = b""
        self._closed = False

    @classmethod
    async def connect(
        cls,
        host: str,
        port: int = 10022,
        *,
        username: str,
        password: str,
        known_hosts: object = None,
        connect_timeout: float = 10.0,
        term_type: str | None = None,
        **ssh_options: object,
    ) -> Self:
        """Open an SSH query session.

        ``known_hosts=None`` (the default) disables host-key verification -
        TeamSpeak servers generate ephemeral query host keys. Pass an
        asyncssh ``known_hosts`` value to pin the key in production.
        Extra ``ssh_options`` go to :func:`asyncssh.connect` verbatim
        (e.g. ``kex_algs``/``encryption_algs`` for legacy TS3 sshd builds).
        """
        conn = await asyncssh.connect(
            host,
            port=port,
            username=username,
            password=password,
            known_hosts=known_hosts,
            connect_timeout=connect_timeout,
            **ssh_options,
        )
        try:
            stdin, stdout, _stderr = await conn.open_session(
                term_type=term_type, encoding=None
            )
        except BaseException:
            conn.abort()
            raise
        return cls(conn, stdin, stdout)

    async def read_line(self) -> bytes:
        """Read up to the next ``\\n``; surrounding ``\\r`` is stripped.

        The wire terminator is ``\\n\\r`` (newline *first*), so the ``\\r``
        of line *n* arrives as the first byte before line *n+1*. Splitting
        on ``\\n`` and stripping stray CR bytes handles both that framing
        and servers that terminate with a bare ``\\n``. Literal CR inside
        values cannot occur - it is escaped as ``\\r`` on the wire.
        """
        while True:
            newline = self._buffer.find(b"\n")
            if newline != -1:
                line = self._buffer[:newline].strip(b"\r")
                self._buffer = self._buffer[newline + 1 :]
                return line
            if self._closed:
                raise ConnectionClosedError("transport closed")
            try:
                chunk = await self._stdout.read(_READ_CHUNK)
            except asyncssh.ConnectionLost as err:
                self._closed = True
                raise ConnectionClosedError(str(err)) from err
            if not chunk:
                self._closed = True
                raise ConnectionClosedError("connection closed by server")
            self._buffer += chunk

    async def send_line(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionClosedError("transport closed")
        try:
            self._stdin.write(data + LINE_TERMINATOR)
            await self._stdin.drain()
        except (asyncssh.ConnectionLost, BrokenPipeError, ConnectionError) as err:
            self._closed = True
            raise ConnectionClosedError(str(err)) from err

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._conn.close()
        with contextlib.suppress(Exception):  # closing must never raise
            await self._conn.wait_closed()

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
