"""Bulk-load the corrected O*NET Oracle distribution into the configured database.

Run ``scripts/fix_onet_sql.py`` first: this loader executes each file's
``CREATE TABLE`` verbatim, so it expects the corrected files, which keep their
foreign keys and NOT NULL declarations.

Each file holds one single-line ``INSERT`` per row. Executing those verbatim would
mean a million network round trips, so the VALUES tuple is parsed out of each line
and streamed through ``executemany`` in batches.

Because foreign keys are enforced, a table cannot load before the tables it
references. Files are therefore grouped into dependency levels -- level 0 references
nothing, level N references only levels below it -- and each level is loaded in
parallel before the next begins.

Usage:
    uv run python scripts/load_onet_sql.py /path/to/db_30_3_oracle_fixed [--only 27_task]

Re-running is safe: a table that already holds rows is skipped.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import oracledb

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.ingest.onet_sql import INSERT_RE, TableSpec, parse_ddl, parse_values

BATCH_SIZE = 5000

_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def read_ddl(path: Path) -> str:
    """Return the file's CREATE TABLE text, dropping comments and SQL*Plus directives."""
    lines: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("INSERT INTO "):
                break
            stripped = line.strip()
            if not stripped or stripped.startswith("--") or stripped.upper().startswith("SET "):
                continue
            lines.append(line)
    return "".join(lines).strip().rstrip(";")


def dependency_levels(specs: dict[Path, TableSpec]) -> list[list[Path]]:
    """Group files so every file lands in a level above each table it references."""
    by_table = {spec.name.lower(): path for path, spec in specs.items()}
    level: dict[Path, int] = {}

    def resolve(path: Path, seen: frozenset[Path] = frozenset()) -> int:
        if path in level:
            return level[path]
        if path in seen:  # circular reference: treat as a root and let Oracle judge
            return 0
        depth = 0
        for fk in specs[path].foreign_keys:
            parent = by_table.get(fk.ref_table.lower())
            if parent is not None and parent != path:
                depth = max(depth, resolve(parent, seen | {path}) + 1)
        level[path] = depth
        return depth

    for path in specs:
        resolve(path)
    grouped: dict[int, list[Path]] = {}
    for path, depth in level.items():
        grouped.setdefault(depth, []).append(path)
    return [
        sorted(grouped[d], key=lambda p: p.stat().st_size, reverse=True)
        for d in sorted(grouped)
    ]


def _table_has_rows(cursor, table: str) -> bool:
    cursor.execute("select count(*) from user_tables where table_name = :t", t=table.upper())
    if cursor.fetchone()[0] == 0:
        return False
    cursor.execute(f"select count(*) from {table}")  # noqa: S608
    return cursor.fetchone()[0] > 0


def create_tables(pool, specs: dict[Path, TableSpec], levels: list[list[Path]]) -> None:
    """Create every table up front, sequentially, in dependency order.

    Adding a foreign key locks the referenced table, so concurrent CREATE TABLE
    statements against a shared parent (every table references
    ``content_model_reference`` or ``occupation_data``) collide with ORA-00054.
    DDL is cheap; doing it serially costs a second and removes the contention
    entirely. Data then loads in parallel, where foreign key validation takes only
    a shared lock and sessions do not block each other.
    """
    with pool.acquire() as connection:
        cursor = connection.cursor()
        created = 0
        for path in [p for level in levels for p in level]:
            spec = specs[path]
            cursor.execute(
                "select count(*) from user_tables where table_name = :t", t=spec.name.upper()
            )
            if cursor.fetchone()[0] == 0:
                cursor.execute(read_ddl(path))
                created += 1
        connection.commit()
    _log(f"  created {created} tables")


def load_file(pool, path: Path, spec: TableSpec) -> tuple[str, int, str]:
    """Load one file's rows. The table is expected to exist already."""
    started = time.monotonic()
    with pool.acquire() as connection:
        cursor = connection.cursor()
        if _table_has_rows(cursor, spec.name):
            cursor.execute(f"select count(*) from {spec.name}")  # noqa: S608
            return (spec.name, cursor.fetchone()[0], "skipped (already loaded)")

        rows: list[list[object]] = []
        inserted = 0
        insert_sql = None
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                match = INSERT_RE.match(line)
                if match is None:
                    continue
                if insert_sql is None:
                    columns = match.group(2)
                    binds = ", ".join(f":{k + 1}" for k in range(len(columns.split(","))))
                    insert_sql = (
                        f"INSERT INTO {match.group(1)} ({columns}) VALUES ({binds})"  # noqa: S608
                    )
                rows.append(parse_values(match.group(3)))
                if len(rows) >= BATCH_SIZE:
                    cursor.executemany(insert_sql, rows)
                    inserted += len(rows)
                    rows.clear()
        if rows and insert_sql:
            cursor.executemany(insert_sql, rows)
            inserted += len(rows)
        connection.commit()
    return (spec.name, inserted, f"loaded in {time.monotonic() - started:.1f}s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="corrected distribution directory")
    parser.add_argument("--only", help="load just files whose name contains this substring")
    args = parser.parse_args(argv)

    files = sorted(args.directory.glob("[0-9][0-9]_*.sql"))
    if args.only:
        files = [f for f in files if args.only in f.name]
    if not files:
        print(f"no distribution files found in {args.directory}", file=sys.stderr)
        return 1

    specs = {path: parse_ddl(read_ddl(path)) for path in files}
    settings = get_settings()
    pool = create_pool(settings)
    failures: list[str] = []

    def run(path: Path) -> None:
        try:
            table, count, status = load_file(pool, path, specs[path])
            _log(f"  {path.name}: {table} {count} rows — {status}")
        except (oracledb.Error, OSError, ValueError) as exc:
            failures.append(path.name)
            _log(f"  {path.name}: FAILED — {exc}")

    levels = dependency_levels(specs)
    workers = max(1, min(settings.pool_max, len(files)))
    _log("Creating tables:")
    create_tables(pool, specs, levels)
    _log(f"Loading {len(files)} files across {len(levels)} dependency levels:")
    for depth, level_files in enumerate(levels):
        _log(f"level {depth} ({len(level_files)} files):")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(run, level_files))
        if failures:
            _log(f"stopping: level {depth} had failures, dependents cannot load")
            break

    if failures:
        _log(f"FAILED files: {', '.join(failures)}")
        return 1
    _log("All files loaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
