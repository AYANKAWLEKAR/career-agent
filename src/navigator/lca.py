"""DOL H-1B LCA disclosure wage normalization and aggregation (Stage 1).

The LCA quarterly workbooks carry employer-offered wages per filing with a
6-digit SOC code, a pay unit, and a worksite county. This module turns those
rows into the per-occupation wage percentiles that ``metro_wages`` stores:
national rows (``metro_id is null``) feed the edge wage penalty in
``scripts/build_graph.py``; San Francisco-Oakland-Berkeley MSA rows are an
additive layer for narration.

Caveats recorded in the design doc: base pay only, certified filing != hire,
and coverage skews to visa-sponsoring professional employers.
"""

from __future__ import annotations

from dataclasses import dataclass

# Annualization factors for the LCA WAGE_UNIT_OF_PAY vocabulary.
WAGE_FACTORS: dict[str, float] = {
    "YEAR": 1.0,
    "MONTH": 12.0,
    "BI-WEEKLY": 26.0,
    "WEEK": 52.0,
    "HOUR": 2080.0,
}

# Annualized sanity window: outside this, the filing is a data-entry error
# (observed in the raw file: hourly rates filed under "Year" and vice versa).
WAGE_FLOOR = 20_000.0
WAGE_CEILING = 1_500_000.0

# San Francisco-Oakland-Berkeley, CA MSA (CBSA 41860) counties.
BAY_AREA_COUNTIES = {"ALAMEDA", "CONTRA COSTA", "MARIN", "SAN FRANCISCO", "SAN MATEO"}

# A FROM rate above this under a sub-annual unit is an annual salary filed
# under the wrong unit (profiled: "$55,000/Month" math teacher -> $660k/yr).
# Hour is exempt: $240-356/hr physician comp is real.
SUBANNUAL_GUARD_UNITS = {"MONTH", "WEEK", "BI-WEEKLY"}
SUBANNUAL_GUARD_RATE = 25_000.0


def parse_rate(raw: object) -> float | None:
    """Return a positive wage rate from a number or a '$120,000.00' string."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip().replace("$", "").replace(",", "")
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    return value if value > 0 else None


def annualize(rate: float | None, unit: object) -> float | None:
    """Annualize a pay rate; None for unknown units or implausible results."""
    if rate is None or unit is None:
        return None
    unit_key = str(unit).strip().upper()
    factor = WAGE_FACTORS.get(unit_key)
    if factor is None:
        return None
    if unit_key in SUBANNUAL_GUARD_UNITS and rate > SUBANNUAL_GUARD_RATE:
        return None
    annual = rate * factor
    if not (WAGE_FLOOR <= annual <= WAGE_CEILING):
        return None
    return annual


def soc6(raw: object) -> str | None:
    """Normalize an LCA SOC code to 6-digit 'NN-NNNN' form."""
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) >= 7 and text[2] == "-" and text[:2].isdigit() and text[3:7].isdigit():
        return text[:7]
    return None


def occupation_soc6(onet_code: str) -> str:
    """Truncate an O*NET 8-digit code ('15-1252.00') to its 6-digit SOC."""
    return onet_code.split(".")[0]


def row_passes(row: dict) -> bool:
    """Keep only certified, full-time filings.

    'Certified - Withdrawn' was certified once but the position was pulled;
    counting it would inflate thin SOCs, so it is excluded.
    """
    status = str(row.get("CASE_STATUS") or "").strip().upper()
    full_time = str(row.get("FULL_TIME_POSITION") or "").strip().upper()
    return status == "CERTIFIED" and full_time == "Y"


def _percentile(ordered: list[float], pct: float) -> float:
    """Linear-interpolation percentile over a pre-sorted list."""
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    frac = rank - low
    if low + 1 >= len(ordered):
        return ordered[-1]
    return ordered[low] * (1 - frac) + ordered[low + 1] * frac


@dataclass(frozen=True)
class WageStats:
    count: int
    p10: float
    p25: float
    median: float
    p75: float
    p90: float
    mean: float


def _stats(wages: list[float]) -> WageStats:
    ordered = sorted(wages)
    return WageStats(
        count=len(ordered),
        p10=_percentile(ordered, 0.10),
        p25=_percentile(ordered, 0.25),
        median=_percentile(ordered, 0.50),
        p75=_percentile(ordered, 0.75),
        p90=_percentile(ordered, 0.90),
        mean=sum(ordered) / len(ordered),
    )


def is_blank_row(row: dict) -> bool:
    """True for Excel used-range padding: every column empty or whitespace."""
    return all(
        value is None or not str(value).strip() for value in row.values()
    )


def _normalize_county(value: object) -> str:
    """Upper-case, collapse whitespace, strip one trailing ' COUNTY' suffix.

    The disclosure file stores 'ALAMEDA COUNTY', not 'ALAMEDA'; a bare-name
    match returns zero rows (observed on FY2026 Q3).
    """
    text = " ".join(str(value or "").upper().split())
    return text.removesuffix(" COUNTY")


def is_bay_area(row: dict) -> bool:
    county = _normalize_county(row.get("WORKSITE_COUNTY"))
    state = str(row.get("WORKSITE_STATE") or "").strip().upper()
    return state == "CA" and county in BAY_AREA_COUNTIES


def aggregate_wages(
    rows: list[dict], min_count: int = 5
) -> dict[tuple[str, str | None], WageStats]:
    """Aggregate annualized wages into (soc6, segment) -> WageStats.

    Segment None is national (every row); segment 'bay' is the SF-Oakland-
    Berkeley MSA subset. Cells with fewer than ``min_count`` filings are
    dropped — a median over a handful of filings is noise, not a statistic.
    """
    buckets: dict[tuple[str, str | None], list[float]] = {}
    for row in rows:
        code = row.get("soc6")
        wage = row.get("annual_wage")
        if not code or wage is None:
            continue
        buckets.setdefault((code, None), []).append(wage)
        if is_bay_area(row):
            buckets.setdefault((code, "bay"), []).append(wage)
    return {
        key: _stats(wages)
        for key, wages in buckets.items()
        if len(wages) >= min_count
    }
