"""O*NET Web Services client and bulk-file loader.

The bulk database text files are the preferred path for the full corpus; the
API is for spot checks. API auth is HTTP Basic with the API key as both
username and password (or a bearer token for newer accounts).
"""

from __future__ import annotations

import asyncio
import csv
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx

from navigator.ingest.base import IngestClient, RawRecord, request_with_retry

BASE_URL = "https://services.onetcenter.org/ws/"
MAX_CONCURRENT_REQUESTS = 5


class OnetClient(IngestClient):
    source_name = "onet"

    def __init__(
        self,
        api_key: str | None = None,
        bearer_token: str | None = None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ):
        auth = None
        headers = {"Accept": "application/json"}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif api_key:
            auth = httpx.BasicAuth(api_key, api_key)
        self._client = client or httpx.AsyncClient(
            base_url=base_url, auth=auth, headers=headers
        )
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        async with self._semaphore:
            response = await request_with_retry(self._client, "GET", path, params=params)
        return response.json()

    async def fetch_occupations(self) -> AsyncIterator[RawRecord]:
        """Yield one record per occupation, following pagination."""
        start = 1
        while True:
            data = await self._get_json("online/occupations/", start=start, end=start + 49)
            occupations = data.get("occupation", [])
            for occupation in occupations:
                yield RawRecord(
                    source=self.source_name,
                    record_key=occupation["code"],
                    payload=occupation,
                )
            total = data.get("total", 0)
            start += len(occupations)
            if not occupations or start > total:
                break

    async def fetch_tasks(self, soc_code: str) -> AsyncIterator[RawRecord]:
        data = await self._get_json(f"online/occupations/{soc_code}/details/tasks")
        for task in data.get("task", []):
            yield RawRecord(
                source=self.source_name,
                record_key=f"{soc_code}/task/{task.get('id', '')}",
                payload={"soc_code": soc_code, **task},
            )

    async def fetch_skills(self, soc_code: str) -> AsyncIterator[RawRecord]:
        data = await self._get_json(f"online/occupations/{soc_code}/details/skills")
        for element in data.get("element", []):
            yield RawRecord(
                source=self.source_name,
                record_key=f"{soc_code}/skill/{element.get('id', '')}",
                payload={"soc_code": soc_code, **element},
            )

    async def fetch_related(self, soc_code: str) -> AsyncIterator[RawRecord]:
        data = await self._get_json(f"online/occupations/{soc_code}/related_occupations")
        for related in data.get("occupation", []):
            yield RawRecord(
                source=self.source_name,
                record_key=f"{soc_code}/related/{related.get('code', '')}",
                payload={"soc_code": soc_code, **related},
            )

    async def fetch(self) -> AsyncIterator[RawRecord]:
        async for record in self.fetch_occupations():
            yield record

    async def aclose(self) -> None:
        await self._client.aclose()


def load_bulk_file(path: Path) -> Iterator[RawRecord]:
    """Read a downloadable O*NET database file (tab-separated text or JSON)."""
    if path.suffix.lower() == ".json":
        payloads = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payloads, dict):
            payloads = payloads.get("occupation", [payloads])
        for payload in payloads:
            yield RawRecord(
                source=OnetClient.source_name,
                record_key=payload.get("code") or payload.get("onetsoc_code", ""),
                payload=payload,
            )
        return
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            key = row.get("O*NET-SOC Code") or row.get("onetsoc_code") or ""
            yield RawRecord(source=OnetClient.source_name, record_key=key, payload=dict(row))
