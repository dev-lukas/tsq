"""Asyncio TeamSpeak ServerQuery client for TeamSpeak 3 and 6 over SSH."""

from atsq.client import ALL_EVENTS, Client, connect
from atsq.connection import RawConnection
from atsq.definitions import LEAVE_REASONS, ClientType, ReasonId, TargetMode
from atsq.dialect import Dialect
from atsq.errors import (
    ConnectionClosedError,
    FloodError,
    QueryError,
    QueryTimeoutError,
    TsqError,
)
from atsq.events import Event
from atsq.filetransfer import FileTransfer
from atsq.transport import SshTransport, Transport

__version__ = "1.0.0a3"

__all__ = [
    "ALL_EVENTS",
    "LEAVE_REASONS",
    "Client",
    "ClientType",
    "ConnectionClosedError",
    "Dialect",
    "Event",
    "FileTransfer",
    "FloodError",
    "QueryError",
    "QueryTimeoutError",
    "RawConnection",
    "ReasonId",
    "SshTransport",
    "TargetMode",
    "Transport",
    "TsqError",
    "__version__",
    "connect",
]
