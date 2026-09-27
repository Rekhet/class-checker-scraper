"""Shared test setup."""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(autouse=True)
def _isolated_trend_cache(tmp_path, monkeypatch):
    """export_json checkpoints trend state under data/trend_cache; tests must
    neither write there nor read a checkpoint another test left behind."""
    for name in ("scraper.export_json", "export_json"):
        module = sys.modules.get(name)
        if module is not None:
            monkeypatch.setattr(module, "TREND_CACHE", tmp_path / "trend_cache")
    yield
