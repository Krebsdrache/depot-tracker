"""Binance als Depot-Provider.

Dünner Adapter: nutzt ausschließlich die bereits vorhandene Logik in
``binance_data`` und übersetzt deren Ergebnisse (``Position``/``PortfolioResult``
sowie die Trade-CSVs) in das neutrale Domänenmodell. ``binance_data`` und
``app.py`` bleiben dadurch unverändert.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

import binance_data
from core.model import (
    AssetClass,
    Capability,
    DepotSnapshot,
    Holding,
    Instrument,
    Transaction,
    TransactionKind,
)
from providers.base import Provider, ProviderInfo

PROVIDER_ID = "binance"

# Asset-Klassifizierung analog zu price_zones.CASH_RESERVE_ASSETS gehalten.
FIAT_ASSETS = frozenset({"EUR", "USD"})
STABLE_ASSETS = frozenset({"USDT", "USDC", "BUSD", "FDUSD"})


def asset_class_for(coin: str) -> AssetClass:
    """Ordnet einen Binance-Coin einer neutralen Anlageklasse zu."""
    symbol = str(coin).strip().upper()
    if symbol in FIAT_ASSETS:
        return AssetClass.FIAT
    if symbol in STABLE_ASSETS:
        return AssetClass.CASH
    return AssetClass.CRYPTO


def instrument_for(coin: str) -> Instrument:
    return Instrument(symbol=coin, asset_class=asset_class_for(coin))


def holding_from_position(position: binance_data.Position) -> Holding:
    """Übersetzt eine Binance-``Position`` in ein neutrales ``Holding``."""
    return Holding(
        instrument=instrument_for(position.coin),
        quantity=position.quantity,
        price_eur=position.current_price_eur,
        value_eur=position.current_value_eur,
        avg_entry_price_eur=position.avg_entry_price_eur,
        profit_loss_eur=position.profit_loss_eur,
        profit_loss_pct=position.profit_loss_pct,
    )


def snapshot_from_portfolio_result(
    result: binance_data.PortfolioResult,
    *,
    loaded_at: datetime | None = None,
) -> DepotSnapshot:
    """Baut aus einem ``PortfolioResult`` einen neutralen ``DepotSnapshot``."""
    return DepotSnapshot(
        depot_id=PROVIDER_ID,
        provider_id=PROVIDER_ID,
        holdings=[holding_from_position(pos) for pos in result.positions],
        total_value_eur=result.total_value_eur,
        loaded_at=loaded_at,
        ok=result.ok,
        message=result.message,
    )


def _parse_timestamp(value: object) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _float_or_none(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _transactions_from_frame(
    df: pd.DataFrame,
    kind: TransactionKind,
    price_column: str,
) -> list[Transaction]:
    if df is None or df.empty:
        return []
    transactions: list[Transaction] = []
    for _, row in df.iterrows():
        quantity = _float_or_none(row.get("menge")) or 0.0
        external_id = row.get("trade_id")
        external = None if _float_or_none(external_id) is None else str(external_id)
        transactions.append(
            Transaction(
                instrument=instrument_for(row.get("coin", "")),
                kind=kind,
                quantity=quantity,
                timestamp=_parse_timestamp(row.get("datum")),
                price_eur=_float_or_none(row.get(price_column)),
                fee_eur=_float_or_none(row.get("gebuehr_eur")),
                external_id=external,
                source=PROVIDER_ID,
            )
        )
    return transactions


def transactions_from_dataframes(
    purchases: pd.DataFrame,
    sells: pd.DataFrame,
) -> list[Transaction]:
    """Übersetzt Kauf-/Verkauf-CSV-Frames in eine neutrale, zeitlich sortierte Historie."""
    transactions = _transactions_from_frame(purchases, TransactionKind.BUY, "kaufpreis_eur")
    transactions += _transactions_from_frame(sells, TransactionKind.SELL, "verkaufspreis_eur")
    transactions.sort(key=lambda tx: tx.timestamp)
    return transactions


class BinanceProvider(Provider):
    """Provider-Adapter für das bestehende Binance-Spot-Depot."""

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id=PROVIDER_ID,
            display_name="Binance",
            asset_classes=frozenset({AssetClass.CRYPTO, AssetClass.CASH, AssetClass.FIAT}),
        )

    def capabilities(self) -> set[Capability]:
        return {
            Capability.LIVE_PRICES,
            Capability.TRADE_HISTORY,
            Capability.AUTO_SYNC,
            Capability.CRYPTO_HALTEFRIST,
            Capability.PRICE_ZONES,
        }

    def is_configured(self) -> bool:
        api_key, api_secret = binance_data._load_credentials()
        return bool(api_key and api_secret)

    def load_snapshot(
        self,
        *,
        price_mode: str | None = None,
        force_live: bool = False,
    ) -> DepotSnapshot:
        mode = price_mode
        if mode is None:
            from settings import get_price_mode

            mode = get_price_mode()
        result, _loaded_at, _from_cache, _day = binance_data.load_portfolio(
            use_local_history=True,
            price_mode=mode,
            force_live=force_live,
        )
        return snapshot_from_portfolio_result(result)

    def load_transactions(self) -> list[Transaction]:
        purchases = binance_data.load_purchases_csv(with_deposits=True)
        sells = binance_data.load_sells_csv(with_withdrawals=True)
        return transactions_from_dataframes(purchases, sells)
