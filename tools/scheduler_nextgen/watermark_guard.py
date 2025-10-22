from __future__ import annotations

class WatermarkGuard:
    def __init__(self, low: float = 0.5, high: float = 0.85, high_penalty: float = 1e6):
        self.low = low
        self.high = high
        self.high_penalty = high_penalty

    def admissible(self, node) -> bool:
        util = node.utilization()
        return util < self.high

    def penalty(self, node) -> float:
        util = node.utilization()
        if util >= self.high:
            return self.high_penalty
        if util >= self.low:
            return 1.2
        return 1.0
