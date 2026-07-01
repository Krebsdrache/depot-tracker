"""Tests für den Binance-Provider-Adapter (Phase 1).

Prüft ausschließlich die reine Übersetzung Position/CSV -> neutrale Typen.
Kein Netzwerk, keine echten Binance-Aufrufe.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import binance_data  # noqa: E402
from core.model import AssetClass, Capability, TransactionKind  # noqa: E402
from providers.binance.provider import (  # noqa: E402
    BinanceProvider,
    asset_class_for,
    holding_from_position,
    snapshot_from_portfolio_result,
    transactions_from_dataframes,
)


def _position(coin: str, qty: float, price: float | None, entry: float | None):
    value = qty * price if price is not None else None
    pl_eur = None
    pl_pct = None
    if price is not None and entry:
        pl_eur = (price - entry) * qty
        pl_pct = ((price / entry) - 1.0) * 100.0
    return binance_data.Position(
        coin=coin,
        quantity=qty,
        current_price_eur=price,
        current_value_eur=value,
        avg_entry_price_eur=entry,
        entry_known=entry is not None,
        profit_loss_eur=pl_eur,
        profit_loss_pct=pl_pct,
    )


class TestAssetClassification(unittest.TestCase):
    def test_crypto_fiat_and_cash(self):
        self.assertEqual(asset_class_for("BTC"), AssetClass.CRYPTO)
        self.assertEqual(asset_class_for("eth"), AssetClass.CRYPTO)
        self.assertEqual(asset_class_for("EUR"), AssetClass.FIAT)
        self.assertEqual(asset_class_for("USD"), AssetClass.FIAT)
        self.assertEqual(asset_class_for("USDT"), AssetClass.CASH)
        self.assertEqual(asset_class_for("usdc"), AssetClass.CASH)


class TestHoldingMapping(unittest.TestCase):
    def test_holding_from_position_preserves_values(self):
        pos = _position("BTC", 0.5, 50000.0, 40000.0)
        holding = holding_from_position(pos)
        self.assertEqual(holding.instrument.symbol, "BTC")
        self.assertEqual(holding.instrument.asset_class, AssetClass.CRYPTO)
        self.assertEqual(holding.quantity, 0.5)
        self.assertEqual(holding.price_eur, 50000.0)
        self.assertEqual(holding.value_eur, 25000.0)
        self.assertEqual(holding.avg_entry_price_eur, 40000.0)
        self.assertTrue(holding.entry_known)
        self.assertAlmostEqual(holding.profit_loss_eur, (50000.0 - 40000.0) * 0.5)

    def test_snapshot_from_portfolio_result(self):
        result = binance_data.PortfolioResult(
            ok=True,
            message="Depot geladen: 2 Coins.",
            positions=[
                _position("BTC", 0.5, 50000.0, 40000.0),
                _position("EUR", 100.0, 1.0, None),
            ],
            total_value_eur=25100.0,
        )
        snap = snapshot_from_portfolio_result(result)
        self.assertEqual(snap.provider_id, "binance")
        self.assertEqual(snap.depot_id, "binance")
        self.assertEqual(len(snap.holdings), 2)
        self.assertEqual(snap.total_value_eur, 25100.0)
        self.assertTrue(snap.ok)
        self.assertEqual(snap.holdings[1].instrument.asset_class, AssetClass.FIAT)


class TestTransactionMapping(unittest.TestCase):
    def test_transactions_sorted_and_typed(self):
        purchases = pd.DataFrame(
            [
                {
                    "trade_id": 111,
                    "coin": "BTC",
                    "datum": "2024-03-01T10:00:00+00:00",
                    "menge": 0.5,
                    "kaufpreis_eur": 40000.0,
                    "gebuehr_eur": 5.0,
                },
            ]
        )
        sells = pd.DataFrame(
            [
                {
                    "trade_id": 222,
                    "coin": "BTC",
                    "datum": "2024-01-15T09:00:00+00:00",
                    "menge": 0.2,
                    "verkaufspreis_eur": 42000.0,
                    "gebuehr_eur": 3.0,
                },
            ]
        )
        txs = transactions_from_dataframes(purchases, sells)
        self.assertEqual(len(txs), 2)
        # Zeitlich sortiert: Verkauf (Januar) vor Kauf (März)
        self.assertEqual(txs[0].kind, TransactionKind.SELL)
        self.assertEqual(txs[0].external_id, "222")
        self.assertEqual(txs[0].price_eur, 42000.0)
        self.assertEqual(txs[0].fee_eur, 3.0)
        self.assertEqual(txs[1].kind, TransactionKind.BUY)
        self.assertEqual(txs[1].instrument.symbol, "BTC")

    def test_empty_frames_return_empty(self):
        empty = pd.DataFrame()
        self.assertEqual(transactions_from_dataframes(empty, empty), [])


class TestProviderMetadata(unittest.TestCase):
    def test_info_and_capabilities(self):
        provider = BinanceProvider()
        self.assertEqual(provider.info().id, "binance")
        self.assertIn(AssetClass.CRYPTO, provider.info().asset_classes)
        self.assertTrue(provider.supports(Capability.LIVE_PRICES))
        self.assertTrue(provider.supports(Capability.CRYPTO_HALTEFRIST))
        self.assertTrue(provider.supports(Capability.PRICE_ZONES))
        self.assertFalse(provider.supports(Capability.ABGELTUNGSSTEUER))


if __name__ == "__main__":
    unittest.main()
