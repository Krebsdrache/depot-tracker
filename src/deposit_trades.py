"""Krypto-Deposits/Withdrawals und Kartenkäufe aus zufluesse.csv für FIFO."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from binance_data import KAUF_COLUMNS, VERKAUF_COLUMNS
from fee_rates import HistoricalRateCache, _minute_ms
from inflows import TYP_CRYPTO, TYP_CRYPTO_AUS, TYP_FIAT_KARTE, load_zufluesse_csv
from portfolio_history import DailyPriceCache

DEPOSIT_PREIS_QUELLE = "krypto_deposit"
WITHDRAW_PREIS_QUELLE = "krypto_withdraw"
CARD_PREIS_QUELLE = "fiat_karte"

_CARD_MATCH_WINDOW_SEC = 900
_CARD_VALUE_TOLERANCE = 0.15
_CARD_MIN_TOLERANCE_EUR = 2.0
_STABLE_EUR_PEG = frozenset({"USDC", "USDT", "USD", "BUSD", "FDUSD"})
# Kein FIFO aus Kartenkauf – Zwischenstopp oder Fee-Asset (sonst Überzählung).
_CARD_SKIP_TARGETS = _STABLE_EUR_PEG | frozenset({"EUR", "BNB"})
_CARD_DAY_LOOKUP_RADIUS = 30


def _synthetic_trade_id(kind: str, coin: str, datum: str, menge: float) -> int:
    """Stabile negative ID (unterscheidbar von Binance trade_id)."""
    raw = f"{kind}|{coin}|{datum}|{menge:.12f}"
    return -(abs(hash(raw)) % 900_000_000 + 100_000_000)


def _price_eur_per_unit(wert_eur: float, menge: float) -> float | None:
    if menge <= 1e-12 or wert_eur <= 1e-12:
        return None
    return wert_eur / menge


def _parse_iso_datetime(datum: str) -> datetime:
    dt = datetime.fromisoformat(str(datum).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_card_target_coin(coin_field: str) -> str | None:
    """EUR→ARKM → ARKM; reines Fiat ohne Pfeil wird ignoriert."""
    raw = str(coin_field).strip().upper()
    if "→" not in raw:
        return None
    target = raw.split("→", 1)[1].strip()
    return target or None


def _lookup_coin_eur_offline(
    coin: str,
    datum: str,
    *,
    fee_cache: HistoricalRateCache | None = None,
    daily_cache: DailyPriceCache | None = None,
) -> tuple[float | None, str, bool]:
    """
    EUR-Kurs pro Coin-Einheit (offline: Minuten-Cache, Tages-Cache, Stable-Peg).

    Returns:
        (preis_eur, quelle, geschaetzt)
    """
    coin = coin.strip().upper()
    if coin == "EUR":
        return 1.0, "eur", False

    if coin in _STABLE_EUR_PEG:
        return 1.0, "stable", True

    time_ms = int(_parse_iso_datetime(datum).timestamp() * 1000)
    cache = fee_cache or HistoricalRateCache()
    cached = cache.get(coin, _minute_ms(time_ms))
    if cached is not None and cached[0] > 0:
        geschaetzt = cached[1] == "ticker"
        return cached[0], cached[1], geschaetzt

    day = _parse_iso_datetime(datum).date()
    daily = daily_cache or DailyPriceCache()
    day_rate = daily.get(coin, day)
    if day_rate is not None and day_rate > 0:
        return day_rate, "tageskurs", True

    for offset in range(1, _CARD_DAY_LOOKUP_RADIUS + 1):
        for candidate in (day - timedelta(days=offset), day + timedelta(days=offset)):
            rate = daily.get(coin, candidate)
            if rate is not None and rate > 0:
                return rate, "tageskurs", True

    return None, "missing", True


def _spot_buy_covers_card(
    spot_purchases: pd.DataFrame,
    coin: str,
    datum: str,
    wert_eur: float,
) -> bool:
    """True, wenn kaeufe.csv bereits einen passenden Spot-Kauf hat."""
    if spot_purchases.empty:
        return False

    card_ts = _parse_iso_datetime(datum).timestamp()
    coin_buys = spot_purchases[spot_purchases["coin"].astype(str).str.upper() == coin.upper()]
    for _, row in coin_buys.iterrows():
        quelle = str(row.get("preis_quelle", ""))
        if quelle in {CARD_PREIS_QUELLE, DEPOSIT_PREIS_QUELLE}:
            continue
        try:
            trade_id = int(row.get("trade_id", 0))
        except (TypeError, ValueError):
            trade_id = 0
        if trade_id < 0:
            continue

        buy_ts = _parse_iso_datetime(str(row["datum"])).timestamp()
        if abs(buy_ts - card_ts) > _CARD_MATCH_WINDOW_SEC:
            continue

        kaufpreis = float(row["kaufpreis_eur"])
        menge = float(row["menge"])
        if kaufpreis <= 0 or menge <= 0:
            continue
        buy_eur = menge * kaufpreis
        tolerance = max(_CARD_MIN_TOLERANCE_EUR, abs(wert_eur) * _CARD_VALUE_TOLERANCE)
        if abs(buy_eur - wert_eur) <= tolerance:
            return True
    return False


def _row_is_legacy_fiat_menge(menge: float, wert_eur: float) -> bool:
    """Alte zufluesse.csv: menge = Fiat-Betrag (≈ wert_eur), nicht Crypto-Menge."""
    if menge <= 0 or wert_eur <= 0:
        return True
    return abs(menge - wert_eur) <= max(0.05, wert_eur * 0.02)


def fiat_card_as_purchases(
    zufluesse: pd.DataFrame | None = None,
    *,
    spot_purchases: pd.DataFrame | None = None,
    fee_cache: HistoricalRateCache | None = None,
    daily_cache: DailyPriceCache | None = None,
) -> pd.DataFrame:
    """
    Kartenkäufe (fiat_karte) → synthetische Käufe, wenn kein Spot-Trade in kaeufe.csv.

    Menge = ausgegebene EUR / Kurs am Kaufdatum (Kline- oder Tages-Cache).
    """
    df = zufluesse if zufluesse is not None else load_zufluesse_csv()
    if df.empty:
        return pd.DataFrame(columns=KAUF_COLUMNS)

    spot = spot_purchases if spot_purchases is not None else pd.DataFrame(columns=KAUF_COLUMNS)
    rows: list[dict[str, object]] = []

    for _, row in df[df["typ"].astype(str) == TYP_FIAT_KARTE].iterrows():
        target = _parse_card_target_coin(str(row["coin"]))
        if target is None or target in _CARD_SKIP_TARGETS:
            continue

        wert_eur = abs(float(row["wert_eur"]))
        if wert_eur <= 1e-12:
            continue

        datum = str(row["datum"])
        if _spot_buy_covers_card(spot, target, datum, wert_eur):
            continue

        row_menge = float(row["menge"])
        if not _row_is_legacy_fiat_menge(row_menge, wert_eur):
            kaufpreis = wert_eur / row_menge
            menge = row_menge
            geschaetzt = False
        else:
            kaufpreis, _quelle, geschaetzt = _lookup_coin_eur_offline(
                target,
                datum,
                fee_cache=fee_cache,
                daily_cache=daily_cache,
            )
            if kaufpreis is None or kaufpreis <= 0:
                continue
            menge = wert_eur / kaufpreis

        rows.append(
            {
                "trade_id": _synthetic_trade_id("card", target, datum, menge),
                "coin": target,
                "datum": datum,
                "menge": menge,
                "kaufpreis_eur": kaufpreis,
                "preis_geschaetzt": 1 if geschaetzt else 0,
                "preis_quelle": CARD_PREIS_QUELLE,
                "commission": 0.0,
                "commission_asset": "",
                "gebuehr_eur": 0.0,
                "gebuehr_kurs_eur": float("nan"),
                "gebuehr_geschaetzt": 0,
                "gebuehr_quelle": "",
            }
        )

    if not rows:
        return pd.DataFrame(columns=KAUF_COLUMNS)
    return pd.DataFrame(rows, columns=KAUF_COLUMNS)


def _merge_synthetic_buys(purchases: pd.DataFrame, synthetic: pd.DataFrame) -> pd.DataFrame:
    if synthetic.empty:
        return purchases
    if purchases.empty:
        return synthetic.sort_values(["coin", "datum"]).reset_index(drop=True)

    synth_ids = set(synthetic["trade_id"].astype(int).tolist())
    base = purchases[~purchases["trade_id"].astype(int).isin(synth_ids)].copy()
    combined = pd.concat([base, synthetic], ignore_index=True)
    combined.sort_values(["coin", "datum"], inplace=True)
    return combined.reset_index(drop=True)


def crypto_deposits_as_purchases(zufluesse: pd.DataFrame | None = None) -> pd.DataFrame:
    """Wandelt krypto_deposit-Zeilen in Kauf-Records (Einstand = wert_eur / menge)."""
    df = zufluesse if zufluesse is not None else load_zufluesse_csv()
    if df.empty:
        return pd.DataFrame(columns=KAUF_COLUMNS)

    rows: list[dict[str, object]] = []
    for _, row in df[df["typ"].astype(str) == TYP_CRYPTO].iterrows():
        coin = str(row["coin"]).strip().upper()
        if "→" in coin:
            continue
        menge = float(row["menge"])
        wert_eur = abs(float(row["wert_eur"]))
        price = _price_eur_per_unit(wert_eur, menge)
        if price is None:
            continue
        datum = str(row["datum"])
        rows.append(
            {
                "trade_id": _synthetic_trade_id("deposit", coin, datum, menge),
                "coin": coin,
                "datum": datum,
                "menge": menge,
                "kaufpreis_eur": price,
                "preis_geschaetzt": 1,
                "preis_quelle": DEPOSIT_PREIS_QUELLE,
                "commission": 0.0,
                "commission_asset": "",
                "gebuehr_eur": 0.0,
                "gebuehr_kurs_eur": float("nan"),
                "gebuehr_geschaetzt": 0,
                "gebuehr_quelle": "",
            }
        )
    return pd.DataFrame(rows, columns=KAUF_COLUMNS)


def crypto_withdrawals_as_sells(zufluesse: pd.DataFrame | None = None) -> pd.DataFrame:
    """Wandelt krypto_withdraw-Zeilen in Verkauf-Records."""
    df = zufluesse if zufluesse is not None else load_zufluesse_csv()
    if df.empty:
        return pd.DataFrame(columns=VERKAUF_COLUMNS)

    rows: list[dict[str, object]] = []
    for _, row in df[df["typ"].astype(str) == TYP_CRYPTO_AUS].iterrows():
        coin = str(row["coin"]).strip().upper()
        menge = float(row["menge"])
        wert_eur = abs(float(row["wert_eur"]))
        price = _price_eur_per_unit(wert_eur, menge)
        if price is None:
            continue
        datum = str(row["datum"])
        rows.append(
            {
                "trade_id": _synthetic_trade_id("withdraw", coin, datum, menge),
                "coin": coin,
                "datum": datum,
                "menge": menge,
                "verkaufspreis_eur": price,
                "preis_geschaetzt": 1,
                "preis_quelle": WITHDRAW_PREIS_QUELLE,
                "commission": 0.0,
                "commission_asset": "",
                "gebuehr_eur": 0.0,
                "gebuehr_kurs_eur": float("nan"),
                "gebuehr_geschaetzt": 0,
                "gebuehr_quelle": "",
            }
        )
    return pd.DataFrame(rows, columns=VERKAUF_COLUMNS)


def merge_purchases_with_deposits(purchases: pd.DataFrame) -> pd.DataFrame:
    """Hängt Krypto-Deposits und Kartenkäufe an (ohne Duplikate über trade_id)."""
    spot = purchases.copy()
    with_cards = _merge_synthetic_buys(spot, fiat_card_as_purchases(spot_purchases=spot))
    return _merge_synthetic_buys(with_cards, crypto_deposits_as_purchases())


def merge_sells_with_withdrawals(sells: pd.DataFrame) -> pd.DataFrame:
    """Hängt Krypto-Withdrawals an (ohne Duplikate über trade_id)."""
    withdrawals = crypto_withdrawals_as_sells()
    if withdrawals.empty:
        return sells
    if sells.empty:
        return withdrawals.sort_values(["coin", "datum"]).reset_index(drop=True)

    withdraw_ids = set(withdrawals["trade_id"].astype(int).tolist())
    base = sells[~sells["trade_id"].astype(int).isin(withdraw_ids)].copy()
    combined = pd.concat([base, withdrawals], ignore_index=True)
    combined.sort_values(["coin", "datum"], inplace=True)
    return combined.reset_index(drop=True)


def deposit_trade_counts() -> tuple[int, int, int]:
    """Anzahl berücksichtigter Deposits / Withdrawals / Kartenkäufe."""
    spot = pd.DataFrame(columns=KAUF_COLUMNS)
    return (
        len(crypto_deposits_as_purchases()),
        len(crypto_withdrawals_as_sells()),
        len(fiat_card_as_purchases(spot_purchases=spot)),
    )


__all__ = [
    "CARD_PREIS_QUELLE",
    "DEPOSIT_PREIS_QUELLE",
    "WITHDRAW_PREIS_QUELLE",
    "crypto_deposits_as_purchases",
    "crypto_withdrawals_as_sells",
    "deposit_trade_counts",
    "fiat_card_as_purchases",
    "merge_purchases_with_deposits",
    "merge_sells_with_withdrawals",
]
