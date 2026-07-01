"""Neutrales Domänenmodell für alle Depots (Binance, Trade Republic, …).

Diese Datei ist bewusst frei von Provider-Importen und externen Abhängigkeiten.
Jeder Provider bildet seine Rohdaten auf diese Typen ab; die UI und die
Gesamt-Aggregation arbeiten ausschließlich mit diesen neutralen Typen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AssetClass(str, Enum):
    """Grobe Anlageklasse eines Instruments – steuert u. a. Steuer- und Preislogik."""

    CRYPTO = "crypto"
    STOCK = "stock"
    ETF = "etf"
    CASH = "cash"
    FIAT = "fiat"
    OTHER = "other"


class Capability(str, Enum):
    """Fähigkeiten, die ein Provider anbietet. Features werden hierüber freigeschaltet."""

    LIVE_PRICES = "live_prices"
    TRADE_HISTORY = "trade_history"
    AUTO_SYNC = "auto_sync"
    MANUAL_IMPORT = "manual_import"
    CRYPTO_HALTEFRIST = "crypto_haltefrist"
    ABGELTUNGSSTEUER = "abgeltungssteuer"
    PRICE_ZONES = "price_zones"


class TransactionKind(str, Enum):
    """Art einer Transaktion in der Depot-Historie."""

    BUY = "buy"
    SELL = "sell"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    DIVIDEND = "dividend"
    INTEREST = "interest"


@dataclass(frozen=True)
class Instrument:
    """Ein handelbares Objekt (Coin, Aktie, ETF, Fiat-Währung)."""

    symbol: str
    asset_class: AssetClass
    name: str = ""
    isin: str | None = None
    native_currency: str = "EUR"

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())


@dataclass(frozen=True)
class Holding:
    """Ein Bestand eines Instruments zu einem Zeitpunkt (neutraler Nachfolger von Position)."""

    instrument: Instrument
    quantity: float
    price_eur: float | None = None
    value_eur: float | None = None
    avg_entry_price_eur: float | None = None
    profit_loss_eur: float | None = None
    profit_loss_pct: float | None = None

    @property
    def entry_known(self) -> bool:
        return self.avg_entry_price_eur is not None


@dataclass(frozen=True)
class Transaction:
    """Ein einzelner Vorgang in der Depot-Historie (neutral über alle Provider)."""

    instrument: Instrument
    kind: TransactionKind
    quantity: float
    timestamp: datetime
    price_eur: float | None = None
    fee_eur: float | None = None
    external_id: str | None = None
    source: str = ""


@dataclass
class DepotSnapshot:
    """Zustand eines Depots: alle Bestände plus Gesamtwert zu einem Ladezeitpunkt."""

    depot_id: str
    provider_id: str
    holdings: list[Holding] = field(default_factory=list)
    total_value_eur: float = 0.0
    loaded_at: datetime | None = None
    ok: bool = True
    message: str = ""
