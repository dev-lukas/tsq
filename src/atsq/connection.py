"""RawConnection: the ServerQuery session state machine.

Owns one :class:`~atsq.transport.Transport` and runs a background receive
loop that separates the two interleaved streams on the wire:

- command responses (0+ data lines closed by an ``error id=...`` line),
- asynchronous ``notify*`` events.

ServerQuery allows exactly **one in-flight command per connection**; the
command lock enforces that, so concurrent ``exec()`` callers serialize
transparently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Self

from atsq.dialect import QUIRKS, Dialect, sniff_dialect
from atsq.errors import (
    ConnectionClosedError,
    FloodError,
    QueryError,
    QueryTimeoutError,
)
from atsq.events import Event
from atsq.protocol import (
    ErrorLine,
    is_error_line,
    is_event_line,
    parse_data_block,
    parse_error_line,
    render_command,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping
    from types import TracebackType

    from atsq.transport import Transport

__all__ = ["RawConnection"]

LOG = logging.getLogger(__name__)

#: Default seconds a command may take before the connection is torn down.
DEFAULT_COMMAND_TIMEOUT = 10.0
#: Default idle seconds before a keepalive is sent (server kicks at ~300s).
DEFAULT_KEEPALIVE_INTERVAL = 240.0

_QUEUE_CLOSED = object()


class RawConnection:
    """One established query session over a transport.

    Use :meth:`start` (or the async context manager) before issuing
    commands; it consumes the server greeting and spawns the receive and
    keepalive tasks.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        dialect: Dialect = Dialect.AUTO,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
        event_queue_size: int = 1024,
        flood_retries: int = 2,
    ) -> None:
        self._transport = transport
        self._dialect = dialect
        self._command_timeout = command_timeout
        self._keepalive_interval = keepalive_interval
        self._flood_retries = flood_retries

        self._cmd_lock = asyncio.Lock()
        self._pending: asyncio.Future[tuple[list[bytes], ErrorLine]] | None = None
        self._data_buffer: list[bytes] = []
        self._event_queue: asyncio.Queue[object] = asyncio.Queue(maxsize=event_queue_size)
        self._greeting: list[bytes] = []
        self._last_activity = 0.0
        self._closed = False
        self._recv_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> Self:
        """Consume the greeting and start the background tasks."""
        # Both generations send the same number of greeting lines and can
        # only be told apart by the welcome line, so read first, sniff after.
        greeting_lines = QUIRKS[Dialect.TS3].greeting_lines
        self._greeting = [await self._transport.read_line() for _ in range(greeting_lines)]
        if self._dialect is Dialect.AUTO:
            self._dialect = sniff_dialect(self._greeting)

        self._touch()
        self._recv_task = asyncio.create_task(self._recv_loop(), name="atsq-recv")
        if self._keepalive_interval > 0:
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(), name="atsq-keepalive"
            )
        return self

    async def close(self) -> None:
        """Close the connection and cancel background tasks. Idempotent.

        Sends a best-effort ``quit`` first: TS6 (probe: 6.0.0-beta11) does
        not emit a timely ``notifyclientleftview`` for query clients that
        just drop the connection - only a clean ``quit`` produces one
        (reasonid=8, immediate on both generations).
        """
        if self._closed:
            return
        self._shutdown(ConnectionClosedError("connection closed locally"))
        for task in (self._keepalive_task, self._recv_task):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        if not self._transport.is_closed:
            # Best-effort with a hard cap: close() must never hang on a peer
            # that stopped reading.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(2.0):
                    await self._transport.send_line(b"quit")
        await self._transport.close()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def dialect(self) -> Dialect:
        """The (possibly sniffed) server dialect. Meaningful after start()."""
        return self._dialect

    @property
    def greeting(self) -> list[bytes]:
        """The raw greeting lines the server sent on connect."""
        return list(self._greeting)

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- commands ----------------------------------------------------------

    async def exec(
        self,
        cmd: str,
        *options: str,
        blocks: Iterable[Mapping[str, object]] | None = None,
        **params: object,
    ) -> list[dict[str, str]]:
        """Send one command and return its parsed data rows.

        ``blocks`` pipelines multi-key parameter blocks (see
        :func:`atsq.protocol.render_command`).

        Flood protection (error 524) is retried automatically up to
        ``flood_retries`` times, waiting the interval the server asks for
        (``please wait N seconds``) plus a small margin; both generations
        keep the connection usable across a 524 (probe-verified).

        Raises :class:`QueryError` (or :class:`FloodError` once the flood
        retries are exhausted) when the server answers with
        ``error id != 0``, :class:`QueryTimeoutError` when no response
        arrives within the command timeout (the connection is then closed:
        an unanswered command means the stream can no longer be trusted),
        and :class:`ConnectionClosedError` if the connection dies.
        """
        line = render_command(cmd, *options, blocks=blocks, **params)
        for attempt in range(self._flood_retries + 1):
            try:
                return await self._exec_line(line)
            except FloodError as err:
                if attempt == self._flood_retries:
                    raise
                LOG.warning(
                    "atsq: flood protection hit for %r, retrying in %.1fs",
                    cmd,
                    err.retry_after,
                )
                await asyncio.sleep(err.retry_after + 0.1)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _exec_line(self, line: bytes) -> list[dict[str, str]]:
        async with self._cmd_lock:
            if self._closed:
                raise ConnectionClosedError("connection is closed")
            loop = asyncio.get_running_loop()
            future: asyncio.Future[tuple[list[bytes], ErrorLine]] = loop.create_future()
            self._pending = future
            try:
                await self._transport.send_line(line)
                self._touch()
                try:
                    async with asyncio.timeout(self._command_timeout):
                        data_lines, error = await future
                except TimeoutError as err:
                    await self.close()
                    raise QueryTimeoutError(
                        f"no response to {line!r} within {self._command_timeout}s"
                    ) from err
            finally:
                self._pending = None
            self._touch()
        if not error.ok:
            raise QueryError.create(error.id, error.msg, error.extra)
        return parse_data_block(data_lines)

    async def send_keepalive(self) -> None:
        """Manually send a keepalive (a ``whoami`` nobody parses)."""
        await self.exec("whoami")

    # -- events ------------------------------------------------------------

    async def wait_for_event(self, timeout: float | None = None) -> Event:
        """Return the next server event.

        Raises :class:`QueryTimeoutError` if *timeout* elapses first and
        :class:`ConnectionClosedError` once the connection is gone.
        """
        if self._closed and self._event_queue.empty():  # pragma: no cover - racy corner
            # Normally the close sentinel keeps the queue non-empty forever.
            raise ConnectionClosedError("connection is closed")
        try:
            async with asyncio.timeout(timeout):
                item = await self._event_queue.get()
        except TimeoutError as err:
            raise QueryTimeoutError(f"no event within {timeout}s") from err
        if item is _QUEUE_CLOSED:
            # Leave the sentinel in place so every other waiter wakes too.
            self._event_queue.put_nowait(_QUEUE_CLOSED)
            raise ConnectionClosedError("connection is closed")
        assert isinstance(item, Event)
        return item

    async def events(self) -> AsyncIterator[Event]:
        """Iterate events until the connection closes."""
        while True:
            try:
                yield await self.wait_for_event()
            except ConnectionClosedError:
                return

    # -- internals ---------------------------------------------------------

    def _touch(self) -> None:
        self._last_activity = asyncio.get_running_loop().time()

    async def _recv_loop(self) -> None:
        try:
            while True:
                line = await self._transport.read_line()
                if not line:
                    continue
                if is_event_line(line):
                    self._publish_event(Event.from_line(line))
                elif is_error_line(line):
                    data_lines = self._data_buffer
                    self._data_buffer = []
                    error = parse_error_line(line)
                    if self._pending is not None and not self._pending.done():
                        self._pending.set_result((data_lines, error))
                    else:
                        LOG.warning("dropping unsolicited response terminator: %r", line)
                else:
                    self._data_buffer.append(line)
        except ConnectionClosedError as err:
            self._shutdown(err)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - defensive
            LOG.exception("atsq receive loop crashed")
            self._shutdown(ConnectionClosedError(f"receive loop crashed: {err}"))

    def _publish_event(self, event: Event) -> None:
        while True:
            try:
                self._event_queue.put_nowait(event)
                return
            except asyncio.QueueFull:
                try:
                    dropped = self._event_queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - racy corner
                    continue
                LOG.warning("event queue full, dropping oldest event: %r", dropped)

    def _shutdown(self, reason: ConnectionClosedError) -> None:
        """Mark closed and wake everything that is waiting."""
        if self._closed:  # pragma: no cover - close()/recv-loop race
            return
        self._closed = True
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(reason)
        self._publish_event_sentinel()
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()

    def _publish_event_sentinel(self) -> None:
        while True:
            try:
                self._event_queue.put_nowait(_QUEUE_CLOSED)
                return
            except asyncio.QueueFull:
                try:
                    self._event_queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - racy corner
                    continue

    async def _keepalive_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while not self._closed:
            due_in = self._keepalive_interval - (loop.time() - self._last_activity)
            if due_in > 0:
                await asyncio.sleep(due_in)
                continue
            try:
                await self.exec("whoami")
            except Exception:  # the recv loop owns error handling
                return
