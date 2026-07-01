"""All-Time-High (Spot) je Coin in EUR – Binance-Tageskerzen."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ATH_CACHE_FILE = DATA_DIR / "ath_price_cache.json"
USDT_EUR_RATES_FILE = DATA_DIR / "ath_usdt_eur_daily.json"

CASH_LIKE = frozenset({"EUR", "USD", "USDT", "USDC", "BUSD", "FDUSD"})
CACHE_MAX_AGE_DAYS = 7
REQUEST_TIMEOUT_SEC = 45
MAX_KLINE_RETRIES = 3

_NETWORK_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ReadTimeout,
    requests.exceptions.ChunkedEncodingError,
)


class AthFetchError(Exception):
    """Binance nicht erreichbar oder ATH-Abruf abgebrochen."""


@dataclass(frozen=True)
class AthResult:
    """ATH-Abfrage für ein oder mehrere Coins."""

    prices_eur: dict[str, float]
    missing: tuple[str, ...]
    from_cache: bool
    stale_cache: bool
    message: str
    errors: tuple[str, ...]


def _ms_to_day(open_ms: int) -> date:
    return datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).date()


def _make_client(client: Client | None) -> Client:
    if client is not None:
        if getattr(client, "requests_params", None) is None:
            client.requests_params = {"timeout": REQUEST_TIMEOUT_SEC}
        return client
    return Client("", "", requests_params={"timeout": REQUEST_TIMEOUT_SEC})


def _get_klines_with_retry(
    client: Client,
    symbol: str,
    start_time: int,
) -> list:
    last_error: Exception | None = None
    for attempt in range(MAX_KLINE_RETRIES):
        try:
            return client.get_klines(
                symbol=symbol,
                interval=Client.KLINE_INTERVAL_1DAY,
                startTime=start_time,
                limit=1000,
            )
        except BinanceAPIException:
            raise
        except _NETWORK_ERRORS as exc:
            last_error = exc
            if attempt + 1 < MAX_KLINE_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    if last_error is not None:
        raise AthFetchError(
            f"Binance-Zeitüberschreitung bei {symbol}. "
            "Bitte Internet prüfen und erneut versuchen."
        ) from last_error
    return []


def _scan_klines_max_eur(
    client: Client,
    symbol: str,
    *,
    direct_high: bool,
    usdt_eur: dict[date, float] | None = None,
) -> float | None:
    """
    Höchster EUR-Kurs aus Tageskerzen.

    direct_high=True  → Kerzen-Hoch in Quote (bereits EUR bei BTCEUR)
    direct_high=False → 1 / Kerzen-Tief (für EUR{asset}-Paare)
    usdt_eur gesetzt  → Hoch in USDT × Tages-USDT/EUR
    """
    max_price = 0.0
    found = False
    cursor = 0
    while True:
        try:
            klines = _get_klines_with_retry(client, symbol, cursor)
        except BinanceAPIException:
            break
        if not klines:
            break

        for candle in klines:
            if direct_high and usdt_eur is None:
                price = float(candle[2])
            elif not direct_high:
                low = float(candle[3])
                price = (1.0 / low) if low > 0 else 0.0
            else:
                high = float(candle[2])
                eur_rate = (usdt_eur or {}).get(_ms_to_day(int(candle[0])))
                price = high * eur_rate if eur_rate and high > 0 else 0.0

            if price > max_price:
                max_price = price
                found = True

        last_open = int(klines[-1][0])
        next_cursor = last_open + 86_400_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return max_price if found else None


def _load_usdt_eur_rates() -> dict[date, float]:
    if not USDT_EUR_RATES_FILE.exists():
        return {}
    try:
        raw = json.loads(USDT_EUR_RATES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    rates_raw = raw.get("rates") if isinstance(raw, dict) else raw
    if not isinstance(rates_raw, dict):
        return {}
    rates: dict[date, float] = {}
    for day_str, value in rates_raw.items():
        try:
            rates[date.fromisoformat(str(day_str))] = float(value)
        except (TypeError, ValueError):
            continue
    return rates


def _save_usdt_eur_rates(rates: dict[date, float]) -> None:
    USDT_EUR_RATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": date.today().isoformat(),
        "rates": {day.isoformat(): rate for day, rate in sorted(rates.items())},
    }
    USDT_EUR_RATES_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _fetch_usdt_eur_by_day(client: Client) -> dict[date, float]:
    """Historische USDT→EUR-Raten (Tages-Schluss) für USDT-Paare."""
    cached = _load_usdt_eur_rates()
    if cached:
        return cached

    rates: dict[date, float] = {}
    rates.update(_scan_klines_for_eur_days(client, "USDTEUR", direct_high=True))
    if not rates:
        inverse = _scan_klines_for_eur_days(client, "EURUSDT", direct_high=False)
        rates.update({day: 1.0 / price for day, price in inverse.items() if price > 0})

    if rates:
        _save_usdt_eur_rates(rates)
    return rates


def _scan_klines_for_eur_days(
    client: Client,
    symbol: str,
    *,
    direct_high: bool,
) -> dict[date, float]:
    """Wie _scan_klines_max_eur, aber mit Tages-Mapping (nur für USDT/EUR-Cache)."""
    by_day: dict[date, float] = {}
    cursor = 0
    while True:
        try:
            klines = _get_klines_with_retry(client, symbol, cursor)
        except BinanceAPIException:
            break
        if not klines:
            break

        for candle in klines:
            day = _ms_to_day(int(candle[0]))
            if direct_high:
                price = float(candle[2])
            else:
                low = float(candle[3])
                price = (1.0 / low) if low > 0 else 0.0
            if price > 0:
                by_day[day] = max(by_day.get(day, 0.0), price)

        last_open = int(klines[-1][0])
        next_cursor = last_open + 86_400_000
        if next_cursor <= cursor:
            break
        cursor = next_cursor

    return by_day


def ath_eur_for_coin(client: Client, coin: str, usdt_eur: dict[date, float]) -> float | None:
    """ATH in EUR für einen Coin (Binance Spot, Tages-Hoch)."""
    asset = coin.strip().upper()
    if asset == "EUR":
        return 1.0
    if asset in CASH_LIKE - {"EUR"}:
        if usdt_eur:
            peak = max(usdt_eur.values())
            return peak if peak > 0 else 1.0
        return 1.0

    for symbol, direct_high, usdt in (
        (f"{asset}EUR", True, None),
        (f"EUR{asset}", False, None),
        (f"{asset}USDT", True, usdt_eur),
        (f"{asset}USDC", True, usdt_eur),
    ):
        peak = _scan_klines_max_eur(
            client,
            symbol,
            direct_high=direct_high,
            usdt_eur=usdt,
        )
        if peak is not None:
            return peak
    return None


def _load_cache() -> dict[str, dict[str, object]]:
    if not ATH_CACHE_FILE.exists():
        return {}
    try:
        raw = json.loads(ATH_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(entries: dict[str, dict[str, object]]) -> None:
    ATH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ATH_CACHE_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _cache_entry_valid(cached: object, *, refresh: bool) -> bool:
    if refresh or not isinstance(cached, dict):
        return False
    day_str = cached.get("day")
    price = cached.get("price_eur")
    if not isinstance(day_str, str) or not isinstance(price, (int, float)):
        return False
    if float(price) <= 0:
        return False
    try:
        cached_day = date.fromisoformat(day_str)
    except ValueError:
        return False
    return (date.today() - cached_day).days <= CACHE_MAX_AGE_DAYS


def _cached_price(cached: object) -> float | None:
    if isinstance(cached, dict) and isinstance(cached.get("price_eur"), (int, float)):
        value = float(cached["price_eur"])
        return value if value > 0 else None
    return None


def change_pct_to_reach_target(current_eur: float | None, target_eur: float | None) -> float | None:
    """Prozentuale Kursänderung von current → target."""
    if current_eur is None or target_eur is None or current_eur <= 0 or target_eur <= 0:
        return None
    return (target_eur / current_eur - 1.0) * 100.0


def fetch_ath_prices_eur(
    coins: list[str],
    *,
    client: Client | None = None,
    use_cache: bool = True,
    refresh: bool = False,
) -> AthResult:
    """
    ATH in EUR für alle angegebenen Coins.

    Nutzt Tages-Hoch aus Binance-Kerzen (EUR-, USDT- oder inverse EUR-Paare).
    """
    api_client = _make_client(client)
    cache = _load_cache()
    prices: dict[str, float] = {}
    missing: list[str] = []
    errors: list[str] = []
    from_cache_only = True
    stale_cache = False

    usdt_eur: dict[date, float] | None = None

    for coin in sorted({c.strip().upper() for c in coins if c.strip()}):
        cached = cache.get(coin)
        if use_cache and _cache_entry_valid(cached, refresh=refresh):
            price = _cached_price(cached)
            if price is not None:
                prices[coin] = price
                continue

        from_cache_only = False
        try:
            if usdt_eur is None:
                usdt_eur = _fetch_usdt_eur_by_day(api_client)

            ath = ath_eur_for_coin(api_client, coin, usdt_eur)

            if ath is None or ath <= 0:
                stale = _cached_price(cached)
                if stale is not None:
                    prices[coin] = stale
                    stale_cache = True
                    errors.append(f"{coin}: kein frischer ATH-Wert – Cache genutzt")
                else:
                    missing.append(coin)
                continue

            prices[coin] = ath
            cache[coin] = {"price_eur": ath, "day": date.today().isoformat()}
        except AthFetchError:
            raise
        except _NETWORK_ERRORS as exc:
            stale = _cached_price(cached)
            if stale is not None:
                prices[coin] = stale
                stale_cache = True
                errors.append(f"{coin}: Netzwerk – Cache genutzt")
            else:
                missing.append(coin)
                errors.append(f"{coin}: {type(exc).__name__}")
        except BinanceAPIException as exc:
            missing.append(coin)
            errors.append(f"{coin}: Binance {exc.message}")

    if not from_cache_only:
        _save_cache(cache)

    if prices and not missing and not errors:
        msg = f"ATH für {len(prices)} Coin(s) geladen."
    elif prices:
        parts = [f"ATH für {len(prices)} Coin(s)"]
        if missing:
            parts.append(f"ohne Daten: {', '.join(missing)}")
        if stale_cache:
            parts.append("teilweise aus Cache")
        msg = "; ".join(parts) + "."
    else:
        msg = "Keine ATH-Kurse ermittelt. Bitte erneut versuchen."

    return AthResult(
        prices_eur=prices,
        missing=tuple(missing),
        from_cache=from_cache_only and bool(prices),
        stale_cache=stale_cache,
        message=msg,
        errors=tuple(errors),
    )


def ath_changes_pct_for_positions(
    positions,
    ath_prices_eur: dict[str, float],
) -> dict[str, float]:
    """Kursänderung % je Coin, um das jeweilige ATH zu erreichen."""
    changes: dict[str, float] = {}
    for pos in positions:
        target = ath_prices_eur.get(pos.coin)
        pct = change_pct_to_reach_target(pos.current_price_eur, target)
        if pct is not None:
            changes[pos.coin] = pct
    return changes
