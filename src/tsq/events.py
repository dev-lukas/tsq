"""Server notification events."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from tsq.protocol import parse_data_line

__all__ = ["Event"]

_NOTIFY_PREFIX = "notify"


class Event(Mapping[str, str]):
    """One ``notify*`` server event.

    Behaves as a read-only mapping over the event's (first row of)
    properties, so existing py-ts3-style code keeps working:
    ``event.get("reasonid")``, ``event["clid"]``.
    """

    __slots__ = ("name", "raw", "rows")

    def __init__(self, name: str, rows: list[dict[str, str]], raw: bytes) -> None:
        self.name = name
        #: All parsed rows; almost every notify carries exactly one.
        self.rows = rows
        self.raw = raw

    @classmethod
    def from_line(cls, line: bytes) -> Event:
        """Parse ``b"notifycliententerview cfid=0 clid=2 ..."``."""
        head, sep, rest = line.partition(b" ")
        name = head.decode("utf-8", "replace")
        name = name.removeprefix(_NOTIFY_PREFIX)
        rows = parse_data_line(rest) if sep else []
        if not rows:
            rows = [{}]
        return cls(name, rows, line)

    def __getitem__(self, key: str) -> str:
        return self.rows[0][key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.rows[0])

    def __len__(self) -> int:
        return len(self.rows[0])

    def __repr__(self) -> str:
        return f"Event({self.name!r}, {self.rows[0]!r})"
