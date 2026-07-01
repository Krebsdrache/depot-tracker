"""Tests für historische Depot-Entwicklung."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import portfolio_history as ph  # noqa: E402


class TestPortfolioHistory(unittest.TestCase):
    def test_collect_balance_events_from_trades_and_flows(self):
        purchases = pd.DataFrame(
            [
                (1, "BTC", "2024-01-10T12:00:00+00:00", 0.5, 100.0, 0, "kline", 0.0, "EUR", 0.0, 0.0, 0, "eur"),
            ],
            columns=[
                "trade_id",
                "coin",
                "datum",
                "menge",
                "kaufpreis_eur",
                "preis_geschaetzt",
                "preis_quelle",
                "commission",
                "commission_asset",
                "gebuehr_eur",
                "gebuehr_kurs_eur",
                "gebuehr_geschaetzt",
                "gebuehr_quelle",
            ],
        )
        sells = pd.DataFrame(columns=purchases.columns).rename(
            columns={"kaufpreis_eur": "verkaufspreis_eur"}
        )
        flows = pd.DataFrame(
            [
                ("fiat_einzahlung", "EUR", "2024-01-01T10:00:00+00:00", 100.0, 100.0, "in"),
            ],
            columns=["typ", "coin", "datum", "menge", "wert_eur", "richtung"],
        )
        with patch.object(ph, "load_purchases_csv", return_value=purchases), patch.object(
            ph, "load_sells_csv", return_value=sells
        ), patch.object(ph, "load_zufluesse_csv", return_value=flows):
            events = ph.collect_balance_events()

        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0][1]["EUR"], 100.0)
        self.assertAlmostEqual(events[1][1]["BTC"], 0.5)
        self.assertAlmostEqual(events[1][1]["EUR"], -50.0)

    def test_portfolio_timeseries_with_fixed_prices(self):
        purchases = pd.DataFrame(
            [
                (1, "BTC", "2024-01-10T12:00:00+00:00", 1.0, 100.0, 0, "kline", 0.0, "EUR", 0.0, 0.0, 0, "eur"),
            ],
            columns=[
                "trade_id",
                "coin",
                "datum",
                "menge",
                "kaufpreis_eur",
                "preis_geschaetzt",
                "preis_quelle",
                "commission",
                "commission_asset",
                "gebuehr_eur",
                "gebuehr_kurs_eur",
                "gebuehr_geschaetzt",
                "gebuehr_quelle",
            ],
        )
        sells = pd.DataFrame(columns=purchases.columns).rename(
            columns={"kaufpreis_eur": "verkaufspreis_eur"}
        )
        flows = pd.DataFrame(
            [
                ("fiat_einzahlung", "EUR", "2024-01-01T10:00:00+00:00", 200.0, 200.0, "in"),
            ],
            columns=["typ", "coin", "datum", "menge", "wert_eur", "richtung"],
        )

        def fixed_price(asset: str, day: date) -> float | None:
            if asset == "BTC":
                return 120.0
            if asset == "EUR":
                return 1.0
            return None

        now = pd.Timestamp("2024-01-15", tz=ph.LOCAL_TZ)
        with patch.object(ph, "load_purchases_csv", return_value=purchases), patch.object(
            ph, "load_sells_csv", return_value=sells
        ), patch.object(ph, "load_zufluesse_csv", return_value=flows):
            series = ph.build_portfolio_timeseries(price_lookup=fixed_price, now=now)

        self.assertFalse(series.empty)
        last = series.iloc[-1]
        # 200 EUR cash - 100 EUR buy cost + 1 BTC * 120 EUR
        self.assertAlmostEqual(float(last["depotwert_eur"]), 220.0)

    def test_prepare_chart_adds_capital_and_performance(self):
        tz = ph.LOCAL_TZ
        portfolio_df = pd.DataFrame(
            [
                (pd.Timestamp("2024-05-01", tz=tz), 1000.0, False),
                (pd.Timestamp("2024-06-01", tz=tz), 1200.0, False),
            ],
            columns=["zeit", "depotwert_eur", "missing_price"],
        )
        capital_series = pd.DataFrame(
            [
                (pd.Timestamp("2024-04-01", tz=tz), 100.0, 800.0),
                (pd.Timestamp("2024-05-15", tz=tz), 50.0, 900.0),
            ],
            columns=["zeit", "delta_eur", "kapital_netto"],
        )
        now = pd.Timestamp("2024-07-01", tz=tz)
        view = ph.prepare_portfolio_chart_data(portfolio_df, capital_series, "3m", now=now)

        self.assertTrue(view.has_capital)
        self.assertEqual(len(view.line_df), 2)
        self.assertAlmostEqual(float(view.line_df.iloc[0]["kapital_netto"]), 800.0)
        self.assertAlmostEqual(float(view.line_df.iloc[1]["kapital_netto"]), 900.0)
        self.assertAlmostEqual(float(view.line_df.iloc[1]["performance_eur"]), 300.0)
        self.assertTrue(bool(view.line_df.iloc[1]["gewinn"]))


if __name__ == "__main__":
    unittest.main()
