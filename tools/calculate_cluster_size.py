#!/usr/bin/env python3
"""计算合理的集群规模"""
import pandas as pd
import numpy as np
import sys

chunks = []
for chunk in pd.read_csv(f"{sys.argv[1]}/batch_instance.csv", 
                         chunksize=1000000, header=None):
    terminated = chunk[chunk[4] == 'Terminated']
    if len(terminated) > 0:
        chunks.append(terminated)
    if sum(len(c) for c in chunks) >= 20000:
        break

df = pd.concat(chunks).head(20000)
df['cpu'] = pd.to_numeric(df[12], errors='coerce')
df['mem'] = pd.to_numeric(df[13], errors='coerce')
valid = df['cpu'].notna() & df['mem'].notna() & (df['cpu'] > 0) & (df['mem'] > 0)
df = df[valid].head(10000)

print("━━━ 资源需求分析 ━━━")
total_cpu = df['cpu'].sum()
total_mem = df['mem'].sum()

print(f"10000 任务总需求:")
print(f"  CPU: {total_cpu:.2f}")
print(f"  MEM: {total_mem:.2f}")

print(f"\n如果节点容量 = 1.0:")
print(f"  需要节点数（按 CPU）: {total_cpu / 1.0:.0f}")
print(f"  需要节点数（按 MEM）: {total_mem / 1.0:.0f}")
print(f"  瓶颈: {'CPU' if total_cpu > total_mem else 'MEM'}")

print(f"\n如果目标利用率 = 80%:")
needed_nodes = int(max(total_cpu, total_mem) / 1.0 / 0.8) + 1
print(f"  推荐节点数: {needed_nodes}")

print(f"\n或者调整节点容量:")
target_nodes = 114
needed_cap = max(total_cpu, total_mem) / target_nodes / 0.8
print(f"  114 节点，单节点容量应为: {needed_cap:.2f}")

