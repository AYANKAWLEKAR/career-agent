"""Migration runner for the numbered SQL files in ``sql/``.

Statements are split on ``;`` for plain SQL and on a lone ``/`` line for
PL/SQL blocks, so blocks containing embedded semicolons execute intact.

A ``schema_migrations`` table records each applied file with a SHA-256 of its
contents. A file whose hash matches an applied row is skipped; a file whose
hash differs raises, because silently re-applying changed DDL is how schemas
drift.
"""

from __future__ import annotations

import argparse
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from navigator.db.pool import acquire

SQL_DIR = Path(__file__).parent / "sql"
NOTES_PATH = Path(__file__).resolve().parents[3] / "NOTES.md"

_PLSQL_STARTERS = ("declare", "begin")

_ENSURE_SCHEMA_MIGRATIONS = """
declare
  l_count number;
begin
  select count(*) into l_count from user_tables where table_name = 'SCHEMA_MIGRATIONS';
  if l_count = 0 then
    execute immediate q'[
      create table schema_migrations (
        filename   varchar2(200) primary key,
        checksum   varchar2(64) not null,
        applied_at timestamp with time zone default systimestamp not null,
        status     varchar2(20) not null
      )
    ]';
  end if;
end;
"""


class MigrationError(Exception):
    pass


class ChecksumMismatchError(MigrationError):
    pass


@dataclass
class MigrationResult:
    filename: str
    status: str
    statement_count: int = 0
    elapsed_ms: float = 0.0
    error: str | None = None


@dataclass
class _Statement:
    text: str
    is_plsql: bool = field(default=False)


def discover_sql_files(sql_dir: Path = SQL_DIR) -> list[Path]:
    """Return the migration files in filename order."""
    return sorted(sql_dir.glob("*.sql"))


def _first_keyword(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        return stripped.split()[0].lower()
    return ""


def split_statements(sql_text: str) -> list[str]:
    """Split a migration file into executable statements.

    PL/SQL blocks (starting with DECLARE or BEGIN) run until a line holding
    only ``/``; everything else terminates on ``;``.
    """
    statements: list[str] = []
    buffer: list[str] = []
    in_plsql = False

    def flush() -> None:
        nonlocal in_plsql
        text = "\n".join(buffer).strip()
        buffer.clear()
        in_plsql = False
        # Drop chunks that are only comments or whitespace.
        if _first_keyword(text):
            statements.append(text)

    for line in sql_text.splitlines():
        stripped = line.strip()
        if in_plsql:
            if stripped == "/":
                flush()
            else:
                buffer.append(line)
            continue

        buffer.append(line)
        if not in_plsql and _first_keyword("\n".join(buffer)) in _PLSQL_STARTERS:
            in_plsql = True
            continue
        if stripped.endswith(";"):
            buffer[-1] = line.rstrip().rstrip(";")
            flush()

    if _first_keyword("\n".join(buffer)):
        flush()
    return statements


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _applied_migrations(connection) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute("select filename, checksum from schema_migrations")
        return {row[0]: row[1] for row in cursor.fetchall()}


def _record_migration(connection, filename: str, checksum: str, status: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "insert into schema_migrations (filename, checksum, status)"
            " values (:1, :2, :3)",
            [filename, checksum, status],
        )
    connection.commit()


def _note_property_graph_outcome(message: str, notes_path: Path | None = None) -> None:
    """Append the CREATE PROPERTY GRAPH outcome to NOTES.md."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    line = f"- [{stamp}] property graph: {message}\n"
    try:
        with (notes_path or NOTES_PATH).open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def _is_property_graph_statement(statement: str) -> bool:
    return "create property graph" in statement.lower()


def run_migrations(pool, target: str | None = None) -> list[MigrationResult]:
    """Apply migration files in order, up to and including ``target`` if given."""
    results: list[MigrationResult] = []
    with acquire(pool) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_ENSURE_SCHEMA_MIGRATIONS.strip())
        connection.commit()
        applied = _applied_migrations(connection)

        for path in discover_sql_files():
            if target is not None and path.name > target:
                break
            checksum = _checksum(path)
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise ChecksumMismatchError(
                        f"{path.name} was applied with a different checksum;"
                        " refusing to re-run changed DDL"
                    )
                results.append(MigrationResult(path.name, "skipped"))
                continue

            statements = split_statements(path.read_text(encoding="utf-8"))
            started = time.monotonic()
            status = "applied"
            error: str | None = None
            for index, statement in enumerate(statements):
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(statement)
                except Exception as exc:  # noqa: BLE001 - driver errors vary by backend
                    if _is_property_graph_statement(statement):
                        # Feature may be unavailable on the instance; record and
                        # continue so the next phase knows to use the CTE fallback.
                        _note_property_graph_outcome(f"CREATE PROPERTY GRAPH failed ({exc})")
                        status = "applied_without_graph"
                        continue
                    error = f"{path.name} statement {index}: {exc}"
                    status = "failed"
                    break

            elapsed_ms = (time.monotonic() - started) * 1000
            if status != "failed":
                if _is_property_graph_statement("\n".join(statements)) and status == "applied":
                    _note_property_graph_outcome("CREATE PROPERTY GRAPH succeeded")
                _record_migration(connection, path.name, checksum, status)
            results.append(
                MigrationResult(path.name, status, len(statements), elapsed_ms, error)
            )
            if status == "failed":
                raise MigrationError(error)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m navigator.db.migrations",
        description="Apply the numbered SQL migrations to the configured database.",
    )
    parser.add_argument(
        "--target",
        default=None,
        help="stop after this filename (e.g. 004_geography_and_wages.sql)",
    )
    args = parser.parse_args(argv)

    # Imports that need credentials stay inside main so --help works without a .env.
    from navigator.config import get_settings
    from navigator.db.pool import create_pool

    pool = create_pool(get_settings())
    for result in run_migrations(pool, target=args.target):
        print(
            f"{result.filename}: {result.status}"
            f" ({result.statement_count} statements, {result.elapsed_ms:.0f} ms)"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
