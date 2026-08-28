"""Parsing for the O*NET Oracle SQL distribution.

The distribution ships one file per table: a ``CREATE TABLE`` followed by one
single-line ``INSERT`` per row. Three dialect details matter when reading it:

* Dates are written as ``TO_DATE('2023-08-01', 'yyyy-mm-dd')``, so a parser that
  splits on commas reads one column as two.
* Values containing ``&`` are split around it and rejoined with ``||``
  (``'Predict &'||' Control'``) to dodge SQL*Plus substitution, so a parser that
  stops at the closing quote reads one column as several.
* The empty string is used for "no value". Oracle stores ``''`` as NULL, which
  collides with the ``NOT NULL`` those same files declare.

This module is shared by ``scripts/fix_onet_sql.py`` (which rewrites the
distribution into files Oracle accepts) and ``scripts/load_onet_sql.py`` (which
loads them in batches).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

INSERT_RE = re.compile(r"^INSERT INTO (\w+) \(([^)]*)\) VALUES \((.*)\);\s*$")
CREATE_RE = re.compile(r"^\s*CREATE TABLE (\w+)", re.IGNORECASE)
_NOT_NULL_RE = re.compile(r"\s+NOT\s+NULL\b", re.IGNORECASE)
_FK_RE = re.compile(
    r"FOREIGN\s+KEY\s*\(([^)]*)\)\s*REFERENCES\s*(\w+)\s*\(([^)]*)\)", re.IGNORECASE
)
_PK_RE = re.compile(r"PRIMARY\s+KEY\s*\(([^)]*)\)", re.IGNORECASE)


def split_top_level(body: str) -> list[str]:
    """Split on commas that are not nested inside parentheses.

    Needed because both column types (``DECIMAL(5,2)``) and key clauses
    (``PRIMARY KEY (a, b)``) contain commas of their own.
    """
    items, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(body[start:i])
            start = i + 1
    items.append(body[start:])
    return [item.strip() for item in items if item.strip()]


def _read_quoted(raw: str, i: int) -> tuple[str, int]:
    """Read a single-quoted literal starting at ``raw[i]``; return (text, next index)."""
    i += 1
    buf: list[str] = []
    n = len(raw)
    while i < n:
        if raw[i] == "'":
            if i + 1 < n and raw[i + 1] == "'":
                buf.append("'")
                i += 2
                continue
            i += 1
            break
        buf.append(raw[i])
        i += 1
    return "".join(buf), i


def _coerce(token: str) -> object:
    if token.upper() == "NULL":
        return None
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def parse_values(raw: str) -> list[object]:
    """Split a SQL ``VALUES`` body into Python values."""
    values: list[object] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch in ", ":
            i += 1
            continue
        if ch == "'":
            text, i = _read_quoted(raw, i)
            # Rejoin '&'-escaping concatenations into the single value they encode.
            while True:
                j = i
                while j < n and raw[j] == " ":
                    j += 1
                if raw[j : j + 2] != "||":
                    break
                j += 2
                while j < n and raw[j] == " ":
                    j += 1
                fragment, i = _read_quoted(raw, j)
                text += fragment
            values.append(text)
            continue
        j = i
        while j < n and raw[j] not in ",(":
            j += 1
        token = raw[i:j].strip()
        if j < n and raw[j] == "(":
            if token.upper() != "TO_DATE":
                raise ValueError(f"unsupported SQL function in VALUES: {token}")
            date_text, k = _read_quoted(raw, raw.index("'", j))
            _, k = _read_quoted(raw, raw.index("'", k))  # discard the format mask
            while k < n and raw[k] != ")":
                k += 1
            values.append(date.fromisoformat(date_text))
            i = k + 1
            continue
        values.append(_coerce(token))
        i = j
    return values


def format_value(value: object) -> str:
    """Render a Python value back into an Oracle literal."""
    if value is None:
        return "NULL"
    if isinstance(value, date):
        return f"TO_DATE('{value.isoformat()}', 'yyyy-mm-dd')"
    if isinstance(value, (int, float)):
        return repr(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]

    def render(self) -> str:
        cols = ", ".join(self.columns)
        ref = ", ".join(self.ref_columns)
        return f"FOREIGN KEY ({cols}) REFERENCES {self.ref_table}({ref})"


@dataclass
class TableSpec:
    """A parsed ``CREATE TABLE``, rebuildable after modification."""

    name: str
    columns: dict[str, str] = field(default_factory=dict)  # name -> type, sans NOT NULL
    not_null: set[str] = field(default_factory=set)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    def render(self) -> str:
        items = [
            f"{name} {dtype}" + (" NOT NULL" if name in self.not_null else "")
            for name, dtype in self.columns.items()
        ]
        if self.primary_key:
            items.append(f"PRIMARY KEY ({', '.join(self.primary_key)})")
        items.extend(fk.render() for fk in self.foreign_keys)
        body = ",\n  ".join(items)
        return f"CREATE TABLE {self.name} (\n  {body});"


def parse_ddl(ddl: str) -> TableSpec:
    """Parse a ``CREATE TABLE`` statement into a modifiable spec."""
    name_match = CREATE_RE.match(ddl)
    if name_match is None:
        raise ValueError("not a CREATE TABLE statement")
    spec = TableSpec(name=name_match.group(1))
    body = ddl[ddl.index("(") + 1 : ddl.rindex(")")]
    for item in split_top_level(body):
        upper = item.upper()
        if upper.startswith("PRIMARY KEY"):
            match = _PK_RE.search(item)
            if match:
                spec.primary_key = [c.strip() for c in match.group(1).split(",")]
            continue
        if upper.startswith("FOREIGN KEY"):
            match = _FK_RE.search(item)
            if match:
                spec.foreign_keys.append(
                    ForeignKey(
                        columns=[c.strip() for c in match.group(1).split(",")],
                        ref_table=match.group(2),
                        ref_columns=[c.strip() for c in match.group(3).split(",")],
                    )
                )
            continue
        stripped = _NOT_NULL_RE.sub("", item)
        column_name, _, dtype = stripped.strip().partition(" ")
        spec.columns[column_name] = dtype.strip()
        if _NOT_NULL_RE.search(item):
            spec.not_null.add(column_name)
    return spec
