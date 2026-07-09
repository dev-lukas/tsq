"""Server dialect handling.

All TS3-vs-TS6 divergence must live in this module. The quirks table is
populated from the recorded dialect probe (``scripts/probe_dialect.py``,
findings in ``docs/dialects.md``) - values marked PLACEHOLDER are unverified
until the probe has run against a real server of that generation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

__all__ = ["QUIRKS", "Dialect", "DialectQuirks", "sniff_dialect"]


class Dialect(enum.Enum):
    TS3 = "ts3"
    TS6 = "ts6"
    #: Detect from the greeting's first line at connect time.
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class DialectQuirks:
    #: Exact first greeting line sent by the server after the channel opens.
    greeting_head: bytes
    #: Total number of greeting lines to consume before commands may be sent.
    greeting_lines: int


QUIRKS: dict[Dialect, DialectQuirks] = {
    # TS3: b"TS3" + b"Welcome to the TeamSpeak 3 ServerQuery interface, ..."
    Dialect.TS3: DialectQuirks(greeting_head=b"TS3", greeting_lines=2),
    # PLACEHOLDER until the M4 probe records a real TS6 greeting.
    Dialect.TS6: DialectQuirks(greeting_head=b"TS6", greeting_lines=2),
}


def sniff_dialect(first_greeting_line: bytes) -> Dialect:
    """Guess the dialect from the first greeting line (AUTO mode)."""
    if first_greeting_line.startswith(b"TS3"):
        return Dialect.TS3
    return Dialect.TS6
