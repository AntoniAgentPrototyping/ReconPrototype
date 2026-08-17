"""Fixtures for the workbook golden gate.

Data-dependent tests skip when goldens are absent, so the suite stays green on
a machine without client data while still running the differ self-test and the
manifest PII guards. Point RECON_GOLDENS at a goldens tree to enable them.
"""

from __future__ import annotations

import pytest

from goldens import discover_windows, load_manifest


@pytest.fixture(scope="session")
def committed_manifest():
    return load_manifest()


def pytest_generate_tests(metafunc):
    """Parametrize per window, so a failure names the window rather than the
    whole suite."""
    if "golden_window" not in metafunc.fixturenames:
        return
    windows = discover_windows()
    if windows:
        metafunc.parametrize("golden_window", list(windows.items()), ids=list(windows))
    else:
        metafunc.parametrize("golden_window", [None], ids=["no-goldens-present"])
