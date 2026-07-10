"""Exception hierarchy for tsq."""

from __future__ import annotations

import re

__all__ = [
    "ConnectionClosedError",
    "FloodError",
    "QueryError",
    "QueryTimeoutError",
    "TsqError",
]

#: Server error id signalling flood protection ("client is flooding").
FLOOD_ERROR_ID = 524


class TsqError(Exception):
    """Base class for all tsq errors."""


class ConnectionClosedError(TsqError):
    """The connection was closed (locally or by the server/transport)."""


class QueryTimeoutError(TsqError, TimeoutError):
    """A command response or event did not arrive in time."""


class QueryError(TsqError):
    """The server answered a command with ``error id != 0``.

    ``str()`` always contains the server message so callers can match on it
    (e.g. ``"banned" in str(exc).lower()``).
    """

    def __init__(self, error_id: int, msg: str, extra: dict[str, str] | None = None) -> None:
        self.error_id = error_id
        self.msg = msg
        self.extra = extra or {}
        detail = "".join(f" {k}={v}" for k, v in self.extra.items())
        super().__init__(f"error {error_id}: {msg}{detail}")

    @classmethod
    def create(cls, error_id: int, msg: str, extra: dict[str, str] | None = None) -> QueryError:
        """Build the most specific error subclass for *error_id*."""
        if error_id == FLOOD_ERROR_ID:
            return FloodError(error_id, msg, extra)
        return cls(error_id, msg, extra)


class FloodError(QueryError):
    """The server's flood protection kicked in (error id 524)."""

    @property
    def retry_after(self) -> float:
        """Seconds the server asked us to wait (default 1.0).

        Both generations phrase it as ``extra_msg=please wait N seconds``.
        """
        match = re.search(r"wait (\d+) second", self.extra.get("extra_msg", ""))
        return float(match.group(1)) if match else 1.0
