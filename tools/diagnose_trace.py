#!/usr/bin/env python3
"""诊断 Alibaba trace 数据分布"""
import pandas as pd
import sys

cols = ["instance_id", "task_name", "job_id", "task_id", "status",
        "start_time", "end_time", "machine_id", "seq_no", "total_seq",
        "cpu_avg", "cpu_max", "mem_avg", "mem_max"]

chunks = []
for chunk in pd.read_csv(f"{sys.argv[1]}/batch_instance.csv", 
                         names=cols, chunksize=1000000):
    running = chunk[chunk['status'] == 'Running']
    if len(running) > 0:
        chunks.append(running)
    if sum(len(c) for c in chunks) >= 10000:
        break

df = pd.concat(chunks).head(10000)

print("━━━ 原始数据分析 ━━━")
print(f"cpu_max NaN 比例: {df['cpu_max'].isna().sum() / len(df) * 100:.1f}%")
print(f"mem_max NaN 比例: {df['mem_max'].isna().sum() / len(df) * 100:.1f}%")

print("\ncpu_max 分布（非 NaN）:")
print(df['cpu_max'].dropna().describe())

print("\nmem_max 分布（非 NaN）:")
print(df['mem_max'].dropna().describe())

print("\n填充后（fillna(1.0)）:")
df['cpu_max'] = df['cpu_max'].fillna(1.0)
df['mem_max'] = df['mem_max'].fillna(1.0)

print(f"cpu_max 唯一值数: {df['cpu_max'].nunique()}")
print(f"cpu_max 值分布:\n{df['cpu_max'].value_counts().head(10)}")

print(f"\nmem_max 唯一值数: {df['mem_max'].nunique()}")
print(f"mem_max 值分布:\n{df['mem_max'].value_counts().head(10)}")

