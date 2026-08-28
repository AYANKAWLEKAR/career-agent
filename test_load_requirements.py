"""SQL-shape and transaction tests for the O*NET requirement loader."""

from __future__ import annotations

import pytest

from scripts.load_requirements import (
    EDUCATION_SOURCE,
    EXPERIENCE_SOURCE,
    TOOL_SOURCE,
    _run_section,
    load_education,
    load_experience,
    load_tools,
)


class RecordingCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, sql: str, **params) -> None:
        self.calls.append((sql, params))


@pytest.mark.parametrize(
    ("loader", "source", "expected_fragments"),
    [
        (
            load_tools,
            TOOL_SOURCE,
            ["from software_skills", "hot_technology = 'Y'", "requirement_type = 'tool'"],
        ),
        (
            load_education,
            EDUCATION_SOURCE,
            ["from education_categories", "e.data_value >= 10", "alternative_group"],
        ),
        (
            load_experience,
            EXPERIENCE_SOURCE,
            ["from training_and_experience_categories", "cumulative_pct >= 50", "rw-"],
        ),
    ],
)
def test_loader_sections_are_source_scoped_and_set_based(loader, source, expected_fragments):
    cursor = RecordingCursor()
    loader(cursor)

    assert len(cursor.calls) == 3
    delete_sql, delete_params = cursor.calls[0]
    assert "delete from occupation_requirements" in delete_sql
    assert delete_params == {"source": source}
    combined_sql = "\n".join(sql for sql, _ in cursor.calls).lower()
    for fragment in expected_fragments:
        assert fragment.lower() in combined_sql


class TransactionConnection:
    def __init__(self) -> None:
        self.cursor_value = object()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_section_commits_after_success():
    connection = TransactionConnection()
    seen = []
    _run_section(connection, "tools", seen.append)
    assert seen == [connection.cursor_value]
    assert (connection.commits, connection.rollbacks) == (1, 0)


def test_section_rolls_back_and_reraises_failure():
    connection = TransactionConnection()

    def fail(_cursor) -> None:
        raise RuntimeError("simulated Oracle error")

    with pytest.raises(RuntimeError, match="simulated Oracle error"):
        _run_section(connection, "tools", fail)
    assert (connection.commits, connection.rollbacks) == (0, 1)
