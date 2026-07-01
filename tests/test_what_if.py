"""Tests: Was-wäre-wenn-Szenarien."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from binance_data import Position, PortfolioResult  # noqa: E402
from what_if import compute_price_scenario, scenario_from_portfolio  # noqa: E402


def _pos(
    coin: str,
    qty: float,
    price: float,
    entry: float | None = 10.0,
) -> Position:
    value = qty * price
    entry_known = entry is not None
    pl = value - qty * entry if entry_known and entry else None
    pl_pct = (pl / (qty * entry) * 100) if pl is not None and entry else None
    return Position(
        coin=coin,
        quantity=qty,
        current_price_eur=price,
        current_value_eur=value,
        avg_entry_price_eur=entry,
        entry_known=entry_known,
        profit_loss_eur=pl,
        profit_loss_pct=pl_pct,
    )


class TestWhatIf(unittest.TestCase):
    def test_global_plus_10_prozent(self):
        positions = [_pos("BTC", 1.0, 100.0, 80.0), _pos("ETH", 2.0, 50.0, 40.0)]
        summary = compute_price_scenario(positions, global_change_pct=10.0)
        self.assertAlmostEqual(summary.total_current_eur, 200.0)
        self.assertAlmostEqual(summary.total_scenario_eur, 220.0)
        self.assertAlmostEqual(summary.delta_eur, 20.0)
        self.assertAlmostEqual(summary.delta_pct, 10.0)

    def test_coin_override(self):
        positions = [_pos("BTC", 1.0, 100.0), _pos("ETH", 1.0, 100.0)]
        summary = compute_price_scenario(
            positions,
            global_change_pct=0.0,
            coin_changes_pct={"BTC": -20.0},
        )
        self.assertAlmostEqual(summary.total_scenario_eur, 180.0)

    def test_portfolio_wrapper(self):
        result = PortfolioResult(
            ok=True,
            message="ok",
            positions=[_pos("SOL", 10.0, 20.0, 15.0)],
            total_value_eur=200.0,
        )
        summary = scenario_from_portfolio(result, global_change_pct=-50.0)
        self.assertAlmostEqual(summary.total_scenario_eur, 100.0)

    def test_target_price_override(self):
        positions = [_pos("BTC", 1.0, 100.0), _pos("ETH", 1.0, 50.0)]
        summary = compute_price_scenario(
            positions,
            global_change_pct=10.0,
            coin_target_prices_eur={"BTC": 200.0},
        )
        self.assertAlmostEqual(summary.total_scenario_eur, 255.0)

    def test_scale_target_prices(self):
        from what_if import (
            ATH_LEVEL_ATH,
            ATH_LEVEL_MAX,
            ath_ceiling_eur,
            compute_ath_target_prices,
            scale_target_prices_eur,
            target_from_ath_level,
        )

        scaled = scale_target_prices_eur({"BTC": 100.0, "ETH": 50.0}, 50.0)
        self.assertAlmostEqual(scaled["BTC"], 150.0)
        self.assertAlmostEqual(scaled["ETH"], 75.0)

        self.assertAlmostEqual(target_from_ath_level(100.0, 200.0, 0.0), 100.0)
        self.assertAlmostEqual(target_from_ath_level(100.0, 200.0, ATH_LEVEL_ATH), 200.0)
        self.assertAlmostEqual(target_from_ath_level(100.0, 200.0, 50.0), 150.0)
        self.assertAlmostEqual(target_from_ath_level(100.0, 200.0, ATH_LEVEL_MAX), 800.0)
        self.assertAlmostEqual(ath_ceiling_eur(100.0), 400.0)

        targets = compute_ath_target_prices(
            [_pos("BTC", 1.0, 100.0), _pos("ETH", 1.0, 50.0)],
            {"BTC": 200.0, "ETH": 100.0},
            coin_level_pct={"BTC": ATH_LEVEL_ATH, "ETH": 0.0},
        )
        self.assertAlmostEqual(targets["BTC"], 200.0)
        self.assertAlmostEqual(targets["ETH"], 50.0)


if __name__ == "__main__":
    unittest.main()
