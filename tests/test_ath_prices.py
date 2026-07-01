"""Tests: ATH-Preise und Szenario-Zielkurse."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ath_prices import (  # noqa: E402
    _cache_entry_valid,
    ath_changes_pct_for_positions,
    change_pct_to_reach_target,
)
from binance_data import Position  # noqa: E402
from what_if import compute_price_scenario  # noqa: E402


def _pos(coin: str, qty: float, price: float) -> Position:
    value = qty * price
    return Position(
        coin=coin,
        quantity=qty,
        current_price_eur=price,
        current_value_eur=value,
        avg_entry_price_eur=price * 0.8,
        entry_known=True,
        profit_loss_eur=value - qty * price * 0.8,
        profit_loss_pct=25.0,
    )


class TestAthPrices(unittest.TestCase):
    def test_change_pct_to_reach_target(self):
        self.assertAlmostEqual(change_pct_to_reach_target(100.0, 150.0), 50.0)
        self.assertAlmostEqual(change_pct_to_reach_target(200.0, 100.0), -50.0)
        self.assertIsNone(change_pct_to_reach_target(None, 100.0))

    def test_ath_changes_for_positions(self):
        positions = [_pos("BTC", 1.0, 50000.0), _pos("ETH", 2.0, 2500.0)]
        changes = ath_changes_pct_for_positions(
            positions,
            {"BTC": 70000.0, "ETH": 2000.0},
        )
        self.assertAlmostEqual(changes["BTC"], 40.0)
        self.assertAlmostEqual(changes["ETH"], -20.0)

    def test_scenario_with_target_prices(self):
        positions = [_pos("BTC", 1.0, 100.0)]
        summary = compute_price_scenario(
            positions,
            coin_target_prices_eur={"BTC": 150.0},
        )
        self.assertAlmostEqual(summary.total_current_eur, 100.0)
        self.assertAlmostEqual(summary.total_scenario_eur, 150.0)
        self.assertAlmostEqual(summary.positions[0].price_change_pct, 50.0)

    def test_cache_entry_valid(self):
        today = __import__("datetime").date.today().isoformat()
        self.assertTrue(
            _cache_entry_valid({"day": today, "price_eur": 100.0}, refresh=False)
        )
        self.assertFalse(
            _cache_entry_valid({"day": today, "price_eur": 100.0}, refresh=True)
        )


if __name__ == "__main__":
    unittest.main()
