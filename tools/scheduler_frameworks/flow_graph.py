#!/usr/bin/env python3
"""
Firmament Flow Graph 完整实现
源码: baselines/firmament/src/scheduling/flow/flow_graph.{cc,h}

Flow Graph 结构:
  Task Node → Equiv Class → Resource Node → PU Node → Sink
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
from enum import Enum

class NodeType(Enum):
    """节点类型（flow_graph_node.h）"""
    TASK = 0
    UNSCHEDULED_AGG = 1
    EQUIV_CLASS = 2
    RESOURCE_MACHINE = 3
    RESOURCE_PU = 4
    SINK = 5

@dataclass
class FlowGraphNode:
    """
    Flow Graph 节点
    源码: baselines/firmament/src/scheduling/flow/flow_graph_node.{h,cc}
    """
    id: int
    type: NodeType
    task_id: Optional[int] = None
    resource_id: Optional[int] = None
    equiv_class: Optional[int] = None
    outgoing_arcs: List['FlowGraphArc'] = field(default_factory=list)
    incoming_arcs: List['FlowGraphArc'] = field(default_factory=list)
    # 资源节点特有
    num_running_tasks: int = 0
    num_slots: int = 0

@dataclass(eq=True, frozen=False)
class FlowGraphArc:
    """
    Flow Graph 边
    源码: baselines/firmament/src/scheduling/flow/flow_graph_arc.{h,cc}
    """
    src: FlowGraphNode
    dst: FlowGraphNode
    cost: int  # 成本
    cap_lower: int  # 容量下界
    cap_upper: int  # 容量上界
    flow: int = 0  # 当前流量
    
    def __hash__(self):
        return hash((id(self.src), id(self.dst), self.cost))

class FlowGraph:
    """
    完整 Flow Graph 实现
    源码: baselines/firmament/src/scheduling/flow/flow_graph.cc
    """
    
    def __init__(self):
        self.nodes: Dict[int, FlowGraphNode] = {}
        self.arcs: List[FlowGraphArc] = []  # 改用 List 而非 Set
        self.current_id = 0
        self.sink_node: Optional[FlowGraphNode] = None
    
    def add_node(self, node_type: NodeType) -> FlowGraphNode:
        """
        AddNode() - flow_graph.cc
        """
        node_id = self.current_id
        self.current_id += 1
        
        node = FlowGraphNode(id=node_id, type=node_type)
        self.nodes[node_id] = node
        
        if node_type == NodeType.SINK:
            self.sink_node = node
        
        return node
    
    def add_arc(self, src: FlowGraphNode, dst: FlowGraphNode, 
                cost: int, cap_lower: int, cap_upper: int) -> FlowGraphArc:
        """
        AddArc() - flow_graph.cc L39-57
        """
        arc = FlowGraphArc(
            src=src, dst=dst,
            cost=cost,
            cap_lower=cap_lower,
            cap_upper=cap_upper
        )
        
        src.outgoing_arcs.append(arc)
        dst.incoming_arcs.append(arc)
        self.arcs.append(arc)
        
        return arc
    
    def num_nodes(self) -> int:
        return len(self.nodes)
    
    def num_arcs(self) -> int:
        return len(self.arcs)

