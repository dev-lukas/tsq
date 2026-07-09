"""In-memory Transport implementation for connection-logic tests (no I/O)."""

from __future__ import annotations

import asyncio

from tsq.errors import ConnectionClosedError

TS3_GREETING = [
    b"TS3",
    (
        b'Welcome to the TeamSpeak 3 ServerQuery interface, type "help" '
        b'for a list of commands and "help <command>" for information on a '
        b"specific command."
    ),
]

_EOF = object()


class FakeTransport:
    """Scriptable fake server endpoint.

    - ``when(prefix, [lines...])`` registers a canned response for the next
      command whose rendered line starts with *prefix* (consumed FIFO per
      prefix; the last registered list repeats if requests keep coming).
    - ``inject(*lines)`` pushes unsolicited lines (events) to the reader.
    - ``drop()`` simulates the server closing the connection.
    - ``sent`` records every line the client sent.
    """

    def __init__(self, greeting: list[bytes] | None = None) -> None:
        self._read_queue: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[bytes] = []
        self._responders: list[tuple[bytes, list[list[bytes]]]] = []
        self._closed = False
        self.auto_respond = True
        for line in TS3_GREETING if greeting is None else greeting:
            self._read_queue.put_nowait(line)

    def when(self, prefix: bytes, *replies: list[bytes]) -> None:
        for existing_prefix, queue in self._responders:
            if existing_prefix == prefix:
                queue.extend(replies)
                return
        self._responders.append((prefix, list(replies)))

    def inject(self, *lines: bytes) -> None:
        for line in lines:
            self._read_queue.put_nowait(line)

    def drop(self) -> None:
        self._closed = True
        self._read_queue.put_nowait(_EOF)

    async def read_line(self) -> bytes:
        item = await self._read_queue.get()
        if item is _EOF:
            # Keep signalling EOF for any further reads.
            self._read_queue.put_nowait(_EOF)
            raise ConnectionClosedError("fake connection dropped")
        assert isinstance(item, bytes)
        return item

    async def send_line(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionClosedError("fake transport closed")
        self.sent.append(data)
        if not self.auto_respond:
            return
        for prefix, replies in self._responders:
            if data.startswith(prefix):
                if replies:
                    reply = replies.pop(0) if len(replies) > 1 else replies[0]
                    self.inject(*reply)
                return

    async def close(self) -> None:
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed
