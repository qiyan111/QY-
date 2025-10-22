#!/usr/bin/env python3
"""分析不同算法的节点利用率分布"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, 'tools/scheduler_frameworks')

from run_complete_comparison import load_alibaba_trace, run_mesos_drf, run_tetris, run_slo_driven

tasks = load_alibaba_trace(sys.argv[1], 10000)

print("\n━━━ 运行算法 ━━━")
res_mesos = run_mesos_drf(tasks, 114)
res_tetris = run_tetris(tasks, 114)
res_ours = run_slo_driven(tasks, 114)

def analyze_distribution(result):
    """分析节点利用率分布"""
    machines = result['machines']
    utils = [m.utilization() for m in machines]
    
    print(f"\n━━━ {result['name']} ━━━")
    print(f"利用率分布:")
    print(f"  Min:  {min(utils)*100:.1f}%")
    print(f"  p25:  {np.percentile(utils, 25)*100:.1f}%")
    print(f"  p50:  {np.percentile(utils, 50)*100:.1f}%")
    print(f"  p75:  {np.percentile(utils, 75)*100:.1f}%")
    print(f"  p95:  {np.percentile(utils, 95)*100:.1f}%")
    print(f"  Max:  {max(utils)*100:.1f}%")
    print(f"  Std:  {np.std(utils)*100:.1f}%")
    
    # 统计各利用率区间的节点数
    bins = [0, 0.70, 0.80, 0.85, 0.90, 0.95, 1.0, 2.0]
    hist, _ = np.histogram(utils, bins=bins)
    
    print(f"\n节点分布（按违约风险区间）:")
    print(f"  <70% (低风险):    {hist[0]} 节点")
    print(f"  70-80% (中低):   {hist[1]} 节点")
    print(f"  80-85% (中):     {hist[2]} 节点")
    print(f"  85-90% (中高):   {hist[3]} 节点")
    print(f"  90-95% (高):     {hist[4]} 节点")
    print(f"  >95% (极高):     {hist[5]} 节点")

analyze_distribution(res_mesos)
analyze_distribution(res_tetris)
analyze_distribution(res_ours)

