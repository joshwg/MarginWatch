"""
Integration test: fetch AAPL price from Massive.com.

Skipped automatically when MASSIVE_API_KEY is not set so the suite remains
green in CI / on machines without the key.

Run manually:
    MASSIVE_API_KEY=<your_key> venv/bin/python -m pytest tests/test_massive_aapl.py -v
"""

import os
import sys

import pytest

# Make option_lib importable from its sibling directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'option_lib'))

massive_key = os.environ.get("MASSIVE_API_KEY", "")
skip_no_key = pytest.mark.skipif(
    not massive_key,
    reason="MASSIVE_API_KEY is not set — skipping Massive integration test",
)


@skip_no_key
def test_aapl_stock_price():
    """get_stock_info returns a plausible AAPL price from Massive."""
    from option_lib import massive_data

    info = massive_data.get_stock_info("AAPL")

    assert info.get("success"), f"get_stock_info returned failure: {info}"

    price = info.get("current_price")
    assert price is not None, "current_price is None"
    assert isinstance(price, (int, float)), f"current_price is not numeric: {price!r}"
    assert 1.0 <= price <= 1_000_000, f"current_price {price} is implausible"

    # Confirm the data actually came from Massive (not Yahoo fallback).
    source = info.get("_source", "")
    assert source == "massive", (
        f"Expected _source='massive', got {source!r} — "
        "check that MASSIVE_API_KEY is valid and the API is reachable"
    )

    print(f"\nAAPL price from Massive: ${price:,.2f}  (source: {source})")


@skip_no_key
def test_aapl_stock_price_via_provider():
    """MassiveDataProvider.get_stock_info returns the same plausible price."""
    from option_lib.data_provider import MassiveDataProvider

    provider = MassiveDataProvider()
    info = provider.get_stock_info("AAPL")

    assert info.get("success"), f"MassiveDataProvider.get_stock_info failed: {info}"

    price = info.get("current_price")
    assert price is not None
    assert 1.0 <= price <= 1_000_000, f"Price {price} is out of plausible range"

    print(f"\nAAPL price via MassiveDataProvider: ${price:,.2f}")
