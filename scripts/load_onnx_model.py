"""Load the all-MiniLM-L12-v2 augmented ONNX model into the database.

Design section 3 calls for ``DBMS_VECTOR.LOAD_ONNX_MODEL`` from Object Storage, but
the documented pre-authenticated URL rotates and DBMS_CLOUD cannot follow the docs
redirect, so the model is shipped up from a local file through the BLOB overload
instead. The result is identical: a mining model named ALL_MINILM_L12_V2 usable in
``vector_embedding(ALL_MINILM_L12_V2 using :text as data)``.

The augmented model embeds its own tokenizer and pooling, so no metadata JSON is
needed. Oracle's download page (linked from the 26ai Vector Search guide):
https://docs.oracle.com/pls/topic/lookup?ctx=en/database/oracle/oracle-database/26/vecse&id=oml_ai_models_object_storage

Usage:
    uv run python scripts/load_onnx_model.py /path/to/all_MiniLM_L12_v2.onnx [--force]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import oracledb

from navigator.config import get_settings
from navigator.db.pool import create_pool

MODEL_NAME = "ALL_MINILM_L12_V2"
EXPECTED_DIMENSIONS = 384


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("onnx_file", type=Path, help="augmented .onnx file")
    parser.add_argument("--force", action="store_true", help="drop and reload if present")
    args = parser.parse_args(argv)

    if not args.onnx_file.is_file():
        print(f"not a file: {args.onnx_file}", file=sys.stderr)
        return 1

    pool = create_pool(get_settings())
    with pool.acquire() as connection:
        cursor = connection.cursor()
        cursor.execute(
            "select count(*) from user_mining_models where model_name = :m", m=MODEL_NAME
        )
        if cursor.fetchone()[0] > 0:
            if not args.force:
                print(f"{MODEL_NAME} already loaded; use --force to reload")
                return 0
            cursor.execute("begin dbms_data_mining.drop_model(:m); end;", m=MODEL_NAME)
            print(f"dropped existing {MODEL_NAME}")

        data = args.onnx_file.read_bytes()
        print(f"uploading {len(data) / 1e6:.0f}MB model...", flush=True)
        started = time.monotonic()
        blob = connection.createlob(oracledb.DB_TYPE_BLOB)
        blob.write(data)
        cursor.callTimeout = 900_000
        cursor.execute(
            "begin dbms_vector.load_onnx_model(:name, :model_data); end;",
            name=MODEL_NAME,
            model_data=blob,
        )
        connection.commit()
        print(f"loaded {MODEL_NAME} in {time.monotonic() - started:.0f}s")

        cursor.execute(
            f"select vector_dims(vector_embedding({MODEL_NAME}"
            " using 'smoke test' as data)) from dual"
        )
        dims = cursor.fetchone()[0]
        print(f"vector_embedding works, dimensions: {dims}")
        if dims != EXPECTED_DIMENSIONS:
            print(f"ERROR: expected {EXPECTED_DIMENSIONS} dims", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
