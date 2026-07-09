import pytest

from tsq.errors import (
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


def test_hierarchy() -> None:
    assert issubclass(ConnectionClosedError, TsqError)
    assert issubclass(QueryTimeoutError, TsqError)
    assert issubclass(QueryTimeoutError, TimeoutError)
    with pytest.raises(TsqError):
        raise ConnectionClosedError("gone")
