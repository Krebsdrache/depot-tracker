"""Tests für Gebühren-Übersicht und CSV-Backfill."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from binance_data import _backfill_dataframe_from_trades, average_entry_from_local  # noqa: E402
from gebuehren import (  # noqa: E402
    gebuehren_liste_dataframe,
    pruefe_gebuehren_plausibilitaet,
    summarize_gebuehren,
)


class TestGebuehren(unittest.TestCase):
    def test_summarize_and_list(self):
        purchases = pd.DataFrame(
            [
                (1, "BTC", "2024-01-01T00:00:00+00:00", 1.0, 100.0, 0, "eur", 0.0, "EUR", 1.5, 1.0, 0, "eur"),
                (2, "ETH", "2024-02-01T00:00:00+00:00", 2.0, 50.0, 0, "eur", float("nan"), "", float("nan"), float("nan"), float("nan"), ""),
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
        sells = pd.DataFrame(
            [
                (3, "BTC", "2024-06-01T00:00:00+00:00", 0.5, 120.0, 0, "kline", 0.0001, "BNB", 0.8, 8000.0, 0, "kline"),
            ],
            columns=[
                "trade_id",
                "coin",
                "datum",
                "menge",
                "verkaufspreis_eur",
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

        summary = summarize_gebuehren(purchases, sells)
        self.assertAlmostEqual(summary.gesamt_eur, 2.3)
        self.assertAlmostEqual(summary.exakt_eur, 2.3)
        self.assertAlmostEqual(summary.exakt_anteil_pct, 100.0)
        self.assertEqual(summary.fehlend_anzahl, 1)

        fee_list = gebuehren_liste_dataframe(purchases, sells)
        self.assertEqual(len(fee_list), 3)
        self.assertIn("EUR-Kurs am Trade", fee_list.columns)

    def test_plausibilitaet(self):
        purchases = pd.DataFrame(
            [
                (1, "BTC", "2024-01-01T00:00:00+00:00", 1.0, 1000.0, 0, "kline", 0.0, "EUR", 1.0, 1.0, 0, "eur"),
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
        plausi = pruefe_gebuehren_plausibilitaet(purchases, pd.DataFrame())
        self.assertTrue(plausi.ok)
        self.assertAlmostEqual(plausi.median_fee_pct, 0.1)


class TestBackfillMatching(unittest.TestCase):
    def test_backfill_by_trade_id(self):
        sells = pd.DataFrame(
            [
                (999, "BTC", "2024-06-01T00:00:00+00:00", 0.1),
            ],
            columns=["trade_id", "coin", "datum", "menge"],
        )
        api_trades = [
            {
                "trade_id": 999,
                "coin": "BTC",
                "datum": "2024-06-01T00:00:00+00:00",
                "zeit": pd.Timestamp("2024-06-01T00:00:00+00:00").to_pydatetime(),
                "menge": 0.1,
                "verkaufspreis_eur": 50000.0,
                "preis_geschaetzt": 0,
                "preis_quelle": "kline",
                "commission": 0.0001,
                "commission_asset": "BNB",
                "gebuehr_eur": 2.5,
                "gebuehr_kurs_eur": 25000.0,
                "gebuehr_geschaetzt": 0,
                "gebuehr_quelle": "kline",
            },
        ]

        filled, updated = _backfill_dataframe_from_trades(
            sells,
            api_trades,
            {
                "verkaufspreis_eur": "verkaufspreis_eur",
                "gebuehr_eur": "gebuehr_eur",
                "gebuehr_kurs_eur": "gebuehr_kurs_eur",
            },
        )

        self.assertEqual(updated, 1)
        self.assertAlmostEqual(filled.loc[0, "verkaufspreis_eur"], 50000.0)


class TestAverageEntryWithFees(unittest.TestCase):
    def test_buy_fee_increases_einstand(self):
        purchases = pd.DataFrame(
            [
                ("BTC", "2024-01-01T00:00:00+00:00", 1.0, 100.0, 0, "kline", 0.0, "EUR", 2.0, 1.0, 0, "eur"),
            ],
            columns=[
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
        avg = average_entry_from_local("BTC", purchases, pd.DataFrame())
        self.assertAlmostEqual(avg, 102.0)


if __name__ == "__main__":
    unittest.main()
