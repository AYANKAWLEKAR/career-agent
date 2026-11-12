"""Download College Scorecard field-of-study data (schools + programs) to JSONL.

One line per school; each carries its 4-digit-CIP programs with 1/4/5/7-year
median earnings and median Grad-PLUS/Stafford debt. Pages are appended as they
arrive, so an interrupted run resumes with --start-page. Needs SCORECARD_API_KEY.

Usage:
    uv run python scripts/fetch_scorecard.py [--out ~/Downloads/scorecard_data/fos_national.jsonl]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from navigator.config import get_settings
from navigator.ingest.scorecard import PAGE_SIZE, ScorecardClient

DEFAULT_OUT = Path.home() / "Downloads" / "scorecard_data" / "fos_national.jsonl"


async def run(out: Path, start_page: int) -> None:
    key = get_settings().scorecard_api_key
    client = ScorecardClient(key.get_secret_value() if key else None)
    out.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if start_page else "w"
    schools = programs = 0
    try:
        with out.open(mode, encoding="utf-8") as handle:
            async for record in client.fetch_field_of_study(page=start_page):
                handle.write(json.dumps(record.payload) + "\n")
                schools += 1
                programs += len(record.payload.get("latest.programs.cip_4_digit") or [])
                if schools % PAGE_SIZE == 0:
                    print(f"{schools:,} schools, {programs:,} programs", flush=True)
    finally:
        await client.aclose()
    print(f"done: {schools:,} schools, {programs:,} programs -> {out}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-page", type=int, default=0)
    args = parser.parse_args(argv)
    asyncio.run(run(args.out, args.start_page))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
