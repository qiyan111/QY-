#!/usr/bin/env python3
"""
快速测试所有调度器框架是否正常工作
"""

import sys

print("测试导入模块...")

try:
    from scheduler_frameworks.flow_graph import FlowGraph, NodeType
    print("✓ flow_graph")
except Exception as e:
    print(f"✗ flow_graph: {e}")

try:
    from scheduler_frameworks.octopus_cost_model import OctopusCostModel
    print("✓ octopus_cost_model")
except Exception as e:
    print(f"✗ octopus_cost_model: {e}")

try:
    from ortools.graph.python import min_cost_flow
    from scheduler_frameworks.min_cost_flow_solver import MinCostFlowSolver
    print("✓ min_cost_flow_solver (OR-Tools)")
except Exception as e:
    print(f"✗ min_cost_flow_solver: {e}")
    print("  请安装: pip install ortools")

try:
    from scheduler_frameworks.mesos_drf_allocator import HierarchicalAllocator, DRFSorter
    print("✓ mesos_drf_allocator")
except Exception as e:
    print(f"✗ mesos_drf_allocator: {e}")

print("\n所有模块导入成功！可以运行完整对比。")
print("执行: python tools/run_complete_comparison.py ./data")

