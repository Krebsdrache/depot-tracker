"""Tests für FIFO und Haltefrist in steuer.py (ohne Binance-API)."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from steuer import (  # noqa: E402
    _apply_fifo,
    berechne_frist_kalender,
    berechne_haltefristen,
    haltefrist_status_text,
    pruefe_mengen_plausibilitaet,
    plausibilitaet_pro_coin,
    steuerfrei_ab,
)


def _purchases(*rows: tuple[str, str, float, float]) -> pd.DataFrame:
    return pd.DataFrame(
        list(rows),
        columns=["coin", "datum", "menge", "kaufpreis_eur"],
    )


def _sells(coin: str, *entries: tuple[str, float]) -> dict[str, list[dict]]:
    return {
        coin: [
            {
                "menge": menge,
                "zeit": datetime.fromisoformat(datum).replace(tzinfo=timezone.utc),
            }
            for datum, menge in entries
        ]
    }


class TestSteuerfreiAb(unittest.TestCase):
    def test_normales_kaufdatum(self):
        self.assertEqual(steuerfrei_ab(date(2024, 1, 15)), date(2025, 1, 16))

    def test_schaltjahr_29_februar(self):
        self.assertEqual(steuerfrei_ab(date(2024, 2, 29)), date(2025, 3, 1))

    def test_haltefrist_status_text_steuerfrei_mit_tagen(self):
        frei_ab = steuerfrei_ab(date(2024, 1, 1))
        heute = date(2026, 6, 27)
        tage_seit = (heute - frei_ab).days
        self.assertEqual(
            haltefrist_status_text(frei_ab, heute=heute),
            f"steuerfrei (seit {tage_seit} Tagen)",
        )


class TestHaltefristStatus(unittest.TestCase):
    def test_alter_kauf_ohne_verkauf_ist_steuerfrei(self):
        purchases = _purchases(("BTC", "2024-01-01T10:00:00+00:00", 1.0, 40000.0))
        heute = date(2026, 6, 27)

        tranches = berechne_haltefristen(purchases, {}, heute=heute)

        self.assertEqual(len(tranches), 1)
        t = tranches[0]
        self.assertTrue(t.steuerfrei)
        self.assertEqual(t.tage_verbleibend, 0)
        tage_seit = (heute - t.steuerfrei_ab).days
        self.assertEqual(t.status, f"steuerfrei (seit {tage_seit} Tagen)")
        self.assertEqual(t.steuerfrei_ab, date(2025, 1, 2))

    def test_frischer_kauf_ist_gesperrt_mit_korrekten_resttagen(self):
        purchases = _purchases(("ETH", "2026-06-01T12:00:00+00:00", 2.0, 3000.0))
        heute = date(2026, 6, 27)
        frei_ab = steuerfrei_ab(date(2026, 6, 1))
        erwartete_tage = (frei_ab - heute).days

        tranches = berechne_haltefristen(purchases, {}, heute=heute)

        self.assertEqual(len(tranches), 1)
        t = tranches[0]
        self.assertFalse(t.steuerfrei)
        self.assertEqual(t.tage_verbleibend, erwartete_tage)
        self.assertEqual(t.status, f"gesperrt ({erwartete_tage} Tage)")
        self.assertGreater(erwartete_tage, 30)

    def test_mehrere_kaeufe_teilverkauf_fifo(self):
        purchases = _purchases(
            ("BTC", "2024-01-01T10:00:00+00:00", 1.0, 40000.0),
            ("BTC", "2026-03-01T10:00:00+00:00", 0.5, 60000.0),
        )
        sells = _sells("BTC", ("2025-12-01T10:00:00+00:00", 0.8))
        heute = date(2026, 6, 27)

        tranches = berechne_haltefristen(purchases, sells, heute=heute)

        self.assertEqual(len(tranches), 2)
        by_date = {t.kaufdatum: t for t in tranches}

        self.assertAlmostEqual(by_date[date(2024, 1, 1)].menge, 0.2)
        self.assertTrue(by_date[date(2024, 1, 1)].steuerfrei)

        self.assertAlmostEqual(by_date[date(2026, 3, 1)].menge, 0.5)
        self.assertFalse(by_date[date(2026, 3, 1)].steuerfrei)


class TestGrenzfall365vs366(unittest.TestCase):
    def test_tag_365_nach_kauf_noch_gesperrt(self):
        kauf = date(2025, 1, 1)
        heute = date(2026, 1, 1)
        frei_ab = steuerfrei_ab(kauf)

        self.assertEqual(frei_ab, date(2026, 1, 2))
        self.assertLess(heute, frei_ab)

        purchases = _purchases(("BTC", "2025-01-01T00:00:00+00:00", 1.0, 50000.0))
        tranches = berechne_haltefristen(purchases, {}, heute=heute)

        self.assertFalse(tranches[0].steuerfrei)
        self.assertEqual(tranches[0].tage_verbleibend, 1)
        self.assertEqual(tranches[0].status, "bald steuerfrei (1 Tage)")

    def test_tag_366_nach_kauf_steuerfrei(self):
        heute = date(2026, 1, 2)
        self.assertEqual(heute, steuerfrei_ab(date(2025, 1, 1)))

        purchases = _purchases(("BTC", "2025-01-01T00:00:00+00:00", 1.0, 50000.0))
        tranches = berechne_haltefristen(purchases, {}, heute=heute)

        self.assertTrue(tranches[0].steuerfrei)
        self.assertEqual(tranches[0].tage_verbleibend, 0)
        self.assertEqual(tranches[0].status, "steuerfrei (seit 0 Tagen)")


class TestFifoDirekt(unittest.TestCase):
    def test_verkauf_loescht_aelteste_tranche_komplett(self):
        buys = [
            {
                "coin": "BTC",
                "datum": "2024-01-01T00:00:00+00:00",
                "menge": 1.0,
                "kaufpreis_eur": 100.0,
            },
            {
                "coin": "BTC",
                "datum": "2025-01-01T00:00:00+00:00",
                "menge": 1.0,
                "kaufpreis_eur": 200.0,
            },
        ]
        sells = [{"menge": 1.0, "zeit": datetime(2025, 6, 1, tzinfo=timezone.utc)}]

        open_lots = _apply_fifo(buys, sells)

        self.assertEqual(len(open_lots), 1)
        self.assertEqual(open_lots[0]["kaufdatum"], date(2025, 1, 1))
        self.assertAlmostEqual(open_lots[0]["menge"], 1.0)


class TestFristKalender(unittest.TestCase):
    def test_13_monate_und_monatszuordnung(self):
        purchases = _purchases(
            ("BTC", "2025-01-01T00:00:00+00:00", 1.0, 50000.0),
            ("ETH", "2026-04-01T00:00:00+00:00", 2.0, 3000.0),
        )
        heute = date(2026, 6, 27)
        tranches = berechne_haltefristen(purchases, {}, heute=heute)
        balances = {"BTC": 1.0, "ETH": 2.0}
        prices = {"BTC": 100000.0, "ETH": 3500.0}

        kalender = berechne_frist_kalender(tranches, balances, prices, heute=heute)

        self.assertEqual(len(kalender.monate), 13)
        self.assertEqual(kalender.monate[0].label, "Jun 26")
        self.assertEqual(kalender.monate[-1].label, "Jun 27")
        self.assertAlmostEqual(kalender.heute_freier_wert_eur, 100000.0)
        self.assertAlmostEqual(kalender.gesperrter_wert_eur, 7000.0)
        self.assertTrue(kalender.plausibility_ok)

        eth_monat = next(m for m in kalender.monate if m.month_key == "2027-04")
        self.assertEqual(len(eth_monat.details), 1)
        self.assertEqual(eth_monat.details[0][0], "ETH")
        self.assertAlmostEqual(eth_monat.wert_eur, 7000.0)

    def test_plausibilitaet_erkennt_abweichung(self):
        purchases = _purchases(("BTC", "2024-01-01T00:00:00+00:00", 0.5, 40000.0))
        tranches = berechne_haltefristen(purchases, {}, heute=date(2026, 6, 27))
        ok, details = pruefe_mengen_plausibilitaet(tranches, {"BTC": 1.0})
        self.assertFalse(ok)
        self.assertTrue(any("BTC" in line for line in details))

    def test_plausibilitaet_status_rundung_vs_abweichung(self):
        purchases = _purchases(("DOGE", "2024-01-01T00:00:00+00:00", 403.0, 0.1))
        tranches = berechne_haltefristen(purchases, {}, heute=date(2026, 6, 27))
        ok, rows, _details = plausibilitaet_pro_coin(tranches, {"DOGE": 402.61715})
        self.assertTrue(ok)
        doge = next(row for row in rows if row.coin == "DOGE")
        self.assertEqual(doge.status, "rundung")

        ok_big, rows_big, details_big = plausibilitaet_pro_coin(tranches, {"DOGE": 350.0})
        self.assertFalse(ok_big)
        doge_big = next(row for row in rows_big if row.coin == "DOGE")
        self.assertEqual(doge_big.status, "abweichung")
        self.assertTrue(details_big)

    def test_plausibilitaet_ignoriert_stablecoins(self):
        ok, rows, details = plausibilitaet_pro_coin([], {"USDC": 100.0})
        self.assertTrue(ok)
        self.assertFalse(details)
        usdc = next(row for row in rows if row.coin == "USDC")
        self.assertEqual(usdc.status, "ignoriert")


if __name__ == "__main__":
    unittest.main()
