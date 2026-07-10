"""Server dialect handling.

All TS3-vs-TS6 divergence must live in this module. Values below are taken
from the recorded dialect probe (``scripts/probe_dialect.py``; transcripts in
``tests/unit/fixtures/probe_ts3.log`` / ``probe_ts6.log``, findings in
``docs/dialects.md``), run against teamspeak:3.13 (3.13.7) and
teamspeaksystems/teamspeak6-server (6.0.0-beta11).

Probe verdict: the wire dialects are almost identical. Both greet with a
literal ``TS3`` first line, frame lines as ``\\n\\r``, use the same escape
table, the same error codes, and emit the same events. TS6 only *adds*
fields (``virtualserver_uuid``, ``client_is_streaming``). The reliable
distinguishing marks are the second greeting line and the ``version``
command.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

__all__ = ["QUIRKS", "Dialect", "DialectQuirks", "sniff_dialect"]

#: Second greeting line prefix that identifies a TS3-generation server.
#: TS3: 'Welcome to the TeamSpeak 3 ServerQuery interface, ...'
#: TS6: 'Welcome to the TeamSpeak ServerQuery interface, ...'
_TS3_WELCOME_PREFIX = b"Welcome to the TeamSpeak 3 "


class Dialect(enum.Enum):
    TS3 = "ts3"
    TS6 = "ts6"
    #: Detect from the greeting at connect time.
    AUTO = "auto"


@dataclass(frozen=True, slots=True)
class DialectQuirks:
    """Per-generation wire deviations.

    Currently only the greeting length - the probe found no behavioural
    divergence that the client must branch on. The type stays as the
    containment point for future TS6-beta drift.
    """

    #: Total number of greeting lines to consume before commands may be sent.
    #: (The first line is literally ``TS3`` on BOTH generations.)
    greeting_lines: int


QUIRKS: dict[Dialect, DialectQuirks] = {
    Dialect.TS3: DialectQuirks(greeting_lines=2),
    Dialect.TS6: DialectQuirks(greeting_lines=2),
}


def sniff_dialect(greeting: list[bytes]) -> Dialect:
    """Determine the dialect from the full greeting.

    The first line is ``TS3`` on both generations, so only the welcome line
    distinguishes them. Unknown shapes default to TS6 (the growing side).
    """
    for line in greeting:
        if line.startswith(_TS3_WELCOME_PREFIX):
            return Dialect.TS3
    return Dialect.TS6
