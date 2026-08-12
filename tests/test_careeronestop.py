"""CareerOneStop client: credentials required, parsing, retry behavior."""

from __future__ import annotations

import httpx
import pytest

from navigator.ingest.base import ConfigurationError, RetryableStatusError
from navigator.ingest.careeronestop import CareerOneStopClient
from tests.conftest import load_fixture

CERT_URL = "https://api.careeronestop.org/v1/certificationfinder/user-1/15-1252.00/0/0/0/0/0/0/0/0/1/50"
LICENSE_URL = "https://api.careeronestop.org/v1/license/user-1/29-1141.00/CA/0/1/50"


async def collect(iterator):
    return [record async for record in iterator]


@pytest.fixture
def client() -> CareerOneStopClient:
    return CareerOneStopClient(user_id="user-1", token="tok-1")


def test_missing_user_id_names_the_setting():
    with pytest.raises(ConfigurationError, match="CAREERONESTOP_USER_ID"):
        CareerOneStopClient(user_id=None, token="tok-1")


def test_missing_token_names_the_setting():
    with pytest.raises(ConfigurationError, match="CAREERONESTOP_TOKEN"):
        CareerOneStopClient(user_id="user-1", token=None)


async def test_certifications_parse(respx_mock, client):
    respx_mock.get(CERT_URL).respond(json=load_fixture("careeronestop_sample.json"))

    records = await collect(client.fetch_certifications("15-1252.00"))

    assert len(records) == 2
    assert records[0].source == "careeronestop"
    assert records[0].record_key == "15-1252.00/cert/2158-C"
    assert records[0].payload["Name"] == "AWS Certified Solutions Architect - Associate"
    assert records[0].payload["soc_code"] == "15-1252.00"


async def test_licenses_parse(respx_mock, client):
    respx_mock.get(LICENSE_URL).respond(
        json={"LicenseList": [{"Id": "L-1", "Title": "Registered Nurse License"}]}
    )
    records = await collect(client.fetch_licenses("29-1141.00", "CA"))
    assert len(records) == 1
    assert records[0].record_key == "29-1141.00/CA/license/L-1"
    assert records[0].payload["state"] == "CA"


async def test_500_then_200_retries_once(respx_mock, client):
    route = respx_mock.get(CERT_URL)
    route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json=load_fixture("careeronestop_sample.json")),
    ]
    records = await collect(client.fetch_certifications("15-1252.00"))
    assert len(records) == 2
    assert route.call_count == 2


async def test_three_500s_raise(respx_mock, client):
    route = respx_mock.get(CERT_URL)
    route.side_effect = [httpx.Response(500)] * 3
    with pytest.raises(RetryableStatusError):
        await collect(client.fetch_certifications("15-1252.00"))
    assert route.call_count == 3


async def test_429_is_retried(respx_mock, client):
    route = respx_mock.get(CERT_URL)
    route.side_effect = [
        httpx.Response(429),
        httpx.Response(200, json=load_fixture("careeronestop_sample.json")),
    ]
    records = await collect(client.fetch_certifications("15-1252.00"))
    assert len(records) == 2


async def test_empty_result_set_returns_empty_iterator(respx_mock, client):
    respx_mock.get(CERT_URL).respond(json={"CertList": [], "RecordCount": 0})
    assert await collect(client.fetch_certifications("15-1252.00")) == []


async def test_generic_fetch_directs_to_per_soc_methods(client):
    with pytest.raises(NotImplementedError):
        await anext(client.fetch())
