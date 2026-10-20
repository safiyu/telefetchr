"""Per-client in-memory sliding-window rate limiter.

Used to protect credential and Telegram-code endpoints from brute force and
flood abuse. Limits are keyed by resolved client IP (spoofing-safe, see
auth_dependencies.get_client_ip) and held in memory, which is acceptable for
a single-process deployment: a restart clears the counters, and there is no
cross-replica state to synchronize.

Design notes:
- Sliding window (not fixed buckets) so an attacker cannot burst 2x the limit
  by straddling a bucket boundary.
- Per-key bookkeeping is pruned lazily on access plus a periodic sweep, so
  memory stays bounded even after many distinct clients have connected.
- count_success=False records only failed attempts (login brute force);
  count_success=True records every request (code-sending flood protection).
"""

import logging
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)


class RateLimiter:
    """Fixed-capacity sliding window: at most `max_requests` within `window_seconds`."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = max(1.0, float(window_seconds))
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str, count_success: bool = False) -> tuple[bool, int]:
        """Record a hit (or a pending one) and return (allowed, retry_after_seconds).

        When count_success is False, the timestamp is only committed if the
        limit has not been exceeded, so rejected attempts do not extend the
        client's lockout window.
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = self._hits[key] = deque()
            while dq and dq[0] <= cutoff:
                dq.popleft()

            if len(dq) >= self.max_requests:
                retry_after = int(dq[0] + self.window_seconds - now) + 1
                return False, max(retry_after, 1)

            if count_success:
                dq.append(now)
            return True, 0

    def commit(self, key: str) -> None:
        """Commit a previously allowed (but not yet recorded) hit."""
        now = time.monotonic()
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = self._hits[key] = deque()
            dq.append(now)

    def sweep(self, max_idle_seconds: float | None = None) -> None:
        """Drop stale keys so the in-memory map cannot grow unbounded."""
        idle = max_idle_seconds or self.window_seconds * 2
        now = time.monotonic()
        with self._lock:
            stale = [
                k for k, dq in self._hits.items() if not dq or dq[-1] < now - idle
            ]
            for k in stale:
                del self._hits[k]
            if stale:
                logger.debug(f"Rate limiter: pruned {len(stale)} stale client entries")


def _start_sweeper(limiter: RateLimiter, interval_seconds: float = 300.0) -> None:
    """Start a daemon thread that periodically prunes stale rate-limit state."""
    def _run() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                limiter.sweep()
            except Exception:  # noqa: BLE001 - sweeper must never die
                logger.exception("Rate limiter sweep failed")

    t = threading.Thread(target=_run, name="rate-limiter-sweeper", daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# Endpoint policies
# ---------------------------------------------------------------------------

# Web login: 5 attempts / 15 min per IP on FAILED attempts only, so a user
# who mistypes a password a couple of times is not locked out, while
# credential-stuffing (thousands of guesses) is stopped quickly.
login_limiter = RateLimiter(max_requests=5, window_seconds=15 * 60)

# Telegram verification-code FLOOD protection: every /login/request-code call
# counts (a successful one included), because each call sends a real code and
# hammers the Telegram DC. 5 per 15 minutes is plenty for a human.
code_request_limiter = RateLimiter(max_requests=5, window_seconds=15 * 60)

# Telegram verification-code BRUTE FORCE protection: only FAILED verify/
# password attempts count, so retrying the code you actually received is fine,
# while guessing 6-digit codes is throttled.
code_verify_limiter = RateLimiter(max_requests=10, window_seconds=15 * 60)

_start_sweeper(login_limiter)
_start_sweeper(code_request_limiter)
_start_sweeper(code_verify_limiter)
