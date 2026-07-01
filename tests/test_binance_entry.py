"""Tests für FIFO-Einstand (fifo_avg_entry_from_local / average_entry_from_local)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from binance_data import average_entry_from_local  # noqa: E402
from fifo import fifo_avg_entry_from_local  # noqa: E402


def _purchases(*rows: tuple) -> pd.DataFrame:
    cols = ["coin", "datum", "menge", "kaufpreis_eur", "gebuehr_eur"]
    return pd.DataFrame(rows, columns=cols)


def _sells(*rows: tuple) -> pd.DataFrame:
    cols = ["coin", "datum", "menge", "verkaufspreis_eur", "gebuehr_eur", "gebuehr_geschaetzt"]
    normalized = []
    for row in rows:
        if len(row) == 4:
            normalized.append((*row, float("nan"), float("nan")))
        else:
            normalized.append(row)
    return pd.DataFrame(normalized, columns=cols)


class TestFifoEntry(unittest.TestCase):
    def test_nur_kaeufe_durchschnitt(self):
        purchases = _purchases(
            ("BTC", "2024-01-01T00:00:00+00:00", 1.0, 100.0, float("nan")),
            ("BTC", "2024-06-01T00:00:00+00:00", 1.0, 200.0, float("nan")),
        )
        avg = fifo_avg_entry_from_local("BTC", purchases, _sells())
        self.assertAlmostEqual(avg, 150.0)
        self.assertAlmostEqual(average_entry_from_local("BTC", purchases, _sells()), avg)

    def test_verkauf_verbraucht_aeltestes_lot(self):
        """FIFO: Verkauf 1 BTC entfernt Lot @100, Rest nur noch @200."""
        purchases = _purchases(
            ("BTC", "2024-01-01T00:00:00+00:00", 1.0, 100.0, float("nan")),
            ("BTC", "2024-06-01T00:00:00+00:00", 1.0, 200.0, float("nan")),
        )
        sells = _sells(("BTC", "2024-03-01T00:00:00+00:00", 1.0, 150.0))
        avg = fifo_avg_entry_from_local("BTC", purchases, sells)
        self.assertAlmostEqual(avg, 200.0)

    def test_teilverkauf_aeltestes_lot(self):
        purchases = _purchases(
            ("BTC", "2024-01-01T00:00:00+00:00", 1.0, 100.0, float("nan")),
            ("BTC", "2024-07-01T00:00:00+00:00", 1.0, 200.0, float("nan")),
        )
        sells = _sells(("BTC", "2024-06-01T00:00:00+00:00", 0.5, 150.0))
        avg = fifo_avg_entry_from_local("BTC", purchases, sells)
        self.assertAlmostEqual(avg, 250.0 / 1.5)

    def test_gebuehr_im_einstand(self):
        purchases = _purchases(
            ("ETH", "2024-01-01T00:00:00+00:00", 1.0, 100.0, 5.0),
        )
        avg = fifo_avg_entry_from_local("ETH", purchases, _sells())
        self.assertAlmostEqual(avg, 105.0)


if __name__ == "__main__":
    unittest.main()
