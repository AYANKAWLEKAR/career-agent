"""Ingest data contracts.

``RawRecord`` is what every client yields and what lands in ``staging_raw``.
The typed models below document the normalized shape each source produces;
transformation from staging into these shapes happens in a later phase.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RawRecord(BaseModel):
    source: str
    record_key: str
    payload: dict[str, Any]


class OnetOccupation(BaseModel):
    soc_code: str
    title: str
    description: str | None = None
    job_zone: int | None = None
    bright_outlook: bool | None = None


class OnetTask(BaseModel):
    soc_code: str
    task_statement: str
    task_type: str | None = None
    importance: float | None = None


class OnetSkillRating(BaseModel):
    soc_code: str
    element_code: str
    element_name: str
    element_type: str
    importance: float | None = None
    level: float | None = None


class OewsWageRow(BaseModel):
    soc_code: str
    area_code: str
    area_title: str | None = None
    employment_count: int | None = None
    employment_per_thousand: float | None = None
    wage_p10: float | None = None
    wage_p25: float | None = None
    wage_median: float | None = None
    wage_p75: float | None = None
    wage_p90: float | None = None
    wage_mean: float | None = None
    data_year: int


class ScorecardInstitution(BaseModel):
    unit_id: str
    name: str
    city: str | None = None
    state: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    control: str | None = None


class ScorecardProgram(BaseModel):
    unit_id: str
    cip_code: str
    program_name: str | None = None
    credential_level: str | None = None
    median_earnings_1yr: float | None = None
    median_earnings_4yr: float | None = None
    median_debt: float | None = None
    data_year: int | None = None


class CareerOneStopCertification(BaseModel):
    certification_name: str
    issuing_organization: str | None = None
    abbreviation: str | None = None
    description: str | None = None
    soc_code: str | None = None


class CareerOneStopLicense(BaseModel):
    license_name: str
    issuing_agency: str | None = None
    description: str | None = None
    jurisdiction: str | None = None
    soc_code: str | None = None
