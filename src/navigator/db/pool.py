"""Connection pool management for Oracle Autonomous AI Database.

Thin mode only: ``oracledb.init_oracle_client`` is never called. The wallet
directory doubles as both ``config_dir`` and ``wallet_location``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import oracledb

from navigator.config import Settings


def _pool_kwargs(settings: Settings) -> dict[str, Any]:
    wallet_dir = str(settings.oracle_tns_admin)
    return {
        "user": settings.oracle_user,
        "password": settings.oracle_password.get_secret_value(),
        "dsn": settings.oracle_dsn,
        "config_dir": wallet_dir,
        "wallet_location": wallet_dir,
        "wallet_password": settings.oracle_wallet_password.get_secret_value(),
        "min": settings.pool_min,
        "max": settings.pool_max,
        "increment": settings.pool_increment,
        "getmode": oracledb.POOL_GETMODE_WAIT,
    }


def create_pool(settings: Settings) -> oracledb.ConnectionPool:
    return oracledb.create_pool(**_pool_kwargs(settings))


def create_async_pool(settings: Settings) -> oracledb.AsyncConnectionPool:
    return oracledb.create_pool_async(**_pool_kwargs(settings))


@contextmanager
def acquire(pool: oracledb.ConnectionPool) -> Iterator[oracledb.Connection]:
    """Yield a pooled connection, guaranteeing release."""
    connection = pool.acquire()
    try:
        yield connection
    finally:
        pool.release(connection)


def check_connection(pool: oracledb.ConnectionPool) -> dict[str, str]:
    """Live smoke test: return the database version and current schema."""
    with acquire(pool) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select banner_full, sys_context('USERENV', 'CURRENT_SCHEMA') from v$version"
            )
            banner, schema = cursor.fetchone()
    return {"version": banner, "schema": schema}
