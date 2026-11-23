from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        now = time.monotonic()
        queue = self._events[key]
        cutoff = now - self.window_seconds
        while queue and queue[0] <= cutoff:
            queue.popleft()
        if len(queue) >= self.limit:
            return False
        queue.append(now)
        return True

    def forget(self, key: int) -> None:
        self._events.pop(key, None)


class WarningCooldown:
    def __init__(self, seconds: int = 10) -> None:
        self.seconds = seconds
        self._last: dict[int, float] = {}

    def should_warn(self, key: int) -> bool:
        now = time.monotonic()
        previous = self._last.get(key, 0.0)
        if now - previous < self.seconds:
            return False
        self._last[key] = now
        return True
