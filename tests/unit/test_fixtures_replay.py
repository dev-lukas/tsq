"""Replay the recorded real-server transcripts through the parser.

The probe fixtures (tests/unit/fixtures/probe_*.log) contain the raw bytes
both server generations actually sent. Feeding every received line through
the protocol layer pins the parser to reality, not to assumptions - if a
future refactor breaks parsing of any construct a real server produces,
this fails without needing docker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from atsq.events import Event
from atsq.protocol import (
    is_error_line,
    is_event_line,
    parse_data_line,
    parse_error_line,
)

FIXTURES = Path(__file__).parent / "fixtures"
RECV_RE = re.compile(r"^< (?:\[[^]]+\] )?(b'.*'|b\".*\")$")


def iter_received_lines(log: Path) -> list[bytes]:
    lines: list[bytes] = []
    for entry in log.read_text(encoding="utf-8").splitlines():
        match = RECV_RE.match(entry)
        if not match:
            continue
        blob = ast.literal_eval(match.group(1))
        assert isinstance(blob, bytes)
        for line in re.split(rb"\n\r?", blob):
            if line := line.strip(b"\r"):
                lines.append(line)
    return lines


@pytest.mark.parametrize("generation", ["ts3", "ts6"])
def test_every_recorded_server_line_parses(generation: str) -> None:
    log = FIXTURES / f"probe_{generation}.log"
    if not log.exists():  # pragma: no cover - fixtures are committed
        pytest.skip(f"{log} missing")
    lines = iter_received_lines(log)
    assert len(lines) > 100, "fixture should contain a full probe session"

    errors = events = data = 0
    for line in lines:
        if is_error_line(line):
            parsed = parse_error_line(line)
            assert parsed.id >= 0
            errors += 1
        elif is_event_line(line):
            event = Event.from_line(line)
            assert event.name
            events += 1
        else:
            rows = parse_data_line(line)
            assert rows, line  # every recorded data line carries properties
            data += 1
    # A full probe session exercises all three shapes on both generations.
    assert errors > 50
    assert events >= 3
    assert data > 30
