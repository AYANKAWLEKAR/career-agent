"""BLS OEWS bulk-file parser.

OEWS data files are downloaded manually; this module parses them. The BLS API
is deliberately stubbed: it caps at 500 requests per day and returns only the
current year, which makes it unsuitable for corpus assembly.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from navigator.ingest.base import IngestClient, RawRecord

SOURCE_NAME = "bls_oews"


def _sniff_delimiter(sample: str) -> str:
    return "\t" if sample.count("\t") > sample.count(",") else ","


def parse_oews_file(path: Path) -> Iterator[RawRecord]:
    """Yield one RawRecord per occupation-and-area row of an OEWS data file.

    Handles the delimited text exports (comma or tab separated). For the Excel
    export, save it as CSV first; reading .xlsx directly would need a
    dependency that belongs to a later phase.
    """
    if path.suffix.lower() in {".xlsx", ".xls"}:
        raise ValueError(
            f"cannot parse Excel file {path.name} directly; export it as CSV first"
        )
    with path.open(encoding="utf-8-sig", newline="") as handle:
        sample = handle.readline()
        handle.seek(0)
        reader = csv.DictReader(handle, delimiter=_sniff_delimiter(sample))
        for row in reader:
            normalized = {(key or "").strip().lower(): value for key, value in row.items()}
            occ_code = normalized.get("occ_code", "")
            area = normalized.get("area", "")
            yield RawRecord(
                source=SOURCE_NAME,
                record_key=f"{area}/{occ_code}",
                payload=normalized,
            )


class BlsClient(IngestClient):
    source_name = SOURCE_NAME

    def fetch(self):
        # The BLS API caps at 500 requests/day and returns only the current
        # year, so it cannot assemble the corpus. Use parse_oews_file on the
        # bulk download instead.
        raise NotImplementedError(
            "BLS API fetching is out of scope; parse the OEWS bulk files with parse_oews_file"
        )
