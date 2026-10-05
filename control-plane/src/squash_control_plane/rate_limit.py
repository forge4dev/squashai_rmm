import asyncio
import hashlib
import time
from collections import defaultdict, deque


class FixedWindowRateLimiter:
    def __init__(self, requests_per_minute: int) -> None:
        self._limit = requests_per_minute
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, identity: str) -> bool:
        now = time.monotonic()
        cutoff = now - 60
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        async with self._lock:
            entries = self._requests[identity_hash]
            while entries and entries[0] <= cutoff:
                entries.popleft()
            if len(entries) >= self._limit:
                return False
            entries.append(now)
            return True
