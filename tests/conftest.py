"""Shared fixtures: fake wallet, environment, fake database objects."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import tenacity

import navigator.ingest.base as ingest_base
from navigator.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"

REQUIRED_ENV = {
    "ORACLE_DSN": "testdb_low",
    "ORACLE_USER": "navigator",
    "ORACLE_PASSWORD": "s3cret-db-pass",
    "ORACLE_WALLET_PASSWORD": "s3cret-wallet-pass",
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def has_live_wallet() -> bool:
    """True when a real wallet is configured, enabling live tests."""
    tns_admin = os.environ.get("NAVIGATOR_LIVE_TNS_ADMIN", "")
    return bool(tns_admin) and (Path(tns_admin) / "tnsnames.ora").is_file()


@pytest.fixture
def wallet_dir(tmp_path: Path) -> Path:
    wallet = tmp_path / "wallet"
    wallet.mkdir()
    (wallet / "tnsnames.ora").write_text("testdb_low = (description=...)")
    (wallet / "ewallet.pem").write_text("-- fake pem --")
    return wallet


@pytest.fixture
def full_env(monkeypatch: pytest.MonkeyPatch, wallet_dir: Path) -> Path:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ORACLE_TNS_ADMIN", str(wallet_dir))
    get_settings.cache_clear()
    yield wallet_dir
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out retry backoff so retry tests run instantly."""
    monkeypatch.setattr(ingest_base, "RETRY_WAIT", tenacity.wait_fixed(0))


class FakeCursor:
    def __init__(self, db: FakeDb):
        self._db = db
        self._rows: list[tuple] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def execute(self, sql: str, params: list | None = None) -> None:
        self._db.executed.append((sql, params))
        lowered = sql.lower()
        for substring in self._db.failing_substrings:
            if substring in lowered:
                raise RuntimeError(f"ORA-simulated failure near {substring!r}")
        if lowered.lstrip().startswith("select") and "schema_migrations" in lowered:
            self._rows = list(self._db.applied)
        elif lowered.lstrip().startswith("insert into schema_migrations") and params:
            self._db.applied.append((params[0], params[1]))
        elif lowered.lstrip().startswith("select") and self._db.query_rows is not None:
            self._rows = list(self._db.query_rows)

    def executemany(self, sql: str, batches: list) -> None:
        self._db.executed.append((sql, batches))
        self._db.executemany_batches.append(list(batches))

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None


class FakeConnection:
    def __init__(self, db: FakeDb):
        self._db = db
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._db)

    def commit(self) -> None:
        self.commits += 1


class FakeDb:
    """Scriptable in-memory stand-in for a connection's behavior."""

    def __init__(self) -> None:
        self.executed: list[tuple] = []
        self.executemany_batches: list[list] = []
        self.applied: list[tuple[str, str]] = []
        self.failing_substrings: list[str] = []
        self.query_rows: list[tuple] | None = None


class FakePool:
    def __init__(self, db: FakeDb | None = None):
        self.db = db or FakeDb()
        self.connection = FakeConnection(self.db)
        self.acquired = 0
        self.released = 0

    def acquire(self) -> FakeConnection:
        self.acquired += 1
        return self.connection

    def release(self, connection: FakeConnection) -> None:
        assert connection is self.connection
        self.released += 1


@pytest.fixture
def fake_pool() -> FakePool:
    return FakePool()
