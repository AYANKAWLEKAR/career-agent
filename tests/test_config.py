"""Settings construction, validation, caching, and secret hygiene."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from navigator.config import Settings, get_settings


def test_complete_environment_produces_valid_settings(full_env):
    settings = Settings()
    assert settings.oracle_dsn == "testdb_low"
    assert settings.oracle_user == "navigator"
    assert settings.oracle_tns_admin == full_env
    assert settings.demo_mode is True
    assert settings.pool_max == 4


def test_missing_required_setting_raises_at_construction(full_env, monkeypatch):
    monkeypatch.delenv("ORACLE_DSN")
    with pytest.raises(ValidationError) as excinfo:
        Settings(_env_file=None)
    assert "oracle_dsn" in str(excinfo.value)


def test_wallet_dir_missing_ewallet_pem_names_the_file(full_env, monkeypatch, tmp_path):
    incomplete = tmp_path / "incomplete-wallet"
    incomplete.mkdir()
    (incomplete / "tnsnames.ora").write_text("testdb_low = (description=...)")
    monkeypatch.setenv("ORACLE_TNS_ADMIN", str(incomplete))
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    assert "ewallet.pem" in str(excinfo.value)


def test_nonexistent_wallet_dir_raises(full_env, monkeypatch, tmp_path):
    monkeypatch.setenv("ORACLE_TNS_ADMIN", str(tmp_path / "nope"))
    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_cached(full_env):
    first = get_settings()
    second = get_settings()
    assert first is second


def test_repr_contains_no_secret_values(full_env):
    settings = Settings()
    for text in (repr(settings), str(settings)):
        assert "s3cret-db-pass" not in text
        assert "s3cret-wallet-pass" not in text
    assert settings.oracle_password.get_secret_value() == "s3cret-db-pass"


class TestWageScope:
    def test_defaults_to_national(self, full_env):
        assert Settings().wage_scope == "national"

    def test_normalizes_case_and_whitespace(self, full_env, monkeypatch):
        monkeypatch.setenv("WAGE_SCOPE", "  ALAMEDA ")
        assert Settings().wage_scope == "alameda"

    def test_bay_is_accepted(self, full_env, monkeypatch):
        monkeypatch.setenv("WAGE_SCOPE", "bay")
        assert Settings().wage_scope == "bay"

    def test_invalid_scope_names_the_valid_ones(self, full_env, monkeypatch):
        monkeypatch.setenv("WAGE_SCOPE", "berkeley")
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        message = str(excinfo.value)
        assert "national" in message and "alameda" in message


class TestGraphEngine:
    def test_defaults_to_graph_table(self, full_env):
        assert Settings().graph_engine == "graph_table"

    def test_normalizes_case_and_whitespace(self, full_env, monkeypatch):
        monkeypatch.setenv("GRAPH_ENGINE", "  GRAPH_TABLE ")
        assert Settings().graph_engine == "graph_table"

    def test_hop_join_is_accepted(self, full_env, monkeypatch):
        monkeypatch.setenv("GRAPH_ENGINE", "hop_join")
        assert Settings().graph_engine == "hop_join"

    def test_invalid_engine_names_the_valid_ones(self, full_env, monkeypatch):
        monkeypatch.setenv("GRAPH_ENGINE", "dijkstra")
        with pytest.raises(ValidationError) as excinfo:
            Settings()
        message = str(excinfo.value)
        assert "graph_table" in message and "hop_join" in message
