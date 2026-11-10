"""Download DOL OFLC H-1B LCA disclosure workbooks (Stage 1 wage source).

Quarterly files are cumulative within a fiscal year, so the newest quarter
of a fiscal year supersedes the earlier ones. The FY2026+ files live under
dol.gov/media/; FY2025 and older under /sites/dolgov/files/ETA/oflc/pdfs/.

Usage:
    uv run python scripts/fetch_lca.py [--release FY2026_Q3] [--dest DIR]

The destination directory should hold downloaded data only (no scripts);
the loader takes the file path as an argument.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

RELEASES = {
    "FY2026_Q3": "https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx",
    "FY2025_Q4": ("https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/"
                  "LCA_Disclosure_Data_FY2025_Q4.xlsx"),
}
DEFAULT_RELEASE = "FY2026_Q3"
DEFAULT_DEST = Path.home() / "Downloads" / "lca_data"
CHUNK = 1 << 20  # 1 MiB


def fetch(release: str, dest_dir: Path) -> Path:
    url = RELEASES[release]
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / url.rsplit("/", 1)[-1]

    with httpx.Client(follow_redirects=True, timeout=120.0) as client:
        head = client.head(url)
        head.raise_for_status()
        expected = int(head.headers.get("content-length", 0))
        if target.exists() and expected and target.stat().st_size == expected:
            print(f"already complete: {target} ({expected:,} bytes)")
            return target

        print(f"downloading {url}\n  -> {target} ({expected:,} bytes)")
        done = 0
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(target, "wb") as handle:
                for chunk in response.iter_bytes(CHUNK):
                    handle.write(chunk)
                    done += len(chunk)
                    if done % (50 * CHUNK) < CHUNK:
                        print(f"  {done / 1e6:,.0f} MB", flush=True)
        if expected and target.stat().st_size != expected:
            raise RuntimeError(
                f"size mismatch: got {target.stat().st_size}, expected {expected}")
    print(f"done: {target} ({target.stat().st_size:,} bytes)")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", choices=sorted(RELEASES), default=DEFAULT_RELEASE)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = parser.parse_args(argv)
    fetch(args.release, args.dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
