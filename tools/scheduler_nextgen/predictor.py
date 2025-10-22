from __future__ import annotations

from typing import Dict


class EWMA:
    def __init__(self, alpha: float = 0.5):
        self.alpha = alpha
        self.state: Dict[int, float] = {}

    def update(self, node_id: int, utilization: float):
        prev = self.state.get(node_id, utilization)
        self.state[node_id] = self.alpha * utilization + (1 - self.alpha) * prev

    def forecast(self, node_id: int) -> float:
        return self.state.get(node_id, 0.0)
