"""O*NET client: parsing, retry behavior, bulk file loading, staging writes."""

from __future__ import annotations

import httpx
import pytest

from navigator.ingest.base import RetryableStatusError, write_staging
from navigator.ingest.models import RawRecord
from navigator.ingest.onet import BASE_URL, OnetClient, load_bulk_file
from tests.conftest import FakePool, load_fixture

OCCUPATIONS_URL = f"{BASE_URL}online/occupations/"


async def collect(iterator):
    return [record async for record in iterator]


@pytest.fixture
def client() -> OnetClient:
    return OnetClient(api_key="test-key")


async def test_successful_response_parses_records(respx_mock, client):
    fixture = load_fixture("onet_occupation_sample.json")
    respx_mock.get(OCCUPATIONS_URL).respond(json=fixture)

    records = await collect(client.fetch_occupations())

    assert len(records) == 2
    assert all(isinstance(record, RawRecord) for record in records)
    assert records[0].source == "onet"
    assert records[0].record_key == "15-1252.00"
    assert records[0].payload["title"] == "Software Developers"
    assert records[1].record_key == "29-1141.00"


async def test_fetch_tasks_parses_records(respx_mock, client):
    fixture = load_fixture("onet_task_sample.json")
    respx_mock.get(f"{OCCUPATIONS_URL}15-1252.00/details/tasks").respond(json=fixture)

    records = await collect(client.fetch_tasks("15-1252.00"))

    assert len(records) == 3
    assert records[0].record_key == "15-1252.00/task/8823"
    assert records[0].payload["category"] == "Core"
    assert records[0].payload["soc_code"] == "15-1252.00"


async def test_500_then_200_retries_once_and_succeeds(respx_mock, client):
    fixture = load_fixture("onet_occupation_sample.json")
    route = respx_mock.get(OCCUPATIONS_URL)
    route.side_effect = [httpx.Response(500), httpx.Response(200, json=fixture)]

    records = await collect(client.fetch_occupations())

    assert len(records) == 2
    assert route.call_count == 2


async def test_three_consecutive_500s_raise(respx_mock, client):
    route = respx_mock.get(OCCUPATIONS_URL)
    route.side_effect = [httpx.Response(500)] * 3

    with pytest.raises(RetryableStatusError):
        await collect(client.fetch_occupations())
    assert route.call_count == 3


async def test_429_is_retried(respx_mock, client):
    fixture = load_fixture("onet_occupation_sample.json")
    route = respx_mock.get(OCCUPATIONS_URL)
    route.side_effect = [httpx.Response(429), httpx.Response(200, json=fixture)]

    records = await collect(client.fetch_occupations())

    assert len(records) == 2
    assert route.call_count == 2


async def test_empty_result_set_returns_empty_iterator(respx_mock, client):
    respx_mock.get(OCCUPATIONS_URL).respond(json={"total": 0, "occupation": []})
    assert await collect(client.fetch_occupations()) == []


def test_load_bulk_file_tab_separated(tmp_path):
    bulk = tmp_path / "Occupation Data.txt"
    bulk.write_text(
        "O*NET-SOC Code\tTitle\tDescription\n"
        "15-1252.00\tSoftware Developers\tDevelop applications.\n"
        "29-1141.00\tRegistered Nurses\tProvide patient care.\n"
    )
    records = list(load_bulk_file(bulk))
    assert len(records) == 2
    assert records[0].record_key == "15-1252.00"
    assert records[1].payload["Title"] == "Registered Nurses"


def test_load_bulk_file_json(tmp_path):
    bulk = tmp_path / "occupations.json"
    bulk.write_text(
        '{"occupation": [{"code": "15-1252.00", "title": "Software Developers"}]}'
    )
    records = list(load_bulk_file(bulk))
    assert len(records) == 1
    assert records[0].record_key == "15-1252.00"


def test_write_staging_batches_of_500(fake_pool: FakePool):
    records = (
        RawRecord(source="onet", record_key=str(i), payload={"i": i}) for i in range(1201)
    )
    total = write_staging(fake_pool, records, batch_id="batch-1")
    assert total == 1201
    sizes = [len(batch) for batch in fake_pool.db.executemany_batches]
    assert sizes == [500, 500, 201]
    assert fake_pool.connection.commits == 1
    assert fake_pool.released == 1
