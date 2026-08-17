"""A sliding-window attempt counter. In-process, bounded, thread-safe, no deps.

WHY THIS KEYS ON USERNAME AND NOT ON IP, which is the decision worth writing
down. The api's only client is the Next.js BFF, so `request.client.host` is *the
BFF's address for every user in the company*. An IP-keyed limit would therefore
throttle everyone as though they were one attacker, and the month a settlement
window has to close is exactly when that costs the most. "Fixing" it by trusting
`X-Forwarded-For` without pinning the proxy is worse still: the key becomes
spoofable, which is a limit that stops honest users and not dishonest ones.

So the api throttles per username and per-IP belongs at the BFF, where the real
client address is. `deploy/docker-compose.yml` publishes the api on 127.0.0.1
only, so the internet-facing brute-force target is the BFF's sign-in action.
**This throttle is the backstop, not the front line** — said explicitly because
someone will otherwise notice the BFF-side limit and delete one of them as
redundant.

Construct one PER APP in `create_app`, never at module level. A module-global
throttle makes the test suite order-dependent: one test's deliberate failed
logins would throttle another's. `tests/service/conftest.py::make_client` builds
a fresh app per client, so per-app construction gives every test a clean window
for free.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque


class Throttle:
    """Allow `limit` failures per `window_s`; then refuse for `cooloff_s`."""

    def __init__(self, *, limit: int = 10, window_s: float = 300.0,
                 cooloff_s: float = 900.0, max_keys: int = 4096) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self.window_s = float(window_s)
        self.cooloff_s = float(cooloff_s)
        # Bounded so a username-spraying attacker cannot grow the dict without
        # limit. Oldest key is evicted, which at worst forgives an attacker some
        # history — acceptable, because the account-level lock in the `users`
        # table survives a process restart and this does not.
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._blocked: dict[str, float] = {}

    # `time.monotonic` and not `time.time`: a clock adjustment must not unblock
    # an account early or lock one out for hours.
    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            hits = deque()
            self._hits[key] = hits
        cutoff = now - self.window_s
        while hits and hits[0] < cutoff:
            hits.popleft()
        return hits

    def _evict_if_needed(self) -> None:
        while len(self._hits) > self.max_keys:
            self._hits.popitem(last=False)

    def check(self, key: str) -> float | None:
        """Seconds the caller must wait, or None if it may proceed."""
        now = self._now()
        with self._lock:
            until = self._blocked.get(key)
            if until is not None:
                if until > now:
                    return max(1.0, until - now)
                del self._blocked[key]
            return None

    def record_failure(self, key: str) -> float | None:
        """Record one failure. Returns the cool-off if this attempt tripped it."""
        now = self._now()
        with self._lock:
            hits = self._prune(key, now)
            hits.append(now)
            self._hits.move_to_end(key)
            self._evict_if_needed()
            if len(hits) >= self.limit:
                until = now + self.cooloff_s
                self._blocked[key] = until
                hits.clear()
                return self.cooloff_s
            return None

    def clear(self, key: str) -> None:
        """Forget a key's history. Called on a successful sign-in."""
        with self._lock:
            self._hits.pop(key, None)
            self._blocked.pop(key, None)

    def tracked_keys(self) -> int:
        """For tests, and for a future metric. Not part of the throttling logic."""
        with self._lock:
            return len(self._hits)
