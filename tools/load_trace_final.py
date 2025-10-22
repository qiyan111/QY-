#!/usr/bin/env python3
"""
最终修正版：使用 Terminated 状态的任务（有真实资源数据）
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass

@dataclass
class Task:
    id: int
    cpu: float
    mem: float
    tenant: str
    arrival: int

def load_alibaba_trace_final(trace_dir: str, max_inst: int = 10000):
    """
    使用 Terminated 状态（有真实资源使用数据）
    
    列映射:
      12: cpu_avg (实际 CPU 使用)
      13: mem_avg (实际内存使用)
    """
    print(f"━━━ 加载 Alibaba 2018 Trace (使用 Terminated 状态) ━━━\n")
    
    chunks = []
    for chunk in pd.read_csv(f"{trace_dir}/batch_instance.csv", 
                             chunksize=1000000,
                             header=None):
        # 使用 Terminated 状态（有真实资源数据）
        terminated = chunk[chunk[4] == 'Terminated']
        if len(terminated) > 0:
            chunks.append(terminated)
        if sum(len(c) for c in chunks) >= max_inst:
            break
    
    df = pd.concat(chunks).head(max_inst)
    
    # 提取资源数据
    df['cpu'] = pd.to_numeric(df[12], errors='coerce')
    df['mem'] = pd.to_numeric(df[13], errors='coerce')
    
    # 过滤有效数据
    valid = df['cpu'].notna() & df['mem'].notna()
    df = df[valid].copy()
    
    print(f"✓ 加载 {len(df)} 条有效记录")
    print(f"  租户数: {df[2].nunique()}")
    print(f"  CPU 范围: {df['cpu'].min():.3f} - {df['cpu'].max():.3f}")
    print(f"  MEM 范围: {df['mem'].min():.3f} - {df['mem'].max():.3f}")
    print(f"  CPU 变异系数: {df['cpu'].std()/df['cpu'].mean():.2f}")
    print(f"  MEM 变异系数: {df['mem'].std()/df['mem'].mean():.2f}\n")
    
    # 转换为 Task 对象
    tasks = [
        Task(
            id=idx,
            cpu=row['cpu'],
            mem=row['mem'],
            tenant=str(row[2]),
            arrival=int(row[5])
        )
        for idx, row in df.sort_values(5).iterrows()
    ]
    
    return tasks

if __name__ == "__main__":
    import sys
    tasks = load_alibaba_trace_final(sys.argv[1], 10000)
    
    cpus = [t.cpu for t in tasks]
    mems = [t.mem for t in tasks]
    tenants = [t.tenant for t in tasks]
    
    print("━━━ 任务资源分布 ━━━")
    print(f"CPU: p25={np.percentile(cpus,25):.3f}, p50={np.percentile(cpus,50):.3f}, p95={np.percentile(cpus,95):.3f}")
    print(f"MEM: p25={np.percentile(mems,25):.3f}, p50={np.percentile(mems,50):.3f}, p95={np.percentile(mems,95):.3f}")
    
    from collections import Counter
    tenant_counts = Counter(tenants)
    print(f"\n━━━ Top 5 租户（按任务数）━━━")
    for tenant, count in tenant_counts.most_common(5):
        print(f"  {tenant}: {count} 任务")

