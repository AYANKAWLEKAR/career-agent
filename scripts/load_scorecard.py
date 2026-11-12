"""Load College Scorecard outcomes + the CIP->SOC crosswalk (Stage 2).

Reads the JSONL from scripts/fetch_scorecard.py and the NCES CIP2020-SOC2018
crosswalk workbook, then (idempotently, full replace):

* institutions  -- with an SDO_GEOMETRY point for spatial radius queries
* programs      -- priced programs at the loaded credential levels
* program_occupations -- 4-digit CIP -> graph occupations via 6-digit SOC
* cip_codes     -- the CIP taxonomy, titles embedded in-database, plus the
                   HNSW index (built after the embeddings exist)

Usage:
    uv run python scripts/load_scorecard.py \
        [--jsonl ~/Downloads/scorecard_data/fos_national.jsonl] \
        [--crosswalk ~/Downloads/scorecard_data/CIP2020_SOC2018_Crosswalk.xlsx] \
        [--data-year 2026]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import openpyxl

from navigator.config import get_settings
from navigator.db.pool import create_pool
from navigator.lca import occupation_soc6
from navigator.programs import crosswalk_pairs, flatten_school

DATA_DIR = Path.home() / "Downloads" / "scorecard_data"
MODEL_NAME = "ALL_MINILM_L12_V2"
BATCH = 5000

INSTITUTION_SQL = """
insert into institutions (unit_id, name, city, state, control, location)
values (:unit_id, :name, :city, :state, :control,
        case when :lon is not null and :lat is not null
             then sdo_geometry(2001, 4326, sdo_point_type(:lon, :lat, null), null, null)
        end)
"""
PROGRAM_SQL = """
insert into programs
  (institution_id, cip_code, program_name, credential_level,
   median_earnings_1yr, median_earnings_4yr, median_earnings_5yr,
   median_earnings_7yr, median_debt, median_debt_payment, awards, data_year)
values
  (:institution_id, :cip_code, :program_name, :credential_level,
   :e1, :e4, :e5, :e7, :debt, :payment, :awards, :data_year)
"""


def read_crosswalk(path: Path) -> dict[str, set[str]]:
    workbook = openpyxl.load_workbook(path, read_only=True)
    return crosswalk_pairs(workbook["CIP-SOC"].iter_rows(values_only=True))


def read_schools(jsonl: Path, data_year: int):
    institutions: list[dict] = []
    programs: list[dict] = []
    titles: dict[str, str] = {}
    with jsonl.open(encoding="utf-8") as handle:
        for line in handle:
            institution, school_programs = flatten_school(json.loads(line), data_year)
            if not institution["name"]:
                continue
            institutions.append(institution)
            seen = set()
            for program in school_programs:
                key = (program["cip_code"], program["credential_level"])
                if key in seen:
                    continue
                seen.add(key)
                titles.setdefault(program["cip_code"], program["program_name"])
                programs.append({"unit_id": institution["unit_id"], **program})
    return institutions, programs, titles


def load(jsonl: Path, crosswalk: Path, data_year: int) -> None:
    pairs = read_crosswalk(crosswalk)
    institutions, programs, titles = read_schools(jsonl, data_year)
    print(f"read {len(institutions):,} institutions, {len(programs):,} priced programs, "
          f"{len(titles)} CIPs, crosswalk covers {len(pairs)} CIPs")

    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        connection.call_timeout = 900_000

        started = time.monotonic()
        cursor.execute("delete from institutions")  # cascades to programs
        cursor.executemany(INSTITUTION_SQL, institutions)
        cursor.execute("select unit_id, institution_id from institutions")
        institution_ids = {unit: iid for unit, iid in cursor.fetchall()}
        rows = [{**p, "institution_id": institution_ids[p["unit_id"]]} for p in programs]
        for p in rows:
            del p["unit_id"]
        for i in range(0, len(rows), BATCH):
            cursor.executemany(PROGRAM_SQL, rows[i:i + BATCH])
        connection.commit()
        print(f"institutions + programs loaded in {time.monotonic() - started:.1f}s")

        cursor.execute("select occupation_id, soc_code from occupations")
        by_soc6: dict[str, list[int]] = {}
        for occupation_id, soc_code in cursor.fetchall():
            by_soc6.setdefault(occupation_soc6(soc_code), []).append(occupation_id)
        links = sorted({(cip, oid) for cip, socs in pairs.items()
                        for soc in socs for oid in by_soc6.get(soc, ())})
        cursor.execute("delete from program_occupations")
        cursor.executemany(
            "insert into program_occupations (cip_code, occupation_id) values (:1, :2)",
            links)
        connection.commit()
        print(f"program_occupations: {len(links):,} CIP->occupation links, "
              f"{len({oid for _, oid in links})}/{sum(map(len, by_soc6.values()))} "
              "occupations reachable from a CIP")

        started = time.monotonic()
        cursor.execute("delete from cip_codes")
        cursor.executemany("insert into cip_codes (cip_code, title) values (:1, :2)",
                           sorted(titles.items()))
        cursor.execute(
            f"update cip_codes set embedding = "
            f"vector_embedding({MODEL_NAME} using title as data)")
        connection.commit()
        cursor.execute(
            "select count(*) from user_indexes where index_name = 'CIP_CODES_EMBEDDING_VIX'")
        if cursor.fetchone()[0]:
            cursor.execute("drop index cip_codes_embedding_vix")
        cursor.execute("""
            create vector index cip_codes_embedding_vix on cip_codes (embedding)
            organization inmemory neighbor graph distance cosine
            with target accuracy 95""")
        print(f"cip_codes embedded + indexed in {time.monotonic() - started:.1f}s")

        cursor.execute("""
            select count(distinct o.occupation_id)
            from program_occupations po
            join occupations o on o.occupation_id = po.occupation_id
            join programs p on p.cip_code = po.cip_code
            where p.credential_level = 'Bachelor''s Degree'
              and p.median_earnings_4yr is not null""")
        print(f"occupations with bachelor's 4-yr earnings coverage: {cursor.fetchone()[0]}")
        for table in ("institutions", "programs", "cip_codes"):
            cursor.execute(f"select count(*) from {table}")
            print(f"  {table}: {cursor.fetchone()[0]:,}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl", type=Path, default=DATA_DIR / "fos_national.jsonl")
    parser.add_argument("--crosswalk", type=Path,
                        default=DATA_DIR / "CIP2020_SOC2018_Crosswalk.xlsx")
    parser.add_argument("--data-year", type=int, default=2026)
    args = parser.parse_args(argv)
    load(args.jsonl, args.crosswalk, args.data_year)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
