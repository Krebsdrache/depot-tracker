"""Tests für Kapitalfluss (Ein- und Auszahlungen)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import inflows  # noqa: E402


class TestCapitalFlowSeries(unittest.TestCase):
    def test_net_series_goes_up_and_down(self):
        df = pd.DataFrame(
            [
                ("fiat_einzahlung", "EUR", "2024-01-01T10:00:00+00:00", 100.0, 100.0, "in"),
                ("fiat_auszahlung", "EUR", "2024-02-01T10:00:00+00:00", 30.0, 30.0, "out"),
                ("fiat_karte", "EUR→BTC", "2024-03-01T10:00:00+00:00", 50.0, 50.0, "in"),
            ],
            columns=["typ", "coin", "datum", "menge", "wert_eur", "richtung"],
        )
        with patch.object(inflows, "load_zufluesse_csv", return_value=df):
            series = inflows.capital_flow_timeseries()
            summary = inflows.compute_capital_flow_summary()

        self.assertAlmostEqual(summary.netto_eur, 120.0)
        self.assertAlmostEqual(summary.einzahlungen_eur, 150.0)
        self.assertAlmostEqual(summary.auszahlungen_eur, 30.0)
        self.assertEqual(len(series), 3)
        self.assertAlmostEqual(series.iloc[-1]["kapital_netto"], 120.0)
        self.assertAlmostEqual(series.iloc[0]["kapital_netto"], 100.0)

    def test_legacy_rows_without_richtung_column(self):
        df = pd.DataFrame(
            [
                ("fiat_einzahlung", "EUR", "2024-01-01T10:00:00+00:00", 200.0, 200.0),
                ("krypto_withdraw", "BTC", "2024-06-01T10:00:00+00:00", 0.01, 500.0),
            ],
            columns=["typ", "coin", "datum", "menge", "wert_eur"],
        )
        with patch.object(inflows, "load_zufluesse_csv", return_value=df):
            normalized = inflows._ensure_zufluss_columns(df)
            summary = inflows.compute_capital_flow_summary()

        self.assertEqual(normalized.loc[0, "richtung"], "in")
        self.assertEqual(normalized.loc[1, "richtung"], "out")
        self.assertAlmostEqual(summary.netto_eur, -300.0)

    def test_range_filter_keeps_baseline(self):
        tz = inflows.LOCAL_TZ
        series = pd.DataFrame(
            [
                (pd.Timestamp("2024-01-15", tz=tz), 100.0, 100.0, "Bank"),
                (pd.Timestamp("2024-05-15", tz=tz), 50.0, 150.0, "Karte"),
                (pd.Timestamp("2024-06-15", tz=tz), -20.0, 130.0, "Auszahlung"),
            ],
            columns=["zeit", "delta_eur", "kapital_netto", "typ_label"],
        )
        now = pd.Timestamp("2024-07-01", tz=tz)
        line_df, points_df, x_min, x_max = inflows.prepare_capital_flow_chart_data(
            series,
            "3m",
            now=now,
        )
        self.assertEqual(len(points_df), 2)
        self.assertAlmostEqual(float(line_df.iloc[0]["kapital_netto"]), 100.0)
        self.assertLess(x_min, points_df["zeit"].min())

    def test_donut_gesamt_uses_total_netto(self):
        tz = inflows.LOCAL_TZ
        series = pd.DataFrame(
            [
                (pd.Timestamp("2024-01-15", tz=tz), 9000.0, 9000.0),
                (pd.Timestamp("2024-06-15", tz=tz), -300.0, 8700.0),
            ],
            columns=["zeit", "delta_eur", "kapital_netto"],
        )
        donut = inflows.prepare_capital_flow_donut(series, "max", 8700.0)
        self.assertAlmostEqual(donut.center_eur, 8700.0)
        self.assertAlmostEqual(donut.period_ein_eur, 9000.0)
        self.assertAlmostEqual(donut.period_aus_eur, 300.0)
        self.assertEqual(len(donut.slices), 1)
        self.assertAlmostEqual(float(donut.slices.iloc[0]["value"]), 8700.0)
        self.assertTrue(bool(donut.slices.iloc[0]["hell"]))

    def test_donut_period_shows_share_of_total(self):
        tz = inflows.LOCAL_TZ
        series = pd.DataFrame(
            [
                (pd.Timestamp("2024-01-15", tz=tz), 8000.0, 8000.0),
                (pd.Timestamp("2024-06-20", tz=tz), 700.0, 8700.0),
            ],
            columns=["zeit", "delta_eur", "kapital_netto"],
        )
        now = pd.Timestamp("2024-07-01", tz=tz)
        donut = inflows.prepare_capital_flow_donut(series, "1m", 8700.0, now=now)
        self.assertAlmostEqual(donut.center_eur, 700.0)
        self.assertIn("8.0 %", donut.center_subtitle)
        self.assertEqual(len(donut.slices), 2)
        zeitraum = donut.slices.loc[donut.slices["Kategorie"] == "Zeitraum"].iloc[0]
        rest = donut.slices.loc[donut.slices["Kategorie"] == "Rest"].iloc[0]
        self.assertAlmostEqual(float(zeitraum["value"]), 700.0)
        self.assertAlmostEqual(float(rest["value"]), 8000.0)
        self.assertTrue(bool(zeitraum["hell"]))
        self.assertFalse(bool(rest["hell"]))

    def test_altair_naive_local_strips_zoneinfo(self):
        ts = pd.Timestamp("2024-06-01 12:00", tz=inflows.LOCAL_TZ)
        naive = inflows.to_altair_naive_local(ts)
        self.assertIsNone(naive.tzinfo)


if __name__ == "__main__":
    unittest.main()
