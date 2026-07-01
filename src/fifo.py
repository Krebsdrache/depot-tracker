"""Gemeinsame FIFO-Logik für steuer.py und bilanz.py."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd


def parse_kaufdatum(value: str) -> date:
    """CSV-Datum (ISO) in ein reines Kalenderdatum umwandeln."""
    return datetime.fromisoformat(value).date()


def optional_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(number):
        return None
    return number


def optional_preis(value: object) -> float | None:
    price = optional_float(value)
    if price is None or price <= 0:
        return None
    return price


def optional_gebuehr(value: object) -> float | None:
    fee = optional_float(value)
    if fee is None or fee < 0:
        return None
    return fee


@dataclass(frozen=True)
class FifoVerkaufAnteil:
    """Welche Kauf-Tranche (FIFO) in einem Verkauf steckt."""

    kaufdatum: date
    menge: float
    kaufpreis_eur: float
    einstand_eur: float


@dataclass(frozen=True)
class FifoVerkaufMatch:
    """Ein Verkauf mit zugeordneten FIFO-Kauf-Anteilen."""

    coin: str
    verkaufsdatum: date
    verkaufszeit: datetime
    menge: float
    anteile: tuple[FifoVerkaufAnteil, ...]
    menge_ohne_einstand: float
    verkaufspreis_eur: float | None = None
    gebuehr_eur: float | None = None
    gebuehr_geschaetzt: bool = False

    @property
    def einstand_bekannt(self) -> bool:
        return self.menge_ohne_einstand <= 1e-12 and len(self.anteile) > 0

    @property
    def einstand_eur(self) -> float:
        return sum(anteil.einstand_eur for anteil in self.anteile)

    @property
    def gebuehr_bekannt(self) -> bool:
        return self.gebuehr_eur is not None


def build_fifo_lots(buys: list[dict[str, object]]) -> list[dict[str, object]]:
    """Sortierte Kauf-Lots für FIFO (Menge + Gebühr je Lot)."""
    lots: list[dict[str, object]] = []
    for buy in buys:
        menge = float(buy["menge"])
        lots.append(
            {
                "coin": buy["coin"],
                "kaufdatum": parse_kaufdatum(str(buy["datum"])),
                "menge": menge,
                "menge_original": menge,
                "kaufpreis_eur": float(buy["kaufpreis_eur"]),
                "gebuehr_eur": optional_gebuehr(buy.get("gebuehr_eur")) or 0.0,
            }
        )
    return sorted(lots, key=lambda lot: (lot["kaufdatum"], lot["menge"]))


def _einstand_fuer_anteil(lot: dict[str, object], consumed: float) -> float:
    """Anteiliger Einstand inkl. Kaufgebühr (proportional bei Teilverbrauch)."""
    base = consumed * float(lot["kaufpreis_eur"])
    fee_total = float(lot.get("gebuehr_eur") or 0.0)
    original = float(lot.get("menge_original") or lot["menge"])
    if fee_total <= 0 or original <= 1e-12:
        return base
    return base + (consumed / original) * fee_total


def fifo_zuordnung_fuer_verkaeufe(
    purchases: pd.DataFrame,
    sells_df: pd.DataFrame,
) -> list[FifoVerkaufMatch]:
    """
    Ordnet jedem Verkauf in der CSV die FIFO-Kauf-Tranchen zu.

    Gleiche FIFO-Reihenfolge wie in steuer._apply_fifo.
    """
    matches: list[FifoVerkaufMatch] = []
    if sells_df.empty:
        return matches

    for coin, coin_sells in sells_df.groupby("coin"):
        coin_str = str(coin)
        coin_buys = purchases[purchases["coin"].astype(str) == coin_str]
        lots = build_fifo_lots(coin_buys.to_dict("records")) if not coin_buys.empty else []

        for _, row in coin_sells.sort_values("datum").iterrows():
            verkaufszeit = datetime.fromisoformat(str(row["datum"]))
            if verkaufszeit.tzinfo is None:
                verkaufszeit = verkaufszeit.replace(tzinfo=timezone.utc)
            remaining_sell = float(row["menge"])
            anteile: list[FifoVerkaufAnteil] = []

            for lot in lots:
                if remaining_sell <= 1e-12:
                    break
                if float(lot["menge"]) <= 1e-12:
                    continue
                consumed = min(float(lot["menge"]), remaining_sell)
                anteile.append(
                    FifoVerkaufAnteil(
                        kaufdatum=lot["kaufdatum"],
                        menge=consumed,
                        kaufpreis_eur=float(lot["kaufpreis_eur"]),
                        einstand_eur=_einstand_fuer_anteil(lot, consumed),
                    )
                )
                lot["menge"] = float(lot["menge"]) - consumed
                remaining_sell -= consumed

            geschaetzt_raw = row.get("gebuehr_geschaetzt")
            gebuehr_geschaetzt = False
            if geschaetzt_raw is not None and not pd.isna(geschaetzt_raw):
                gebuehr_geschaetzt = bool(int(geschaetzt_raw))

            matches.append(
                FifoVerkaufMatch(
                    coin=coin_str,
                    verkaufsdatum=verkaufszeit.date(),
                    verkaufszeit=verkaufszeit,
                    menge=float(row["menge"]),
                    anteile=tuple(anteile),
                    menge_ohne_einstand=max(remaining_sell, 0.0),
                    verkaufspreis_eur=optional_preis(row.get("verkaufspreis_eur")),
                    gebuehr_eur=optional_gebuehr(row.get("gebuehr_eur")),
                    gebuehr_geschaetzt=gebuehr_geschaetzt,
                )
            )

    matches.sort(key=lambda item: item.verkaufszeit)
    return matches


def _apply_fifo_sells_to_lots(
    lots: list[dict[str, object]],
    sells_df: pd.DataFrame,
    coin: str,
) -> None:
    """Reduziert Lot-Mengen nach FIFO für alle Verkäufe eines Coins."""
    if sells_df.empty:
        return
    coin_sells = sells_df[sells_df["coin"].astype(str) == str(coin)].sort_values("datum")
    for _, row in coin_sells.iterrows():
        remaining_sell = float(row["menge"])
        for lot in lots:
            if remaining_sell <= 1e-12:
                break
            if float(lot["menge"]) <= 1e-12:
                continue
            consumed = min(float(lot["menge"]), remaining_sell)
            lot["menge"] = float(lot["menge"]) - consumed
            remaining_sell -= consumed


def fifo_avg_entry_from_local(
    coin: str,
    purchases: pd.DataFrame,
    sells_df: pd.DataFrame,
) -> float | None:
    """
    Gewichteter Einstand offener FIFO-Lots (Kaufpreis + anteilige Gebühr).

    Gleiche Logik wie Bilanz und Steuer-Haltefrist.
    """
    coin_str = str(coin)
    coin_buys = purchases[purchases["coin"].astype(str) == coin_str]
    if coin_buys.empty:
        return None

    lots = build_fifo_lots(coin_buys.to_dict("records"))
    _apply_fifo_sells_to_lots(lots, sells_df, coin_str)

    total_qty = 0.0
    total_cost = 0.0
    for lot in lots:
        qty = float(lot["menge"])
        if qty <= 1e-12:
            continue
        total_qty += qty
        total_cost += _einstand_fuer_anteil(lot, qty)

    if total_qty <= 1e-12 or total_cost <= 0:
        return None
    return total_cost / total_qty


def offene_fifo_lots(
    purchases: pd.DataFrame,
    sells_df: pd.DataFrame,
) -> list[dict[str, object]]:
    """Offene Kauf-Lots nach FIFO (für Tests und Plausibilität)."""
    open_lots: list[dict[str, object]] = []
    if purchases.empty:
        return open_lots

    coins = sorted(purchases["coin"].astype(str).unique())
    for coin_str in coins:
        coin_buys = purchases[purchases["coin"].astype(str) == coin_str]
        lots = build_fifo_lots(coin_buys.to_dict("records"))
        _apply_fifo_sells_to_lots(lots, sells_df, coin_str)

        for lot in lots:
            if float(lot["menge"]) > 1e-12:
                open_lots.append(
                    {
                        "coin": coin_str,
                        "kaufdatum": lot["kaufdatum"],
                        "menge": float(lot["menge"]),
                        "kaufpreis_eur": float(lot["kaufpreis_eur"]),
                    }
                )
    return open_lots
