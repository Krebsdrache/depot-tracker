"""Tests für die Gesamt-Aggregation mehrerer Depots (Phase 3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.aggregate import combine_holdings, combine_snapshots  # noqa: E402
from core.model import AssetClass, DepotSnapshot, Holding, Instrument  # noqa: E402


def _holding(symbol, qty, price, value, entry=None, pl=None):
    return Holding(
        instrument=Instrument(symbol=symbol, asset_class=AssetClass.CRYPTO),
        quantity=qty,
        price_eur=price,
        value_eur=value,
        avg_entry_price_eur=entry,
        profit_loss_eur=pl,
    )


class TestCombineHoldings(unittest.TestCase):
    def test_same_instrument_is_summed(self):
        holdings = [
            _holding("BTC", 0.5, 50000.0, 25000.0, entry=40000.0, pl=5000.0),
            _holding("BTC", 0.5, 50000.0, 25000.0, entry=30000.0, pl=10000.0),
        ]
        combined = combine_holdings(holdings)
        self.assertEqual(len(combined), 1)
        btc = combined[0]
        self.assertEqual(btc.quantity, 1.0)
        self.assertEqual(btc.value_eur, 50000.0)
        # Mengen-gewichteter Einstand: (0.5*40000 + 0.5*30000) / 1.0 = 35000
        self.assertAlmostEqual(btc.avg_entry_price_eur, 35000.0)
        self.assertAlmostEqual(btc.profit_loss_eur, 15000.0)

    def test_different_instruments_sorted_by_value(self):
        holdings = [
            _holding("ETH", 2.0, 2000.0, 4000.0),
            _holding("BTC", 1.0, 50000.0, 50000.0),
        ]
        combined = combine_holdings(holdings)
        self.assertEqual([h.instrument.symbol for h in combined], ["BTC", "ETH"])


class TestCombineSnapshots(unittest.TestCase):
    def test_total_value_across_depots(self):
        snap_a = DepotSnapshot(
            depot_id="binance",
            provider_id="binance",
            holdings=[_holding("BTC", 1.0, 50000.0, 50000.0)],
            total_value_eur=50000.0,
        )
        snap_b = DepotSnapshot(
            depot_id="tr",
            provider_id="traderepublic",
            holdings=[
                _holding("BTC", 0.5, 50000.0, 25000.0),
                _holding("ETH", 3.0, 2000.0, 6000.0),
            ],
            total_value_eur=31000.0,
        )
        gesamt = combine_snapshots([snap_a, snap_b])
        self.assertEqual(gesamt.depot_id, "gesamt")
        self.assertAlmostEqual(gesamt.total_value_eur, 81000.0)
        btc = next(h for h in gesamt.holdings if h.instrument.symbol == "BTC")
        self.assertEqual(btc.quantity, 1.5)
        self.assertEqual(btc.value_eur, 75000.0)

    def test_empty_snapshots(self):
        gesamt = combine_snapshots([])
        self.assertEqual(gesamt.total_value_eur, 0.0)
        self.assertEqual(gesamt.holdings, [])
        self.assertTrue(gesamt.ok)


if __name__ == "__main__":
    unittest.main()
