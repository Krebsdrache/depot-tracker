"""Tests für realisierte G/V in bilanz.py (FIFO, Gebühren, Steuer-Aufteilung)."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from bilanz import _berechne_realisierten_verkauf, berechne_bilanz, jahres_bilanz_dataframe  # noqa: E402
from fifo import fifo_zuordnung_fuer_verkaeufe, offene_fifo_lots  # noqa: E402


def _purchases(*rows: tuple) -> pd.DataFrame:
    cols = ["coin", "datum", "menge", "kaufpreis_eur", "gebuehr_eur"]
    normalized = []
    for row in rows:
        if len(row) == 4:
            normalized.append((*row, float("nan")))
        else:
            normalized.append(row)
    return pd.DataFrame(normalized, columns=cols)


def _sells(*rows: tuple) -> pd.DataFrame:
    cols = ["coin", "datum", "menge", "verkaufspreis_eur", "gebuehr_eur", "gebuehr_geschaetzt"]
    normalized = []
    for row in rows:
        if len(row) == 4:
            normalized.append((*row, float("nan"), float("nan")))
        elif len(row) == 5:
            normalized.append((*row, float("nan")))
        else:
            normalized.append(row)
    return pd.DataFrame(normalized, columns=cols)


class TestTeilverkauf(unittest.TestCase):
    def test_kauf_0_2_verkauf_0_1_rest_bleibt_offen(self):
        purchases = _purchases(("BTC", "2024-01-01T00:00:00+00:00", 0.2, 100.0))
        sells = _sells(("BTC", "2024-06-01T00:00:00+00:00", 0.1, 150.0))

        match = fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0]
        rv = _berechne_realisierten_verkauf(match)
        open_lots = offene_fifo_lots(purchases, sells)

        self.assertAlmostEqual(rv.menge, 0.1)
        self.assertAlmostEqual(rv.einstand_eur, 10.0)
        self.assertAlmostEqual(rv.erloes_eur, 15.0)
        self.assertAlmostEqual(rv.gv_eur, 5.0)
        self.assertAlmostEqual(rv.gv_prozent, 50.0)

        self.assertEqual(len(open_lots), 1)
        self.assertAlmostEqual(open_lots[0]["menge"], 0.1)
        self.assertEqual(open_lots[0]["kaufdatum"], date(2024, 1, 1))
        self.assertAlmostEqual(open_lots[0]["kaufpreis_eur"], 100.0)


class TestFifoMehrereKaeufe(unittest.TestCase):
    def test_ein_verkauf_ueber_zwei_tranchen(self):
        purchases = _purchases(
            ("ETH", "2024-01-01T00:00:00+00:00", 1.0, 100.0),
            ("ETH", "2024-06-01T00:00:00+00:00", 1.0, 200.0),
        )
        sells = _sells(("ETH", "2024-12-01T00:00:00+00:00", 1.5, 250.0))

        match = fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0]

        self.assertEqual(len(match.anteile), 2)
        self.assertAlmostEqual(match.anteile[0].menge, 1.0)
        self.assertAlmostEqual(match.anteile[0].kaufpreis_eur, 100.0)
        self.assertAlmostEqual(match.anteile[1].menge, 0.5)
        self.assertAlmostEqual(match.anteile[1].kaufpreis_eur, 200.0)
        self.assertAlmostEqual(match.einstand_eur, 200.0)


class TestSteuerAufteilung(unittest.TestCase):
    def test_gemischtes_alter_korrekt_aufgeteilt(self):
        purchases = _purchases(
            ("BTC", "2023-01-01T00:00:00+00:00", 1.0, 100.0),
            ("BTC", "2024-06-01T00:00:00+00:00", 1.0, 100.0),
        )
        sells = _sells(("BTC", "2024-12-01T00:00:00+00:00", 1.5, 200.0))

        rv = _berechne_realisierten_verkauf(fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0])

        # Erlös 300, Einstand 150 → G/V 150
        # 1.0 aus steuerfreiem Kauf (2023): +100 steuerfrei
        # 0.5 aus jungem Kauf (2024-06): +50 steuerpflichtig
        self.assertAlmostEqual(rv.gv_eur, 150.0)
        self.assertAlmostEqual(rv.steuerfrei_realisiert_eur, 100.0)
        self.assertAlmostEqual(rv.steuerpflichtig_realisiert_eur, 50.0)

    def test_steuer_spalten_summe_ergibt_gv(self):
        purchases = _purchases(
            ("BTC", "2024-06-01T00:00:00+00:00", 1.0, 100.0),
            ("BTC", "2023-01-01T00:00:00+00:00", 1.0, 100.0),
        )
        sells = _sells(
            ("BTC", "2024-12-01T00:00:00+00:00", 1.0, 150.0),
            ("BTC", "2025-06-01T00:00:00+00:00", 1.0, 200.0),
        )
        for match in fifo_zuordnung_fuer_verkaeufe(purchases, sells):
            rv = _berechne_realisierten_verkauf(match)
            if rv.gv_eur is None:
                continue
            total = rv.steuerfrei_realisiert_eur + rv.steuerpflichtig_realisiert_eur
            self.assertAlmostEqual(total, rv.gv_eur, places=6)


class TestGebuehren(unittest.TestCase):
    def test_gebuehr_reduziert_netto_gv(self):
        purchases = _purchases(("BTC", "2024-01-01T00:00:00+00:00", 0.2, 100.0, 2.0))
        sells = _sells(("BTC", "2024-06-01T00:00:00+00:00", 0.1, 150.0, 1.0, 0))

        rv = _berechne_realisierten_verkauf(fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0])

        # Erlös netto: 15 - 1 = 14, Einstand: 10 + 1 (halbe Kaufgebühr) = 11
        self.assertAlmostEqual(rv.erloes_eur, 14.0)
        self.assertAlmostEqual(rv.einstand_eur, 11.0)
        self.assertAlmostEqual(rv.gv_eur, 3.0)


class TestGewinnVerlustSteuer(unittest.TestCase):
    def test_gewinn_steuerpflichtig(self):
        purchases = _purchases(("BTC", "2024-06-01T00:00:00+00:00", 1.0, 100.0))
        sells = _sells(("BTC", "2024-12-01T00:00:00+00:00", 1.0, 150.0))
        rv = _berechne_realisierten_verkauf(fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0])
        self.assertAlmostEqual(rv.gv_eur, 50.0)
        self.assertAlmostEqual(rv.steuerpflichtig_realisiert_eur, 50.0)

    def test_gewinn_steuerfrei(self):
        purchases = _purchases(("BTC", "2023-01-01T00:00:00+00:00", 1.0, 100.0))
        sells = _sells(("BTC", "2025-06-01T00:00:00+00:00", 1.0, 200.0))
        rv = _berechne_realisierten_verkauf(fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0])
        self.assertAlmostEqual(rv.steuerfrei_realisiert_eur, 100.0)

    def test_verlust(self):
        purchases = _purchases(("ETH", "2024-01-01T00:00:00+00:00", 2.0, 100.0))
        sells = _sells(("ETH", "2024-06-01T00:00:00+00:00", 2.0, 80.0))
        rv = _berechne_realisierten_verkauf(fifo_zuordnung_fuer_verkaeufe(purchases, sells)[0])
        self.assertAlmostEqual(rv.gv_eur, -40.0)
        self.assertAlmostEqual(rv.steuerfrei_realisiert_eur + rv.steuerpflichtig_realisiert_eur, -40.0)


class TestJahresBilanz(unittest.TestCase):
    def test_jahres_summen_nach_verkaufsdatum(self):
        purchases = _purchases(
            ("BTC", "2023-01-01T00:00:00+00:00", 1.0, 100.0),
            ("BTC", "2024-06-01T00:00:00+00:00", 1.0, 100.0),
        )
        sells = _sells(
            ("BTC", "2024-03-01T00:00:00+00:00", 1.0, 150.0),
            ("BTC", "2025-01-01T00:00:00+00:00", 1.0, 120.0),
        )
        with (
            patch("bilanz.load_purchases_csv", return_value=purchases),
            patch("bilanz.load_sells_csv", return_value=sells),
        ):
            bilanz = berechne_bilanz()
            jahres = jahres_bilanz_dataframe(bilanz)

        y2024 = jahres[jahres["Jahr"] == 2024].iloc[0]
        y2025 = jahres[jahres["Jahr"] == 2025].iloc[0]
        gesamt = jahres[jahres["Jahr"] == "Gesamt"].iloc[0]

        self.assertAlmostEqual(float(y2024["Netto realisiert (EUR)"]), 50.0)
        self.assertAlmostEqual(float(y2025["Netto realisiert (EUR)"]), 20.0)
        self.assertAlmostEqual(float(gesamt["Netto realisiert (EUR)"]), 70.0)
        self.assertEqual(int(y2024["Verkäufe"]), 1)
        self.assertEqual(int(y2025["Verkäufe"]), 1)


if __name__ == "__main__":
    unittest.main()
