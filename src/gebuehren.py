"""Handelsgebühren aus kaeufe.csv und verkaeufe.csv (Anzeige)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from binance_data import load_purchases_csv, load_sells_csv


@dataclass(frozen=True)
class GebuehrenSummary:
    kauf_gebuehren_eur: float
    verkauf_gebuehren_eur: float
    gesamt_eur: float
    exakt_eur: float
    geschaetzt_eur: float
    exakt_anteil_pct: float | None
    geschaetzt_anteil_pct: float | None
    anzahl_kauf: int
    anzahl_verkauf: int
    exakt_anzahl: int
    geschaetzt_anzahl: int
    fehlend_anzahl: int


@dataclass(frozen=True)
class GebuehrenPlausibilitaet:
    ok: bool
    message: str
    median_fee_pct: float | None
    auffaelligkeiten: list[str] = field(default_factory=list)


def _parse_datum(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _fee_known(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    return float(value) >= 0


def _fee_estimated(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    return bool(int(value))


def _quelle_label(value: object) -> str:
    source = str(value or "").strip().lower()
    if source == "eur":
        return "exakt (EUR)"
    if source == "kline":
        return "exakt (Kurs am Trade)"
    if source == "ticker":
        return "geschätzt (Live-Kurs)"
    if source == "none":
        return "keine Gebühr"
    return "unbekannt"


def _trade_value_eur(row: pd.Series, art: str) -> float | None:
    menge = row.get("menge")
    if menge is None or pd.isna(menge):
        return None
    price_col = "kaufpreis_eur" if art == "Kauf" else "verkaufspreis_eur"
    price = row.get(price_col)
    if price is None or pd.isna(price):
        return None
    value = float(menge) * float(price)
    return value if value > 1e-12 else None


def _pct(part: float, whole: float) -> float | None:
    if whole <= 1e-12:
        return None
    return (part / whole) * 100.0


def gebuehren_liste_dataframe(
    purchases: pd.DataFrame | None = None,
    sells: pd.DataFrame | None = None,
    *,
    include_missing: bool = True,
) -> pd.DataFrame:
    """Alle Gebühren einzeln; optional auch Trades ohne berechenbare Gebühr."""
    if purchases is None:
        purchases = load_purchases_csv()
    if sells is None:
        sells = load_sells_csv()

    rows: list[dict[str, object]] = []

    def _append_row(row: pd.Series, art: str) -> None:
        known = _fee_known(row.get("gebuehr_eur"))
        if not known and not include_missing:
            return

        commission = row.get("commission")
        commission_asset = row.get("commission_asset")
        original = ""
        if commission is not None and not pd.isna(commission) and str(commission_asset or "").strip():
            original = f"{float(commission):g} {commission_asset}"

        kurs = row.get("gebuehr_kurs_eur")
        kurs_display: float | None = None
        if kurs is not None and not pd.isna(kurs):
            kurs_display = float(kurs)

        fee_pct: float | None = None
        if known:
            trade_value = _trade_value_eur(row, art)
            if trade_value is not None:
                fee_pct = float(row["gebuehr_eur"]) / trade_value * 100.0

        status = "ok"
        if not known:
            status = "fehlend"
        elif _fee_estimated(row.get("gebuehr_geschaetzt")):
            status = "geschätzt"

        rows.append(
            {
                "Datum": _parse_datum(row["datum"]).strftime("%d.%m.%Y %H:%M"),
                "Coin": str(row["coin"]),
                "Art": art,
                "Gebühr (Original)": original,
                "EUR-Kurs am Trade": kurs_display,
                "Gebühr (EUR)": float(row["gebuehr_eur"]) if known else None,
                "Gebühr (% vom Handelswert)": fee_pct,
                "Quelle": _quelle_label(row.get("gebuehr_quelle")) if known else "fehlend",
                "Status": status,
            }
        )

    if not purchases.empty:
        for _, row in purchases.iterrows():
            _append_row(row, "Kauf")

    if not sells.empty:
        for _, row in sells.iterrows():
            _append_row(row, "Verkauf")

    if not rows:
        return pd.DataFrame(
            columns=[
                "Datum",
                "Coin",
                "Art",
                "Gebühr (Original)",
                "EUR-Kurs am Trade",
                "Gebühr (EUR)",
                "Gebühr (% vom Handelswert)",
                "Quelle",
                "Status",
            ]
        )

    df = pd.DataFrame(rows)
    df["_sort"] = pd.to_datetime(df["Datum"], format="%d.%m.%Y %H:%M", dayfirst=True)
    return df.sort_values("_sort", ascending=False).drop(columns=["_sort"]).reset_index(drop=True)


def gebuehren_nach_coin_dataframe(fee_list: pd.DataFrame) -> pd.DataFrame:
    """Summe der Gebühren pro Coin (nur bekannte Gebühren)."""
    known = fee_list[fee_list["Status"] != "fehlend"].copy()
    if known.empty:
        return pd.DataFrame(columns=["Coin", "Gebühr (EUR)", "Anzahl"])
    grouped = (
        known.groupby("Coin", as_index=False)
        .agg(**{"Gebühr (EUR)": ("Gebühr (EUR)", "sum"), "Anzahl": ("Gebühr (EUR)", "count")})
        .sort_values("Gebühr (EUR)", ascending=False)
    )
    return grouped.reset_index(drop=True)


def gebuehren_nach_monat_dataframe(fee_list: pd.DataFrame) -> pd.DataFrame:
    """Summe der Gebühren pro Monat inkl. Anzahl geschätzter Trades."""
    empty_cols = [
        "Monat",
        "Kauf (EUR)",
        "Verkauf (EUR)",
        "Gesamt (EUR)",
        "Trades gesamt",
        "Trades geschätzt",
    ]
    if fee_list.empty:
        return pd.DataFrame(columns=empty_cols)

    df = fee_list[fee_list["Status"] != "fehlend"].copy()
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    df["_sort"] = pd.to_datetime(df["Datum"], format="%d.%m.%Y %H:%M", dayfirst=True)
    df["Monat"] = df["_sort"].dt.strftime("%Y-%m")
    df["_geschaetzt"] = df["Status"] == "geschätzt"

    pivot = (
        df.pivot_table(
            index="Monat",
            columns="Art",
            values="Gebühr (EUR)",
            aggfunc="sum",
            fill_value=0.0,
        )
        .reset_index()
    )
    for col in ("Kauf", "Verkauf"):
        if col not in pivot.columns:
            pivot[col] = 0.0
    pivot["Gesamt (EUR)"] = pivot["Kauf"] + pivot["Verkauf"]
    pivot = pivot.rename(columns={"Kauf": "Kauf (EUR)", "Verkauf": "Verkauf (EUR)"})

    counts = (
        df.groupby("Monat")
        .agg(
            **{
                "Trades gesamt": ("Gebühr (EUR)", "count"),
                "Trades geschätzt": ("_geschaetzt", "sum"),
            }
        )
        .reset_index()
    )
    return pivot.merge(counts, on="Monat", how="left").sort_values("Monat").reset_index(drop=True)


def summarize_gebuehren(
    purchases: pd.DataFrame | None = None,
    sells: pd.DataFrame | None = None,
) -> GebuehrenSummary:
    if purchases is None:
        purchases = load_purchases_csv()
    if sells is None:
        sells = load_sells_csv()

    kauf_sum = 0.0
    verkauf_sum = 0.0
    exakt_sum = 0.0
    geschaetzt_sum = 0.0
    kauf_count = 0
    verkauf_count = 0
    exakt_count = 0
    geschaetzt_count = 0
    fehlend = 0

    for df in (purchases, sells):
        if df.empty:
            continue
        for _, row in df.iterrows():
            if _fee_known(row.get("gebuehr_eur")):
                amount = float(row["gebuehr_eur"])
                if df is purchases:
                    kauf_sum += amount
                    kauf_count += 1
                else:
                    verkauf_sum += amount
                    verkauf_count += 1
                if _fee_estimated(row.get("gebuehr_geschaetzt")):
                    geschaetzt_sum += amount
                    geschaetzt_count += 1
                else:
                    exakt_sum += amount
                    exakt_count += 1
            else:
                fehlend += 1

    gesamt = kauf_sum + verkauf_sum
    return GebuehrenSummary(
        kauf_gebuehren_eur=kauf_sum,
        verkauf_gebuehren_eur=verkauf_sum,
        gesamt_eur=gesamt,
        exakt_eur=exakt_sum,
        geschaetzt_eur=geschaetzt_sum,
        exakt_anteil_pct=_pct(exakt_sum, gesamt),
        geschaetzt_anteil_pct=_pct(geschaetzt_sum, gesamt),
        anzahl_kauf=kauf_count,
        anzahl_verkauf=verkauf_count,
        exakt_anzahl=exakt_count,
        geschaetzt_anzahl=geschaetzt_count,
        fehlend_anzahl=fehlend,
    )


def pruefe_gebuehren_plausibilitaet(
    purchases: pd.DataFrame | None = None,
    sells: pd.DataFrame | None = None,
    *,
    warn_pct: float = 0.35,
    spot_typical_pct: float = 0.1,
) -> GebuehrenPlausibilitaet:
    """Prüft Gebühr / Handelswert je Trade (Spot üblich ~0,1 %)."""
    if purchases is None:
        purchases = load_purchases_csv()
    if sells is None:
        sells = load_sells_csv()

    ratios: list[float] = []
    auffaellig: list[str] = []

    for df, art in ((purchases, "Kauf"), (sells, "Verkauf")):
        if df.empty:
            continue
        for _, row in df.iterrows():
            if not _fee_known(row.get("gebuehr_eur")):
                continue
            trade_value = _trade_value_eur(row, art)
            if trade_value is None:
                continue
            ratio_pct = float(row["gebuehr_eur"]) / trade_value * 100.0
            ratios.append(ratio_pct)
            if ratio_pct > warn_pct:
                auffaellig.append(
                    f"{row['coin']} {art} {_parse_datum(row['datum']).date()}: "
                    f"{ratio_pct:.2f} % vom Handelswert"
                )

    if not ratios:
        return GebuehrenPlausibilitaet(
            ok=True,
            message="Keine Gebühren mit Handelswert zum Vergleich.",
            median_fee_pct=None,
        )

    median = float(pd.Series(ratios).median())
    ok = len(auffaellig) == 0 and median <= warn_pct
    if ok:
        message = (
            f"Plausibilität OK: Median {median:.3f} % "
            f"(Spot üblich ~{spot_typical_pct:.1f} %)."
        )
    else:
        message = (
            f"Plausibilität prüfen: Median {median:.3f} %, "
            f"{len(auffaellig)} auffällige Trade(s)."
        )

    return GebuehrenPlausibilitaet(
        ok=ok,
        message=message,
        median_fee_pct=median,
        auffaelligkeiten=auffaellig[:10],
    )
