"""Steuer-Haltefrist nach deutscher 1-Jahres-Regel (FIFO, nur Anzeige)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from binance_data import (
    _account_balances_from_client,
    _sells_by_coin_from_csv,
    create_authenticated_client,
    load_purchases_csv,
    load_sells_csv,
    sync_trade_history_from_binance,
)
from sync_meta import mark_trades_synced
from fifo import (
    FifoVerkaufAnteil,
    FifoVerkaufMatch,
    build_fifo_lots,
    fifo_zuordnung_fuer_verkaeufe,
    parse_kaufdatum,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class HaltefristTranche:
    """Eine offene Kauf-Tranche nach FIFO-Abzug der Verkäufe."""

    coin: str
    kaufdatum: date
    menge: float
    kaufpreis_eur: float
    steuerfrei_ab: date
    tage_verbleibend: int
    steuerfrei: bool
    status: str


@dataclass
class SteuerResult:
    """Ergebnis der Haltefrist-Berechnung."""

    ok: bool
    message: str
    tranches: list[HaltefristTranche] = field(default_factory=list)
    frei_menge_pro_coin: dict[str, float] = field(default_factory=dict)
    coin_summaries: list[dict[str, float | str]] = field(default_factory=list)
    sync_info: str = ""


# Haltefrist: mehr als 1 Jahr → steuerfrei ab Kaufdatum + 1 Jahr + 1 Tag
HALTEFRIST_TAGE = 366


def steuerfrei_ab(kaufdatum: date) -> date:
    """
    Erstes Kalenderdatum, ab dem die Haltefrist von mehr als einem Jahr
    erfüllt ist: Kaufdatum + 1 Jahr + 1 Tag.
    """
    try:
        plus_ein_jahr = kaufdatum.replace(year=kaufdatum.year + 1)
    except ValueError:
        # Schaltjahr 29.02. → 28.02. im Folgejahr
        plus_ein_jahr = date(kaufdatum.year + 1, 2, 28)
    return plus_ein_jahr + timedelta(days=1)


def _parse_kaufdatum(value: str) -> date:
    """CSV-Datum (ISO) in ein reines Kalenderdatum umwandeln."""
    return parse_kaufdatum(value)


def _apply_fifo(
    buys: list[dict[str, object]],
    sells: list[dict[str, object]],
) -> list[dict[str, object]]:
    """
    FIFO: Verkäufe werden chronologisch von den ältesten Käufen abgezogen.
    Gibt nur noch offene Tranchen mit Restmenge zurück.
    """
    lots = sorted(
        [
            {
                "coin": buy["coin"],
                "kaufdatum": _parse_kaufdatum(str(buy["datum"])),
                "menge": float(buy["menge"]),
                "kaufpreis_eur": float(buy["kaufpreis_eur"]),
            }
            for buy in buys
        ],
        key=lambda lot: (lot["kaufdatum"], lot["menge"]),
    )

    for sell in sorted(sells, key=lambda item: item["zeit"]):
        remaining_sell = float(sell["menge"])
        for lot in lots:
            if remaining_sell <= 1e-12:
                break
            if lot["menge"] <= 1e-12:
                continue
            consumed = min(lot["menge"], remaining_sell)
            lot["menge"] -= consumed
            remaining_sell -= consumed

    return [lot for lot in lots if lot["menge"] > 1e-12]


def _tranche_status(heute: date, frei_ab: date) -> tuple[bool, int, str]:
    """Berechnet Steuerfreiheit, verbleibende Tage und eindeutigen Status-Text."""
    if heute >= frei_ab:
        tage_seit = (heute - frei_ab).days
        return True, 0, f"steuerfrei (seit {tage_seit} Tagen)"

    tage = (frei_ab - heute).days
    if tage <= 30:
        return False, tage, f"bald steuerfrei ({tage} Tage)"
    return False, tage, f"gesperrt ({tage} Tage)"


def haltefrist_status_text(steuerfrei_ab_datum: date, heute: date | None = None) -> str:
    """Status-Label für Tabellen (immer mit aktuellem Datum berechnen)."""
    _, _, status = _tranche_status(heute or date.today(), steuerfrei_ab_datum)
    return status


def berechne_haltefristen(
    purchases: pd.DataFrame,
    sells_by_coin: dict[str, list[dict[str, object]]],
    heute: date | None = None,
) -> list[HaltefristTranche]:
    """Berechnet für alle offenen FIFO-Tranchen die Haltefrist."""
    if heute is None:
        heute = date.today()

    tranches: list[HaltefristTranche] = []
    if purchases.empty:
        return tranches

    for coin, coin_buys in purchases.groupby("coin"):
        coin_sells = sells_by_coin.get(str(coin), [])
        open_lots = _apply_fifo(
            coin_buys.to_dict("records"),
            coin_sells,
        )

        for lot in open_lots:
            frei_ab = steuerfrei_ab(lot["kaufdatum"])
            steuerfrei, tage, status = _tranche_status(heute, frei_ab)
            tranches.append(
                HaltefristTranche(
                    coin=str(lot["coin"]),
                    kaufdatum=lot["kaufdatum"],
                    menge=lot["menge"],
                    kaufpreis_eur=lot["kaufpreis_eur"],
                    steuerfrei_ab=frei_ab,
                    tage_verbleibend=tage,
                    steuerfrei=steuerfrei,
                    status=status,
                )
            )

    tranches.sort(key=lambda item: (item.coin, item.kaufdatum))
    return tranches


def _coin_summaries(
    tranches: list[HaltefristTranche],
    balances_by_coin: dict[str, float],
) -> list[dict[str, float | str]]:
    """Pro Coin: Gesamtbestand (Binance) vs. steuerfrei verkaufbare Menge (FIFO)."""
    frei: dict[str, float] = {}
    for tranche in tranches:
        if not tranche.steuerfrei:
            continue
        frei[tranche.coin] = frei.get(tranche.coin, 0.0) + tranche.menge

    coins = sorted(set(balances_by_coin) | set(frei) | {t.coin for t in tranches})
    summaries: list[dict[str, float | str]] = []
    for coin in coins:
        gesamt = balances_by_coin.get(coin, 0.0)
        steuerfrei_menge = frei.get(coin, 0.0)
        summaries.append(
            {
                "coin": coin,
                "gesamt_menge": gesamt,
                "steuerfrei_menge": steuerfrei_menge,
                "gesperrt_menge": max(gesamt - steuerfrei_menge, 0.0),
            }
        )
    return [row for row in summaries if float(row["gesamt_menge"]) > 0]


def _account_balances(client) -> dict[str, float]:
    """Aktuelle Spot-Bestände je Coin (free + locked)."""
    return _account_balances_from_client(client)


def load_steuer_uebersicht(
    use_local_history: bool = True,
    balances_by_coin: dict[str, float] | None = None,
) -> SteuerResult:
    """
    Berechnet Haltefristen aus lokaler CSV (schnell) oder nach Binance-Sync (langsam).

    use_local_history=True  → liest data/kaeufe.csv + data/verkaeufe.csv
    use_local_history=False → holt zuerst neue Trades von Binance
    """
    sync_info = ""
    if not use_local_history:
        sync = sync_trade_history_from_binance()
        if not sync.ok:
            return SteuerResult(ok=False, message=sync.message)
        sync_info = sync.message
        mark_trades_synced()

    purchases = load_purchases_csv(with_deposits=True)
    if purchases.empty:
        return SteuerResult(
            ok=True,
            message=(
                "Noch keine Käufe in data/kaeufe.csv. "
                'Klicke oben auf „Daten von Binance aktualisieren“.'
            ),
            sync_info=sync_info,
        )

    if balances_by_coin is not None:
        balances = balances_by_coin
    else:
        client_result = create_authenticated_client()
        if not client_result.ok:
            return SteuerResult(ok=False, message=client_result.message, sync_info=sync_info)
        assert client_result.client is not None
        balances = _account_balances(client_result.client)
    sells_by_coin = _sells_by_coin_from_csv(load_sells_csv(with_withdrawals=True))
    tranches = berechne_haltefristen(purchases, sells_by_coin)
    frei_mengen = {
        row["coin"]: float(row["steuerfrei_menge"])
        for row in _coin_summaries(tranches, balances)
        if float(row["steuerfrei_menge"]) > 0
    }
    summaries = _coin_summaries(tranches, balances)

    return SteuerResult(
        ok=True,
        message=f"{len(tranches)} offene Tranchen berechnet.",
        tranches=tranches,
        frei_menge_pro_coin=frei_mengen,
        coin_summaries=summaries,
        sync_info=sync_info,
    )


_MONAT_KURZ = (
    "Jan",
    "Feb",
    "Mär",
    "Apr",
    "Mai",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Okt",
    "Nov",
    "Dez",
)


@dataclass(frozen=True)
class FristKalenderCoinRow:
    """Aktueller Steuer-Status je Coin (Mengen + EUR-Werte)."""

    coin: str
    steuerfrei_menge: float
    gesperrt_menge: float
    steuerfreier_wert_eur: float | None
    gesperrter_wert_eur: float | None


@dataclass(frozen=True)
class FristKalenderMonat:
    """Ein Monat im 12-Monats-Zeitstrahl."""

    month_key: str
    label: str
    wert_eur: float
    details: tuple[tuple[str, float, float | None], ...]


@dataclass
class FristKalenderResult:
    """Ergebnis für Frist-Kalender (Status + 12-Monats-Zeitstrahl)."""

    ok: bool
    message: str
    heute_freier_wert_eur: float
    heute_freie_mengen: dict[str, float]
    gesperrter_wert_eur: float
    coin_rows: list[FristKalenderCoinRow] = field(default_factory=list)
    monate: list[FristKalenderMonat] = field(default_factory=list)
    plausibility_ok: bool = True
    plausibility_details: list[str] = field(default_factory=list)
    plausibility_rows: list["PlausibilitaetCoinRow"] = field(default_factory=list)


# Coins ohne Krypto-Haltefrist-FIFO (Fiat/Stable/Fee) – nur informativ.
PLAUSIBILITY_IGNORE_COINS = frozenset(
    {"EUR", "USD", "USDC", "USDT", "BUSD", "FDUSD", "BNB", "AXS"}
)
PLAUSIBILITY_ROUNDING_PCT = 2.0


@dataclass(frozen=True)
class PlausibilitaetCoinRow:
    """Eine Zeile im Plausibilitäts-Check (Bestand vs. FIFO-Tranchen)."""

    coin: str
    bestand: float
    tranchen: float
    diff: float
    diff_pct: float | None
    status: str
    status_label: str


def _plausibilitaet_status_label(status: str) -> str:
    return {
        "ok": "OK",
        "rundung": "Rundung",
        "abweichung": "Abweichung",
        "fehlend": "Fehlend",
        "ignoriert": "Ignoriert",
    }.get(status, status)


def _classify_plausibilitaet(
    coin: str,
    balance: float,
    tracked: float,
    *,
    tolerance: float,
) -> str:
    if coin in PLAUSIBILITY_IGNORE_COINS:
        return "ignoriert"
    if tracked <= tolerance:
        return "fehlend"
    diff = balance - tracked
    if abs(diff) <= tolerance:
        return "ok"
    if balance > tolerance:
        diff_pct = abs(diff) / balance * 100.0
        if diff_pct <= PLAUSIBILITY_ROUNDING_PCT:
            return "rundung"
    return "abweichung"


def plausibilitaet_pro_coin(
    tranches: list[HaltefristTranche],
    balances_by_coin: dict[str, float],
    tolerance: float = 1e-8,
) -> tuple[bool, list[PlausibilitaetCoinRow], list[str]]:
    """
    Prüft je Coin: steuerfrei + gesperrt = Bestand.

    Returns:
        (ok, rows, legacy_detail_strings)
    """
    rows: list[PlausibilitaetCoinRow] = []
    details: list[str] = []
    coins = sorted(
        coin
        for coin in (set(balances_by_coin) | {t.coin for t in tranches})
        if balances_by_coin.get(coin, 0.0) > tolerance
    )

    for coin in coins:
        balance = balances_by_coin.get(coin, 0.0)
        steuerfrei = sum(t.menge for t in tranches if t.coin == coin and t.steuerfrei)
        gesperrt = sum(t.menge for t in tranches if t.coin == coin and not t.steuerfrei)
        tracked = steuerfrei + gesperrt
        diff = balance - tracked
        diff_pct = abs(diff) / balance * 100.0 if balance > tolerance else None
        status = _classify_plausibilitaet(coin, balance, tracked, tolerance=tolerance)

        rows.append(
            PlausibilitaetCoinRow(
                coin=coin,
                bestand=balance,
                tranchen=tracked,
                diff=diff,
                diff_pct=diff_pct,
                status=status,
                status_label=_plausibilitaet_status_label(status),
            )
        )

        if status in {"fehlend", "abweichung"}:
            if status == "fehlend":
                details.append(
                    f"{coin}: Bestand {balance:.8f}, aber keine offenen Kauf-Tranchen in der CSV."
                )
            else:
                details.append(
                    f"{coin}: Bestand {balance:.8f} ≠ steuerfrei ({steuerfrei:.8f}) "
                    f"+ gesperrt ({gesperrt:.8f}) = {tracked:.8f}"
                )

    ok = not any(r.status in {"fehlend", "abweichung"} for r in rows)
    return ok, rows, details


# Zeitstrahl Teil 2: aktueller Monat + 12 Folgemonate (= 13 Säulen, inkl. letzter Freigabe)
FRIST_KALENDER_MONATE = 13


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _month_label(value: date) -> str:
    return f"{_MONAT_KURZ[value.month - 1]} {value.year % 100:02d}"


def _naechste_kalendermonate(ab: date, anzahl: int = 12) -> list[tuple[str, str]]:
    """Liefert (month_key, label) für die nächsten Kalendermonate ab `ab`."""
    start = date(ab.year, ab.month, 1)
    jahr, monat = start.year, start.month
    result: list[tuple[str, str]] = []
    for _ in range(anzahl):
        key = f"{jahr:04d}-{monat:02d}"
        result.append((key, _month_label(date(jahr, monat, 1))))
        monat += 1
        if monat > 12:
            monat = 1
            jahr += 1
    return result


def _menge_wert_eur(menge: float, preis_eur: float | None) -> float | None:
    if preis_eur is None:
        return None
    return menge * preis_eur


def pruefe_mengen_plausibilitaet(
    tranches: list[HaltefristTranche],
    balances_by_coin: dict[str, float],
    tolerance: float = 1e-8,
) -> tuple[bool, list[str]]:
    """
    Prüft je Coin: steuerfreie Menge + gesperrte Menge = aktueller Bestand.

    Abweichungen entstehen z. B. bei fehlendem EUR-Wert in zufluesse.csv
    oder Coins ohne Käufe/Deposits in der Historie.
    """
    ok, _rows, details = plausibilitaet_pro_coin(tranches, balances_by_coin, tolerance)
    return ok, details


def berechne_frist_kalender(
    tranches: list[HaltefristTranche],
    balances_by_coin: dict[str, float],
    prices_eur_by_coin: dict[str, float | None],
    heute: date | None = None,
) -> FristKalenderResult:
    """
    Baut Status-Tabelle und 13-Monats-Zeitstrahl aus bestehenden FIFO-Tranchen.

    Gesperrte Tranchen werden dem Kalendermonat ihres steuerfrei_ab-Datums zugeordnet.
    EUR-Werte nutzen den übergebenen Tageskurs (Menge × Preis).
    """
    if heute is None:
        heute = date.today()

    plausibility_ok, plausibility_rows, plausibility_details = plausibilitaet_pro_coin(
        tranches, balances_by_coin
    )

    coins = sorted(
        coin
        for coin in (set(balances_by_coin) | {t.coin for t in tranches})
        if balances_by_coin.get(coin, 0.0) > 1e-12
    )

    coin_rows: list[FristKalenderCoinRow] = []
    heute_freie_mengen: dict[str, float] = {}
    heute_freier_wert = 0.0
    gesperrter_wert = 0.0
    fehlende_kurse: set[str] = set()

    for coin in coins:
        steuerfrei_menge = sum(t.menge for t in tranches if t.coin == coin and t.steuerfrei)
        gesperrt_menge = sum(
            t.menge for t in tranches if t.coin == coin and not t.steuerfrei
        )
        if gesperrt_menge <= 1e-12 and steuerfrei_menge <= 1e-12:
            gesperrt_menge = max(balances_by_coin.get(coin, 0.0) - steuerfrei_menge, 0.0)

        preis = prices_eur_by_coin.get(coin)
        freier_wert = _menge_wert_eur(steuerfrei_menge, preis)
        gesperrt_wert = _menge_wert_eur(gesperrt_menge, preis)

        if steuerfrei_menge > 1e-12:
            heute_freie_mengen[coin] = steuerfrei_menge
        if freier_wert is not None:
            heute_freier_wert += freier_wert
        elif steuerfrei_menge > 1e-12:
            fehlende_kurse.add(coin)
        if gesperrt_wert is not None:
            gesperrter_wert += gesperrt_wert
        elif gesperrt_menge > 1e-12:
            fehlende_kurse.add(coin)

        coin_rows.append(
            FristKalenderCoinRow(
                coin=coin,
                steuerfrei_menge=steuerfrei_menge,
                gesperrt_menge=gesperrt_menge,
                steuerfreier_wert_eur=freier_wert,
                gesperrter_wert_eur=gesperrt_wert,
            )
        )

    monatsfenster = _naechste_kalendermonate(heute, FRIST_KALENDER_MONATE)
    monats_keys = {key for key, _label in monatsfenster}
    bucket: dict[str, list[tuple[str, float, float | None]]] = {
        key: [] for key in monats_keys
    }

    for tranche in tranches:
        if tranche.steuerfrei:
            continue
        key = _month_key(tranche.steuerfrei_ab)
        if key not in bucket:
            continue
        preis = prices_eur_by_coin.get(tranche.coin)
        wert = _menge_wert_eur(tranche.menge, preis)
        if wert is None and tranche.menge > 1e-12:
            fehlende_kurse.add(tranche.coin)
        bucket[key].append((tranche.coin, tranche.menge, wert))

    monate: list[FristKalenderMonat] = []
    for key, label in monatsfenster:
        details = tuple(sorted(bucket[key], key=lambda item: item[0]))
        wert_summe = sum(item[2] for item in details if item[2] is not None)
        monate.append(
            FristKalenderMonat(
                month_key=key,
                label=label,
                wert_eur=wert_summe,
                details=details,
            )
        )

    message = "Frist-Kalender berechnet."
    if fehlende_kurse:
        message += f" Kein EUR-Kurs für: {', '.join(sorted(fehlende_kurse))}."

    return FristKalenderResult(
        ok=True,
        message=message,
        heute_freier_wert_eur=heute_freier_wert,
        heute_freie_mengen=heute_freie_mengen,
        gesperrter_wert_eur=gesperrter_wert,
        coin_rows=coin_rows,
        monate=monate,
        plausibility_ok=plausibility_ok,
        plausibility_details=plausibility_details,
        plausibility_rows=plausibility_rows,
    )
