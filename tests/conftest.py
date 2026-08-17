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
            "exact_check_vnd": 1,
            "split_rounding_vnd": 10000,
            # The team's own TikTok tolerances (settings.yaml:49-63).
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
