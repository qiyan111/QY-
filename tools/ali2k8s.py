#!/usr/bin/env python3
"""Convert Alibaba cluster-trace-v2018 to a kube-replay YAML.

Usage:
  python ali2k8s.py --trace /path/cluster-trace-v2018 \
                    --window 3600 \
                    --tenant-mapping user \
                    --out k8s_events.yaml

It scans `batch_instance.csv` (≈8 GB) in streaming mode using pandas chunks to
emit Pod creation events with tenant label and SLO annotation (p99 ≤120 ms by
default, configurable via --latency-slo).
"""

import argparse
import csv
import gzip
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import yaml
import pandas as pd
from tqdm import tqdm
import math  # at top file ensure import


COLS = [
    "task_id",
    "instance_id",
    "job_id",
    "user_id",
    "status",
    "start_time",
    "end_time",
    "cpu_req",
    "mem_req",
]

API_VERSION = "v1"
KIND = "Pod"

def parse_args():
    p = argparse.ArgumentParser(description="Alibaba 2018 trace → k8s events")
    p.add_argument("--trace", required=True, help="Path to cluster-trace-v2018 dir")
    p.add_argument("--window", type=int, default=3600, help="Time window seconds")
    p.add_argument("--tenant-mapping", default="user", choices=["user"], help="Mapping field to tenant")
    p.add_argument("--latency-slo", type=int, default=120, help="p99 latency ms target")
    p.add_argument("--out", required=True, help="Output YAML file")
    p.add_argument("--max-instances", type=int, default=0, help="Stop after N instances (0 = unlimited)")
    return p.parse_args()


def iter_instances(trace_dir):
    inst_file = Path(trace_dir) / "batch_instance.csv"
    if not inst_file.exists():
        sys.exit("batch_instance.csv not found in trace dir")
    # The csv has no header per dataset doc; supply names
    chunks = pd.read_csv(inst_file, names=COLS, iterator=True, chunksize=1_000_000)
    for chunk in chunks:
        for row in chunk.itertuples(index=False):
            yield row


def _sanitize(val, default):
    if val is None:
        return default
    if isinstance(val, float) and math.isnan(val):
        return default
    return val


def make_pod_yaml(row, latency_slo):
    cpu_req = _sanitize(row.cpu_req, 1)
    mem_req = _sanitize(row.mem_req, 1024)

    metadata = {
        "name": f"job-{row.job_id}-task{row.task_id}-ins{row.instance_id}",
        "labels": {"tenant": str(row.user_id)},
        "annotations": {
            "slo.p99ms": str(latency_slo),
        },
    }

    spec = {
        "containers": [
            {
                "name": "work",
                "image": "alibaba/trace-workload:dummy",
                "resources": {
                    "requests": {
                        "cpu": str(max(1, int(cpu_req))),
                        "memory": f"{int(mem_req)}Mi",
                    }
                },
            }
        ],
        "restartPolicy": "Never",
    }

    return {"apiVersion": API_VERSION, "kind": KIND, "metadata": metadata, "spec": spec}


def main():
    args = parse_args()
    start_ts = None
    window_delta = timedelta(seconds=args.window)

    with open(args.out, "w") as fout:
        writer = yaml.safe_dump_all([], stream=fout)  # placeholder, will not use

    # reopen to append manually
    with open(args.out, "w") as fout:
        count = 0
        for row in tqdm(iter_instances(args.trace), desc="instances"):
            if start_ts is None:
                start_ts = row.start_time
                current_window = start_ts // args.window
            # filter by status, only running instances (status=4 per doc)
            if row.status != 4:
                continue
            pod = make_pod_yaml(row, args.latency_slo)
            yaml.safe_dump(pod, fout)
            fout.write("---\n")

            count += 1
            if args.max_instances and count >= args.max_instances:
                break

    print(f"Written Kubernetes events to {args.out}")


if __name__ == "__main__":
    main()
