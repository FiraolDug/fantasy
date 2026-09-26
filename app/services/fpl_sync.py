"""
Rate-limited FPL sync worker (token bucket).

Run as a background process:  python -m app.services.fpl_sync
This is a minimal, single-process token bucket suitable for MVP scale.
For multi-instance deployments, move the bucket into Redis.
"""
import threading
import time
from collections import deque

MAX_CALLS_PER_MINUTE = 55  # stays under the agreed 50-60 req/min ceiling


class TokenBucket:
    def __init__(self, max_per_minute: int = MAX_CALLS_PER_MINUTE):
        self.max_per_minute = max_per_minute
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > 60:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_per_minute:
                sleep_for = 60 - (now - self._timestamps[0])
                time.sleep(max(sleep_for, 0))
            self._timestamps.append(time.monotonic())


fpl_rate_limiter = TokenBucket()
