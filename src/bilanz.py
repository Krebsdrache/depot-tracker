"""Bilanz: realisierte Gewinne/Verluste nach FIFO (nur Anzeige)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from binance_data import PortfolioResult, load_purchases_csv, load_sells_csv
from fifo import FifoVerkaufMatch, fifo_zuordnung_fuer_verkaeufe
from steuer import steuerfrei_ab


@dataclass(frozen=True)
class RealisierterVerkauf:
    """Ein Verkauf mit Netto-G/V und Steuer-Aufteilung."""

    coin: str
    verkaufsdatum: date
    menge: float
    einstand_eur: float | None
    erloes_eur: float | None
    gebuehr_eur: float | None
    gebuehr_geschaetzt: bool
    gv_eur: float | None
    gv_prozent: float | None
    steuerfrei_realisiert_eur: float
    steuerpflichtig_realisiert_eur: float
    erloes_bekannt: bool
    einstand_bekannt: bool
    gebuehr_bekannt: bool
    vollstaendig_berechenbar: bool


@dataclass
class BilanzResult:
    """Gesamtergebnis für die Bilanz-Übersicht."""

    ok: bool
    message: str
    verkaeufe: list[RealisierterVerkauf] = field(default_factory=list)
    realisierte_gewinne_eur: float = 0.0
    realisierte_verluste_eur: float = 0.0
    netto_realisiert_eur: float = 0.0
    rendite_auf_verkauftes_pct: float | None = None
    gebuehren_gesamt_eur: float = 0.0
    gebuehren_geschaetzt_anzahl: int = 0
    unbekannter_erloes_anzahl: int = 0
    unvollstaendige_verkaeufe: list[str] = field(default_factory=list)


def steuer_status_label(rv: RealisierterVerkauf) -> str:
    """Kurzlabel: steuerfrei, steuerpflichtig oder gemischt."""
    if not rv.vollstaendig_berechenbar:
        return "unbekannt"
    frei = abs(rv.steuerfrei_realisiert_eur) > 1e-8
    pflicht = abs(rv.steuerpflichtig_realisiert_eur) > 1e-8
    if frei and pflicht:
        return "gemischt"
    if pflicht:
        return "steuerpflichtig"
    if frei:
        return "steuerfrei"
    if rv.gv_eur is not None and abs(rv.gv_eur) <= 1e-8:
        return "neutral"
    return "unbekannt"


def _menge_summe(df: pd.DataFrame, coin: str) -> float:
    if df.empty or "coin" not in df.columns:
        return 0.0
    rows = df[df["coin"].astype(str) == coin]
    if rows.empty:
        return 0.0
    return float(rows["menge"].sum())


def coin_gesamtuebersicht_dataframe(
    result: PortfolioResult,
    bilanz: BilanzResult,
) -> pd.DataFrame:
    """Pro Coin: gekauft, verkauft, im Depot, Wert und unrealisierter G/V."""
    purchases = load_purchases_csv(with_deposits=True)
    sells = load_sells_csv(with_withdrawals=True)
    portfolio_by_coin = {pos.coin: pos for pos in result.positions}

    coins = sorted(
        set(purchases["coin"].astype(str).tolist() if not purchases.empty else [])
        | set(sells["coin"].astype(str).tolist() if not sells.empty else [])
        | set(portfolio_by_coin.keys())
    )

    rows: list[dict[str, object]] = []
    for coin in coins:
        pos = portfolio_by_coin.get(coin)
        im_depot = pos.quantity if pos else 0.0
        rows.append(
            {
                "Coin": coin,
                "Gekauft gesamt": _menge_summe(purchases, coin),
                "Verkauft gesamt": _menge_summe(sells, coin),
                "Im Depot": im_depot,
                "Aktueller Wert (EUR)": pos.current_value_eur if pos else None,
                "Unreal. G/V (EUR)": pos.profit_loss_eur if pos and pos.entry_known else None,
                "Unreal. G/V (%)": pos.profit_loss_pct if pos and pos.entry_known else None,
            }
        )
    return pd.DataFrame(rows)


def coin_verkauf_summary_dataframe(bilanz: BilanzResult) -> pd.DataFrame:
    """Pro Coin mit Verkäufen: Mengen, realisierter G/V und Steuer-Aufteilung."""
    buckets: dict[str, dict[str, float | str]] = {}

    for rv in bilanz.verkaeufe:
        if not rv.vollstaendig_berechenbar or rv.gv_eur is None:
            continue
        bucket = buckets.setdefault(
            rv.coin,
            {
                "verkauft_menge": 0.0,
                "einstand_eur": 0.0,
                "erloes_eur": 0.0,
                "gv_eur": 0.0,
                "steuerfrei_eur": 0.0,
                "steuerpflichtig_eur": 0.0,
            },
        )
        bucket["verkauft_menge"] = float(bucket["verkauft_menge"]) + rv.menge
        if rv.einstand_eur is not None:
            bucket["einstand_eur"] = float(bucket["einstand_eur"]) + rv.einstand_eur
        if rv.erloes_eur is not None:
            bucket["erloes_eur"] = float(bucket["erloes_eur"]) + rv.erloes_eur
        bucket["gv_eur"] = float(bucket["gv_eur"]) + rv.gv_eur
        bucket["steuerfrei_eur"] = float(bucket["steuerfrei_eur"]) + rv.steuerfrei_realisiert_eur
        bucket["steuerpflichtig_eur"] = (
            float(bucket["steuerpflichtig_eur"]) + rv.steuerpflichtig_realisiert_eur
        )

    purchases = load_purchases_csv(with_deposits=True)
    sells = load_sells_csv(with_withdrawals=True)
    rows: list[dict[str, object]] = []
    for coin in sorted(buckets):
        b = buckets[coin]
        einstand = float(b["einstand_eur"])
        gv = float(b["gv_eur"])
        gv_pct = (gv / einstand * 100.0) if einstand > 1e-12 else None
        frei = float(b["steuerfrei_eur"])
        pflicht = float(b["steuerpflichtig_eur"])
        if abs(frei) > 1e-8 and abs(pflicht) > 1e-8:
            status = "gemischt"
        elif abs(pflicht) > 1e-8:
            status = "steuerpflichtig"
        elif abs(frei) > 1e-8:
            status = "steuerfrei"
        else:
            status = "neutral"

        rows.append(
            {
                "Coin": coin,
                "Gekauft gesamt": _menge_summe(purchases, coin),
                "Verkauft gesamt": _menge_summe(sells, coin),
                "Real. G/V (EUR)": gv,
                "Real. G/V (%)": gv_pct,
                "Steuerfrei (EUR)": frei,
                "Steuerpflichtig (EUR)": pflicht,
                "Steuer-Status": status,
            }
        )
    return pd.DataFrame(rows)


def _anteil_steuerpflichtig(verkaufsdatum: date, kaufdatum: date) -> bool:
    return verkaufsdatum < steuerfrei_ab(kaufdatum)


def _berechne_realisierten_verkauf(match: FifoVerkaufMatch) -> RealisierterVerkauf:
    erloes_bekannt = match.verkaufspreis_eur is not None
    einstand_bekannt = match.einstand_bekannt
    gebuehr_bekannt = match.gebuehr_bekannt

    erloes_brutto = match.menge * match.verkaufspreis_eur if erloes_bekannt else None
    gebuehr = match.gebuehr_eur if gebuehr_bekannt else 0.0
    erloes_netto = (
        erloes_brutto - gebuehr if erloes_bekannt and erloes_brutto is not None else None
    )
    einstand = match.einstand_eur if einstand_bekannt else None

    steuerfrei = 0.0
    steuerpflichtig = 0.0

    if (
        erloes_netto is not None
        and einstand_bekannt
        and match.menge > 1e-12
    ):
        for anteil in match.anteile:
            anteil_erloes_net = (anteil.menge / match.menge) * erloes_netto
            anteil_gv = anteil_erloes_net - anteil.einstand_eur
            if _anteil_steuerpflichtig(match.verkaufsdatum, anteil.kaufdatum):
                steuerpflichtig += anteil_gv
            else:
                steuerfrei += anteil_gv

    gv_eur: float | None = None
    gv_prozent: float | None = None
    if erloes_netto is not None and einstand is not None:
        gv_eur = erloes_netto - einstand
        if einstand > 1e-12:
            gv_prozent = (gv_eur / einstand) * 100.0

    vollstaendig = erloes_bekannt and einstand_bekannt

    return RealisierterVerkauf(
        coin=match.coin,
        verkaufsdatum=match.verkaufsdatum,
        menge=match.menge,
        einstand_eur=einstand,
        erloes_eur=erloes_netto,
        gebuehr_eur=match.gebuehr_eur if gebuehr_bekannt else None,
        gebuehr_geschaetzt=match.gebuehr_geschaetzt if gebuehr_bekannt else False,
        gv_eur=gv_eur,
        gv_prozent=gv_prozent,
        steuerfrei_realisiert_eur=steuerfrei,
        steuerpflichtig_realisiert_eur=steuerpflichtig,
        erloes_bekannt=erloes_bekannt,
        einstand_bekannt=einstand_bekannt,
        gebuehr_bekannt=gebuehr_bekannt,
        vollstaendig_berechenbar=vollstaendig,
    )


def berechne_bilanz() -> BilanzResult:
    """Realisierte Bilanz aus kaeufe.csv und verkaeufe.csv (FIFO)."""
    purchases = load_purchases_csv(with_deposits=True)
    sells_df = load_sells_csv(with_withdrawals=True)
    matches = fifo_zuordnung_fuer_verkaeufe(purchases, sells_df)

    verkaeufe = [_berechne_realisierten_verkauf(match) for match in matches]

    realisierte_gewinne = 0.0
    realisierte_verluste = 0.0
    netto = 0.0
    einstand_verkauft = 0.0
    gebuehren_gesamt = 0.0
    gebuehren_geschaetzt = 0
    unbekannter_erloes = 0
    unvollstaendige: list[str] = []

    for rv in verkaeufe:
        if not rv.erloes_bekannt:
            unbekannter_erloes += 1
        if rv.gebuehr_bekannt and rv.gebuehr_eur is not None:
            gebuehren_gesamt += rv.gebuehr_eur
            if rv.gebuehr_geschaetzt:
                gebuehren_geschaetzt += 1
        if not rv.vollstaendig_berechenbar or rv.gv_eur is None:
            continue
        netto += rv.gv_eur
        if rv.gv_eur > 0:
            realisierte_gewinne += rv.gv_eur
        elif rv.gv_eur < 0:
            realisierte_verluste += abs(rv.gv_eur)
        if rv.einstand_eur is not None:
            einstand_verkauft += rv.einstand_eur

    for match in matches:
        if match.menge_ohne_einstand > 1e-12 or not match.anteile:
            unvollstaendige.append(
                f"{match.coin} am {match.verkaufsdatum.isoformat()}: "
                f"{match.menge_ohne_einstand:.8f} ohne passenden Kauf in kaeufe.csv"
            )

    rendite: float | None = None
    if einstand_verkauft > 1e-12:
        rendite = (netto / einstand_verkauft) * 100.0

    messages: list[str] = []
    if unbekannter_erloes:
        messages.append(
            f"{unbekannter_erloes} Verkauf/Verkäufe ohne Preis/Gebühr in verkaeufe.csv "
            "(bitte „Daten von Binance aktualisieren“)."
        )
    if unvollstaendige:
        messages.append(f"{len(unvollstaendige)} Verkauf/Verkäufe mit unbekanntem Einstand.")
    if gebuehren_geschaetzt:
        messages.append(
            f"{gebuehren_geschaetzt} Verkaufs-Gebühr/en mit Live-Kurs-Fallback "
            "(kein historisches Binance-Paar am Trade-Tag)."
        )

    return BilanzResult(
        ok=True,
        message=" ".join(messages) if messages else "Bilanz berechnet.",
        verkaeufe=verkaeufe,
        realisierte_gewinne_eur=realisierte_gewinne,
        realisierte_verluste_eur=realisierte_verluste,
        netto_realisiert_eur=netto,
        rendite_auf_verkauftes_pct=rendite,
        gebuehren_gesamt_eur=gebuehren_gesamt,
        gebuehren_geschaetzt_anzahl=gebuehren_geschaetzt,
        unbekannter_erloes_anzahl=unbekannter_erloes,
        unvollstaendige_verkaeufe=unvollstaendige,
    )


def jahres_bilanz_dataframe(bilanz: BilanzResult) -> pd.DataFrame:
    """Realisierte G/V und Steuer-Aufteilung pro Kalenderjahr (Verkaufsdatum)."""
    buckets: dict[int, dict[str, float | int]] = {}

    for rv in bilanz.verkaeufe:
        if not rv.vollstaendig_berechenbar or rv.gv_eur is None:
            continue
        year = rv.verkaufsdatum.year
        bucket = buckets.setdefault(
            year,
            {
                "verkaeufe": 0,
                "gewinne": 0.0,
                "verluste": 0.0,
                "netto": 0.0,
                "steuerfrei": 0.0,
                "steuerpflichtig": 0.0,
                "gebuehren": 0.0,
                "einstand": 0.0,
            },
        )
        bucket["verkaeufe"] = int(bucket["verkaeufe"]) + 1
        gv = float(rv.gv_eur)
        bucket["netto"] = float(bucket["netto"]) + gv
        if gv > 0:
            bucket["gewinne"] = float(bucket["gewinne"]) + gv
        elif gv < 0:
            bucket["verluste"] = float(bucket["verluste"]) + abs(gv)
        bucket["steuerfrei"] = float(bucket["steuerfrei"]) + rv.steuerfrei_realisiert_eur
        bucket["steuerpflichtig"] = (
            float(bucket["steuerpflichtig"]) + rv.steuerpflichtig_realisiert_eur
        )
        if rv.gebuehr_bekannt and rv.gebuehr_eur is not None:
            bucket["gebuehren"] = float(bucket["gebuehren"]) + rv.gebuehr_eur
        if rv.einstand_eur is not None:
            bucket["einstand"] = float(bucket["einstand"]) + rv.einstand_eur

    columns = [
        "Jahr",
        "Verkäufe",
        "Real. Gewinne (EUR)",
        "Real. Verluste (EUR)",
        "Netto realisiert (EUR)",
        "Steuerfrei (EUR)",
        "Steuerpflichtig (EUR)",
        "Gebühren (EUR)",
        "Rendite (%)",
    ]
    if not buckets:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, object]] = []
    for year in sorted(buckets):
        b = buckets[year]
        einstand = float(b["einstand"])
        netto = float(b["netto"])
        rendite = (netto / einstand * 100.0) if einstand > 1e-12 else None
        rows.append(
            {
                "Jahr": year,
                "Verkäufe": int(b["verkaeufe"]),
                "Real. Gewinne (EUR)": float(b["gewinne"]),
                "Real. Verluste (EUR)": float(b["verluste"]),
                "Netto realisiert (EUR)": netto,
                "Steuerfrei (EUR)": float(b["steuerfrei"]),
                "Steuerpflichtig (EUR)": float(b["steuerpflichtig"]),
                "Gebühren (EUR)": float(b["gebuehren"]),
                "Rendite (%)": rendite,
            }
        )

    total_einstand = sum(float(buckets[y]["einstand"]) for y in buckets)
    rows.append(
        {
            "Jahr": "Gesamt",
            "Verkäufe": sum(int(buckets[y]["verkaeufe"]) for y in buckets),
            "Real. Gewinne (EUR)": bilanz.realisierte_gewinne_eur,
            "Real. Verluste (EUR)": bilanz.realisierte_verluste_eur,
            "Netto realisiert (EUR)": bilanz.netto_realisiert_eur,
            "Steuerfrei (EUR)": sum(float(buckets[y]["steuerfrei"]) for y in buckets),
            "Steuerpflichtig (EUR)": sum(float(buckets[y]["steuerpflichtig"]) for y in buckets),
            "Gebühren (EUR)": sum(float(buckets[y]["gebuehren"]) for y in buckets),
            "Rendite (%)": (
                (bilanz.netto_realisiert_eur / total_einstand * 100.0)
                if total_einstand > 1e-12
                else None
            ),
        }
    )
    return pd.DataFrame(rows)


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV für Excel (UTF-8 mit BOM, Komma-Trenner)."""
    return df.to_csv(index=False, lineterminator="\n").encode("utf-8-sig")


def verkaeufe_to_dataframe(verkaeufe: list[RealisierterVerkauf]) -> pd.DataFrame:
    """Realisierte Verkäufe als Tabelle für die UI."""
    rows: list[dict[str, object]] = []
    for rv in verkaeufe:
        if not rv.vollstaendig_berechenbar:
            continue
        rows.append(
            {
                "Coin": rv.coin,
                "Verkaufsdatum": rv.verkaufsdatum.isoformat(),
                "Menge verkauft": rv.menge,
                "Einstand (EUR)": rv.einstand_eur,
                "Erlös (EUR)": rv.erloes_eur,
                "Gebühr (EUR)": rv.gebuehr_eur,
                "G/V (EUR)": rv.gv_eur,
                "G/V (%)": rv.gv_prozent,
                "Steuer-Status": steuer_status_label(rv),
                "Steuerfrei (EUR)": rv.steuerfrei_realisiert_eur,
                "Steuerpflichtig (EUR)": rv.steuerpflichtig_realisiert_eur,
            }
        )
    return pd.DataFrame(rows)


__all__ = [
    "BilanzResult",
    "RealisierterVerkauf",
    "berechne_bilanz",
    "coin_gesamtuebersicht_dataframe",
    "coin_verkauf_summary_dataframe",
    "dataframe_to_csv_bytes",
    "jahres_bilanz_dataframe",
    "steuer_status_label",
    "verkaeufe_to_dataframe",
]

