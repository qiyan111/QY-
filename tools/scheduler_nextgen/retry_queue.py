from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Tuple, Optional


@dataclass
class _RetryEntry:
    task: Tuple[int, float, float, str, int]
    deadline_ms: int
    attempts: int


class RetryQueue:
    def __init__(self, ttl_ms: int = 5000, max_attempts: int = 2):
        self.ttl_ms = ttl_ms
        self.max_attempts = max_attempts
        self.queue: Deque[_RetryEntry] = deque()

    def push(self, task: Tuple[int, float, float, str, int], now_ms: int, attempts: int = 0) -> None:
        self.queue.append(_RetryEntry(task=task, deadline_ms=now_ms + self.ttl_ms, attempts=attempts))

    def pop_ready(self, now_ms: int) -> Optional[Tuple[Tuple[int, float, float, str, int], int]]:
        if not self.queue:
            return None
        if self.queue[0].deadline_ms <= now_ms:
            entry = self.queue.popleft()
            return entry.task, entry.attempts
        return None

    def next_deadline(self) -> Optional[int]:
        if not self.queue:
            return None
        return self.queue[0].deadline_ms

    def has_ready(self, now_ms: int) -> bool:
        if not self.queue:
            return False
        return self.queue[0].deadline_ms <= now_ms

    def __len__(self):
        return len(self.queue)
