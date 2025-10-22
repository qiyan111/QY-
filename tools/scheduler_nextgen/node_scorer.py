from __future__ import annotations

from typing import Dict
import math


def dominant_util(machine, task) -> float:
    cpu_util = (machine.cpu_used + task.cpu) / machine.cpu if machine.cpu else 1.0
    mem_util = (machine.mem_used + task.mem) / machine.mem if machine.mem else 1.0
    return max(cpu_util, mem_util)


def _rem_vector(machine, extra_dims: Dict[str, float]) -> Dict[str, float]:
    vec = {
        "cpu": max(machine.cpu - machine.cpu_used, 0.0),
        "mem": max(machine.mem - machine.mem_used, 0.0),
    }
    vec.update({k: max(extra_dims.get(k, 0.0), 0.0) for k in extra_dims})
    return vec


def _rem_vector_after(machine, task, extra_dims: Dict[str, float]) -> Dict[str, float]:
    vec = {
        "cpu": max(machine.cpu - (machine.cpu_used + task.cpu), 0.0),
        "mem": max(machine.mem - (machine.mem_used + task.mem), 0.0),
    }
    vec.update({k: max(extra_dims.get(k, 0.0) - getattr(task, k, 0.0), 0.0) for k in extra_dims})
    return vec


def _jain(vec: Dict[str, float]) -> float:
    values = list(vec.values())
    if not values or sum(values) <= 0:
        return 0.0
    total = sum(values)
    denominator = len(values) * sum(v * v for v in values)
    if denominator <= 0:
        return 0.0
    return (total * total) / denominator


def frag_increase(machine, task, extra_dims: Dict[str, float] = None) -> float:
    extra_dims = extra_dims or {}
    before = _jain(_rem_vector(machine, extra_dims))
    after = _jain(_rem_vector_after(machine, task, extra_dims))
    return max(0.0, before - after)


def score_node(machine, task, alpha: float = 0.7, extra_dims: Dict[str, float] = None,
               use_affinity: bool = False, affinity_bonus: float = 0.05) -> float:
    """Smaller score means better node.
    Penalize higher utilization and larger fragmentation increase.
    Bonus for locality/affinity (task returns to original machine).
    
    Args:
        machine: Target machine
        task: Task to place
        alpha: Weight for utilization vs fragmentation
        extra_dims: Extra resource dimensions (mem_bandwidth, net_bandwidth, disk_io)
        use_affinity: Enable locality-aware scoring
        affinity_bonus: Score reduction for affinity match
    """
    alpha = min(max(alpha, 0.0), 1.0)
    util = dominant_util(machine, task)
    frag_inc = frag_increase(machine, task, extra_dims)
    
    score = alpha * util + (1 - alpha) * frag_inc
    
    # 亲和性奖励：如果任务原本在这台机器上运行，降低分数（优先选择）
    if use_affinity and hasattr(task, 'machine_id') and task.machine_id:
        if str(machine.id) == str(task.machine_id):
            score -= affinity_bonus
    
    return max(0.0, score)  # 确保分数非负
