"""Historische Depot-Entwicklung: Bestände aus CSV-Events × Tageskurse."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

from binance_data import KAUF_CSV, VERKAUF_CSV, load_purchases_csv, load_sells_csv
from inflows import (
    CAPITAL_FLOW_RANGE_OPTIONS,
    LOCAL_TZ,
    TYP_CRYPTO,
    TYP_CRYPTO_AUS,
    TYP_FIAT,
    TYP_FIAT_AUS,
    TYP_FIAT_KARTE,
    ZUFLUSS_CSV,
    _capital_flow_range_cutoff,
    load_zufluesse_csv,
    to_altair_naive_local,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRICE_CACHE_FILE = DATA_DIR / "portfolio_price_cache.json"

CASH_LIKE = frozenset({"EUR", "USDT", "USDC", "BUSD", "FDUSD", "USD"})
STABLE_FOR_EUR = frozenset({"USDT", "USDC", "BUSD", "FDUSD"})

PriceLookup = Callable[[str, date], float | None]


@dataclass(frozen=True)
class PortfolioHistoryView:
    """Daten für den Depot-Entwicklungs-Chart."""

    line_df: pd.DataFrame
    x_min: pd.Timestamp
    x_max: pd.Timestamp
    has_capital: bool
    missing_price_days: int


class DailyPriceCache:
    """Tages-Schlusskurse in EUR (Persistenz in JSON)."""

    def __init__(self, path: Path = PRICE_CACHE_FILE) -> None:
        self.path = path
        self._entries: dict[str, float] = {}
        self._dirty = False
        self._load()

    @staticmethod
    def _key(asset: str, day: date) -> str:
        return f"{asset.upper()}@{day.isoformat()}"

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(raw, dict):
            self._entries = {str(k): float(v) for k, v in raw.items()}

    def get(self, asset: str, day: date) -> float | None:
        value = self._entries.get(self._key(asset, day))
        return float(value) if value is not None else None

    def set(self, asset: str, day: date, rate: float) -> None:
        key = self._key(asset, day)
        value = float(rate)
        if self._entries.get(key) != value:
            self._entries[key] = value
            self._dirty = True

    def save(self) -> None:
        if not getattr(self, "_dirty", False):
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._entries, indent=2), encoding="utf-8")
        self._dirty = False


def _parse_event_time(value: object) -> pd.Timestamp:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return pd.Timestamp(dt).tz_convert(LOCAL_TZ)


def _float_or_none(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: object) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


def _parse_zufluss_coin(coin_field: object) -> str:
    coin = str(coin_field).strip().upper()
    if "→" in coin:
        return coin.split("→")[-1].strip() or coin.split("→")[0].strip()
    return coin


def _apply_fee_delta(deltas: dict[str, float], row: pd.Series) -> None:
    commission = _float_or_zero(row.get("commission"))
    asset = str(row.get("commission_asset", "")).strip().upper()
    if commission > 0 and asset:
        deltas[asset] = deltas.get(asset, 0.0) - commission


def collect_balance_events() -> list[tuple[pd.Timestamp, dict[str, float]]]:
    """Saldenändernde Events aus Käufen, Verkäufen und Kapitalflüssen."""
    events: list[tuple[pd.Timestamp, dict[str, float]]] = []

    purchases = load_purchases_csv()
    if not purchases.empty:
        for _, row in purchases.iterrows():
            coin = str(row["coin"]).strip().upper()
            menge = _float_or_zero(row["menge"])
            if menge <= 0:
                continue
            price = _float_or_none(row.get("kaufpreis_eur"))
            deltas: dict[str, float] = {coin: menge}
            if price is not None:
                deltas["EUR"] = deltas.get("EUR", 0.0) - menge * price
            _apply_fee_delta(deltas, row)
            events.append((_parse_event_time(row["datum"]), deltas))

    sells = load_sells_csv()
    if not sells.empty:
        for _, row in sells.iterrows():
            coin = str(row["coin"]).strip().upper()
            menge = _float_or_zero(row["menge"])
            if menge <= 0:
                continue
            price = _float_or_none(row.get("verkaufspreis_eur"))
            deltas = {coin: -menge}
            if price is not None:
                deltas["EUR"] = deltas.get("EUR", 0.0) + menge * price
            _apply_fee_delta(deltas, row)
            events.append((_parse_event_time(row["datum"]), deltas))

    flows = load_zufluesse_csv()
    if not flows.empty:
        for _, row in flows.iterrows():
            typ = str(row["typ"])
            wert_eur = abs(_float_or_zero(row.get("wert_eur")))
            menge = _float_or_zero(row.get("menge"))
            deltas: dict[str, float] = {}

            if typ in {TYP_FIAT, TYP_FIAT_KARTE}:
                deltas["EUR"] = wert_eur
            elif typ == TYP_FIAT_AUS:
                deltas["EUR"] = -wert_eur
            elif typ == TYP_CRYPTO:
                coin = _parse_zufluss_coin(row.get("coin"))
                if menge > 0:
                    deltas[coin] = menge
            elif typ == TYP_CRYPTO_AUS:
                coin = _parse_zufluss_coin(row.get("coin"))
                if menge > 0:
                    deltas[coin] = -menge
            else:
                continue

            if deltas:
                events.append((_parse_event_time(row["datum"]), deltas))

    events.sort(key=lambda item: item[0])
    return events


def _end_of_local_day(day: date) -> pd.Timestamp:
    return pd.Timestamp(day, tz=LOCAL_TZ) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)


def _day_from_timestamp(ts: pd.Timestamp) -> date:
    return ts.tz_convert(LOCAL_TZ).date()


def _merge_holdings(holdings: dict[str, float], deltas: dict[str, float]) -> None:
    for asset, delta in deltas.items():
        if abs(delta) <= 1e-15:
            continue
        holdings[asset] = holdings.get(asset, 0.0) + delta
        if abs(holdings[asset]) <= 1e-12:
            del holdings[asset]


def _positive_holdings(holdings: dict[str, float]) -> dict[str, float]:
    return {asset: qty for asset, qty in holdings.items() if qty > 1e-12}


def _assets_needed(events: list[tuple[pd.Timestamp, dict[str, float]]]) -> set[str]:
    assets: set[str] = set()
    for _, deltas in events:
        assets.update(deltas.keys())
    assets.discard("EUR")
    return assets


def _ms_for_day(day: date) -> tuple[int, int]:
    start = pd.Timestamp(day, tz=timezone.utc)
    end = start + pd.Timedelta(days=1) - pd.Timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _fetch_daily_closes_range(
    client: Client,
    symbol: str,
    start_day: date,
    end_day: date,
) -> dict[date, float]:
    start_ms = int(pd.Timestamp(start_day, tz=timezone.utc).timestamp() * 1000)
    end_ms = int(
        (pd.Timestamp(end_day, tz=timezone.utc) + pd.Timedelta(days=1)).timestamp() * 1000
    ) - 1
    closes: dict[date, float] = {}
    cursor = start_ms
    while cursor <= end_ms:
        try:
            klines = client.get_klines(
                symbol=symbol,
                interval=Client.KLINE_INTERVAL_1DAY,
                startTime=cursor,
                endTime=end_ms,
                limit=1000,
            )
        except BinanceAPIException:
            break
        if not klines:
            break
        for candle in klines:
            day = datetime.fromtimestamp(int(candle[0]) / 1000, tz=timezone.utc).date()
            close = float(candle[4])
            if close > 0:
                closes[day] = close
        last_open = int(klines[-1][0])
        next_cursor = last_open + 86_400_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    return closes


def sources_fingerprint() -> str:
    """Änderungs-Stempel für Cache-Invalidierung (CSV + Kurs-Cache + heute)."""
    parts: list[str] = []
    for path in (KAUF_CSV, VERKAUF_CSV, ZUFLUSS_CSV, PRICE_CACHE_FILE):
        parts.append(str(path.stat().st_mtime_ns) if path.exists() else "0")
    parts.append(date.today().isoformat())
    return "|".join(parts)


def _date_range_simple(start_day: date, end_day: date) -> list[date]:
    return [ts.date() for ts in pd.date_range(start_day, end_day, freq="D")]


def _store_closes_in_cache(
    cache: DailyPriceCache,
    asset: str,
    closes: dict[date, float],
    *,
    scale_by: dict[date, float] | None = None,
) -> None:
    asset = asset.upper()
    for day, close in closes.items():
        if close <= 0:
            continue
        if cache.get(asset, day) is not None:
            continue
        rate = close
        if scale_by is not None:
            factor = scale_by.get(day)
            if factor is None:
                continue
            rate = close * factor
        cache.set(asset, day, rate)


def _usdt_eur_rates_for_range(
    client: Client,
    cache: DailyPriceCache,
    start_day: date,
    end_day: date,
) -> dict[date, float]:
    """USDT→EUR für alle Tage im Fenster (Cache + ein Bulk-Abruf)."""
    rates: dict[date, float] = {}
    for day in _date_range_simple(start_day, end_day):
        cached = cache.get("USDT", day)
        if cached is not None:
            rates[day] = cached

    direct = _fetch_daily_closes_range(client, "USDTEUR", start_day, end_day)
    _store_closes_in_cache(cache, "USDT", direct)
    rates.update(direct)

    missing = [day for day in _date_range_simple(start_day, end_day) if day not in rates]
    if missing:
        inverse = _fetch_daily_closes_range(client, "EURUSDT", start_day, end_day)
        for day, inv in inverse.items():
            if inv > 0 and day not in rates:
                rate = 1.0 / inv
                cache.set("USDT", day, rate)
                rates[day] = rate

    return rates


def _asset_fully_cached(
    cache: DailyPriceCache,
    asset: str,
    start_day: date,
    end_day: date,
) -> bool:
    asset = asset.upper()
    for day in _date_range_simple(start_day, end_day):
        if cache.get(asset, day) is None:
            return False
    return True


def prefetch_asset_prices(
    assets: set[str],
    start_day: date,
    end_day: date,
    *,
    client: Client | None = None,
    cache: DailyPriceCache | None = None,
) -> None:
    """Lädt fehlende Tageskurse gebündelt (EUR-Paar oder USDT×EUR), nicht tageweise."""
    api_client = client or Client("", "")
    price_cache = cache or DailyPriceCache()
    usdt_eur = _usdt_eur_rates_for_range(api_client, price_cache, start_day, end_day)

    for stable in STABLE_FOR_EUR | {"USD"}:
        for day, rate in usdt_eur.items():
            if price_cache.get(stable, day) is None:
                price_cache.set(stable, day, rate)

    for asset in sorted({a.upper() for a in assets if a.upper() not in {"EUR"}}):
        if _asset_fully_cached(price_cache, asset, start_day, end_day):
            continue

        direct = _fetch_daily_closes_range(api_client, f"{asset}EUR", start_day, end_day)
        if direct:
            _store_closes_in_cache(price_cache, asset, direct)
            if _asset_fully_cached(price_cache, asset, start_day, end_day):
                continue

        inverse = _fetch_daily_closes_range(api_client, f"EUR{asset}", start_day, end_day)
        if inverse:
            inv_eur = {
                day: (1.0 / price if price > 0 else 0.0) for day, price in inverse.items()
            }
            _store_closes_in_cache(price_cache, asset, inv_eur)
            if _asset_fully_cached(price_cache, asset, start_day, end_day):
                continue

        usdt_closes = _fetch_daily_closes_range(api_client, f"{asset}USDT", start_day, end_day)
        if usdt_closes:
            _store_closes_in_cache(price_cache, asset, usdt_closes, scale_by=usdt_eur)

    price_cache.save()


def build_price_lookup(
    assets: set[str],
    start_day: date,
    end_day: date,
    *,
    client: Client | None = None,
    cache: DailyPriceCache | None = None,
    live_tickers: dict[str, float] | None = None,
) -> PriceLookup:
    """Lookup nach Bulk-Prefetch – im Tages-Loop nur noch Cache-Lesen."""
    price_cache = cache or DailyPriceCache()
    prefetch_asset_prices(
        assets,
        start_day,
        end_day,
        client=client,
        cache=price_cache,
    )
    today = date.today()

    def lookup(asset: str, day: date) -> float | None:
        asset = asset.upper()
        if asset == "EUR":
            return 1.0
        if day == today and live_tickers:
            from binance_data import asset_price_in_eur_detailed

            price, _source = asset_price_in_eur_detailed(asset, live_tickers)
            if price is not None:
                price_cache.set(asset, day, price)
                price_cache.save()
                return price
        return price_cache.get(asset, day)

    return lookup


def value_holdings_eur(
    holdings: dict[str, float],
    day: date,
    price_lookup: PriceLookup,
) -> tuple[float, bool]:
    """Bewertet Bestände in EUR. Zweiter Wert: True wenn ein Kurs fehlte."""
    total = 0.0
    missing = False
    for asset, qty in _positive_holdings(holdings).items():
        if asset == "EUR":
            total += qty
            continue
        price = price_lookup(asset, day)
        if price is None:
            missing = True
            continue
        total += qty * price
    return total, missing


def build_portfolio_timeseries(
    *,
    price_lookup: PriceLookup | None = None,
    today_value_override: float | None = None,
    now: pd.Timestamp | None = None,
    client: Client | None = None,
    live_tickers: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    Tägliche Depotwerte aus CSV-Events.

    Spalten: tag, zeit, depotwert_eur, missing_price
    """
    events = collect_balance_events()
    if not events:
        return pd.DataFrame(columns=["tag", "zeit", "depotwert_eur", "missing_price"])

    now_ts = now or pd.Timestamp.now(tz=LOCAL_TZ)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize(LOCAL_TZ)
    else:
        now_ts = now_ts.tz_convert(LOCAL_TZ)

    start_day = _day_from_timestamp(events[0][0])
    end_day = now_ts.date()
    assets = _assets_needed(events)

    cache = DailyPriceCache()
    lookup = price_lookup or build_price_lookup(
        assets,
        start_day,
        end_day,
        client=client,
        cache=cache,
        live_tickers=live_tickers,
    )

    rows: list[dict[str, object]] = []
    holdings: dict[str, float] = defaultdict(float)
    event_idx = 0
    missing_days = 0

    for day in _date_range_simple(start_day, end_day):
        eod = _end_of_local_day(day)
        if eod > now_ts:
            eod = now_ts
        while event_idx < len(events) and events[event_idx][0] <= eod:
            _merge_holdings(holdings, events[event_idx][1])
            event_idx += 1

        value, missing = value_holdings_eur(dict(holdings), day, lookup)
        if missing:
            missing_days += 1
        rows.append(
            {
                "tag": day,
                "zeit": eod,
                "depotwert_eur": value,
                "missing_price": missing,
            }
        )

    out = pd.DataFrame(rows)
    if today_value_override is not None and not out.empty:
        out.loc[out.index[-1], "depotwert_eur"] = float(today_value_override)
        out.loc[out.index[-1], "missing_price"] = False
    return out


def align_capital_to_portfolio(
    portfolio_df: pd.DataFrame,
    capital_series: pd.DataFrame,
) -> pd.Series:
    """Ordnet Netto-Kapital jedem Portfolio-Zeitpunkt zu (step function)."""
    if portfolio_df.empty:
        return pd.Series(dtype=float)
    if capital_series.empty:
        return pd.Series(0.0, index=portfolio_df.index)

    targets = portfolio_df[["zeit"]].sort_values("zeit").copy()
    flows = capital_series[["zeit", "kapital_netto"]].sort_values("zeit").copy()
    merged = pd.merge_asof(targets, flows, on="zeit", direction="backward")
    return merged["kapital_netto"].fillna(0.0)


def prepare_portfolio_chart_data(
    portfolio_df: pd.DataFrame,
    capital_series: pd.DataFrame,
    range_key: str,
    *,
    now: pd.Timestamp | None = None,
) -> PortfolioHistoryView:
    """Filtert auf Zeitraum und reichert um Kapital/Performance an."""
    empty = pd.DataFrame(
        columns=["zeit", "depotwert_eur", "kapital_netto", "performance_eur", "y0", "y1", "gewinn"]
    )
    now_ts = now or pd.Timestamp.now(tz=LOCAL_TZ)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize(LOCAL_TZ)
    else:
        now_ts = now_ts.tz_convert(LOCAL_TZ)

    if portfolio_df.empty:
        return PortfolioHistoryView(empty, now_ts, now_ts, False, 0)

    series = portfolio_df.sort_values("zeit").reset_index(drop=True)
    cutoff = _capital_flow_range_cutoff(range_key, now_ts)
    has_capital = not capital_series.empty

    if cutoff is None:
        x_min = series["zeit"].iloc[0] - pd.Timedelta(hours=12)
        x_max = now_ts
        visible = series.copy()
    else:
        x_min = cutoff
        x_max = now_ts
        visible = series[series["zeit"] >= cutoff].copy()
        if visible.empty:
            before = series[series["zeit"] < cutoff]
            if not before.empty:
                anchor_row = before.iloc[-1].copy()
                anchor_row["zeit"] = x_min
                visible = pd.DataFrame([anchor_row])

    capital = align_capital_to_portfolio(visible, capital_series)
    line_df = visible.copy()
    line_df["kapital_netto"] = capital.values
    line_df["performance_eur"] = line_df["depotwert_eur"] - line_df["kapital_netto"]
    line_df["y0"] = line_df[["depotwert_eur", "kapital_netto"]].min(axis=1)
    line_df["y1"] = line_df[["depotwert_eur", "kapital_netto"]].max(axis=1)
    line_df["gewinn"] = line_df["depotwert_eur"] >= line_df["kapital_netto"]
    line_df["zeit"] = line_df["zeit"].map(to_altair_naive_local)

    missing_days = int(visible["missing_price"].sum()) if "missing_price" in visible.columns else 0
    return PortfolioHistoryView(
        line_df=line_df.reset_index(drop=True),
        x_min=to_altair_naive_local(x_min),
        x_max=to_altair_naive_local(x_max),
        has_capital=has_capital,
        missing_price_days=missing_days,
    )


__all__ = [
    "CAPITAL_FLOW_RANGE_OPTIONS",
    "DailyPriceCache",
    "PortfolioHistoryView",
    "align_capital_to_portfolio",
    "build_portfolio_timeseries",
    "build_price_lookup",
    "collect_balance_events",
    "prefetch_asset_prices",
    "prepare_portfolio_chart_data",
    "sources_fingerprint",
    "value_holdings_eur",
]
