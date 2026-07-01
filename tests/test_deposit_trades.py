"""Tests: Krypto-Deposits als FIFO-Käufe."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from deposit_trades import (  # noqa: E402
    CARD_PREIS_QUELLE,
    DEPOSIT_PREIS_QUELLE,
    crypto_deposits_as_purchases,
    crypto_withdrawals_as_sells,
    fiat_card_as_purchases,
    merge_purchases_with_deposits,
)
from fifo import offene_fifo_lots  # noqa: E402
from inflows import TYP_CRYPTO, TYP_CRYPTO_AUS, TYP_FIAT_KARTE  # noqa: E402
from steuer import berechne_haltefristen  # noqa: E402


def _zufluesse(*rows: tuple) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["typ", "coin", "datum", "menge", "wert_eur", "richtung"],
    )


class TestDepositTrades(unittest.TestCase):
    def test_deposit_wird_kauf_mit_einstand(self):
        flows = _zufluesse(
            (TYP_CRYPTO, "BTC", "2024-03-01T12:00:00+00:00", 0.5, 25_000.0, "in"),
        )
        purchases = crypto_deposits_as_purchases(flows)
        self.assertEqual(len(purchases), 1)
        self.assertAlmostEqual(float(purchases.iloc[0]["kaufpreis_eur"]), 50_000.0)
        self.assertEqual(str(purchases.iloc[0]["preis_quelle"]), DEPOSIT_PREIS_QUELLE)

    def test_deposit_ohne_wert_wird_uebersprungen(self):
        flows = _zufluesse(
            (TYP_CRYPTO, "ETH", "2024-03-01T12:00:00+00:00", 1.0, 0.0, "in"),
        )
        self.assertTrue(crypto_deposits_as_purchases(flows).empty)

    def test_merge_liefert_offene_lots(self):
        flows = _zufluesse(
            (TYP_CRYPTO, "BTC", "2024-01-01T10:00:00+00:00", 1.0, 40_000.0, "in"),
        )
        merged = crypto_deposits_as_purchases(flows)
        sells = pd.DataFrame(columns=["coin", "datum", "menge"])
        lots = offene_fifo_lots(merged, sells)
        self.assertEqual(len(lots), 1)
        self.assertAlmostEqual(lots[0]["menge"], 1.0)

    def test_withdrawal_wird_verkauf(self):
        flows = _zufluesse(
            (TYP_CRYPTO_AUS, "ETH", "2024-06-01T10:00:00+00:00", 2.0, 600.0, "out"),
        )
        sells = crypto_withdrawals_as_sells(flows)
        self.assertEqual(len(sells), 1)
        self.assertAlmostEqual(float(sells.iloc[0]["verkaufspreis_eur"]), 300.0)

    def test_haltefrist_erkennt_deposit(self):
        flows = _zufluesse(
            (TYP_CRYPTO, "BTC", "2023-06-01T10:00:00+00:00", 0.2, 5_000.0, "in"),
        )
        purchases = crypto_deposits_as_purchases(flows)
        tranches = berechne_haltefristen(purchases, {})
        self.assertEqual(len(tranches), 1)
        self.assertAlmostEqual(tranches[0].menge, 0.2)

    def test_merge_idempotent(self):
        flows = _zufluesse(
            (TYP_CRYPTO, "ETH", "2024-02-01T10:00:00+00:00", 1.0, 2500.0, "in"),
        )
        base = pd.DataFrame(
            [
                {
                    "trade_id": 1,
                    "coin": "BTC",
                    "datum": "2024-01-01T00:00:00+00:00",
                    "menge": 0.1,
                    "kaufpreis_eur": 3000.0,
                    "preis_geschaetzt": 0,
                    "preis_quelle": "kline",
                    "commission": 0.0,
                    "commission_asset": "",
                    "gebuehr_eur": 0.0,
                    "gebuehr_kurs_eur": float("nan"),
                    "gebuehr_geschaetzt": 0,
                    "gebuehr_quelle": "",
                }
            ]
        )
        with patch("deposit_trades.load_zufluesse_csv", return_value=flows):
            once = merge_purchases_with_deposits(base.copy())
            twice = merge_purchases_with_deposits(once.copy())
        self.assertEqual(len(once), 2)
        self.assertEqual(len(twice), 2)

    @patch(
        "deposit_trades._lookup_coin_eur_offline",
        return_value=(2.5, "tageskurs", True),
    )
    def test_kartenkauf_wird_synthetischer_kauf(self, _mock_price):
        flows = _zufluesse(
            (TYP_FIAT_KARTE, "EUR→ARKM", "2025-01-03T20:08:34+00:00", 30.0, 30.0, "in"),
        )
        purchases = fiat_card_as_purchases(flows, spot_purchases=pd.DataFrame())
        self.assertEqual(len(purchases), 1)
        self.assertEqual(str(purchases.iloc[0]["coin"]), "ARKM")
        self.assertAlmostEqual(float(purchases.iloc[0]["menge"]), 12.0)
        self.assertAlmostEqual(float(purchases.iloc[0]["kaufpreis_eur"]), 2.5)
        self.assertEqual(str(purchases.iloc[0]["preis_quelle"]), CARD_PREIS_QUELLE)

    @patch(
        "deposit_trades._lookup_coin_eur_offline",
        return_value=(2.5, "tageskurs", True),
    )
    def test_kartenkauf_uebersprungen_wenn_spot_kauf_existiert(self, _mock_price):
        flows = _zufluesse(
            (TYP_FIAT_KARTE, "EUR→ARKM", "2025-01-03T20:08:34+00:00", 30.0, 30.0, "in"),
        )
        spot = pd.DataFrame(
            [
                {
                    "trade_id": 207655,
                    "coin": "ARKM",
                    "datum": "2025-01-03T20:08:34+00:00",
                    "menge": 12.0,
                    "kaufpreis_eur": 2.5,
                    "preis_geschaetzt": 0,
                    "preis_quelle": "kline",
                    "commission": 0.0,
                    "commission_asset": "",
                    "gebuehr_eur": 0.0,
                    "gebuehr_kurs_eur": float("nan"),
                    "gebuehr_geschaetzt": 0,
                    "gebuehr_quelle": "",
                }
            ]
        )
        self.assertTrue(fiat_card_as_purchases(flows, spot_purchases=spot).empty)

    def test_kartenkauf_ueberspringt_stablecoins(self):
        flows = _zufluesse(
            (TYP_FIAT_KARTE, "EUR→USDC", "2025-01-08T20:52:44+00:00", 100.0, 100.0, "in"),
        )
        self.assertTrue(fiat_card_as_purchases(flows, spot_purchases=pd.DataFrame()).empty)

    def test_kartenkauf_mit_crypto_menge_aus_api(self):
        flows = _zufluesse(
            (TYP_FIAT_KARTE, "EUR→SEI", "2025-01-03T20:05:59+00:00", 65.5, 30.0, "in"),
        )
        purchases = fiat_card_as_purchases(flows, spot_purchases=pd.DataFrame())
        self.assertEqual(len(purchases), 1)
        self.assertAlmostEqual(float(purchases.iloc[0]["menge"]), 65.5)
        self.assertAlmostEqual(float(purchases.iloc[0]["kaufpreis_eur"]), 30.0 / 65.5)

    def test_merge_fuegt_kartenkaeufe_hinzu(self):
        flows = _zufluesse(
            (TYP_FIAT_KARTE, "EUR→ARKM", "2025-01-08T20:52:44+00:00", 12.5, 100.0, "in"),
        )
        with patch("deposit_trades.crypto_deposits_as_purchases", return_value=pd.DataFrame()):
            with patch("deposit_trades.load_zufluesse_csv", return_value=flows):
                merged = merge_purchases_with_deposits(pd.DataFrame())
        self.assertEqual(len(merged), 1)
        self.assertEqual(str(merged.iloc[0]["coin"]), "ARKM")
        self.assertAlmostEqual(float(merged.iloc[0]["menge"]), 12.5)
        self.assertAlmostEqual(float(merged.iloc[0]["kaufpreis_eur"]), 8.0)


if __name__ == "__main__":
    unittest.main()
