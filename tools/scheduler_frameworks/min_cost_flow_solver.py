#!/usr/bin/env python3
"""
Min-Cost Max-Flow Solver
使用 Google OR-Tools 实现（Firmament 使用 cs2/Relax IV，我们用 OR-Tools 替代）

Firmament 调用流程:
  flow_scheduler.cc:RunSchedulingIteration() 
    → solver_dispatcher.cc:Run()
    → 外部 solver (cs2/relaxiv)
"""

from ortools.graph.python import min_cost_flow
from typing import List, Tuple, Dict
from .flow_graph import FlowGraph, FlowGraphArc, NodeType

class MinCostFlowSolver:
    """
    最小成本流求解器
    使用 OR-Tools 的 SimpleMinCostFlow
    """
    
    def __init__(self):
        self.smcf = min_cost_flow.SimpleMinCostFlow()
    
    def solve(self, graph: FlowGraph) -> Dict[FlowGraphArc, int]:
        """
        求解 min-cost max-flow
        
        返回: {arc → flow} 映射
        """
        # 构建 OR-Tools 输入
        node_to_index = {node_id: idx for idx, node_id in enumerate(graph.nodes.keys())}
        
        # 添加所有边
        for arc in graph.arcs:
            src_idx = node_to_index[arc.src.id]
            dst_idx = node_to_index[arc.dst.id]
            
            self.smcf.add_arc_with_capacity_and_unit_cost(
                src_idx, dst_idx,
                arc.cap_upper,  # capacity
                arc.cost         # unit cost
            )
        
        # 设置 supply/demand
        # Task 节点供应 1，Sink 需求等于任务数，其他节点守恒（supply=0）
        task_nodes = [n for n in graph.nodes.values() if n.type == NodeType.TASK]
        sink_demand = len(task_nodes)
        
        for node_id, node in graph.nodes.items():
            idx = node_to_index[node_id]
            if node.type == NodeType.TASK:
                self.smcf.set_node_supply(idx, 1)
            elif node.type == NodeType.SINK:
                self.smcf.set_node_supply(idx, -sink_demand)
            else:
                # 中间节点（EC, Machine, PU 等）守恒
                self.smcf.set_node_supply(idx, 0)
        
        # 求解
        status = self.smcf.solve()
        
        if status != self.smcf.OPTIMAL:
            print(f"  Solver 状态: {status} (非最优)")
            return {}
        
        # 提取流量结果
        flow_result = {}
        arc_list = list(graph.arcs)
        
        for i in range(self.smcf.num_arcs()):
            if self.smcf.flow(i) > 0:
                arc = arc_list[i]
                flow_result[arc] = self.smcf.flow(i)
        
        return flow_result

