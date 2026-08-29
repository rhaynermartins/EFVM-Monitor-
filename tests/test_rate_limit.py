from __future__ import annotations

import pytest

from efvm_monitor.rate_limit import RateLimitExceeded, SlidingWindowRateLimiter


def test_blocks_only_until_sliding_window_expires() -> None:
    current_time = [100.0]
    limiter = SlidingWindowRateLimiter(clock=lambda: current_time[0])

    limiter.consume("login:user", limit=2, window_seconds=60)
    limiter.consume("login:user", limit=2, window_seconds=60)

    with pytest.raises(RateLimitExceeded) as blocked:
        limiter.consume("login:user", limit=2, window_seconds=60)
    assert blocked.value.retry_after_seconds == 60

    current_time[0] = 161.0
    limiter.consume("login:user", limit=2, window_seconds=60)


def test_reset_releases_only_selected_operation_key() -> None:
    limiter = SlidingWindowRateLimiter(clock=lambda: 10.0)
    limiter.consume("login:first", limit=1, window_seconds=60)
    limiter.consume("login:second", limit=1, window_seconds=60)

    limiter.reset("login:first")

    limiter.consume("login:first", limit=1, window_seconds=60)
    with pytest.raises(RateLimitExceeded):
        limiter.consume("login:second", limit=1, window_seconds=60)
