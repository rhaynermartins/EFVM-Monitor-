"""Limites defensivos leves para uma instância com um único processo web."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable


class RateLimitExceeded(RuntimeError):
    """Informa por quantos segundos uma operação deve aguardar."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Muitas tentativas. Aguarde um pouco e tente novamente.")
        self.retry_after_seconds = retry_after_seconds


class SlidingWindowRateLimiter:
    """Mantém somente timestamps recentes e não persiste identificadores."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, window_seconds: int) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("Limite e janela devem ser positivos.")
        now = self._clock()
        threshold = now - window_seconds
        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= threshold:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, math.ceil(bucket[0] + window_seconds - now))
                raise RateLimitExceeded(retry_after)
            bucket.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)
