"""Fixtures for the reconciliation test suite.

Import discipline here is load-bearing: this module must stay importable in an
environment with NO pandas, because the target state drops pandas from the
runtime deps and the polars suite still has to collect. So the pandas-backed
frame builders in helpers.py are imported lazily, inside the one fixture that
needs them, and RecordingLog lives in its own stdlib-only module.

Helpers are in tests/helpers.py rather than here because importing helpers
*from* conftest collides as soon as a subdirectory has its own conftest.
"""

from __future__ import annotations

import pytest

from recording_log import RecordingLog


def pytest_configure(config) -> None:
    """Arm the opt-in Excel read cache, and say so loudly if it is armed.

    `tests/io_cache.py` explains why it is off by default. It is announced through
    the terminal header rather than a log line because a run whose reads were
    served from a cache is a WEAKER run, and "did the cache do this?" must never be
    a question somebody has to think to ask.
    """
    from io_cache import CACHE_ROOT, install

    if install():
        config.stash.setdefault("recon_io_cache", True)
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        if reporter is not None:                                # pragma: no branch
            reporter.write_line(
                f"RECON_TEST_IO_CACHE=1 — Excel decodes are served from "
                f"{CACHE_ROOT}, keyed on file content. Reads are NOT being "
                f"re-executed. Unset it before trusting a timing or a golden claim.",
                yellow=True, bold=True)


@pytest.fixture
def log() -> RecordingLog:
    return RecordingLog()


@pytest.fixture
def settings() -> dict:
    """Minimal settings matching config/settings.yaml's shape for the keys the
    units under test actually read."""
    return {
        "vat_factors": {"default": 1.08},
        "tolerances": {
            # `exact_check_vnd` and `split_rounding_vnd` were here until
            # 2026-08-18. Nothing read them in the pipeline either, so seeding them
            # made this fixture claim a shape the contract no longer has.
            # The team's own TikTok tolerances.
            "tiktok": {
                "pv_sum_vnd": 12000,
                "xuat_hd_vnd": 2000,
                "pv_xuat_hd_vnd": 1000,
            },
        },
    }


@pytest.fixture
def sku_level():
    from helpers import make_sku_level  # pandas-backed; imported on demand
    return make_sku_level()
