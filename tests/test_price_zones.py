"""Tests für Preis-Zonen-Tabelle."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import price_zones  # noqa: E402


class TestPriceZones(unittest.TestCase):
    def test_closest_zone_picks_nearest_threshold(self):
        thresholds = [100000.0, 90000.0, 80000.0, 70000.0, 65000.0, 50000.0]
        self.assertEqual(price_zones.closest_zone_index(90000.0, thresholds), 1)
        self.assertEqual(price_zones.closest_zone_index(88000.0, thresholds), 2)

    def test_closest_zone_tie_prefers_lower_zone(self):
        thresholds = [100.0, 90.0, 80.0]
        self.assertEqual(price_zones.closest_zone_index(95.0, thresholds), 0)

    def test_build_eur_table_converts_thresholds(self):
        config = {
            "currency": "EUR",
            "threshold_currency": "USD",
            "exchange_rate_note": "test",
            "zones": [{"id": 1, "color": "#f00", "label": "Z1"}],
            "categories": [
                {
                    "name": "Kat",
                    "header_color": "#ccc",
                    "coins": [{"symbol": "BTC", "thresholds": [100.0, 50.0]}],
                }
            ],
        }
        tickers = {"BTCEUR": 72.0, "USDTEUR": 0.9}
        table = price_zones.build_price_zone_table(tickers, config=config, display_currency="EUR")
        state = table.coin_states["BTC"]
        self.assertEqual(table.display_currency, "EUR")
        self.assertAlmostEqual(state.live_price, 72.0)
        self.assertAlmostEqual(state.thresholds[0], 90.0)
        self.assertAlmostEqual(state.thresholds[1], 45.0)
        self.assertEqual(state.active_zone_index, 0)
        self.assertEqual(state.price_source, "direkt (EUR-Paar)")

    def test_build_usd_table_keeps_original_thresholds(self):
        config = {
            "currency": "EUR",
            "threshold_currency": "USD",
            "exchange_rate_note": "",
            "zones": [{"id": 1, "color": "#f00", "label": "Z1"}],
            "categories": [
                {
                    "name": "Kat",
                    "header_color": "#ccc",
                    "coins": [{"symbol": "BTC", "thresholds": [100000.0, 50000.0]}],
                }
            ],
        }
        tickers = {"BTCUSDT": 95000.0, "USDTEUR": 0.9}
        table = price_zones.build_price_zone_table(tickers, config=config, display_currency="USD")
        state = table.coin_states["BTC"]
        self.assertEqual(table.display_currency, "USD")
        self.assertAlmostEqual(state.live_price, 95000.0)
        self.assertAlmostEqual(state.thresholds[0], 106192.8)
        self.assertAlmostEqual(state.thresholds[1], 54180.0)
        self.assertEqual(state.price_source, "direkt (USDT-Paar)")
        self.assertEqual(state.active_zone_index, 0)

    def test_usd_thresholds_converted_with_live_rate_for_eur_only(self):
        config = {
            "currency": "EUR",
            "threshold_currency": "USD",
            "exchange_rate_note": "",
            "zones": [{"id": 1, "color": "#f00", "label": "Z1"}],
            "categories": [
                {
                    "name": "Kat",
                    "header_color": "#ccc",
                    "coins": [{"symbol": "ETH", "thresholds": [1000.0]}],
                }
            ],
        }
        tickers = {"USDTEUR": 0.92, "ETHEUR": 3000.0}
        table = price_zones.build_price_zone_table(tickers, config=config, display_currency="EUR")
        self.assertAlmostEqual(table.usd_eur_rate, 0.92)
        self.assertAlmostEqual(table.coin_states["ETH"].thresholds[0], 920.0)

    def test_indirect_price_source_flagged(self):
        config = {
            "currency": "EUR",
            "threshold_currency": "USD",
            "exchange_rate_note": "",
            "zones": [{"id": 1, "color": "#f00", "label": "Z1"}],
            "categories": [
                {
                    "name": "Kat",
                    "header_color": "#ccc",
                    "coins": [{"symbol": "HYPE", "thresholds": [50.0, 20.0]}],
                }
            ],
        }
        tickers = {"HYPEUSDT": 40.0, "USDTEUR": 0.92}
        eur_table = price_zones.build_price_zone_table(tickers, config=config, display_currency="EUR")
        usd_table = price_zones.build_price_zone_table(tickers, config=config, display_currency="USD")
        html = price_zones.render_price_zone_table_html(eur_table)
        self.assertIn("pz-conv-badge", html)
        self.assertIn("USDT", html)
        self.assertEqual(eur_table.coin_states["HYPE"].price_source, "USDT→EUR")
        self.assertAlmostEqual(usd_table.coin_states["HYPE"].live_price, 40.0)

    def test_category_allocation_four_buckets(self):
        from dataclasses import dataclass

        @dataclass
        class _Pos:
            coin: str
            current_value_eur: float | None

        config = {
            "categories": [
                {
                    "name": "Kategorie 1: 80%",
                    "header_color": "#92d050",
                    "coins": [{"symbol": "BTC", "thresholds": [1.0]}],
                },
                {
                    "name": "Kategorie 2: 15%",
                    "header_color": "#ffff00",
                    "coins": [{"symbol": "XRP", "thresholds": [1.0]}],
                },
                {
                    "name": "Kategorie 3: 5%",
                    "header_color": "#ffc000",
                    "coins": [{"symbol": "TAO", "thresholds": [1.0]}],
                },
            ]
        }
        positions = [
            _Pos("BTC", 800.0),
            _Pos("ETH", 100.0),
            _Pos("XRP", 150.0),
            _Pos("TAO", 50.0),
            _Pos("DOGE", 25.0),
        ]
        rows = price_zones.build_category_allocation(positions, config)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(rows[0].value_eur, 800.0)
        self.assertEqual(rows[0].coins, ("BTC",))
        self.assertAlmostEqual(rows[1].value_eur, 150.0)
        self.assertAlmostEqual(rows[2].value_eur, 50.0)
        self.assertAlmostEqual(rows[3].value_eur, 125.0)
        self.assertIn("ETH", rows[3].coins)
        self.assertIn("DOGE", rows[3].coins)
        self.assertEqual(rows[3].label, price_zones.REST_CATEGORY_LABEL)
        self.assertAlmostEqual(sum(r.share_pct for r in rows), 100.0)

        df = price_zones.category_allocation_dataframe(positions, config)
        self.assertEqual(len(df), 4)
        self.assertEqual(list(df["Reihenfolge"]), [1, 2, 3, 4])

    def test_category_coin_breakdown_within_category(self):
        from dataclasses import dataclass

        @dataclass
        class _Pos:
            coin: str
            current_value_eur: float | None

        config = {
            "categories": [
                {
                    "name": "Kategorie 1: 80%",
                    "header_color": "#92d050",
                    "coins": [{"symbol": "BTC", "thresholds": [1.0]}],
                },
                {
                    "name": "Kategorie 2: 15%",
                    "header_color": "#ffff00",
                    "coins": [{"symbol": "XRP", "thresholds": [1.0]}],
                },
                {
                    "name": "Kategorie 3: 5%",
                    "header_color": "#ffc000",
                    "coins": [{"symbol": "TAO", "thresholds": [1.0]}],
                },
            ]
        }
        positions = [
            _Pos("BTC", 800.0),
            _Pos("ETH", 100.0),
            _Pos("DOGE", 25.0),
        ]
        rest_label = price_zones.REST_CATEGORY_LABEL
        coin_df = price_zones.category_coin_breakdown_dataframe(positions, rest_label, config)
        detail = coin_df[coin_df["Coin"] != price_zones.CATEGORY_BREAKDOWN_TOTAL_LABEL]
        self.assertEqual(len(detail), 2)
        self.assertEqual(set(detail["Coin"]), {"ETH", "DOGE"})
        self.assertAlmostEqual(detail["Anteil in Kategorie %"].sum(), 100.0)
        eth_row = detail.loc[detail["Coin"] == "ETH"].iloc[0]
        self.assertAlmostEqual(eth_row["Anteil in Kategorie %"], 100.0 * 100.0 / 125.0)
        self.assertAlmostEqual(eth_row["Anteil in Gesamt ohne Cashreserve %"], 100.0 * 100.0 / 925.0)
        self.assertAlmostEqual(eth_row["Anteil in Gesamt mit Cashreserve %"], 100.0 * 100.0 / 925.0)
        total = coin_df.loc[coin_df["Coin"] == price_zones.CATEGORY_BREAKDOWN_TOTAL_LABEL].iloc[0]
        self.assertAlmostEqual(total["Wert (EUR)"], 125.0)
        self.assertAlmostEqual(total["Anteil in Kategorie %"], 100.0)
        self.assertAlmostEqual(total["Anteil in Gesamt ohne Cashreserve %"], 125.0 / 925.0 * 100.0)
        self.assertAlmostEqual(total["Anteil in Gesamt mit Cashreserve %"], 125.0 / 925.0 * 100.0)

    def test_category_coin_breakdown_gesamt_mit_ohne_cashreserve(self):
        from dataclasses import dataclass

        @dataclass
        class _Pos:
            coin: str
            current_value_eur: float | None

        config = {
            "categories": [
                {
                    "name": "Kat 1",
                    "header_color": "#111",
                    "coins": [{"symbol": "BTC", "thresholds": [1.0]}],
                },
                {"name": "Kat 2", "header_color": "#222", "coins": []},
                {"name": "Kat 3", "header_color": "#333", "coins": []},
            ]
        }
        positions = [
            _Pos("BTC", 800.0),
            _Pos("EUR", 50.0),
            _Pos("USDT", 30.0),
        ]
        coin_df = price_zones.category_coin_breakdown_dataframe(
            positions,
            "Kat 1",
            config,
            include_cash_reserve=True,
        )
        btc_row = coin_df.iloc[0]
        self.assertEqual(btc_row["Coin"], "BTC")
        self.assertAlmostEqual(btc_row["Anteil in Kategorie %"], 100.0)
        self.assertAlmostEqual(btc_row["Anteil in Gesamt ohne Cashreserve %"], 800.0 / 800.0 * 100.0)
        self.assertAlmostEqual(btc_row["Anteil in Gesamt mit Cashreserve %"], 800.0 / 880.0 * 100.0)
        total = coin_df.loc[coin_df["Coin"] == price_zones.CATEGORY_BREAKDOWN_TOTAL_LABEL].iloc[0]
        self.assertAlmostEqual(total["Wert (EUR)"], 800.0)
        self.assertAlmostEqual(total["Anteil in Gesamt mit Cashreserve %"], 800.0 / 880.0 * 100.0)

    def test_category_labels_in_order(self):
        config = {
            "categories": [
                {"name": "Kat A", "header_color": "#111", "coins": []},
                {"name": "Kat B", "header_color": "#222", "coins": []},
                {"name": "Kat C", "header_color": "#333", "coins": []},
            ]
        }
        labels = price_zones.category_labels_in_order(config)
        self.assertEqual(
            labels,
            ["Kat A", "Kat B", "Kat C", price_zones.REST_CATEGORY_LABEL],
        )
        labels_five = price_zones.category_labels_in_order(config, include_cash_reserve=True)
        self.assertEqual(
            labels_five,
            [
                "Kat A",
                "Kat B",
                "Kat C",
                price_zones.REST_CATEGORY_LABEL,
                price_zones.CASH_RESERVE_CATEGORY_LABEL,
            ],
        )

    def test_five_category_cash_reserve_separate(self):
        from dataclasses import dataclass

        @dataclass
        class _Pos:
            coin: str
            current_value_eur: float | None

        config = {
            "categories": [
                {
                    "name": "Kategorie 1: 80%",
                    "header_color": "#92d050",
                    "coins": [{"symbol": "BTC", "thresholds": [1.0]}],
                },
                {
                    "name": "Kategorie 2: 15%",
                    "header_color": "#ffff00",
                    "coins": [{"symbol": "XRP", "thresholds": [1.0]}],
                },
                {
                    "name": "Kategorie 3: 5%",
                    "header_color": "#ffc000",
                    "coins": [{"symbol": "TAO", "thresholds": [1.0]}],
                },
            ]
        }
        positions = [
            _Pos("BTC", 800.0),
            _Pos("ETH", 100.0),
            _Pos("EUR", 50.0),
            _Pos("USDT", 30.0),
            _Pos("USDC", 20.0),
        ]
        rows = price_zones.build_category_allocation(
            positions,
            config,
            include_cash_reserve=True,
        )
        self.assertEqual(len(rows), 5)
        self.assertAlmostEqual(rows[3].value_eur, 100.0)
        self.assertIn("ETH", rows[3].coins)
        self.assertAlmostEqual(rows[4].value_eur, 100.0)
        self.assertEqual(set(rows[4].coins), {"EUR", "USDT", "USDC"})
        self.assertAlmostEqual(sum(r.share_pct for r in rows), 100.0)

    def test_four_category_excludes_cash_reserve(self):
        from dataclasses import dataclass

        @dataclass
        class _Pos:
            coin: str
            current_value_eur: float | None

        config = {
            "categories": [
                {
                    "name": "Kat 1",
                    "header_color": "#111",
                    "coins": [{"symbol": "BTC", "thresholds": [1.0]}],
                },
                {"name": "Kat 2", "header_color": "#222", "coins": []},
                {"name": "Kat 3", "header_color": "#333", "coins": []},
            ]
        }
        positions = [_Pos("BTC", 100.0), _Pos("USDT", 25.0), _Pos("EUR", 10.0)]
        rows = price_zones.build_category_allocation(positions, config)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(rows[0].value_eur, 100.0)
        self.assertAlmostEqual(rows[3].value_eur, 0.0)
        self.assertEqual(rows[0].coins, ("BTC",))
        self.assertAlmostEqual(rows[0].share_pct, 100.0)


if __name__ == "__main__":
    unittest.main()
