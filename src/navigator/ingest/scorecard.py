"""College Scorecard API client and bulk CSV loader.

The downloadable CSV is the preferred path; the API (keyed via query
parameter) is for incremental pulls.
"""

from __future__ import annotations

import csv
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx

from navigator.ingest.base import (
    ConfigurationError,
    IngestClient,
    RawRecord,
    request_with_retry,
)

BASE_URL = "https://api.data.gov/ed/collegescorecard/v1/schools"
PAGE_SIZE = 100

INSTITUTION_FIELDS = (
    "id,school.name,school.city,school.state,location.lat,location.lon,school.ownership"
)
_PROGRAM = "latest.programs.cip_4_digit"
# Field names verified against the live API (2026-11-12). Requesting a nested
# sub-path returns only that leaf, so one page carries each school plus its
# 4-digit-CIP programs without the ~50 repayment/count columns we never read.
FIELD_OF_STUDY_FIELDS = ",".join((
    "id", "school.name", "school.city", "school.state", "school.ownership",
    "location.lat", "location.lon",
    f"{_PROGRAM}.code", f"{_PROGRAM}.title",
    f"{_PROGRAM}.credential.level", f"{_PROGRAM}.credential.title",
    f"{_PROGRAM}.counts.ipeds_awards1",
    f"{_PROGRAM}.earnings.1_yr.overall_median_earnings",
    f"{_PROGRAM}.earnings.4_yr.overall_median_earnings",
    f"{_PROGRAM}.earnings.5_yr.overall_median_earnings",
    f"{_PROGRAM}.earnings.7_yr.overall_median_earnings",
    f"{_PROGRAM}.debt.staff_grad_plus.all.eval_inst.median",
    f"{_PROGRAM}.debt.staff_grad_plus.all.eval_inst.median_payment",
))


class ScorecardClient(IngestClient):
    source_name = "scorecard"

    def __init__(
        self,
        api_key: str | None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key:
            raise ConfigurationError(
                "College Scorecard API access requires scorecard_api_key"
                " (SCORECARD_API_KEY); use load_bulk_file for the keyless path"
            )
        self._api_key = api_key
        self._base_url = base_url
        self._client = client or httpx.AsyncClient()

    async def _get_page(self, page: int, fields: str) -> dict[str, Any]:
        response = await request_with_retry(
            self._client,
            "GET",
            self._base_url,
            params={
                "api_key": self._api_key,
                "school.operating": 1,
                "fields": fields,
                "page": page,
                "per_page": PAGE_SIZE,
            },
        )
        return response.json()

    async def fetch_institutions(self, page: int = 0) -> AsyncIterator[RawRecord]:
        """Yield institutions from ``page`` onward, following pagination."""
        while True:
            data = await self._get_page(page, INSTITUTION_FIELDS)
            results = data.get("results", [])
            for result in results:
                yield RawRecord(
                    source=self.source_name,
                    record_key=str(result.get("id", "")),
                    payload=result,
                )
            total = data.get("metadata", {}).get("total", 0)
            page += 1
            if not results or page * PAGE_SIZE >= total:
                break

    async def fetch_field_of_study(self, page: int = 0) -> AsyncIterator[RawRecord]:
        """Yield field-of-study rows from ``page`` onward, following pagination."""
        while True:
            data = await self._get_page(page, FIELD_OF_STUDY_FIELDS)
            results = data.get("results", [])
            for result in results:
                yield RawRecord(
                    source=self.source_name,
                    record_key=f"{result.get('id', '')}/fos",
                    payload=result,
                )
            total = data.get("metadata", {}).get("total", 0)
            page += 1
            if not results or page * PAGE_SIZE >= total:
                break

    async def fetch(self) -> AsyncIterator[RawRecord]:
        async for record in self.fetch_institutions():
            yield record

    async def aclose(self) -> None:
        await self._client.aclose()


def load_bulk_file(path: Path) -> Iterator[RawRecord]:
    """Read the downloadable Scorecard CSV, one RawRecord per row."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield RawRecord(
                source=ScorecardClient.source_name,
                record_key=row.get("UNITID") or row.get("unitid") or "",
                payload=dict(row),
            )
