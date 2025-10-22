#!/usr/bin/env python3
"""调试 Firmament Flow Graph"""
import sys
sys.path.insert(0, 'tools/scheduler_frameworks')

from flow_graph import FlowGraph, NodeType
from octopus_cost_model import OctopusCostModel
from min_cost_flow_solver import MinCostFlowSolver

# 创建简单测试
graph = FlowGraph()
cost_model = OctopusCostModel()

# Sink
sink = graph.add_node(NodeType.SINK)

# 1 个 Task
task = graph.add_node(NodeType.TASK)
task.task_id = 0

# Unscheduled Agg
unsch_agg = graph.add_node(NodeType.UNSCHEDULED_AGG)
cost, cap_l, cap_u = cost_model.task_to_unscheduled_agg(0)
graph.add_arc(task, unsch_agg, cost, cap_l, 1)  # cap_upper=1
graph.add_arc(unsch_agg, sink, 0, 0, 1)

# Cluster AGG (EC)
cluster_agg = graph.add_node(NodeType.EQUIV_CLASS)
graph.add_arc(task, cluster_agg, 0, 0, 1)

# 1 个 Machine
machine = graph.add_node(NodeType.RESOURCE_MACHINE)
cost, cap_l, cap_u = cost_model.equiv_class_to_resource(0, 0, 0)
graph.add_arc(cluster_agg, machine, cost, 0, 1)

# 1 个 PU
pu = graph.add_node(NodeType.RESOURCE_PU)
cost, cap_l, cap_u = cost_model.resource_node_to_resource_node(0, 0, 0)
graph.add_arc(machine, pu, cost, 0, 1)

# PU → Sink
cost, cap_l, cap_u = cost_model.leaf_resource_to_sink(0)
graph.add_arc(pu, sink, cost, 0, 1)

print(f"图: {graph.num_nodes()} 节点, {graph.num_arcs()} 边")
print("\n节点:")
for nid, node in graph.nodes.items():
    print(f"  {nid}: {node.type.name}")

print("\n边:")
for arc in graph.arcs:
    print(f"  {arc.src.id}→{arc.dst.id}: cost={arc.cost}, cap=[{arc.cap_lower},{arc.cap_upper}]")

print("\n求解...")
solver = MinCostFlowSolver()
result = solver.solve(graph)

print(f"结果: {len(result)} 条流")
for arc, flow in result.items():
    print(f"  {arc.src.id}→{arc.dst.id}: flow={flow}")

