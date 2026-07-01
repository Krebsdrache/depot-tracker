"""Tests für Live-EUR-Preise aus Binance-Tickern."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from binance_data import (  # noqa: E402
    _asset_price_in_eur,
    _portfolio_total_eur,
    _position_from_balance,
)


class TestAssetPriceInEur(unittest.TestCase):
    def test_usdt_via_inverse_pair(self):
        tickers = {"EURUSDT": 1.14}
        price = _asset_price_in_eur("USDT", tickers)
        self.assertIsNotNone(price)
        self.assertAlmostEqual(price, 1 / 1.14)

    def test_usd_via_usdc_peg(self):
        tickers = {"EURUSDC": 1.14, "USDCUSD": 1.0}
        price = _asset_price_in_eur("USD", tickers)
        self.assertIsNotNone(price)
        self.assertAlmostEqual(price, 1 / 1.14)

    def test_inverse_eur_pair(self):
        tickers = {"EURBNB": 520.0}
        price = _asset_price_in_eur("BNB", tickers)
        self.assertAlmostEqual(price, 1 / 520.0)

    def test_portfolio_total_sums_position_values(self):
        tickers = {"BTCEUR": 50000.0}
        positions = [
            _position_from_balance("BTC", 0.1, tickers, avg_entry=40000.0),
            _position_from_balance("EUR", 100.0, tickers, avg_entry=None),
        ]
        self.assertAlmostEqual(_portfolio_total_eur(positions), 5100.0)


if __name__ == "__main__":
    unittest.main()
