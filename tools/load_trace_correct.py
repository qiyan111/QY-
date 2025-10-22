#!/usr/bin/env python3
"""
修正后的 Alibaba trace 加载器
使用正确的列映射
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

def load_alibaba_trace_correct(trace_dir: str, max_inst: int = 10000):
    """
    正确的列映射（基于实际数据检查）:
      列 0: instance_id
      列 1: task_name
      列 2: job_id (租户)
      列 3: task_id
      列 4: status
      列 5: start_time
      列 6: end_time
      列 7: machine_id
      列 8: seq_no
      列 9: total_seq
      列 10: ? (有 NaN)
      列 11: ? (有 NaN)
      列 12: CPU 资源 (0.02-0.28) ← 真实数据！
      列 13: MEM 资源 (0.02-0.37) ← 真实数据！
    """
    print(f"━━━ 加载 Alibaba 2018 Trace (修正版) ━━━\n")
    
    # 不指定列名，直接用索引
    chunks = []
    for chunk in pd.read_csv(f"{trace_dir}/batch_instance.csv", 
                             chunksize=1000000,
                             header=None):
        # 过滤 Running 状态（列 4）
        running = chunk[chunk[4] == 'Running']
        if len(running) > 0:
            chunks.append(running)
        if sum(len(c) for c in chunks) >= max_inst:
            break
    
    df = pd.concat(chunks).head(max_inst)
    
    # 提取真实资源数据（列 12, 13）
    df['cpu_real'] = pd.to_numeric(df[12], errors='coerce')
    df['mem_real'] = pd.to_numeric(df[13], errors='coerce')
    
    # 统计
    has_resource = df['cpu_real'].notna()
    print(f"✓ 加载 {len(df)} 实例")
    print(f"  有资源数据: {has_resource.sum()} ({has_resource.sum()/len(df)*100:.1f}%)")
    print(f"  无资源数据: {(~has_resource).sum()} ({(~has_resource).sum()/len(df)*100:.1f}%)\n")
    
    # 只保留有资源数据的行
    df = df[has_resource].copy()
    
    print(f"━━━ 资源统计（{len(df)} 条有效记录）━━━")
    print(f"CPU 范围: {df['cpu_real'].min():.3f} - {df['cpu_real'].max():.3f}")
    print(f"CPU 均值: {df['cpu_real'].mean():.3f}")
    print(f"MEM 范围: {df['mem_real'].min():.3f} - {df['mem_real'].max():.3f}")
    print(f"MEM 均值: {df['mem_real'].mean():.3f}")
    print(f"租户数: {df[2].nunique()}\n")
    
    # 转换为 Task 对象
    tasks = [
        Task(
            id=idx,
            cpu=row['cpu_real'],  # 真实 CPU
            mem=row['mem_real'],  # 真实 MEM
            tenant=str(row[2]),   # job_id
            arrival=int(row[5])   # start_time
        )
        for idx, row in df.sort_values(5).iterrows()
    ]
    
    return tasks

if __name__ == "__main__":
    import sys
    tasks = load_alibaba_trace_correct(sys.argv[1], 10000)
    
    # 显示资源分布
    cpus = [t.cpu for t in tasks]
    mems = [t.mem for t in tasks]
    
    print("━━━ 任务资源分布 ━━━")
    print(f"CPU 分位数:")
    print(f"  p25: {np.percentile(cpus, 25):.3f}")
    print(f"  p50: {np.percentile(cpus, 50):.3f}")
    print(f"  p75: {np.percentile(cpus, 75):.3f}")
    print(f"  p95: {np.percentile(cpus, 95):.3f}")
    
    print(f"\nMEM 分位数:")
    print(f"  p25: {np.percentile(mems, 25):.3f}")
    print(f"  p50: {np.percentile(mems, 50):.3f}")
    print(f"  p75: {np.percentile(mems, 75):.3f}")
    print(f"  p95: {np.percentile(mems, 95):.3f}")
    
    print(f"\n资源异构性:")
    print(f"  CPU 变异系数: {np.std(cpus)/np.mean(cpus):.2f}")
    print(f"  MEM 变异系数: {np.std(mems)/np.mean(mems):.2f}")

