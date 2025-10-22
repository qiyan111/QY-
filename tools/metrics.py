#!/usr/bin/env python3
"""Utility functions to compute evaluation metrics without SLO.

Metrics implemented:
* cpu_mem_util(machines) -> (avg, max, std)
* fragmentation(machines) -> float  (how much capacity left after dominant share)
* imbalance(machines) -> float       (std-dev of per-node dominant util)
* net_bandwidth(trace_dir, sample_rows=2_000_000) -> (avg_recv_MBps, avg_send_MBps)
  Uses machine_usage.csv if available. The result is coarse-grained but good
  enough for comparative simulation studies.
"""
from __future__ import annotations
import os
import math
import pandas as pd
from typing import Sequence, Tuple

class _MachineProxy:
    """Duck-typed view of Machine used in simulation files."""
    cpu: float
    mem: float
    cpu_used: float
    mem_used: float

    def utilization(self) -> float:  # type: ignore[override]
        cpu_ratio = self.cpu_used / self.cpu if self.cpu else 0.0
        mem_ratio = self.mem_used / self.mem if self.mem else 0.0
        return max(cpu_ratio, mem_ratio)


def cpu_mem_util(machines: Sequence[_MachineProxy]) -> Tuple[float, float, float]:
    """Return average, max and std-dev of dominant share util across nodes."""
    utils = [m.utilization() for m in machines]
    if not utils:
        return 0.0, 0.0, 0.0
    avg = sum(utils) / len(utils)
    mx = max(utils)
    # population std-dev
    var = sum((u - avg) ** 2 for u in utils) / len(utils)
    std = math.sqrt(var)
    return avg, mx, std


def fragmentation(machines: Sequence[_MachineProxy]) -> float:
    """Simple fragmentation metric: 1- dominant share average util.
    0 means perfectly packed, 1 means empty cluster.
    """
    avg_util, _, _ = cpu_mem_util(machines)
    return 1.0 - avg_util


def imbalance(machines: Sequence[_MachineProxy]) -> float:
    """Coefficient of variation (std/mean) of node dominant utilization."""
    avg, _, std = cpu_mem_util(machines)
    return std / avg if avg > 1e-9 else 0.0


def net_bandwidth(trace_dir: str, sample_rows: int = 2_000_000) -> Tuple[float, float]:
    """Return average recv / send MBps across all samples in machine_usage.csv.
    If file not found, returns (0,0).
    """
    path = os.path.join(trace_dir, "machine_usage.csv")
    if not os.path.exists(path):
        return 0.0, 0.0
    cols = [0, 1, 15, 16]  # machine_id, ts, net_recv, net_send
    try:
        df = pd.read_csv(path, usecols=cols, nrows=sample_rows, header=None)
    except ValueError:
        # 部分数据集缺少网络列，直接返回 0
        return 0.0, 0.0
    recv = pd.to_numeric(df[15], errors="coerce").fillna(0)
    send = pd.to_numeric(df[16], errors="coerce").fillna(0)
    # 单位 bytes / 300s, 转 MB/s
    recv_mbps = recv.mean() / 300 / 1024 / 1024
    send_mbps = send.mean() / 300 / 1024 / 1024
    return float(recv_mbps), float(send_mbps)
