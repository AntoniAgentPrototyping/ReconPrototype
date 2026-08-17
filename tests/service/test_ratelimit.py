"""The sign-in throttle. No database.

Time is injected by monkeypatching `Throttle._now` rather than by sleeping: a
test that sleeps for a 300-second window is a test nobody runs.
"""

from __future__ import annotations

import threading

import pytest

from service.ratelimit import Throttle


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock."""
    state = {"t": 1000.0}
    monkeypatch.setattr(Throttle, "_now", staticmethod(lambda: state["t"]))
    return state


def test_it_admits_up_to_the_limit_then_blocks(clock):
    t = Throttle(limit=3, window_s=300, cooloff_s=900)
    for _ in range(2):
        assert t.check("a") is None
        assert t.record_failure("a") is None
    assert t.check("a") is None
    assert t.record_failure("a") == 900        # the third failure trips it
    assert t.check("a") == pytest.approx(900)


def test_the_cooloff_expires_on_its_own(clock):
    """Self-clearing, not admin-cleared. A permanent lock on a known username is
    a denial of service anyone can trigger, and it is worst at month end."""
    t = Throttle(limit=2, window_s=300, cooloff_s=900)
    t.record_failure("a")
    t.record_failure("a")
    assert t.check("a") is not None

    clock["t"] += 899
    assert t.check("a") is not None
    clock["t"] += 2
    assert t.check("a") is None


def test_the_window_slides(clock):
    """Two failures an hour apart must not add up to a lockout."""
    t = Throttle(limit=3, window_s=300, cooloff_s=900)
    t.record_failure("a")
    clock["t"] += 301
    t.record_failure("a")
    clock["t"] += 301
    assert t.record_failure("a") is None       # the first two aged out
    assert t.check("a") is None


def test_keys_are_independent(clock):
    t = Throttle(limit=2, window_s=300, cooloff_s=900)
    t.record_failure("a")
    t.record_failure("a")
    assert t.check("a") is not None
    assert t.check("b") is None


def test_clear_resets_a_key(clock):
    """Called on a successful sign-in, so a user who mistypes twice and then
    succeeds does not carry the history into next week."""
    t = Throttle(limit=2, window_s=300, cooloff_s=900)
    t.record_failure("a")
    t.record_failure("a")
    assert t.check("a") is not None
    t.clear("a")
    assert t.check("a") is None


def test_the_key_dict_stays_bounded(clock):
    """A username-spraying attacker must not be able to grow memory without
    limit."""
    t = Throttle(limit=100, window_s=300, cooloff_s=900, max_keys=64)
    for i in range(10_000):
        t.record_failure(f"user-{i}")
    assert t.tracked_keys() <= 64


def test_concurrent_failures_do_not_lose_counts():
    """Real threads, real clock — the lock is the thing under test."""
    t = Throttle(limit=10_000, window_s=3600, cooloff_s=900, max_keys=16)
    errors: list[BaseException] = []

    def hammer():
        try:
            for _ in range(200):
                t.record_failure("shared")
        except BaseException as exc:                             # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    assert t.check("shared") is None            # 1600 < limit, so still open
    # 8 threads x 200 failures, none lost.
    assert len(t._hits["shared"]) == 1600


def test_limit_must_be_positive():
    with pytest.raises(ValueError):
        Throttle(limit=0)
