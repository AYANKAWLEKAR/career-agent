"""BLS OEWS file parser (no HTTP: the bulk files are downloaded manually)."""

from __future__ import annotations

from pathlib import Path

import pytest

from navigator.ingest.bls import BlsClient, parse_oews_file

FIXTURE = Path(__file__).parent / "fixtures" / "bls_oews_sample.csv"


def test_parse_oews_fixture_counts_and_values():
    records = list(parse_oews_file(FIXTURE))
    assert len(records) == 3
    national = records[0]
    assert national.source == "bls_oews"
    assert national.record_key == "0000000/15-1252"
    assert national.payload["occ_title"] == "Software Developers"
    assert national.payload["a_median"] == "132270"
    metro = records[2]
    assert metro.record_key == "41940/15-1252"
    assert metro.payload["area_title"] == "San Jose-Sunnyvale-Santa Clara, CA"


def test_parse_tab_separated_variant(tmp_path):
    tsv = tmp_path / "oews.txt"
    tsv.write_text("AREA\tOCC_CODE\tA_MEDIAN\n0000000\t29-1141\t86070\n")
    records = list(parse_oews_file(tsv))
    assert len(records) == 1
    assert records[0].record_key == "0000000/29-1141"


def test_excel_file_is_rejected_with_guidance(tmp_path):
    xlsx = tmp_path / "oews.xlsx"
    xlsx.write_bytes(b"PK\x03\x04")
    with pytest.raises(ValueError, match="CSV"):
        list(parse_oews_file(xlsx))


def test_empty_file_yields_nothing(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("AREA,OCC_CODE\n")
    assert list(parse_oews_file(empty)) == []


def test_api_fetch_is_stubbed():
    with pytest.raises(NotImplementedError):
        BlsClient().fetch()
