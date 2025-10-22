#!/usr/bin/env python3
"""检查 10 万任务需要多少节点"""
import sys
sys.path.insert(0, 'tools/scheduler_frameworks')

from run_complete_comparison import load_alibaba_trace

tasks = load_alibaba_trace(sys.argv[1], 100000)

total_cpu = sum(t.cpu for t in tasks)
total_mem = sum(t.mem for t in tasks)

print(f"\n━━━ 资源需求分析 ━━━")
print(f"10 万任务总需求:")
print(f"  CPU: {total_cpu:.2f}")
print(f"  MEM: {total_mem:.2f}")
print(f"  瓶颈: {'MEM' if total_mem > total_cpu else 'CPU'}")

print(f"\n如果节点容量 = 11.0:")
needed_by_cpu = total_cpu / 11.0
needed_by_mem = total_mem / 11.0
needed = max(needed_by_cpu, needed_by_mem)

print(f"  需要节点数（按 CPU）: {needed_by_cpu:.0f}")
print(f"  需要节点数（按 MEM）: {needed_by_mem:.0f}")
print(f"  实际需要: {needed:.0f}")

print(f"\n如果目标利用率 80%:")
needed_80 = needed / 0.80
print(f"  推荐节点数: {int(needed_80) + 10}")

print(f"\n当前动态计算:")
num_machines = int(total_mem / 11.0 / 0.80) + 10
print(f"  节点数: {num_machines}")
print(f"  总容量: {num_machines * 11:.0f}")
print(f"  理论利用率: {total_mem / (num_machines * 11) * 100:.1f}%")

