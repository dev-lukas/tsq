from atsq.events import Event


def test_from_line_with_properties() -> None:
    event = Event.from_line(b"notifycliententerview cfid=0 reasonid=0 clid=7")
    assert event.name == "cliententerview"
    assert event["reasonid"] == "0"
    assert event.get("missing") is None
    assert event.raw.startswith(b"notifycliententerview")


def test_from_line_without_properties() -> None:
    event = Event.from_line(b"notifyserveredited")
    assert event.name == "serveredited"
    assert len(event) == 0
    assert dict(event) == {}


def test_mapping_interface() -> None:
    event = Event.from_line(b"notifyx a=1 b=x\\sy")
    assert set(iter(event)) == {"a", "b"}
    assert len(event) == 2
    assert dict(event) == {"a": "1", "b": "x y"}
    assert "notifyx" in repr(event) or "x" in repr(event)


def test_multi_row_notify_keeps_extra_rows() -> None:
    event = Event.from_line(b"notifyy clid=1|clid=2")
    assert event["clid"] == "1"  # mapping view = first row
    assert event.rows == [{"clid": "1"}, {"clid": "2"}]
