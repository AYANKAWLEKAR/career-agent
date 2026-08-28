"""Rewrite the O*NET Oracle distribution into files Oracle actually accepts.

The vendor ships these files as the Oracle build, but they cannot be executed
against Oracle as published. Rather than working around that at load time (which
costs every constraint in the staging schema), this produces a corrected copy of
the distribution that any Oracle client can run -- SQL*Plus, SQLcl, or the batch
loader in ``load_onet_sql.py``.

Exactly three things are changed, each only where the data proves it necessary:

1. ``SET DEFINE OFF`` is prepended so SQL*Plus does not treat ``&`` as a
   substitution prefix. The distribution works around this by splitting literals
   and concatenating them; with DEFINE off those are collapsed back into plain
   literals.
2. ``NOT NULL`` is dropped from precisely the columns the data ships ``''`` for.
   Oracle stores the empty string as NULL, so those two declarations contradict
   each other. Every other ``NOT NULL`` survives.
3. Foreign keys that reference a non-unique column are dropped, because Oracle
   rejects the whole table otherwise (ORA-02270). Every other foreign key
   survives, so the staging schema keeps its referential integrity.

Rows are re-emitted only when they contain an empty string; all other lines are
copied byte for byte.

Usage:
    uv run python scripts/fix_onet_sql.py SOURCE_DIR OUTPUT_DIR
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from navigator.ingest.onet_sql import (
    INSERT_RE,
    TableSpec,
    format_value,
    parse_ddl,
    parse_values,
)

HEADER = (
    "-- Rewritten by scripts/fix_onet_sql.py so this file loads on Oracle.\n"
    "-- See NOTES.md for the defects being corrected.\n"
    "SET DEFINE OFF\n"
)


def read_ddl(path: Path) -> tuple[str, list[str]]:
    """Return the file's CREATE TABLE text and the lines preceding the first INSERT."""
    lines: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("INSERT INTO "):
                break
            lines.append(line)
    return "".join(lines).strip().rstrip(";"), lines


def scan_empty_columns(path: Path, columns: list[str]) -> set[str]:
    """Return the columns for which at least one row ships an empty string."""
    empty: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            match = INSERT_RE.match(line)
            if match is None:
                continue
            names = [c.strip() for c in match.group(2).split(",")]
            for name, value in zip(names, parse_values(match.group(3)), strict=True):
                if value == "":
                    empty.add(name)
            if len(empty) == len(columns):
                break  # nothing left to discover
    return empty


def unique_columns(specs: dict[str, TableSpec]) -> dict[str, set[tuple[str, ...]]]:
    """Map each table to the column tuples Oracle will accept as an FK target."""
    return {
        name: {tuple(spec.primary_key)} if spec.primary_key else set()
        for name, spec in specs.items()
    }


def rewrite_rows(src: Path, dst_handle, empty_columns: set[str]) -> int:
    """Copy rows, re-emitting only those that carry an empty string. Returns rows written."""
    rewritten = 0
    with src.open(encoding="utf-8") as fh:
        for line in fh:
            match = INSERT_RE.match(line)
            if match is None:
                continue
            if "''" not in line:
                dst_handle.write(line)  # untouched: no empty string, no escaped quote
                continue
            table, columns, body = match.groups()
            values = parse_values(body)
            if not any(value == "" for value in values):
                dst_handle.write(line)
                continue
            rendered = ", ".join(
                format_value(None if value == "" else value) for value in values
            )
            dst_handle.write(f"INSERT INTO {table} ({columns}) VALUES ({rendered});\n")
            rewritten += 1
    return rewritten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="unzipped distribution directory")
    parser.add_argument("output", type=Path, help="directory to write corrected files to")
    args = parser.parse_args(argv)

    files = sorted(args.source.glob("[0-9][0-9]_*.sql"))
    if not files:
        print(f"no distribution files found in {args.source}", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    specs = {}
    ddl_text = {}
    for path in files:
        ddl, _ = read_ddl(path)
        spec = parse_ddl(ddl)
        specs[spec.name] = spec
        ddl_text[path] = spec

    targets = unique_columns(specs)
    dropped_fks: list[str] = []
    relaxed: list[str] = []

    for path in files:
        spec = ddl_text[path]

        empty = scan_empty_columns(path, list(spec.columns))
        for column in sorted(empty & spec.not_null):
            spec.not_null.discard(column)
            relaxed.append(f"{spec.name}.{column}")

        kept = []
        for fk in spec.foreign_keys:
            valid = tuple(fk.ref_columns) in targets.get(fk.ref_table, set())
            if valid:
                kept.append(fk)
            else:
                dropped_fks.append(
                    f"{spec.name}.{','.join(fk.columns)} -> "
                    f"{fk.ref_table}({','.join(fk.ref_columns)})"
                )
        spec.foreign_keys = kept

        out = args.output / path.name
        with out.open("w", encoding="utf-8") as fh:
            fh.write(HEADER)
            fh.write(spec.render())
            fh.write("\n\n")
            rewritten = rewrite_rows(path, fh, empty)
        note = f" ({rewritten} rows rewritten)" if rewritten else ""
        print(f"  {path.name}{note}", flush=True)

    readme = args.source / "Read Me.txt"
    if readme.is_file():
        shutil.copy2(readme, args.output / readme.name)

    print()
    print(f"NOT NULL relaxed on {len(relaxed)} columns (data ships ''):")
    for item in relaxed:
        print(f"  {item}")
    print(f"Foreign keys dropped as invalid on Oracle: {len(dropped_fks)}")
    for item in dropped_fks:
        print(f"  {item}")
    kept_total = sum(len(s.foreign_keys) for s in specs.values())
    print(f"Foreign keys preserved: {kept_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
