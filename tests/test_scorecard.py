"""College Scorecard client: parsing, pagination, retries, bulk CSV, config."""

from __future__ import annotations

import httpx
import pytest

from navigator.ingest import scorecard
from navigator.ingest.base import ConfigurationError, RetryableStatusError
from navigator.ingest.scorecard import BASE_URL, ScorecardClient, load_bulk_file
from tests.conftest import load_fixture


async def collect(iterator):
    return [record async for record in iterator]


@pytest.fixture
def client() -> ScorecardClient:
    return ScorecardClient(api_key="test-key")


async def test_successful_response_parses_records(respx_mock, client):
    respx_mock.get(BASE_URL).respond(json=load_fixture("scorecard_sample.json"))

    records = await collect(client.fetch_institutions())

    assert len(records) == 2
    assert records[0].source == "scorecard"
    assert records[0].record_key == "110635"
    assert records[0].payload["school.name"] == "University of California-Berkeley"
    assert records[1].payload["school.state"] == "MA"


async def test_pagination_follows_pages(respx_mock, client, monkeypatch):
    monkeypatch.setattr(scorecard, "PAGE_SIZE", 2)
    page0 = {
        "metadata": {"page": 0, "total": 3, "per_page": 2},
        "results": [{"id": 1}, {"id": 2}],
    }
    page1 = {"metadata": {"page": 1, "total": 3, "per_page": 2}, "results": [{"id": 3}]}
    route = respx_mock.get(BASE_URL)
    route.side_effect = [
        httpx.Response(200, json=page0),
        httpx.Response(200, json=page1),
    ]

    records = await collect(client.fetch_institutions())

    assert [record.record_key for record in records] == ["1", "2", "3"]
    assert route.call_count == 2


async def test_field_of_study_records(respx_mock, client):
    fixture = load_fixture("scorecard_sample.json")
    respx_mock.get(BASE_URL).respond(json=fixture)
    records = await collect(client.fetch_field_of_study())
    assert len(records) == 2
    assert records[0].record_key == "110635/fos"


async def test_500_then_200_retries_once(respx_mock, client):
    route = respx_mock.get(BASE_URL)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json=load_fixture("scorecard_sample.json")),
    ]
    records = await collect(client.fetch_institutions())
    assert len(records) == 2
    assert route.call_count == 2


async def test_three_500s_raise(respx_mock, client):
    route = respx_mock.get(BASE_URL)
    route.side_effect = [httpx.Response(500)] * 3
    with pytest.raises(RetryableStatusError):
        await collect(client.fetch_institutions())
    assert route.call_count == 3


async def test_429_is_retried(respx_mock, client):
    route = respx_mock.get(BASE_URL)
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(200, json=load_fixture("scorecard_sample.json")),
    ]
    records = await collect(client.fetch_institutions())
    assert len(records) == 2


async def test_empty_result_set_returns_empty_iterator(respx_mock, client):
    respx_mock.get(BASE_URL).respond(json={"metadata": {"total": 0}, "results": []})
    assert await collect(client.fetch_institutions()) == []


def test_missing_api_key_raises_configuration_error():
    with pytest.raises(ConfigurationError, match="SCORECARD_API_KEY"):
        ScorecardClient(api_key=None)


def test_load_bulk_file_csv(tmp_path):
    csv_path = tmp_path / "scorecard.csv"
    csv_path.write_text(
        "UNITID,INSTNM,CITY,STABBR\n110635,University of California-Berkeley,Berkeley,CA\n"
    )
    records = list(load_bulk_file(csv_path))
    assert len(records) == 1
    assert records[0].record_key == "110635"
    assert records[0].payload["INSTNM"] == "University of California-Berkeley"


async def test_field_of_study_requests_live_api_field_names(respx_mock, client):
    route = respx_mock.get(BASE_URL).respond(json={"metadata": {"total": 0}, "results": []})
    await collect(client.fetch_field_of_study())
    params = route.calls[0].request.url.params
    fields = params["fields"].split(",")
    prefix = "latest.programs.cip_4_digit"
    # Live-verified 2026-11-12: these nested paths exist; the earlier flat
    # names (earnings.median_earnings_1yr, debt.median_debt) return nothing.
    for path in (
        f"{prefix}.earnings.1_yr.overall_median_earnings",
        f"{prefix}.earnings.4_yr.overall_median_earnings",
        f"{prefix}.earnings.5_yr.overall_median_earnings",
        f"{prefix}.earnings.7_yr.overall_median_earnings",
        f"{prefix}.debt.staff_grad_plus.all.eval_inst.median",
        f"{prefix}.credential.level",
        "location.lat",
        "school.ownership",
    ):
        assert path in fields, path
    assert not any("median_earnings_1yr" in f for f in fields)
    assert params["school.operating"] == "1"
