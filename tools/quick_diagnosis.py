#!/usr/bin/env python3
"""快速诊断 10 万规模下的问题"""
import sys
sys.path.insert(0, 'tools/scheduler_frameworks')

from run_complete_comparison import load_alibaba_trace, run_mesos_drf, run_slo_driven
import numpy as np

print("加载 10 万任务...")
tasks = load_alibaba_trace(sys.argv[1], 100000)

total_mem = sum(t.mem for t in tasks)
num_machines = int(total_mem / 11.0 / 0.80) + 10

print(f"\n运行 Mesos DRF...")
res_mesos = run_mesos_drf(tasks, num_machines)

print(f"\n运行 SLO-Driven...")
res_ours = run_slo_driven(tasks, num_machines)

print("\n━━━ 利用率对比 ━━━")
mesos_utils = [m.utilization() for m in res_mesos['machines']]
ours_utils = [m.utilization() for m in res_ours['machines']]

print(f"\nMesos DRF:")
print(f"  Min-Max: {min(mesos_utils)*100:.1f}% - {max(mesos_utils)*100:.1f}%")
print(f"  Std: {np.std(mesos_utils)*100:.1f}%")
print(f"  >85%节点: {sum(1 for u in mesos_utils if u > 0.85)}")
print(f"  >90%节点: {sum(1 for u in mesos_utils if u > 0.90)}")

print(f"\nSLO-Driven:")
print(f"  Min-Max: {min(ours_utils)*100:.1f}% - {max(ours_utils)*100:.1f}%")
print(f"  Std: {np.std(ours_utils)*100:.1f}%")
print(f"  >85%节点: {sum(1 for u in ours_utils if u > 0.85)}")
print(f"  >90%节点: {sum(1 for u in ours_utils if u > 0.90)}")

print(f"\n━━━ 问题诊断 ━━━")
if np.std(ours_utils) > np.std(mesos_utils):
    print(f"SLO-Driven 碎片化更高（{np.std(ours_utils)*100:.1f}% vs {np.std(mesos_utils)*100:.1f}%）")
    print("可能原因：分层策略导致负载集中")

