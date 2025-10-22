#!/usr/bin/env python3
"""
Apache Mesos DRF Allocator 完整实现
源码: baselines/mesos/src/master/allocator/mesos/hierarchical.cpp

核心组件:
1. DRFSorter: 按 dominant share 排序客户端
2. HierarchicalAllocatorProcess: 主分配逻辑
"""

from typing import Dict, List, Tuple
from collections import defaultdict
from dataclasses import dataclass

def predict_violation_risk(util_after: float) -> float:
    """
    预测违约风险（尾延迟与利用率的非线性关系）
    与 run_complete_comparison.py 使用的函数保持一致的分段逻辑
    """
    if util_after > 0.95:
        return 0.40
    elif util_after > 0.90:
        return 0.25
    elif util_after > 0.85:
        return 0.15
    elif util_after > 0.80:
        return 0.10
    elif util_after > 0.75:
        return 0.06
    else:
        return 0.02

@dataclass
class Task:
    """任务定义"""
    id: int
    cpu: float
    mem: float
    tenant: str
    arrival: int

@dataclass
class Client:
    """
    客户端（Framework/Tenant）
    """
    id: str
    cpu_allocated: float = 0
    mem_allocated: float = 0
    weight: float = 1.0

@dataclass
class Agent:
    """
    Agent (Slave/Machine)
    """
    id: int
    cpu_total: float
    mem_total: float
    cpu_available: float
    mem_available: float

class DRFSorter:
    """
    DRF Sorter 完整实现
    源码: baselines/mesos/src/master/allocator/mesos/sorter/drf/sorter.cpp
    """
    
    def __init__(self):
        self.clients: Dict[str, Client] = {}
        self.total_cpu = 0.0
        self.total_mem = 0.0
        self.dirty = True
        self.sorted_clients: List[str] = []
    
    def add_client(self, client_id: str, weight: float = 1.0):
        """
        add() - sorter.cpp L73-170
        """
        self.clients[client_id] = Client(id=client_id, weight=weight)
        self.dirty = True
    
    def add_slave(self, slave_id: int, cpu: float, mem: float):
        """
        addSlave() - 增加总资源
        """
        self.total_cpu += cpu
        self.total_mem += mem
        self.dirty = True
    
    def allocated(self, client_id: str, cpu: float, mem: float):
        """
        allocated() - 记录资源分配
        源码: sorter.cpp::allocated()
        """
        if client_id in self.clients:
            self.clients[client_id].cpu_allocated += cpu
            self.clients[client_id].mem_allocated += mem
            self.dirty = True
    
    def unallocated(self, client_id: str, cpu: float, mem: float):
        """
        unallocated() - ⭐ 资源释放（源码中存在但我们之前未实现）
        源码: sorter.cpp::unallocated()
        
        当任务完成时调用，释放已分配的资源
        """
        if client_id in self.clients:
            self.clients[client_id].cpu_allocated -= cpu
            self.clients[client_id].mem_allocated -= mem
            # 确保不为负数
            self.clients[client_id].cpu_allocated = max(0, self.clients[client_id].cpu_allocated)
            self.clients[client_id].mem_allocated = max(0, self.clients[client_id].mem_allocated)
            self.dirty = True
    
    def calculate_share(self, client: Client) -> float:
        """
        calculateShare() - sorter.cpp L567-594
        
        核心代码（C++ L588-590）:
          share = std::max(share, allocation.value() / scalar.value());
          return share / getWeight(node);
        """
        if self.total_cpu == 0 or self.total_mem == 0:
            return 0.0
        
        cpu_share = client.cpu_allocated / self.total_cpu
        mem_share = client.mem_allocated / self.total_mem
        
        # Dominant share
        dominant_share = max(cpu_share, mem_share)
        
        # 除以权重 (L593)
        return dominant_share / client.weight
    
    def sort(self) -> List[str]:
        """
        sort() - sorter.cpp L481-552
        
        返回按 dominant share 排序的客户端列表
        """
        if self.dirty:
            # 计算所有客户端的 share (L498)
            client_shares = []
            for client_id, client in self.clients.items():
                share = self.calculate_share(client)
                client_shares.append((client_id, share))
            
            # 排序 (L502-504)
            client_shares.sort(key=lambda x: x[1])
            self.sorted_clients = [c[0] for c in client_shares]
            
            self.dirty = False
        
        return self.sorted_clients

class HierarchicalAllocator:
    """
    Mesos Hierarchical Allocator 完整实现
    源码: baselines/mesos/src/master/allocator/mesos/hierarchical.cpp
    """
    
    def __init__(self, agents: List[Agent], tenant_credits: Dict[str, float] = None):
        self.agents = {a.id: a for a in agents}
        self.sorter = DRFSorter()

        # 注册所有 agent
        for agent in agents:
            self.sorter.add_slave(agent.id, agent.cpu_total, agent.mem_total)

        # 信用与风险状态
        self.tenant_credits: Dict[str, float] = tenant_credits or defaultdict(lambda: 1.0)
        self.machine_risk_ema: Dict[int, float] = {a.id: 0.02 for a in agents}
        self.global_risk_ema: float = 0.02
        self.alpha: float = 0.2   # EMA 学习率
        self.beta: float = 2.0    # 风险对权重指数的影响放大系数
    
    def _credit_to_weight(self, credit: float) -> float:
        """
        将信用映射到 DRF 权重区间 [0.55, 1.00]（credit ∈ [0.1, 1.0]）
        credit 高 ⇒ 权重大 ⇒ share 小 ⇒ 优先级高
        """
        credit = max(0.1, min(1.0, credit))
        return 0.5 + 0.5 * credit

    def _compute_gamma(self) -> float:
        """
        根据全局风险调整权重指数 γ ∈ [1.0, 2.0]
        风险越高，γ 越大 ⇒ 权重差异更显著 ⇒ 提高公平性权重的影响力
        """
        excess = max(0.0, self.global_risk_ema - 0.02)
        gamma = 1.0 + min(1.0, self.beta * excess)  # 上限 2.0
        return gamma

    def _update_client_weights(self):
        gamma = self._compute_gamma()
        for client_id, client in self.sorter.clients.items():
            credit = self.tenant_credits[client_id] if isinstance(self.tenant_credits, dict) else 1.0
            base_w = self._credit_to_weight(credit)
            client.weight = max(0.25, min(2.0, base_w ** gamma))

    def _update_risk_after_allocation(self, agent_id: int):
        # 以 agent 的当前利用率估计违约风险，并更新 EMA（机器与全局）
        agent = self.agents[agent_id]
        util = max(1.0 - agent.cpu_available / max(agent.cpu_total, 1e-6),
                   1.0 - agent.mem_available / max(agent.mem_total, 1e-6))
        risk_now = predict_violation_risk(util)
        prev_m = self.machine_risk_ema[agent_id]
        self.machine_risk_ema[agent_id] = (1 - self.alpha) * prev_m + self.alpha * risk_now
        prev_g = self.global_risk_ema
        self.global_risk_ema = (1 - self.alpha) * prev_g + self.alpha * risk_now

    def add_framework(self, framework_id: str):
        """
        addFramework() - hierarchical.cpp
        """
        self.sorter.add_client(framework_id)
    
    def recover_resources(self, framework_id: str, agent_id: int, cpu: float, mem: float):
        """
        ⭐ recoverResources() - 资源回收（源码 hierarchical.cpp L1619-1738）
        
        当任务完成时调用，执行：
        1. 更新 agent 的可用资源（增加）
        2. 更新 sorter 中的已分配资源（减少）
        
        源码关键代码：
            (*slave)->increaseAvailable(frameworkId, resources);    // L1674
            untrackAllocatedResources(slaveId, frameworkId, resources);  // L1686
        """
        # 1. 增加 agent 的可用资源
        if agent_id in self.agents:
            agent = self.agents[agent_id]
            agent.cpu_available += cpu
            agent.mem_available += mem
            # 确保不超过总容量
            agent.cpu_available = min(agent.cpu_available, agent.cpu_total)
            agent.mem_available = min(agent.mem_available, agent.mem_total)
        
        # 2. 从 sorter 中减少已分配资源
        self.sorter.unallocated(framework_id, cpu, mem)
    
    def allocate(self, tasks_by_framework: Dict[str, List[Task]]) -> List[Tuple[int, int]]:
        """
        主分配循环
        源码: hierarchical.cpp:allocate() (实际在 generateOffers 中)
        
        DRF 核心逻辑:
        1. 按 dominant share 排序 frameworks
        2. 依次为每个 framework 分配资源
        3. 更新分配量并重新排序
        """
        placements = []
        
        # 注册所有 frameworks
        for fw_id in tasks_by_framework.keys():
            if fw_id not in self.sorter.clients:
                self.add_framework(fw_id)
        
        # 准备任务队列
        pending_tasks = {}
        for fw_id, tasks in tasks_by_framework.items():
            pending_tasks[fw_id] = list(tasks)
        
        iteration = 0
        failed_rounds = 0  # 记录连续失败轮数
        max_failed_rounds = len(tasks_by_framework) * 2  # 允许的最大连续失败
        
        while True:
            if iteration % 1000 == 0:
                remaining = sum(len(ts) for ts in pending_tasks.values())
                if remaining == 0:
                    break
                print(f"  Mesos DRF 已分配 {len(placements)}, 剩余 {remaining}...", end='\r')
            iteration += 1
            
            # 在排序前根据最新风险/信用更新权重
            self._update_client_weights()

            # 1. 按 dominant share 排序 (调用 sorter.sort())
            sorted_frameworks = self.sorter.sort()
            
            # 2. 为 share 最小的 framework 分配
            allocated_this_round = False
            for fw_id in sorted_frameworks:
                if not pending_tasks.get(fw_id):
                    continue
                
                task = pending_tasks[fw_id].pop(0)
                
                # 查找可用的 agent (generateOffers 逻辑)
                best_agent = None
                for agent in self.agents.values():
                    if (agent.cpu_available >= task.cpu and
                        agent.mem_available >= task.mem):
                        # 选择剩余资源最多的 agent
                        if best_agent is None:
                            best_agent = agent
                        else:
                            if ((agent.cpu_available + agent.mem_available) >
                                (best_agent.cpu_available + best_agent.mem_available)):
                                best_agent = agent
                
                if best_agent:
                    # 分配
                    best_agent.cpu_available -= task.cpu
                    best_agent.mem_available -= task.mem
                    
                    # 记录到 sorter
                    self.sorter.allocated(fw_id, task.cpu, task.mem)
                    
                    placements.append((task.id, best_agent.id))
                    allocated_this_round = True
                    failed_rounds = 0  # 重置失败计数

                    # 风险与信用更新：基于分配后该 agent 的利用率
                    self._update_risk_after_allocation(best_agent.id)
                    util_after = max(1.0 - best_agent.cpu_available / max(best_agent.cpu_total, 1e-6),
                                     1.0 - best_agent.mem_available / max(best_agent.mem_total, 1e-6))
                    credit = self.tenant_credits.get(fw_id, 1.0)
                    if util_after > 0.85:
                        self.tenant_credits[fw_id] = max(0.3, credit - 0.01)
                    elif util_after < 0.70:
                        self.tenant_credits[fw_id] = min(1.0, credit + 0.01)
                    break  # 每轮只分配一个任务
                else:
                    # 无法分配：放回队列
                    pending_tasks[fw_id].insert(0, task)
                    # 不 break，尝试下一个 framework
            
            # 如果本轮没分配任何任务，累计失败
            if not allocated_this_round:
                failed_rounds += 1
                if failed_rounds >= max_failed_rounds:
                    # 连续多轮失败，说明剩余任务都无法分配
                    break
        
        print()
        return placements

