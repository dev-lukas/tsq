"""High-level client: reconnecting, event-dispatching, typed commands.

Two usage styles:

One-shot (context manager)::

    async with await tsq.connect("ts.example.com", password="...",
                                 server_id=1) as ts:
        rows = await ts.client_list()

Long-running bot with listeners and automatic reconnect::

    client = tsq.Client("ts.example.com", password="...", server_id=1,
                        register_events="server")

    @client.on("cliententerview")
    async def on_join(event: tsq.Event) -> None:
        ...

    await client.run_forever()
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Self

from tsq.connection import (
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_KEEPALIVE_INTERVAL,
    RawConnection,
)
from tsq.dialect import Dialect
from tsq.errors import ConnectionClosedError
from tsq.transport import SshTransport, Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from types import TracebackType

    from tsq.events import Event

    EventHandler = Callable[["Event"], Awaitable[None]]
    TransportFactory = Callable[[], Awaitable[Transport]]

__all__ = ["Client", "connect"]

LOG = logging.getLogger(__name__)

#: Handler key receiving every event regardless of name.
ANY_EVENT = "*"


class Client:
    """A (re)connectable ServerQuery client.

    Construction stores the target only; call :meth:`start` (or
    :func:`connect`) for a single connection, or :meth:`run_forever` to let
    the client own connect/reconnect and dispatch events to ``@client.on``
    handlers.
    """

    def __init__(
        self,
        host: str,
        port: int = 10022,
        *,
        password: str,
        username: str = "serveradmin",
        server_id: int | None = None,
        register_events: str | None = None,
        dialect: Dialect = Dialect.AUTO,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
        event_queue_size: int = 1024,
        transport_factory: TransportFactory | None = None,
        **ssh_options: Any,
    ) -> None:
        self._transport_factory: TransportFactory = transport_factory or (
            lambda: SshTransport.connect(
                host, port, username=username, password=password, **ssh_options
            )
        )
        self._server_id = server_id
        self._register_events = register_events
        self._dialect = dialect
        self._command_timeout = command_timeout
        self._keepalive_interval = keepalive_interval
        self._event_queue_size = event_queue_size

        self._conn: RawConnection | None = None
        self._handlers: dict[str, list[EventHandler]] = {}
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> Self:
        """Connect once: transport, greeting, ``use``, event registration."""
        transport = await self._transport_factory()
        conn = RawConnection(
            transport,
            dialect=self._dialect,
            command_timeout=self._command_timeout,
            keepalive_interval=self._keepalive_interval,
            event_queue_size=self._event_queue_size,
        )
        try:
            await conn.start()
            if self._server_id is not None:
                await conn.exec("use", sid=self._server_id)
            if self._register_events is not None:
                await conn.exec("servernotifyregister", event=self._register_events)
        except BaseException:
            await conn.close()
            raise
        self._conn = conn
        return self

    async def close(self) -> None:
        """Disconnect and stop :meth:`run_forever`. Idempotent."""
        self._stopping = True
        if self._conn is not None:
            await self._conn.close()

    @property
    def connected(self) -> bool:
        return self._conn is not None and not self._conn.closed

    @property
    def connection(self) -> RawConnection:
        """The active :class:`RawConnection` (raises when disconnected)."""
        if self._conn is None or self._conn.closed:
            raise ConnectionClosedError("client is not connected")
        return self._conn

    @property
    def dialect(self) -> Dialect:
        return self.connection.dialect

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # -- events ------------------------------------------------------------

    def on(self, event_name: str) -> Callable[[EventHandler], EventHandler]:
        """Register a coroutine handler: ``@client.on("cliententerview")``.

        Use ``"*"`` to receive every event. Handlers run sequentially inside
        :meth:`run_forever`; exceptions are logged and never break the loop.
        """

        def decorator(handler: EventHandler) -> EventHandler:
            self._handlers.setdefault(event_name, []).append(handler)
            return handler

        return decorator

    async def wait_for_event(self, timeout: float | None = None) -> Event:
        """Pull-style event consumption (do not mix with :meth:`run_forever`)."""
        return await self.connection.wait_for_event(timeout)

    async def events(self) -> AsyncIterator[Event]:
        """Iterate events of the current connection until it closes."""
        async for event in self.connection.events():
            yield event

    # -- run loop ----------------------------------------------------------

    async def run_forever(
        self,
        *,
        on_ready: Callable[[Client], Awaitable[None]] | None = None,
        initial_delay: float = 5.0,
        max_delay: float = 300.0,
        banned_delay: float = 300.0,
    ) -> None:
        """Own the connection until :meth:`close` is called.

        (Re)connects with exponential backoff (*initial_delay* doubling up to
        *max_delay*; a server message containing "banned" waits
        *banned_delay* instead), re-runs ``use``/``servernotifyregister``
        and *on_ready* after every (re)connect, and dispatches events to the
        registered handlers.
        """
        delay = initial_delay
        while not self._stopping:
            error: BaseException | None = None
            try:
                if self._conn is None or self._conn.closed:
                    await self.start()
                    delay = initial_delay
                    if on_ready is not None:
                        await on_ready(self)
                assert self._conn is not None
                async for event in self._conn.events():
                    await self._dispatch(event)
                # events() only ends when the connection closed.
            except asyncio.CancelledError:
                raise
            except Exception as err:
                error = err
            if self._stopping:
                break
            wait = banned_delay if error is not None and _is_banned(error) else delay
            LOG.warning(
                "tsq: connection lost%s; reconnecting in %.0fs",
                f" ({error})" if error is not None else "",
                wait,
            )
            await asyncio.sleep(wait)
            delay = min(max_delay, delay * 2)

    async def _dispatch(self, event: Event) -> None:
        handlers = self._handlers.get(event.name, []) + self._handlers.get(ANY_EVENT, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception:
                LOG.exception("tsq: error in %r handler %r", event.name, handler)

    # -- generic command ----------------------------------------------------

    async def exec(self, cmd: str, *options: str, **params: object) -> list[dict[str, str]]:
        """Run any ServerQuery command (see :meth:`RawConnection.exec`)."""
        return await self.connection.exec(cmd, *options, **params)

    async def send_keepalive(self) -> None:
        await self.connection.send_keepalive()

    # -- typed convenience commands -----------------------------------------
    # Thin wrappers over exec() for the commands a bot actually uses. Rows
    # are plain dict[str, str]; TS6-only fields simply appear as extra keys.

    async def use(self, sid: int) -> None:
        await self.exec("use", sid=sid)

    async def server_notify_register(self, event: str = "server", id: int | None = None) -> None:
        await self.exec("servernotifyregister", event=event, id=id)

    async def whoami(self) -> dict[str, str]:
        return (await self.exec("whoami"))[0]

    async def version(self) -> dict[str, str]:
        return (await self.exec("version"))[0]

    async def client_list(self, *options: str) -> list[dict[str, str]]:
        """``client_list("uid", "away")`` renders as ``clientlist -uid -away``."""
        return await self.exec("clientlist", *options)

    async def client_info(self, clid: int | str) -> dict[str, str]:
        return (await self.exec("clientinfo", clid=clid))[0]

    async def client_dbid_from_uid(self, cluid: str) -> str:
        rows = await self.exec("clientgetdbidfromuid", cluid=cluid)
        return rows[0]["cldbid"]

    async def server_groups_by_client(self, cldbid: int | str) -> list[dict[str, str]]:
        return await self.exec("servergroupsbyclientid", cldbid=cldbid)

    async def server_group_add_client(self, sgid: int | str, cldbid: int | str) -> None:
        await self.exec("servergroupaddclient", sgid=sgid, cldbid=cldbid)

    async def server_group_del_client(self, sgid: int | str, cldbid: int | str) -> None:
        await self.exec("servergroupdelclient", sgid=sgid, cldbid=cldbid)

    async def set_client_channel_group(
        self, cgid: int | str, cid: int | str, cldbid: int | str
    ) -> None:
        await self.exec("setclientchannelgroup", cgid=cgid, cid=cid, cldbid=cldbid)

    async def channel_create(self, channel_name: str, **props: object) -> str:
        """Create a channel and return its ``cid``."""
        rows = await self.exec("channelcreate", channel_name=channel_name, **props)
        return rows[0]["cid"]

    async def channel_add_perm(
        self, cid: int | str, permsid: str, permvalue: int | str
    ) -> None:
        await self.exec("channeladdperm", cid=cid, permsid=permsid, permvalue=permvalue)

    async def channel_client_add_perm(
        self, cid: int | str, cldbid: int | str, permsid: str, permvalue: int | str
    ) -> None:
        await self.exec(
            "channelclientaddperm", cid=cid, cldbid=cldbid, permsid=permsid, permvalue=permvalue
        )

    async def channel_move(
        self, cid: int | str, cpid: int | str, order: int | None = None
    ) -> None:
        await self.exec("channelmove", cid=cid, cpid=cpid, order=order)

    async def send_text_message(
        self, target: int | str, msg: str, targetmode: int = 1
    ) -> None:
        await self.exec("sendtextmessage", targetmode=targetmode, target=target, msg=msg)

    async def client_kick(
        self, clid: int | str, reasonid: int = 5, reasonmsg: str | None = None
    ) -> None:
        await self.exec("clientkick", clid=clid, reasonid=reasonid, reasonmsg=reasonmsg)


def _is_banned(error: BaseException) -> bool:
    return "banned" in str(error).lower()


async def connect(
    host: str,
    port: int = 10022,
    *,
    password: str,
    username: str = "serveradmin",
    server_id: int | None = None,
    register_events: str | None = None,
    **kwargs: Any,
) -> Client:
    """Connect once and return a started :class:`Client`.

    Extra keyword arguments are forwarded to :class:`Client` (and from there
    to :func:`asyncssh.connect` - e.g. ``known_hosts`` to pin the host key).
    """
    client = Client(
        host,
        port,
        password=password,
        username=username,
        server_id=server_id,
        register_events=register_events,
        **kwargs,
    )
    try:
        await client.start()
    except BaseException:
        await client.close()
        raise
    return client
