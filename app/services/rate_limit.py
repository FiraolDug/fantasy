"""
Sliding-window rate limiter used as a FastAPI dependency.

In-process only: fine for a single instance. With several instances, back it with
Redis (same interface) — otherwise each instance keeps its own counters.
Verification-specific limits are stricter and stored in the database instead
(see services/verification.py) so they survive restarts and scale-out.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import settings


def client_ip(request: Request) -> str:
    """Only trust as many X-Forwarded-For hops as TRUSTED_PROXY_COUNT says."""
    n = settings.trusted_proxy_count
    if n > 0:
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= n:
            return parts[-n]
    return request.client.host if request.client else "unknown"


class _Window:
    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def hit(self, key: str, limit: int, seconds: int) -> int | None:
        """Returns None if allowed, else seconds until retry."""
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > seconds:
                q.popleft()
            if len(q) >= limit:
                return max(1, int(seconds - (now - q[0])))
            q.append(now)
            return None

    def clear(self):
        with self.lock:
            self.hits.clear()


_window = _Window()
reset_rate_limits = _window.clear


def rate_limit(name: str, limit: int, seconds: int, *, per_user: bool = False):
    def dependency(request: Request):
        key = f"{name}:{client_ip(request)}"
        if per_user:
            auth = request.headers.get("authorization", "")
            key += f":{auth[-24:]}"
        wait = _window.hit(key, limit, seconds)
        if wait is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Try again shortly.",
                headers={"Retry-After": str(wait)},
            )
    return dependency
