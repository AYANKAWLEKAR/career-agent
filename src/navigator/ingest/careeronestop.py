"""CareerOneStop API client for certifications and licenses.

The one source that hard-requires credentials: a bearer token plus a user ID
embedded in the URL path. Every method raises a clear configuration error
naming the missing setting when credentials are absent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from navigator.ingest.base import (
    ConfigurationError,
    IngestClient,
    RawRecord,
    request_with_retry,
)

BASE_URL = "https://api.careeronestop.org/v1/"
PAGE_SIZE = 50


class CareerOneStopClient(IngestClient):
    source_name = "careeronestop"

    def __init__(
        self,
        user_id: str | None,
        token: str | None,
        base_url: str = BASE_URL,
        client: httpx.AsyncClient | None = None,
    ):
        if not user_id:
            raise ConfigurationError(
                "CareerOneStop requires careeronestop_user_id (CAREERONESTOP_USER_ID)"
            )
        if not token:
            raise ConfigurationError(
                "CareerOneStop requires careeronestop_token (CAREERONESTOP_TOKEN)"
            )
        self._user_id = user_id
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def _get_json(self, path: str, **params: Any) -> dict[str, Any]:
        response = await request_with_retry(self._client, "GET", path, params=params)
        return response.json()

    async def fetch_certifications(self, soc_code: str) -> AsyncIterator[RawRecord]:
        data = await self._get_json(
            f"certificationfinder/{self._user_id}/{soc_code}/0/0/0/0/0/0/0/0/1/{PAGE_SIZE}"
        )
        for cert in data.get("CertList", []):
            yield RawRecord(
                source=self.source_name,
                record_key=f"{soc_code}/cert/{cert.get('Id', '')}",
                payload={"soc_code": soc_code, **cert},
            )

    async def fetch_licenses(self, soc_code: str, state: str) -> AsyncIterator[RawRecord]:
        data = await self._get_json(
            f"license/{self._user_id}/{soc_code}/{state}/0/1/{PAGE_SIZE}"
        )
        for lic in data.get("LicenseList", []):
            yield RawRecord(
                source=self.source_name,
                record_key=f"{soc_code}/{state}/license/{lic.get('Id', '')}",
                payload={"soc_code": soc_code, "state": state, **lic},
            )

    async def fetch(self) -> AsyncIterator[RawRecord]:
        raise NotImplementedError(
            "CareerOneStop fetching is per-SOC; use fetch_certifications/fetch_licenses"
        )
        yield  # pragma: no cover - makes this an async generator

    async def aclose(self) -> None:
        await self._client.aclose()
