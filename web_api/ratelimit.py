"""In-memory fixed-window rate limiter for the auth endpoint.

Deliberately process-local and dependency-free: it protects the single-host
stage-1 deployment from brute-forcing ``initData`` / launch tokens. A
multi-process or PostgreSQL deployment (stage 2+) should replace it with a
shared store (e.g. Redis). Disabled when ``limit <= 0`` or ``window <= 0``.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    @property
    def enabled(self) -> bool:
        return self.limit > 0 and self.window > 0

    def allow(self, key: str, *, now: Optional[float] = None) -> bool:
        """Register a hit for ``key``; return ``False`` if it exceeds the limit.

        Uses a monotonic clock so it is immune to wall-clock adjustments. Old
        hits outside the window are discarded; empty buckets are dropped to keep
        the map bounded under light load.
        """
        if not self.enabled:
            return True
        current = time.monotonic() if now is None else now
        cutoff = current - self.window
        bucket = self._hits[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(current)
        return True
