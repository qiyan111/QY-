#!/usr/bin/env python3
"""
Firmament OCTOPUS Cost Model 完整实现
源码: baselines/firmament/src/scheduling/flow/octopus_cost_model.cc

成本模型: cost = num_running_tasks * 100 + core_id
"""

from typing import Dict, List
from .flow_graph import FlowGraph, FlowGraphNode, NodeType

# 常量定义（octopus_cost_model.cc L31）
BUSY_PU_OFFSET = 100
UNSCHEDULED_COST = 1000000

class OctopusCostModel:
    """
    OCTOPUS Cost Model 完整实现
    源码: octopus_cost_model.cc
    """
    
    def __init__(self):
        self.cluster_agg_ec = hash("CLUSTER_AGG")
        self.machines: List[int] = []
    
    def task_to_unscheduled_agg(self, task_id: int) -> tuple:
        """
        TaskToUnscheduledAgg() - L47-49
        返回 (cost, cap_lower, cap_upper)
        """
        # 允许 1 单位流量通过（cap_upper=1）
        return (UNSCHEDULED_COST, 0, 1)
    
    def task_to_resource_node(self, task_id: int, resource_id: int) -> tuple:
        """
        TaskToResourceNode() - L59-62
        """
        # 任务到资源节点：每条边容量 1
        return (0, 0, 1)
    
    def resource_node_to_resource_node(self, src_running_tasks: int,
                                       dst_running_tasks: int,
                                       core_id: int) -> tuple:
        """
        ResourceNodeToResourceNode() - L64-80
        
        核心成本函数（C++ L73-79）:
          cost = core_id + dst.num_running_tasks_below() * BUSY_PU_OFFSET
        """
        cost = core_id + dst_running_tasks * BUSY_PU_OFFSET
        # 机器到 PU：每个 PU 容量 1
        return (cost, 0, 1)
    
    def leaf_resource_to_sink(self, resource_id: int) -> tuple:
        """
        LeafResourceNodeToSink() - L82-85
        """
        # PU 到 Sink：容量 1（每个 PU 同时只运行 1 个任务）
        return (0, 0, 1)  # max_tasks_per_pu = 1 简化
    
    def equiv_class_to_resource(self, ec: int, resource_id: int,
                                 num_running_tasks: int) -> tuple:
        """
        EquivClassToResourceNode() - L100-110
        
        C++ L107-109:
          Cost_t cost = rs->descriptor().num_running_tasks_below() * BUSY_PU_OFFSET;
        """
        cost = num_running_tasks * BUSY_PU_OFFSET
        return (cost, 1, 0)

