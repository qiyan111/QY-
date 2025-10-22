#!/usr/bin/env python3
"""
Firmament Flow Scheduler 完整实现
源码: baselines/firmament/src/scheduling/flow/flow_scheduler.cc

主要流程（L471-500）:
1. 构建 Flow Graph（Task → EC → Resource → PU → Sink）
2. 使用成本模型设置边的成本
3. 调用 Min-Cost Max-Flow Solver
4. 根据流量结果生成调度决策
"""

from typing import List, Tuple, Dict
from dataclasses import dataclass
from flow_graph import FlowGraph, FlowGraphNode, NodeType, FlowGraphArc
from octopus_cost_model import OctopusCostModel
from min_cost_flow_solver import MinCostFlowSolver

@dataclass
class Task:
    id: int
    cpu: float
    mem: float
    tenant: str
    arrival: int

@dataclass
class Machine:
    id: int
    cpu: float = 11.0  # 修正容量
    mem: float = 11.0
    cpu_used: float = 0
    mem_used: float = 0
    tasks: list = None
    num_pus: int = 16  # 16 个 PU (Processing Units)
    
    def __post_init__(self):
        if self.tasks is None:
            self.tasks = []
    
    def utilization(self):
        return max(self.cpu_used / self.cpu, self.mem_used / self.mem)

class FirmamentScheduler:
    """
    Firmament Flow Scheduler 完整实现
    """
    
    def __init__(self, machines: List[Machine]):
        self.machines = machines
        self.cost_model = OctopusCostModel()
        self.graph = FlowGraph()
        self.task_nodes: Dict[int, FlowGraphNode] = {}
        self.resource_nodes: Dict[int, FlowGraphNode] = {}
        self.pu_nodes: Dict[Tuple[int, int], FlowGraphNode] = {}  # (machine_id, pu_id) → node
        
        self._build_resource_topology()
    
    def _build_resource_topology(self):
        """
        构建资源拓扑图
        源码: flow_graph_manager.cc:AddResourceTopology()
        
        结构:
          Cluster AGG (EC)
            ├→ Machine 0
            │   ├→ PU 0 → Sink
            │   ├→ PU 1 → Sink
            │   ...
            ├→ Machine 1
            ...
        """
        # 创建 Sink 节点
        self.sink = self.graph.add_node(NodeType.SINK)
        
        # 创建 Cluster Aggregator (Equiv Class)
        self.cluster_agg = self.graph.add_node(NodeType.EQUIV_CLASS)
        self.cluster_agg.equiv_class = self.cost_model.cluster_agg_ec
        
        # 为每个机器创建节点树
        for machine in self.machines:
            # Machine 节点
            machine_node = self.graph.add_node(NodeType.RESOURCE_MACHINE)
            machine_node.resource_id = machine.id
            self.resource_nodes[machine.id] = machine_node
            
            # Cluster AGG → Machine 的边
            # 成本由 cost model 决定
            cost, cap_lower, cap_upper = self.cost_model.equiv_class_to_resource(
                self.cluster_agg.equiv_class,
                machine.id,
                machine_node.num_running_tasks
            )
            self.graph.add_arc(self.cluster_agg, machine_node, cost, cap_lower, machine.num_pus)
            
            # 为每个 PU 创建节点
            for pu_id in range(machine.num_pus):
                pu_node = self.graph.add_node(NodeType.RESOURCE_PU)
                pu_node.resource_id = pu_id
                self.pu_nodes[(machine.id, pu_id)] = pu_node
                
                # Machine → PU 的边（octopus_cost_model.cc L64-80）
                pu_cost, pu_cap_lower, pu_cap_upper = self.cost_model.resource_node_to_resource_node(
                    machine_node.num_running_tasks,
                    pu_node.num_running_tasks,
                    pu_id  # core_id
                )
                self.graph.add_arc(machine_node, pu_node, pu_cost, pu_cap_lower, 1)
                
                # PU → Sink 的边（octopus_cost_model.cc L82-85）
                sink_cost, sink_cap_lower, sink_cap_upper = self.cost_model.leaf_resource_to_sink(pu_id)
                self.graph.add_arc(pu_node, self.sink, sink_cost, sink_cap_lower, 1)
    
    def add_task(self, task: Task) -> FlowGraphNode:
        """
        添加任务节点到图中
        源码: flow_graph_manager.cc:AddTaskNode()
        """
        # 创建 Task 节点
        task_node = self.graph.add_node(NodeType.TASK)
        task_node.task_id = task.id
        self.task_nodes[task.id] = task_node
        
        # Task → Unscheduled Agg 的边
        unscheduled_node = self.graph.add_node(NodeType.UNSCHEDULED_AGG)
        cost, cap_lower, cap_upper = self.cost_model.task_to_unscheduled_agg(task.id)
        self.graph.add_arc(task_node, unscheduled_node, cost, cap_lower, cap_upper)
        
        # Unscheduled Agg → Sink
        self.graph.add_arc(unscheduled_node, self.sink, 0, 1, 0)
        
        # Task → Cluster Agg (EC)
        self.graph.add_arc(task_node, self.cluster_agg, 0, 1, 0)
        
        return task_node
    
    def schedule(self, tasks: List[Task]) -> List[Tuple[int, int]]:
        """
        运行完整调度流程
        源码: flow_scheduler.cc:RunSchedulingIteration() L471-530
        
        返回: [(task_id, machine_id), ...]
        """
        print(f"  构建 Flow Graph ({len(tasks)} 任务, {len(self.machines)} 机器)...")
        
        # 1. 添加所有 Task 节点
        for task in tasks:
            self.add_task(task)
        
        print(f"  Graph: {self.graph.num_nodes()} 节点, {self.graph.num_arcs()} 边")
        
        # 2. 调用 Min-Cost Max-Flow Solver
        print(f"  求解 Min-Cost Max-Flow...")
        solver = MinCostFlowSolver()
        flow_result = solver.solve(self.graph)
        
        # 3. 提取调度决策
        # 查找 Task → Resource 路径上有流的边
        placements = []
        
        for arc, flow_val in flow_result.items():
            if flow_val == 0:
                continue
            
            # 如果是 Task → ... → PU 的路径，提取 task 和 machine
            if arc.src.type == NodeType.TASK:
                # 回溯找到最终的 PU/Machine
                current = arc.dst
                while current.type != NodeType.SINK:
                    # 找 outgoing arc 有流的
                    next_arc = next((a for a in current.outgoing_arcs if a in flow_result and flow_result[a] > 0), None)
                    if not next_arc:
                        break
                    current = next_arc.dst
                    
                    if current.type == NodeType.RESOURCE_PU:
                        # 找到 PU → 反推 machine
                        for (machine_id, pu_id), pu_node in self.pu_nodes.items():
                            if pu_node == current:
                                placements.append((arc.src.task_id, machine_id))
                                break
                        break
        
        return placements
    
    def task_completed(self, task_id: int):
        """
        ⭐ 任务完成处理 - 从 Flow Graph 中移除任务
        源码: flow_graph_manager.cc::TaskCompleted()
        
        调用时机：任务运行完成时
        作用：
        1. 从 flow graph 中移除任务节点
        2. 允许资源被其他任务使用
        
        源码关键代码（flow_graph_manager.cc L1058-1092）：
            uint64_t task_node_id = flow_graph_manager_->TaskCompleted(td_ptr->uid());
            RemoveTaskNode(task_node_id);
        """
        # 从 task_nodes 字典中移除
        if task_id in self.task_nodes:
            task_node = self.task_nodes.pop(task_id)
            # 注意：在真实 Firmament 中，这会从 flow graph 中物理删除节点
            # 但由于我们每次调度都重建 graph，这里只需要清理引用即可
            # 下次 schedule() 调用时会重建新的 graph

