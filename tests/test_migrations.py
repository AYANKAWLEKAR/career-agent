"""Migration discovery, statement splitting, hash bookkeeping, and errors."""

from __future__ import annotations

import pytest

from navigator.db import migrations
from navigator.db.migrations import (
    ChecksumMismatchError,
    MigrationError,
    _checksum,
    discover_sql_files,
    run_migrations,
    split_statements,
)
from tests.conftest import FakePool, has_live_wallet

EXPECTED_FILES = [
    "001_reference_tables.sql",
    "002_occupation_core.sql",
    "003_credentials.sql",
    "004_geography_and_wages.sql",
    "005_education.sql",
    "006_agent_tables.sql",
    "007_indexes.sql",
    "008_property_graph.sql",
]


def test_sql_files_discovered_in_sorted_order():
    names = [path.name for path in discover_sql_files()]
    assert names == EXPECTED_FILES


def test_split_handles_plsql_block_with_embedded_semicolons():
    text = """
-- leading comment
create table plain_one (id number);

declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'X';
  if l_count = 0 then
    execute immediate 'create table x (id number)';
  end if;
end;
/

create index plain_ix on plain_one (id);
"""
    statements = split_statements(text)
    assert len(statements) == 3
    assert statements[0].endswith("create table plain_one (id number)")
    assert statements[1].startswith("declare")
    assert statements[1].count(";") == 5  # embedded semicolons survive intact
    assert statements[1].rstrip().endswith("end;")
    assert "create index plain_ix" in statements[2]


def test_split_every_shipped_file_yields_statements():
    for path in discover_sql_files():
        statements = split_statements(path.read_text(encoding="utf-8"))
        assert statements, f"{path.name} produced no statements"
        for statement in statements:
            assert statement.strip()


def test_matching_hash_is_skipped(fake_pool: FakePool):
    for path in discover_sql_files():
        fake_pool.db.applied.append((path.name, _checksum(path)))
    results = run_migrations(fake_pool)
    assert [r.status for r in results] == ["skipped"] * len(EXPECTED_FILES)


def test_differing_hash_raises(fake_pool: FakePool):
    fake_pool.db.applied.append(("001_reference_tables.sql", "not-the-real-checksum"))
    with pytest.raises(ChecksumMismatchError) as excinfo:
        run_migrations(fake_pool)
    assert "001_reference_tables.sql" in str(excinfo.value)


def test_fresh_run_applies_all_files(fake_pool: FakePool, monkeypatch, tmp_path):
    monkeypatch.setattr(migrations, "NOTES_PATH", tmp_path / "NOTES.md")
    results = run_migrations(fake_pool)
    assert [r.filename for r in results] == EXPECTED_FILES
    assert all(r.status == "applied" for r in results)
    assert all(r.statement_count > 0 for r in results)
    applied_names = [row[0] for row in fake_pool.db.applied]
    assert applied_names == EXPECTED_FILES


def test_target_stops_after_named_file(fake_pool: FakePool):
    results = run_migrations(fake_pool, target="003_credentials.sql")
    assert [r.filename for r in results] == EXPECTED_FILES[:3]


def test_statement_failure_reports_filename_and_index(fake_pool: FakePool):
    fake_pool.db.failing_substrings.append("create table ingest_runs")
    with pytest.raises(MigrationError) as excinfo:
        run_migrations(fake_pool)
    message = str(excinfo.value)
    assert "001_reference_tables.sql" in message
    assert "statement 3" in message


def test_property_graph_failure_is_logged_not_fatal(
    fake_pool: FakePool, monkeypatch, tmp_path
):
    notes = tmp_path / "NOTES.md"
    notes.write_text("# Environment notes\n")
    monkeypatch.setattr(migrations, "NOTES_PATH", notes)
    fake_pool.db.failing_substrings.append("create property graph")
    results = run_migrations(fake_pool)
    graph_result = results[-1]
    assert graph_result.filename == "008_property_graph.sql"
    assert graph_result.status == "applied_without_graph"
    assert "CREATE PROPERTY GRAPH failed" in notes.read_text()


def test_property_graph_success_is_logged(fake_pool: FakePool, monkeypatch, tmp_path):
    notes = tmp_path / "NOTES.md"
    notes.write_text("# Environment notes\n")
    monkeypatch.setattr(migrations, "NOTES_PATH", notes)
    run_migrations(fake_pool)
    assert "CREATE PROPERTY GRAPH succeeded" in notes.read_text()


def test_cli_help_runs_without_credentials(capsys):
    with pytest.raises(SystemExit) as excinfo:
        migrations.main(["--help"])
    assert excinfo.value.code == 0
    assert "migrations" in capsys.readouterr().out


@pytest.mark.skipif(not has_live_wallet(), reason="no live wallet configured")
def test_live_migration_application():
    from navigator.config import get_settings
    from navigator.db.pool import create_pool

    results = run_migrations(create_pool(get_settings()))
    assert results
