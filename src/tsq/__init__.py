"""Asyncio TeamSpeak ServerQuery client for TeamSpeak 3 and 6 over SSH."""

from tsq.client import Client, connect
from tsq.connection import RawConnection
from tsq.definitions import LEAVE_REASONS, ClientType, ReasonId, TargetMode
from tsq.dialect import Dialect
from tsq.errors import (
    ConnectionClosedError,
    FloodError,
    QueryError,
    QueryTimeoutError,
    TsqError,
)
from tsq.events import Event
from tsq.filetransfer import FileTransfer
from tsq.transport import SshTransport, Transport

__version__ = "1.0.0a2"

__all__ = [
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
