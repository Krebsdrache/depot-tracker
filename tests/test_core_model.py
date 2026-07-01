"""Tests für das neutrale Domänenmodell und das Provider-Interface (Phase 0)."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.model import (  # noqa: E402
    AssetClass,
    Capability,
    DepotSnapshot,
    Holding,
    Instrument,
    Transaction,
    TransactionKind,
)
from providers.base import Provider, ProviderInfo  # noqa: E402


class TestDomainModel(unittest.TestCase):
    def test_instrument_normalises_symbol(self):
        inst = Instrument(symbol=" btc ", asset_class=AssetClass.CRYPTO)
        self.assertEqual(inst.symbol, "BTC")
        self.assertEqual(inst.native_currency, "EUR")

    def test_holding_entry_known(self):
        inst = Instrument(symbol="ETH", asset_class=AssetClass.CRYPTO)
        with_entry = Holding(instrument=inst, quantity=1.0, avg_entry_price_eur=1000.0)
        without_entry = Holding(instrument=inst, quantity=1.0)
        self.assertTrue(with_entry.entry_known)
        self.assertFalse(without_entry.entry_known)

    def test_transaction_builds(self):
        inst = Instrument(symbol="SOL", asset_class=AssetClass.CRYPTO)
        tx = Transaction(
            instrument=inst,
            kind=TransactionKind.BUY,
            quantity=2.0,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            price_eur=50.0,
        )
        self.assertEqual(tx.kind, TransactionKind.BUY)
        self.assertEqual(tx.instrument.symbol, "SOL")

    def test_depot_snapshot_defaults(self):
        snap = DepotSnapshot(depot_id="binance", provider_id="binance")
        self.assertEqual(snap.holdings, [])
        self.assertTrue(snap.ok)
        self.assertEqual(snap.total_value_eur, 0.0)

    def test_enums_are_stable_strings(self):
        self.assertEqual(AssetClass.CRYPTO.value, "crypto")
        self.assertEqual(Capability.LIVE_PRICES.value, "live_prices")
        self.assertEqual(TransactionKind.SELL.value, "sell")


class _DummyProvider(Provider):
    """Minimaler Provider zum Prüfen, dass das Interface implementierbar ist."""

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="dummy",
            display_name="Dummy",
            asset_classes=frozenset({AssetClass.CRYPTO}),
        )

    def capabilities(self) -> set[Capability]:
        return {Capability.LIVE_PRICES}

    def is_configured(self) -> bool:
        return True

    def load_snapshot(self) -> DepotSnapshot:
        return DepotSnapshot(depot_id="dummy", provider_id="dummy")


class TestProviderInterface(unittest.TestCase):
    def test_dummy_provider_implements_interface(self):
        provider = _DummyProvider()
        self.assertEqual(provider.info().id, "dummy")
        self.assertTrue(provider.is_configured())
        self.assertTrue(provider.supports(Capability.LIVE_PRICES))
        self.assertFalse(provider.supports(Capability.TRADE_HISTORY))
        self.assertEqual(provider.load_transactions(), [])
        self.assertEqual(provider.load_snapshot().provider_id, "dummy")

    def test_provider_is_abstract(self):
        with self.assertRaises(TypeError):
            Provider()  # type: ignore[abstract]


if __name__ == "__main__":
    unittest.main()
