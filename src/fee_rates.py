"""Historische EUR-Kurse für Handelsgebühren (Binance Klines + Cache)."""

from __future__ import annotations

import json
from pathlib import Path

from binance.client import Client
from binance.exceptions import BinanceAPIException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEE_RATE_CACHE = PROJECT_ROOT / "data" / "fee_rate_cache.json"


class HistoricalRateCache:
    """Speichert asset→EUR-Kurse pro Minute (Persistenz in JSON)."""

    def __init__(self, path: Path = FEE_RATE_CACHE) -> None:
        self.path = path
        self._entries: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            self._entries = raw

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")

    def get(self, asset: str, minute_ms: int) -> tuple[float, str] | None:
        key = _cache_key(asset, minute_ms)
        entry = self._entries.get(key)
        if not entry:
            return None
        return float(entry["rate"]), str(entry["source"])

    def set(self, asset: str, minute_ms: int, rate: float, source: str) -> None:
        self._entries[_cache_key(asset, minute_ms)] = {"rate": rate, "source": source}


def _cache_key(asset: str, minute_ms: int) -> str:
    return f"{asset.upper()}@{minute_ms}"


def _minute_ms(time_ms: int) -> int:
    return (time_ms // 60_000) * 60_000


def _kline_close_near(
    client: Client,
    symbol: str,
    time_ms: int,
    *,
    invert: bool = False,
) -> float | None:
    """Schließungskurs einer 1m-Kerze um den Trade-Zeitpunkt."""
    try:
        klines = client.get_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_1MINUTE,
            startTime=max(0, time_ms - 120_000),
            endTime=time_ms + 120_000,
            limit=3,
        )
    except BinanceAPIException:
        return None
    if not klines:
        return None

    best_close: float | None = None
    best_delta = 10**18
    for candle in klines:
        open_time = int(candle[0])
        delta = abs(open_time - time_ms)
        if delta < best_delta:
            best_delta = delta
            best_close = float(candle[4])

    if best_close is None or best_close <= 0:
        return None
    if invert:
        return 1.0 / best_close
    return best_close


def _try_symbol_rate(
    client: Client,
    symbol: str,
    time_ms: int,
    *,
    invert: bool,
    valid_symbols: set[str],
) -> float | None:
    if symbol not in valid_symbols:
        return None
    return _kline_close_near(client, symbol, time_ms, invert=invert)


def asset_to_eur_at_time(
    client: Client,
    asset: str,
    time_ms: int,
    tickers: dict[str, float],
    cache: HistoricalRateCache,
    valid_symbols: set[str],
) -> tuple[float | None, str]:
    """
    EUR-Kurs für 1 Einheit asset zum Trade-Zeitpunkt.

    Returns:
        (rate, source) mit source in eur, kline, ticker, missing
    """
    asset = asset.strip().upper()
    if not asset:
        return None, "missing"
    if asset == "EUR":
        return 1.0, "eur"

    minute = _minute_ms(time_ms)
    cached = cache.get(asset, minute)
    if cached is not None:
        return cached

    if asset in {"USDT", "USDC"}:
        for symbol, invert in ((f"{asset}EUR", False), (f"EUR{asset}", True)):
            rate = _try_symbol_rate(client, symbol, time_ms, invert=invert, valid_symbols=valid_symbols)
            if rate is not None:
                cache.set(asset, minute, rate, "kline")
                return rate, "kline"

    direct = _try_symbol_rate(client, f"{asset}EUR", time_ms, invert=False, valid_symbols=valid_symbols)
    if direct is not None:
        cache.set(asset, minute, direct, "kline")
        return direct, "kline"

    inverse = _try_symbol_rate(client, f"EUR{asset}", time_ms, invert=True, valid_symbols=valid_symbols)
    if inverse is not None:
        cache.set(asset, minute, inverse, "kline")
        return inverse, "kline"

    via_usdt = _try_symbol_rate(client, f"{asset}USDT", time_ms, invert=False, valid_symbols=valid_symbols)
    if via_usdt is not None:
        usdt_eur, usdt_source = asset_to_eur_at_time(
            client, "USDT", time_ms, tickers, cache, valid_symbols
        )
        if usdt_eur is not None:
            rate = via_usdt * usdt_eur
            cache.set(asset, minute, rate, "kline")
            return rate, "kline"

    via_btc = _try_symbol_rate(client, f"{asset}BTC", time_ms, invert=False, valid_symbols=valid_symbols)
    if via_btc is not None:
        btc_eur, _ = asset_to_eur_at_time(client, "BTC", time_ms, tickers, cache, valid_symbols)
        if btc_eur is not None:
            rate = via_btc * btc_eur
            cache.set(asset, minute, rate, "kline")
            return rate, "kline"

    ticker_rate = _ticker_asset_to_eur(asset, tickers)
    if ticker_rate is not None:
        cache.set(asset, minute, ticker_rate, "ticker")
        return ticker_rate, "ticker"

    return None, "missing"


def _ticker_asset_to_eur(asset: str, tickers: dict[str, float]) -> float | None:
    if asset == "EUR":
        return 1.0
    direct = f"{asset}EUR"
    if direct in tickers and tickers[direct] > 0:
        return tickers[direct]
    inverse = f"EUR{asset}"
    if inverse in tickers and tickers[inverse] > 0:
        return 1.0 / tickers[inverse]
    via_usdt = f"{asset}USDT"
    if via_usdt in tickers and tickers[via_usdt] > 0:
        usdt_eur = _ticker_asset_to_eur("USDT", tickers)
        if usdt_eur is not None:
            return tickers[via_usdt] * usdt_eur
    return None


def trade_fee_eur_at_time(
    trade: dict,
    client: Client,
    tickers: dict[str, float],
    cache: HistoricalRateCache,
    valid_symbols: set[str],
) -> tuple[float | None, bool, str, float, str, float | None]:
    """
    Gebühr eines Binance-Trades in EUR zum Trade-Zeitpunkt.

    Returns:
        (gebuehr_eur, geschaetzt, quelle, commission, commission_asset, gebuehr_kurs_eur)
        quelle: eur | kline | ticker | none
    """
    commission = float(trade.get("commission", 0) or 0)
    commission_asset = str(trade.get("commissionAsset", "")).strip().upper()

    if commission <= 1e-12:
        return 0.0, False, "none", 0.0, commission_asset, None

    if not commission_asset:
        return None, True, "none", commission, commission_asset, None

    if commission_asset == "EUR":
        return commission, False, "eur", commission, commission_asset, 1.0

    time_ms = int(trade["time"])
    rate, source = asset_to_eur_at_time(
        client, commission_asset, time_ms, tickers, cache, valid_symbols
    )
    if rate is None:
        return None, True, "none", commission, commission_asset, None

    geschaetzt = source == "ticker"
    return commission * rate, geschaetzt, source, commission, commission_asset, rate


def trade_price_eur_at_time(
    trade: dict,
    meta: dict,
    client: Client,
    tickers: dict[str, float],
    cache: HistoricalRateCache,
    valid_symbols: set[str],
) -> tuple[float | None, bool, str]:
    """
    Stückpreis eines Trades in EUR zum Trade-Zeitpunkt.

    Returns:
        (preis_eur, geschaetzt, quelle)
    """
    quote_asset = str(meta.get("quoteAsset", "")).strip().upper()
    time_ms = int(trade["time"])
    trade_price = float(trade["price"])

    if quote_asset == "EUR":
        return trade_price, False, "eur"

    symbol = str(trade.get("symbol", ""))
    if symbol and symbol in valid_symbols:
        kline_price = _kline_close_near(client, symbol, time_ms)
        if kline_price is not None and kline_price > 0:
            return kline_price, False, "kline"

    quote_rate, source = asset_to_eur_at_time(
        client, quote_asset, time_ms, tickers, cache, valid_symbols
    )
    if quote_rate is None:
        return None, True, "missing"

    geschaetzt = source == "ticker"
    return trade_price * quote_rate, geschaetzt, source


def valid_symbols_from_exchange(exchange_info: dict) -> set[str]:
    return {entry["symbol"] for entry in exchange_info.get("symbols", [])}
