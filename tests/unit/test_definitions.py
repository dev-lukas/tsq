from atsq.definitions import LEAVE_REASONS, ClientType, ReasonId, TargetMode
from atsq.events import Event
from atsq.protocol import render_command


def test_reason_ids_compare_against_wire_strings() -> None:
    event = Event.from_line(b"notifycliententerview reasonid=0 clid=7 client_type=0")
    assert event["reasonid"] == ReasonId.CONNECT
    assert event["client_type"] == ClientType.VOICE


def test_leave_reasons_match_firephenix_set() -> None:
    # The disconnect reasons the firephenix bot handles: 8,3,5,6,10,11.
    assert {"8", "3", "5", "6", "10", "11"} == LEAVE_REASONS
    assert "0" not in LEAVE_REASONS  # connect
    assert "4" not in LEAVE_REASONS  # channel kick: client stays on the server


def test_enums_render_as_wire_values_in_commands() -> None:
    assert render_command(
        "sendtextmessage", targetmode=TargetMode.CLIENT, target=5, msg="x"
    ) == (b"sendtextmessage targetmode=1 target=5 msg=x")
