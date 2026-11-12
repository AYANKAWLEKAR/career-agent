"""Pure transforms for the College Scorecard outcome layer (Stage 2).

Scorecard keys programs by 4-digit CIP ("1107"); the NCES crosswalk uses
6-digit CIP ("11.0701"). Everything is normalized to the 4-digit form so the
two sources join on one key. No database access lives here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

# Scorecard credential levels worth showing a student; certificates and
# post-bacc/graduate certificates are outside the degree-ROI story.
CREDENTIAL_LABELS: dict[Any, str] = {
    2: "Associate's Degree",
    3: "Bachelor's Degree",
    5: "Master's Degree",
    6: "Doctoral Degree",
    7: "First Professional Degree",
}
BACHELORS = CREDENTIAL_LABELS[3]
MASTERS = CREDENTIAL_LABELS[5]
DOCTORAL = CREDENTIAL_LABELS[6]

CONTROL_LABELS: dict[Any, str] = {1: "Public", 2: "Private nonprofit", 3: "Private for-profit"}
NO_MATCH_SOC = "99-9999"

_CIP_RE = re.compile(r"^(\d{2})\.?(\d{2})(?:\d{2})?$")
_SOC_RE = re.compile(r"^\d{2}-\d{4}$")


def cip4(code: Any) -> str | None:
    """Normalize a 2020 CIP code to its 4-digit family, or None if malformed."""
    if code is None:
        return None
    match = _CIP_RE.match(str(code).strip())
    return f"{match.group(1)}{match.group(2)}" if match else None


def clean_cip_title(title: str) -> str:
    """CIP titles carry a trailing period; drop it, keep internal punctuation."""
    return title.strip().removesuffix(".")


def crosswalk_pairs(rows: Iterable[tuple]) -> dict[str, set[str]]:
    """Group NCES CIP->SOC rows into {cip4: {soc6}}.

    Header rows, blank rows, malformed codes, and the 99-9999 "NO MATCH"
    sentinel are skipped.
    """
    grouped: dict[str, set[str]] = {}
    for row in rows:
        cip_raw, _title, soc_raw, _soc_title = (list(row) + [None] * 4)[:4]
        cip = cip4(cip_raw)
        soc = str(soc_raw).strip() if soc_raw is not None else ""
        if cip is None or not _SOC_RE.match(soc) or soc == NO_MATCH_SOC:
            continue
        grouped.setdefault(cip, set()).add(soc)
    return grouped


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value == value else None  # NaN guard


def _leaf(node: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _as_int(value: float | None) -> int | float | None:
    return None if value is None else (int(value) if value == int(value) else value)


def flatten_school(
    payload: dict[str, Any], data_year: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One Scorecard school record -> (institution row, priced program rows).

    Programs are kept only at the loaded credential levels and only when at
    least one earnings horizon or the debt figure is published; the Scorecard
    suppresses small cells, so most programs carry nothing to store.
    """
    institution = {
        "unit_id": str(payload.get("id")),
        "name": payload.get("school.name"),
        "city": payload.get("school.city"),
        "state": payload.get("school.state"),
        "control": CONTROL_LABELS.get(payload.get("school.ownership")),
        "lat": payload.get("location.lat"),
        "lon": payload.get("location.lon"),
    }
    programs: list[dict[str, Any]] = []
    for raw in payload.get("latest.programs.cip_4_digit") or []:
        level = _leaf(raw, "credential", "level")
        code = cip4(raw.get("code"))
        if level not in CREDENTIAL_LABELS or code is None:
            continue
        e1 = _number(_leaf(raw, "earnings", "1_yr", "overall_median_earnings"))
        e4 = _number(_leaf(raw, "earnings", "4_yr", "overall_median_earnings"))
        e5 = _number(_leaf(raw, "earnings", "5_yr", "overall_median_earnings"))
        e7 = _number(_leaf(raw, "earnings", "7_yr", "overall_median_earnings"))
        debt = _number(_leaf(raw, "debt", "staff_grad_plus", "all", "eval_inst", "median"))
        if all(v is None for v in (e1, e4, e5, e7, debt)):
            continue
        payment = _number(
            _leaf(raw, "debt", "staff_grad_plus", "all", "eval_inst", "median_payment"))
        awards = _number(_leaf(raw, "counts", "ipeds_awards1"))
        programs.append({
            "cip_code": code,
            "program_name": clean_cip_title(raw.get("title") or ""),
            "credential_level": CREDENTIAL_LABELS[level],
            "e1": _as_int(e1), "e4": _as_int(e4), "e5": _as_int(e5), "e7": _as_int(e7),
            "debt": _as_int(debt), "payment": _as_int(payment),
            "awards": _as_int(awards), "data_year": data_year,
        })
    return institution, programs
