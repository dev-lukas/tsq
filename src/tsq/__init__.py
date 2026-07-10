"""Asyncio TeamSpeak ServerQuery client for TeamSpeak 3 and 6 over SSH."""

from tsq.client import Client, connect
from tsq.connection import RawConnection
from tsq.dialect import Dialect
from tsq.errors import (
    ConnectionClosedError,
    FloodError,
    QueryError,
    QueryTimeoutError,
    TsqError,
)
from tsq.events import Event
from tsq.transport import SshTransport, Transport

__version__ = "0.1.0.dev0"

__all__ = [
    "Client",
    "ConnectionClosedError",
    "Dialect",
    "Event",
    "FloodError",
    "QueryError",
    "QueryTimeoutError",
    "RawConnection",
    "SshTransport",
    "Transport",
    "TsqError",
    "__version__",
    "connect",
]
