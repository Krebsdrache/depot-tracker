"""Tests für historische Gebühren-Umrechnung."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fee_rates import (  # noqa: E402
    HistoricalRateCache,
    asset_to_eur_at_time,
    trade_fee_eur_at_time,
    trade_price_eur_at_time,
)


class TestHistoricalFees(unittest.TestCase):
    def test_eur_fee_is_exact(self):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            cache = HistoricalRateCache(Path(tmp) / "cache.json")
            fee, estimated, source, commission, asset, kurs = trade_fee_eur_at_time(
                {
                    "time": 1700000000000,
                    "commission": "0.5",
                    "commissionAsset": "EUR",
                },
                client,
                {},
                cache,
                set(),
            )
        self.assertAlmostEqual(fee, 0.5)
        self.assertFalse(estimated)
        self.assertEqual(source, "eur")
        self.assertAlmostEqual(kurs, 1.0)
        self.assertEqual(asset, "EUR")
        client.get_klines.assert_not_called()

    def test_kline_fee_uses_trade_time(self):
        client = MagicMock()
        client.get_klines.return_value = [[1700000000000, "1", "2", "1.5", "100.0", "0"]]

        with tempfile.TemporaryDirectory() as tmp:
            cache = HistoricalRateCache(Path(tmp) / "cache.json")
            fee, estimated, source, commission, asset, kurs = trade_fee_eur_at_time(
                {
                    "time": 1700000000000,
                    "commission": "0.01",
                    "commissionAsset": "BNB",
                },
                client,
                {"BNBEUR": 999.0},
                cache,
                {"BNBEUR"},
            )

        self.assertAlmostEqual(fee, 1.0)
        self.assertAlmostEqual(kurs, 100.0)
        self.assertFalse(estimated)
        self.assertEqual(source, "kline")

    def test_trade_price_eur_pair_kline(self):
        client = MagicMock()
        client.get_klines.return_value = [[1700000000000, "1", "2", "1.5", "42000.0", "0"]]

        with tempfile.TemporaryDirectory() as tmp:
            cache = HistoricalRateCache(Path(tmp) / "cache.json")
            price, estimated, source = trade_price_eur_at_time(
                {"time": 1700000000000, "price": "41000", "symbol": "BTCEUR"},
                {"quoteAsset": "USDT"},
                client,
                {},
                cache,
                {"BTCEUR"},
            )

        self.assertAlmostEqual(price, 42000.0)
        self.assertFalse(estimated)
        self.assertEqual(source, "kline")

    def test_trade_price_eur_quote_uses_trade_price(self):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            cache = HistoricalRateCache(Path(tmp) / "cache.json")
            price, estimated, source = trade_price_eur_at_time(
                {"time": 1700000000000, "price": "41000", "symbol": "BTCEUR"},
                {"quoteAsset": "EUR"},
                client,
                {},
                cache,
                {"BTCEUR"},
            )

        self.assertAlmostEqual(price, 41000.0)
        self.assertFalse(estimated)
        self.assertEqual(source, "eur")
        client.get_klines.assert_not_called()

    def test_cache_avoids_second_kline_call(self):
        client = MagicMock()
        client.get_klines.return_value = [[1700000000000, "1", "2", "1.5", "50.0", "0"]]

        with tempfile.TemporaryDirectory() as tmp:
            cache = HistoricalRateCache(Path(tmp) / "cache.json")
            valid = {"BNBEUR"}
            rate1, _ = asset_to_eur_at_time(client, "BNB", 1700000000000, {}, cache, valid)
            rate2, _ = asset_to_eur_at_time(client, "BNB", 1700000000500, {}, cache, valid)

        self.assertAlmostEqual(rate1, 50.0)
        self.assertAlmostEqual(rate2, 50.0)
        self.assertEqual(client.get_klines.call_count, 1)


if __name__ == "__main__":
    unittest.main()
