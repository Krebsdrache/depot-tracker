"""Tests für Wertverteilungs-Kreisdiagramm."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import app  # noqa: E402


class TestPortfolioPie(unittest.TestCase):
    def test_groups_coins_below_one_percent(self):
        chart_df = pd.DataFrame(
            [
                {"Coin": "BTC", "Aktueller Wert (EUR)": 900.0, "Anteil %": 90.0},
                {"Coin": "ETH", "Aktueller Wert (EUR)": 50.0, "Anteil %": 5.0},
                {"Coin": "XRP", "Aktueller Wert (EUR)": 30.0, "Anteil %": 3.0},
                {"Coin": "ADA", "Aktueller Wert (EUR)": 10.0, "Anteil %": 1.0},
                {"Coin": "VET", "Aktueller Wert (EUR)": 5.0, "Anteil %": 0.5},
                {"Coin": "XLM", "Aktueller Wert (EUR)": 5.0, "Anteil %": 0.5},
            ]
        )
        pie_df = app._portfolio_pie_dataframe(chart_df)
        self.assertEqual(len(pie_df), 5)
        other = pie_df.loc[pie_df["Coin"] == "Sonstige (< 1 %)"].iloc[0]
        self.assertAlmostEqual(float(other["Aktueller Wert (EUR)"]), 10.0)
        self.assertAlmostEqual(float(other["Anteil %"]), 1.0)
        self.assertIn("ADA", pie_df["Coin"].tolist())
        self.assertEqual(pie_df.loc[pie_df["Coin"] == "BTC", "PieLabel"].iloc[0], "BTC")
        self.assertEqual(pie_df.loc[pie_df["Coin"] == "XRP", "PieLabel"].iloc[0], "XRP")
        self.assertEqual(pie_df.loc[pie_df["Coin"] == "ETH", "PieLabel"].iloc[0], "ETH")
        self.assertEqual(pie_df.loc[pie_df["Coin"] == "ADA", "PieLabel"].iloc[0], "")
        self.assertEqual(
            pie_df.loc[pie_df["Coin"] == "Sonstige (< 1 %)", "PieLabel"].iloc[0],
            "",
        )

    def test_pie_segment_label_min_three_percent(self):
        self.assertEqual(app._pie_segment_label("BTC", 2.9), "")
        self.assertEqual(app._pie_segment_label("BTC", 3.0), "BTC")
        self.assertEqual(app._pie_segment_label("Sonstige (< 1 %)", 4.0), "Sonst.")

    def test_coin_pie_label(self):
        self.assertEqual(app._coin_pie_label("btc"), "BTC")
        self.assertEqual(app._coin_pie_label("Sonstige (< 1 %)"), "Sonst.")


if __name__ == "__main__":
    unittest.main()
