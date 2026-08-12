"""Connection pool construction and the acquire/release contract."""

from __future__ import annotations

from unittest.mock import MagicMock

import oracledb
import pytest

from navigator.config import Settings
from navigator.db import pool as pool_module
from tests.conftest import FakePool, has_live_wallet


@pytest.fixture
def settings(full_env) -> Settings:
    return Settings()


def test_create_pool_passes_expected_kwargs(settings, wallet_dir, monkeypatch):
    create_pool_mock = MagicMock(return_value="the-pool")
    init_client_mock = MagicMock()
    monkeypatch.setattr(oracledb, "create_pool", create_pool_mock)
    monkeypatch.setattr(oracledb, "init_oracle_client", init_client_mock)

    result = pool_module.create_pool(settings)

    assert result == "the-pool"
    kwargs = create_pool_mock.call_args.kwargs
    assert kwargs["user"] == "navigator"
    assert kwargs["password"] == "s3cret-db-pass"
    assert kwargs["dsn"] == "testdb_low"
    assert kwargs["config_dir"] == str(wallet_dir)
    assert kwargs["wallet_location"] == str(wallet_dir)
    assert kwargs["wallet_password"] == "s3cret-wallet-pass"
    assert kwargs["min"] == 1
    assert kwargs["max"] == 4
    assert kwargs["increment"] == 1
    assert kwargs["getmode"] == oracledb.POOL_GETMODE_WAIT
    # Thin mode: the thick-mode client is never initialized.
    init_client_mock.assert_not_called()


def test_create_async_pool_uses_async_factory(settings, monkeypatch):
    create_async_mock = MagicMock(return_value="the-async-pool")
    monkeypatch.setattr(oracledb, "create_pool_async", create_async_mock)
    assert pool_module.create_async_pool(settings) == "the-async-pool"
    assert create_async_mock.call_args.kwargs["dsn"] == "testdb_low"


def test_acquire_releases_on_success(fake_pool: FakePool):
    with pool_module.acquire(fake_pool) as connection:
        assert connection is fake_pool.connection
    assert fake_pool.acquired == 1
    assert fake_pool.released == 1


def test_acquire_releases_on_exception(fake_pool: FakePool):
    with pytest.raises(RuntimeError):
        with pool_module.acquire(fake_pool):
            raise RuntimeError("boom")
    assert fake_pool.released == 1


def test_check_connection_returns_version_and_schema(fake_pool: FakePool):
    fake_pool.db.query_rows = [("Oracle Database 23ai Enterprise Edition", "NAVIGATOR")]
    result = pool_module.check_connection(fake_pool)
    assert result == {
        "version": "Oracle Database 23ai Enterprise Edition",
        "schema": "NAVIGATOR",
    }
    assert fake_pool.released == 1


@pytest.mark.skipif(not has_live_wallet(), reason="no live wallet configured")
def test_live_connection():
    from navigator.config import get_settings
    from navigator.db.pool import check_connection, create_pool

    result = check_connection(create_pool(get_settings()))
    assert result["version"]
