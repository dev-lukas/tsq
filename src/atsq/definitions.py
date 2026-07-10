"""ServerQuery constants (from the TeamSpeak ServerQuery manual).

Everything on the wire is a string, so these are :class:`enum.StrEnum` -
they compare equal to the raw values in events and rows::

    if event["reasonid"] == ReasonId.CONNECT: ...
    if event.get("reasonid") in LEAVE_REASONS: ...

and render correctly when passed as command parameters.
"""

from __future__ import annotations

import enum

__all__ = [
    "LEAVE_REASONS",
    "ClientType",
    "ReasonId",
    "TargetMode",
]


class ReasonId(enum.StrEnum):
    """``reasonid`` values in enter/left/moved view events."""

    CONNECT = "0"
    MOVED = "1"
    TIMEOUT = "3"
    CHANNEL_KICK = "4"
    SERVER_KICK = "5"
    BAN = "6"
    QUIT = "8"
    SERVER_STOP = "10"
    SERVER_LEFT = "11"


#: Every reasonid that means "this client is gone from the server".
LEAVE_REASONS = frozenset(
    {
        ReasonId.TIMEOUT,
        ReasonId.SERVER_KICK,
        ReasonId.BAN,
        ReasonId.QUIT,
        ReasonId.SERVER_STOP,
        ReasonId.SERVER_LEFT,
    }
)


class TargetMode(enum.StrEnum):
    """``targetmode`` for ``sendtextmessage`` / ``notifytextmessage``."""

    CLIENT = "1"
    CHANNEL = "2"
    SERVER = "3"


class ClientType(enum.StrEnum):
    """``client_type`` in clientlist/clientinfo/events."""

    VOICE = "0"
    QUERY = "1"
