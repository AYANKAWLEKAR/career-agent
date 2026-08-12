"""Shared ingest plumbing: the client interface, retry policy, and staging writes.

Clients fetch raw payloads and write them into ``staging_raw`` untouched. No
transformation, normalization, or embedding happens here — that separation is
deliberate and must not be collapsed.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx
import tenacity

from navigator.db.pool import acquire
from navigator.ingest.models import RawRecord

STAGING_BATCH_SIZE = 500
RETRY_ATTEMPTS = 3
# Module-level so tests can swap in a zero wait.
RETRY_WAIT: tenacity.wait.wait_base = tenacity.wait_exponential(multiplier=0.5, max=8)


class ConfigurationError(Exception):
    """A required setting is missing; the message names it."""


class RetryableStatusError(Exception):
    """An HTTP 429 or 5xx response, eligible for retry."""

    def __init__(self, response: httpx.Response):
        self.response = response
        super().__init__(f"retryable HTTP status {response.status_code}")


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError | RetryableStatusError)


async def request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """Issue a request, retrying connection errors and 429/5xx responses.

    Exponential backoff, three attempts total. Non-retryable HTTP errors
    (4xx other than 429) raise ``httpx.HTTPStatusError`` immediately.
    """
    retrying = tenacity.AsyncRetrying(
        stop=tenacity.stop_after_attempt(RETRY_ATTEMPTS),
        wait=RETRY_WAIT,
        retry=tenacity.retry_if_exception(_is_retryable),
        reraise=True,
    )
    async for attempt in retrying:
        with attempt:
            response = await client.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableStatusError(response)
            response.raise_for_status()
            return response
    raise AssertionError("unreachable")  # pragma: no cover


class IngestClient(ABC):
    """A source-specific fetcher yielding raw records for staging."""

    source_name: str

    @abstractmethod
    def fetch(self) -> AsyncIterator[RawRecord]:
        """Yield every raw record from the source."""


def write_staging(pool, records: Iterable[RawRecord], batch_id: str) -> int:
    """Insert raw records into staging_raw in batches of 500. Returns row count."""
    sql = (
        "insert into staging_raw (source, record_key, payload, batch_id)"
        " values (:1, :2, :3, :4)"
    )
    total = 0
    with acquire(pool) as connection:
        with connection.cursor() as cursor:
            batch: list[tuple[str, str, str, str]] = []
            for record in records:
                batch.append(
                    (record.source, record.record_key, json.dumps(record.payload), batch_id)
                )
                if len(batch) >= STAGING_BATCH_SIZE:
                    cursor.executemany(sql, batch)
                    total += len(batch)
                    batch.clear()
            if batch:
                cursor.executemany(sql, batch)
                total += len(batch)
        connection.commit()
    return total
