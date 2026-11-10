"""Load H-1B LCA offered-wage aggregates into metro_wages (Stage 1).

Reads the slim CSV produced from the DOL FY2026 Q3 disclosure workbook,
keeps certified full-time filings, annualizes offered base wages, and writes
per-SOC percentiles:

* national rows (metro_id null) — these activate the edge wage penalty in
  scripts/build_graph.py, which reads ``metro_id is null`` rows only;
* San Francisco-Oakland-Berkeley MSA rows (CBSA 41860) — additive layer for
  narration and later spatial work.

Idempotent per data year: rows for --data-year are replaced on re-run.

Usage:
    uv run python scripts/load_lca_wages.py \
        [--csv ~/Downloads/lca_data/lca_fy2026_q3_slim.csv] \
        [--data-year 2026] [--min-count 5]
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.lca import (
    aggregate_wages,
    annualize,
    is_blank_row,
    occupation_soc6,
    parse_rate,
    row_passes,
    soc6,
)

DEFAULT_CSV = Path.home() / "Downloads" / "lca_data" / "lca_fy2026_q3_slim.csv"
MSA_CODE = "41860"
MSA_NAME = "San Francisco-Oakland-Berkeley, CA"
# Approximate MSA centroid (Oakland): good enough for radius demos.
MSA_LON, MSA_LAT = -122.27, 37.80


def read_rows(csv_path: Path) -> list[dict]:
    """Stream the slim CSV into normalized aggregation inputs."""
    rows: list[dict] = []
    kept = skipped_status = skipped_wage = skipped_soc = blank = 0
    with open(csv_path, newline="", encoding="utf-8") as handle:
        for raw in csv.DictReader(handle):
            # 57.6% of the slim CSV is xlsx used-range padding; skipping it is
            # lossless and keeps the drop counters honest.
            if is_blank_row(raw):
                blank += 1
                continue
            if not row_passes(raw):
                skipped_status += 1
                continue
            code = soc6(raw.get("SOC_CODE"))
            if code is None:
                skipped_soc += 1
                continue
            wage = annualize(parse_rate(raw.get("WAGE_RATE_OF_PAY_FROM")),
                             raw.get("WAGE_UNIT_OF_PAY"))
            if wage is None:
                skipped_wage += 1
                continue
            kept += 1
            rows.append({
                "soc6": code,
                "annual_wage": wage,
                "WORKSITE_COUNTY": raw.get("WORKSITE_COUNTY"),
                "WORKSITE_STATE": raw.get("WORKSITE_STATE"),
            })
    print(f"rows kept {kept:,} of {kept + skipped_status + skipped_soc + skipped_wage:,} "
          f"real records | dropped: status/ft {skipped_status:,}, "
          f"bad soc {skipped_soc:,}, bad wage {skipped_wage:,} "
          f"| excel padding skipped {blank:,}")
    return rows


def ensure_metro(cursor) -> int:
    cursor.execute("select metro_id from metro_areas where msa_code = :c", c=MSA_CODE)
    row = cursor.fetchone()
    if row:
        return row[0]
    out = cursor.var(int)
    cursor.execute(
        """
        insert into metro_areas (msa_code, msa_name, state_codes, centroid)
        values (:code, :name, :states,
                sdo_geometry(2001, 4326,
                             sdo_point_type(:lon, :lat, null), null, null))
        returning metro_id into :out
        """,
        code=MSA_CODE, name=MSA_NAME, states='["CA"]',
        lon=MSA_LON, lat=MSA_LAT, out=out,
    )
    return out.getvalue()[0]


def load(csv_path: Path, data_year: int, min_count: int) -> None:
    stats = aggregate_wages(read_rows(csv_path), min_count=min_count)
    national = {k[0]: v for k, v in stats.items() if k[1] is None}
    bay = {k[0]: v for k, v in stats.items() if k[1] == "bay"}
    print(f"aggregates: {len(national)} national SOCs, {len(bay)} bay SOCs "
          f"(min_count={min_count})")
    if national and not bay:
        # The county filter silently matching nothing is exactly the failure
        # the FY2026 profiling caught; never load a half-empty year quietly.
        raise RuntimeError(
            "bay segment is empty while national is not - county filter broke")

    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        metro_id = ensure_metro(cursor)

        cursor.execute("select occupation_id, soc_code, title from occupations")
        occ_rows = cursor.fetchall()
        by_soc6: dict[str, list[int]] = {}
        for occupation_id, soc_code, _title in occ_rows:
            by_soc6.setdefault(occupation_soc6(soc_code), []).append(occupation_id)

        cursor.execute("delete from metro_wages where data_year = :y", y=data_year)
        print(f"cleared {cursor.rowcount} prior rows for data_year={data_year}")

        inserts = []
        covered: set[int] = set()
        for segment, metro, cells in (("national", None, national),
                                      ("bay", metro_id, bay)):
            hit = 0
            for code, cell in cells.items():
                for occupation_id in by_soc6.get(code, ()):
                    hit += 1
                    if metro is None:
                        covered.add(occupation_id)
                    inserts.append((
                        occupation_id, metro, cell.count,
                        round(cell.p10, 2), round(cell.p25, 2),
                        round(cell.median, 2), round(cell.p75, 2),
                        round(cell.p90, 2), round(cell.mean, 2), data_year,
                    ))
            print(f"{segment}: wage rows for {hit} occupations")

        cursor.executemany(
            """
            insert into metro_wages
              (occupation_id, metro_id, employment_count,
               wage_p10, wage_p25, wage_median, wage_p75, wage_p90, wage_mean,
               data_year)
            values (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10)
            """,
            inserts,
        )
        connection.commit()

        missing = sorted(
            title for occupation_id, _soc, title in occ_rows
            if occupation_id not in covered
        )
        print(f"coverage: {len(covered)}/{len(occ_rows)} occupations have "
              f"national wages; {len(missing)} without:")
        for title in missing:
            print(f"  - {title}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--data-year", type=int, default=2026)
    parser.add_argument("--min-count", type=int, default=5)
    args = parser.parse_args(argv)
    load(args.csv, args.data_year, args.min_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
