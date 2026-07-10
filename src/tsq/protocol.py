"""Stateless ServerQuery wire logic: command rendering and line parsing.

No I/O happens here - everything operates on single ``bytes`` lines (without
the ``\\n\\r`` terminator) so it can be unit-tested against recorded
transcripts from real servers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from tsq.escape import escape, unescape

__all__ = [
    "ErrorLine",
    "is_error_line",
    "is_event_line",
    "parse_data_line",
    "parse_error_line",
    "render_command",
]

_ENCODING = "utf-8"


def render_command(
    cmd: str,
    *options: str,
    blocks: Iterable[Mapping[str, object]] | None = None,
    **params: object,
) -> bytes:
    """Render one command line (without terminator).

    ``options`` become no-value switches (``"uid"`` → ``-uid``). ``params``
    values are stringified and escaped; a list/tuple value renders as a piped
    parameter block (``key=a|key=b``) for commands that accept multiple
    targets. ``None`` values are skipped.

    ``blocks`` pipelines multi-key parameter blocks in one command: the
    shared ``params`` and the first block form the first segment, each
    further block becomes a ``|``-separated segment.

    >>> render_command("clientkick", clid=5, reasonid=5)
    b'clientkick clid=5 reasonid=5'
    >>> render_command("clientlist", "uid")
    b'clientlist -uid'
    >>> render_command("channeladdperm", cid=60,
    ...                blocks=[{"permsid": "a", "permvalue": 1},
    ...                        {"permsid": "b", "permvalue": 2}])
    b'channeladdperm cid=60 permsid=a permvalue=1|permsid=b permvalue=2'
    """
    if not cmd or " " in cmd:
        raise ValueError(f"invalid command name: {cmd!r}")
    segments = [_render_block(params)]
    if blocks is not None:
        for index, block in enumerate(blocks):
            rendered = _render_block(block)
            if not rendered:
                raise ValueError(f"pipelined block {index} rendered empty: {block!r}")
            if index == 0 and segments[0]:
                segments[0] = f"{segments[0]} {rendered}"
            elif index == 0:
                segments[0] = rendered
            else:
                segments.append(rendered)
    parts = [cmd]
    if piped := "|".join(segment for segment in segments if segment):
        parts.append(piped)
    parts.extend(f"-{option}" for option in options)
    return " ".join(parts).encode(_ENCODING)


def _render_block(block: Mapping[str, object]) -> str:
    parts: list[str] = []
    for key, value in block.items():
        if value is None:
            continue
        if isinstance(value, list | tuple):
            rendered = "|".join(_render_param(key, item) for item in value)
            if rendered:
                parts.append(rendered)
        else:
            parts.append(_render_param(key, value))
    return " ".join(parts)


def _render_param(key: str, value: object) -> str:
    if isinstance(value, bool):
        value = int(value)
    return f"{key}={escape(str(value))}"


def is_event_line(line: bytes) -> bool:
    """True if *line* is an asynchronous server notification."""
    return line.startswith(b"notify")


def is_error_line(line: bytes) -> bool:
    """True if *line* is the ``error id=... msg=...`` response terminator."""
    return line.startswith(b"error ") or line == b"error"


@dataclass(slots=True)
class ErrorLine:
    """Parsed ``error id=<n> msg=<msg> ...`` terminator line."""

    id: int
    msg: str
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.id == 0


def parse_error_line(line: bytes) -> ErrorLine:
    """Parse the response terminator line.

    >>> parse_error_line(b'error id=0 msg=ok')
    ErrorLine(id=0, msg='ok', extra={})
    """
    if not is_error_line(line):
        raise ValueError(f"not an error line: {line!r}")
    props: dict[str, str] = {}
    for chunk in line.decode(_ENCODING, "replace").split(" ")[1:]:
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        props[key] = unescape(value)
    error_id = int(props.pop("id", "-1"))
    msg = props.pop("msg", "")
    return ErrorLine(id=error_id, msg=msg, extra=props)


def parse_data_line(line: bytes) -> list[dict[str, str]]:
    """Parse a data line into rows of unescaped properties.

    Rows are separated by ``|``, properties by spaces. A property without a
    value (``key``) maps to an empty string. Values may legitimately contain
    ``=`` (base64 unique identifiers), so only the first ``=`` splits.

    >>> parse_data_line(b'clid=1 client_nickname=A|clid=2 client_nickname=B\\\\sC')
    [{'clid': '1', 'client_nickname': 'A'}, {'clid': '2', 'client_nickname': 'B C'}]
    """
    rows: list[dict[str, str]] = []
    for raw_row in line.split(b"|"):
        row: dict[str, str] = {}
        for prop in raw_row.decode(_ENCODING, "replace").split(" "):
            if not prop:
                continue
            key, sep, value = prop.partition("=")
            row[key] = unescape(value) if sep else ""
        if row:
            rows.append(row)
    return rows


def parse_data_block(lines: Iterable[bytes]) -> list[dict[str, str]]:
    """Parse all data lines of one response into a flat row list."""
    rows: list[dict[str, str]] = []
    for line in lines:
        rows.extend(parse_data_line(line))
    return rows
