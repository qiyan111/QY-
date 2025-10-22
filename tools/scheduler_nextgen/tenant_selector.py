from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any


@dataclass(order=True)
class _TenantEntry:
    sort_key: float
    tenant_id: str = field(compare=False)
    index: int = field(compare=False, default=0)


@dataclass
class TaskRecord:
    task: Tuple[int, float, float, str, int]
    enqueue_ts: int


class TenantSelector:
    """Weighted DRF-like tenant picker with aging support."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        aging_half_life_ms: int = 30_000,
        tenant_groups: Optional[Dict[str, Any]] = None,
    ):
        self.weights = weights or {}
        self.aging_half_life_ms = aging_half_life_ms
        self.heap: List[_TenantEntry] = []
        self.tasks: Dict[str, List[TaskRecord]] = {}
        self.resource_usage: Dict[str, Dict[str, float]] = {}
        self.cluster_capacity: Dict[str, float] = {"cpu": 1.0, "mem": 1.0}
        self.counter = 0
        self.tenant_groups: Dict[str, Any] = dict(tenant_groups or {})
        self.group_weights: Dict[Any, float] = defaultdict(lambda: 1.0)
        self.group_default = "default"

    def set_cluster_capacity(self, cpu_total: float, mem_total: float):
        self.cluster_capacity["cpu"] = max(cpu_total, 1e-6)
        self.cluster_capacity["mem"] = max(mem_total, 1e-6)

    def _dominant_share(self, tenant: str) -> float:
        usage = self.resource_usage.get(tenant, {"cpu": 0.0, "mem": 0.0})
        ratios = [usage["cpu"] / self.cluster_capacity["cpu"], usage["mem"] / self.cluster_capacity["mem"]]
        return max(ratios)

    def _effective_weight(self, tenant: str, now_ms: int) -> float:
        base_weight = self.weights.get(tenant, 1.0)
        group = self.tenant_groups.get(tenant, self.group_default)
        group_weight = self.group_weights.get(group, 1.0)
        base = base_weight * group_weight
        queue = self.tasks.get(tenant, [])
        if not queue:
            return base
        oldest = queue[0].enqueue_ts
        waiting = max(now_ms - oldest, 0)
        if self.aging_half_life_ms > 0:
            factor = 2 ** (waiting / self.aging_half_life_ms)
        else:
            factor = 1.0
        return base / factor

    def _push(self, tenant: str, now_ms: int):
        dom = self._dominant_share(tenant)
        eff = self._effective_weight(tenant, now_ms)
        sort_key = dom * eff
        heapq.heappush(self.heap, _TenantEntry(sort_key=sort_key, tenant_id=tenant, index=self.counter))
        self.counter += 1

    def add_task(self, task: Tuple[int, float, float, str, int], now_ms: int):
        tid, cpu, mem, tenant, arrival = task
        if tenant not in self.tasks:
            self.tasks[tenant] = []
            self.resource_usage.setdefault(tenant, {"cpu": 0.0, "mem": 0.0})
            self.tenant_groups.setdefault(tenant, self.group_default)
            self.group_weights.setdefault(self.tenant_groups[tenant], 1.0)
        self.tasks[tenant].append(TaskRecord(task=task, enqueue_ts=now_ms))
        self._push(tenant, now_ms)

    def pop_next(self, now_ms: int) -> Optional[Tuple[int, float, float, str, int]]:
        while self.heap:
            entry = heapq.heappop(self.heap)
            tenant = entry.tenant_id
            queue = self.tasks.get(tenant)
            if queue:
                record = queue.pop(0)
                if queue:
                    self._push(tenant, now_ms)
                return record.task
        return None

    def update_usage(self, tenant: str, cpu: float, mem: float):
        usage = self.resource_usage.setdefault(tenant, {"cpu": 0.0, "mem": 0.0})
        usage["cpu"] += cpu
        usage["mem"] += mem

    def release_usage(self, tenant: str, cpu: float, mem: float):
        usage = self.resource_usage.setdefault(tenant, {"cpu": 0.0, "mem": 0.0})
        usage["cpu"] = max(usage["cpu"] - cpu, 0.0)
        usage["mem"] = max(usage["mem"] - mem, 0.0)

    def update_group_weights(self, weight_map: Dict[Any, float]):
        for group, weight in weight_map.items():
            if weight <= 0:
                weight = 1e-3
            self.group_weights[group] = weight

    def group_queue_length(self, group) -> int:
        total = 0
        for tenant, queue in self.tasks.items():
            if self.tenant_groups.get(tenant, self.group_default) == group:
                total += len(queue)
        return total

    def total_pending(self) -> int:
        return sum(len(queue) for queue in self.tasks.values())

    def get_group_queue_lengths(self) -> Dict[Any, int]:
        lengths: Dict[Any, int] = defaultdict(int)
        for tenant, queue in self.tasks.items():
            group = self.tenant_groups.get(tenant, self.group_default)
            lengths[group] += len(queue)
        return dict(lengths)

    def has_pending(self) -> bool:
        return any(queue for queue in self.tasks.values())
