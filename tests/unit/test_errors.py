import pytest

from atsq.errors import (
    ConnectionClosedError,
    FloodError,
    QueryError,
    QueryTimeoutError,
    TsqError,
)


def test_query_error_str_contains_server_msg() -> None:
    # firephenix matches on the message text: `"banned" in str(e).lower()`.
    err = QueryError.create(3329, "connection failed, you are banned", {"extra_msg": "1h"})
    assert "banned" in str(err).lower()
    assert "extra_msg=1h" in str(err)
    assert err.error_id == 3329


def test_flood_error_is_query_error() -> None:
    err = QueryError.create(524, "client is flooding")
    assert isinstance(err, FloodError)
    assert isinstance(err, QueryError)
    assert err.error_id == 524


def test_non_flood_stays_base_class() -> None:
    assert type(QueryError.create(0, "ok")) is QueryError


def test_flood_retry_after_parses_server_hint() -> None:
    # Probe-verified live format on TS3 3.13.7 and TS6 6.0.0-beta11.
    err = QueryError.create(524, "client is flooding", {"extra_msg": "please wait 1 seconds"})
    assert isinstance(err, FloodError)
    assert err.retry_after == 1.0
    longer = QueryError.create(524, "client is flooding", {"extra_msg": "please wait 5 seconds"})
    assert isinstance(longer, FloodError)
    assert longer.retry_after == 5.0


def test_flood_retry_after_defaults_without_hint() -> None:
    err = QueryError.create(524, "client is flooding")
    assert isinstance(err, FloodError)
    assert err.retry_after == 1.0


def test_hierarchy() -> None:
    assert issubclass(ConnectionClosedError, TsqError)
    assert issubclass(QueryTimeoutError, TsqError)
    assert issubclass(QueryTimeoutError, TimeoutError)
    with pytest.raises(TsqError):
        raise ConnectionClosedError("gone")
