#!/usr/bin/env python3
"""Extract average CPU/MEM usage per instance from container_usage.csv.

Usage:
  python tools/extract_avg_usage.py <trace_dir> [max_rows] [output_csv]

If max_rows is given, only the first N rows are scanned (fast sampling).
Outputs CSV with columns: instance_id,cpu_used,mem_used
"""
import os
import sys
import pandas as pd
from pathlib import Path

USE_COLS = [2, 3, 4]  # instance_id, cpu_used, mem_used
CHUNK = 1_000_000

def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/extract_avg_usage.py <trace_dir> [max_rows] [output_csv]")
        sys.exit(1)

    trace_dir = Path(sys.argv[1])
    max_rows = int(sys.argv[2]) if len(sys.argv) >= 3 else None
    out_path = Path(sys.argv[3]) if len(sys.argv) >= 4 else trace_dir / "usage_avg.csv"

    src = trace_dir / "container_usage.csv"
    if not src.exists():
        print(f"✗ {src} not found")
        sys.exit(1)

    print("⏳ scanning", src)
    rows_read = 0
    agg_dfs = []
    for chunk in pd.read_csv(src, header=None, usecols=USE_COLS,
                             chunksize=CHUNK):
        if max_rows and rows_read >= max_rows:
            break
        if max_rows and rows_read + len(chunk) > max_rows:
            chunk = chunk.head(max_rows - rows_read)
        rows_read += len(chunk)

        grp = chunk.groupby(2).agg({3: "mean", 4: "mean"}).reset_index()
        grp.columns = ["instance_id", "cpu_used", "mem_used"]
        agg_dfs.append(grp)
        print(f"  processed rows: {rows_read:,}", end="\r")

        if max_rows and rows_read >= max_rows:
            break

    if not agg_dfs:
        print("No data extracted.")
        sys.exit(1)

    df_out = pd.concat(agg_dfs).groupby("instance_id").mean()
    df_out.to_csv(out_path)
    print(f"\n✓ saved {len(df_out)} rows to {out_path}")


if __name__ == "__main__":
    main()
