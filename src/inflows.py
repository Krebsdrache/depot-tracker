"""Kapitalflüsse: Ein- und Auszahlungen auf Binance (Spot-relevant)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from binance.exceptions import BinanceAPIException

from binance_data import (
    _asset_price_in_eur,
    _build_ticker_map,
    create_authenticated_client,
)

from core.storage import binance_dir

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEPOT_DIR = binance_dir()
ZUFLUSS_CSV = _DEPOT_DIR / "zufluesse.csv"
ZUFLUSS_META = _DEPOT_DIR / "zufluesse_meta.json"
ZUFLUSS_COLUMNS = ["typ", "coin", "datum", "menge", "wert_eur", "richtung"]

TYP_FIAT = "fiat_einzahlung"
TYP_FIAT_KARTE = "fiat_karte"
TYP_CRYPTO = "krypto_deposit"
TYP_FIAT_AUS = "fiat_auszahlung"
TYP_CRYPTO_AUS = "krypto_withdraw"
TYP_SPOT = "spot_kauf"

INFLOW_TYPES = {TYP_FIAT, TYP_FIAT_KARTE, TYP_CRYPTO}
OUTFLOW_TYPES = {TYP_FIAT_AUS, TYP_CRYPTO_AUS}
CAPITAL_FLOW_TYPES = INFLOW_TYPES | OUTFLOW_TYPES
TRANSFER_TYPES = INFLOW_TYPES
DISPLAY_TYPES = CAPITAL_FLOW_TYPES

TYPE_LABELS = {
    TYP_FIAT: "Bank (SEPA/Überweisung)",
    TYP_FIAT_KARTE: "Karte (Direktkauf)",
    TYP_CRYPTO: "Krypto (externes Wallet)",
    TYP_FIAT_AUS: "Auszahlung (Bank)",
    TYP_CRYPTO_AUS: "Auszahlung (Krypto-Wallet)",
}

_FIAT_HISTORY_START_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
LOCAL_TZ = ZoneInfo("Europe/Berlin")


@dataclass
class InflowSummary:
    """Summiert Einzahlungen von außen (Brutto, nur Zuflüsse)."""

    total_eur: float
    fiat_eur: float
    fiat_karte_eur: float
    crypto_deposits_eur: float
    anzahl_eintraege: int


@dataclass
class CapitalFlowSummary:
    """Netto-Kapital: Einzahlungen minus Auszahlungen."""

    netto_eur: float
    einzahlungen_eur: float
    auszahlungen_eur: float
    anzahl_ein: int
    anzahl_aus: int


def _load_seen_ids() -> set[str]:
    if not ZUFLUSS_META.exists():
        return set()
    try:
        payload = json.loads(ZUFLUSS_META.read_text(encoding="utf-8"))
        return {str(item) for item in payload.get("ids", [])}
    except (json.JSONDecodeError, TypeError):
        return set()


def _save_seen_ids(ids: set[str]) -> None:
    _DEPOT_DIR.mkdir(parents=True, exist_ok=True)
    ZUFLUSS_META.write_text(json.dumps({"ids": sorted(ids)}, indent=2), encoding="utf-8")


def _richtung_for_typ(typ: str) -> str:
    return "out" if typ in OUTFLOW_TYPES else "in"


def _ensure_zufluss_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in ZUFLUSS_COLUMNS:
        if column not in out.columns:
            if column == "richtung":
                out[column] = out["typ"].astype(str).map(_richtung_for_typ)
            else:
                out[column] = float("nan") if column == "wert_eur" else ""
    if "richtung" in out.columns:
        missing = out["richtung"].isna() | (out["richtung"].astype(str).str.strip() == "")
        out.loc[missing, "richtung"] = out.loc[missing, "typ"].astype(str).map(_richtung_for_typ)
    return out


def load_zufluesse_csv() -> pd.DataFrame:
    if not ZUFLUSS_CSV.exists():
        return pd.DataFrame(columns=ZUFLUSS_COLUMNS)
    return _ensure_zufluss_columns(pd.read_csv(ZUFLUSS_CSV))


def _append_rows(new_rows: list[dict[str, object]]) -> None:
    if not new_rows:
        return
    new_df = pd.DataFrame(new_rows, columns=ZUFLUSS_COLUMNS)
    if ZUFLUSS_CSV.exists():
        combined = pd.concat([load_zufluesse_csv(), new_df], ignore_index=True)
    else:
        combined = new_df
    combined.sort_values(["datum", "typ"], inplace=True)
    combined.to_csv(ZUFLUSS_CSV, index=False)


def _is_success_status(entry: dict[str, object]) -> bool:
    status = entry.get("status")
    if status in (1, "1", True):
        return True
    text = str(status).upper()
    if text in {"FAILED", "FAILURE", "REJECTED", "CANCELLED", "CANCELED", "EXPIRED"}:
        return False
    return text in {"SUCCESS", "COMPLETED", "SUCCEEDED", "FINISHED", "SUCCESSFUL", "4", "2", "0", "6"}


def _fiat_amount_to_eur(amount: float, fiat_coin: str, tickers: dict[str, float]) -> float:
    if fiat_coin == "EUR":
        return amount
    rate = _asset_price_in_eur(fiat_coin, tickers)
    return amount * rate if rate else amount


def _fetch_fiat_history(
    client,
    tickers: dict[str, float],
    *,
    transaction_type: int,
    typ: str,
    richtung: str,
    id_prefix: str,
) -> list[dict[str, object]]:
    """Fiat-Ein- oder Auszahlungen (get_fiat_deposit_withdraw_history)."""
    rows: list[dict[str, object]] = []
    page = 1
    while True:
        try:
            response = client.get_fiat_deposit_withdraw_history(
                transactionType=transaction_type,
                beginTime=_FIAT_HISTORY_START_MS,
                page=page,
                rows=500,
            )
        except BinanceAPIException:
            break

        data = response if isinstance(response, list) else response.get("data", [])
        if not data:
            break

        for entry in data:
            if not _is_success_status(entry):
                continue

            fiat_amount = float(entry.get("amount", entry.get("fiatAmount", 0)))
            fiat_coin = str(entry.get("fiatCurrency", entry.get("sourceCurrency", "EUR")))
            order_id = str(entry.get("orderNo", entry.get("id", entry.get("createTime", ""))))
            fee = float(entry.get("totalFee", entry.get("fee", 0)) or 0)
            wert_eur = _fiat_amount_to_eur(fiat_amount + fee, fiat_coin, tickers)

            ts = entry.get("createTime", entry.get("updateTime", 0))
            datum = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat()

            rows.append(
                {
                    "id": f"{id_prefix}-{order_id}",
                    "typ": typ,
                    "coin": fiat_coin,
                    "datum": datum,
                    "menge": fiat_amount,
                    "wert_eur": wert_eur,
                    "richtung": richtung,
                }
            )

        if len(data) < 500:
            break
        page += 1

    return rows


def _fetch_fiat_deposits(client, tickers: dict[str, float]) -> list[dict[str, object]]:
    return _fetch_fiat_history(
        client,
        tickers,
        transaction_type=0,
        typ=TYP_FIAT,
        richtung="in",
        id_prefix="fiat",
    )


def _fetch_fiat_withdrawals(client, tickers: dict[str, float]) -> list[dict[str, object]]:
    return _fetch_fiat_history(
        client,
        tickers,
        transaction_type=1,
        typ=TYP_FIAT_AUS,
        richtung="out",
        id_prefix="fiat-out",
    )


def _fetch_fiat_card_payments(client, tickers: dict[str, float]) -> list[dict[str, object]]:
    """Zahlungen per Karte (Binance Fiat Payments – z. B. Krypto mit Karte kaufen)."""
    rows: list[dict[str, object]] = []
    page = 1
    while True:
        try:
            response = client.get_fiat_payments_history(
                transactionType=0,
                beginTime=_FIAT_HISTORY_START_MS,
                page=page,
                rows=500,
            )
        except BinanceAPIException:
            break

        data = response if isinstance(response, list) else response.get("data", [])
        if not data:
            break

        for entry in data:
            if not _is_success_status(entry):
                continue

            fiat_amount = float(entry.get("sourceAmount", entry.get("amount", 0)))
            fiat_coin = str(entry.get("fiatCurrency", "EUR"))
            order_id = str(entry.get("orderNo", entry.get("id", entry.get("createTime", ""))))
            crypto = str(entry.get("cryptoCurrency", ""))
            wert_eur = _fiat_amount_to_eur(fiat_amount, fiat_coin, tickers)
            obtain = entry.get("obtainAmount", entry.get("indicatedAmount"))
            if obtain is not None and float(obtain) > 0:
                crypto_menge = float(obtain)
            else:
                crypto_menge = fiat_amount

            ts = entry.get("createTime", entry.get("updateTime", 0))
            datum = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).isoformat()

            rows.append(
                {
                    "id": f"karte-{order_id}",
                    "typ": TYP_FIAT_KARTE,
                    "coin": f"{fiat_coin}→{crypto}" if crypto else fiat_coin,
                    "datum": datum,
                    "menge": crypto_menge,
                    "wert_eur": wert_eur,
                    "richtung": "in",
                }
            )

        if len(data) < 500:
            break
        page += 1

    return rows


def _fetch_crypto_deposits(client, tickers: dict[str, float]) -> list[dict[str, object]]:
    """Krypto von externen Wallets auf Binance (Deposit-Historie)."""
    rows: list[dict[str, object]] = []
    start_time: int | None = None

    while True:
        params: dict[str, int] = {"limit": 1000}
        if start_time is not None:
            params["startTime"] = start_time

        try:
            batch = client.get_deposit_history(**params)
        except BinanceAPIException:
            break

        if not batch:
            break

        for entry in batch:
            if not _is_success_status(entry):
                continue

            coin = str(entry["coin"])
            menge = float(entry["amount"])
            price = _asset_price_in_eur(coin, tickers)
            wert_eur = menge * price if price is not None else 0.0
            tx_id = str(entry.get("txId", entry.get("id", "")))
            insert_time = int(entry["insertTime"])
            datum = datetime.fromtimestamp(insert_time / 1000, tz=timezone.utc).isoformat()

            rows.append(
                {
                    "id": f"crypto-{tx_id}-{coin}-{insert_time}",
                    "typ": TYP_CRYPTO,
                    "coin": coin,
                    "datum": datum,
                    "menge": menge,
                    "wert_eur": wert_eur,
                    "richtung": "in",
                }
            )

        if len(batch) < 1000:
            break
        start_time = int(batch[-1]["insertTime"]) + 1

    return rows


def _fetch_crypto_withdrawals(client, tickers: dict[str, float]) -> list[dict[str, object]]:
    """Krypto von Binance an externe Wallets (Withdraw-Historie, netto inkl. Gebühr)."""
    rows: list[dict[str, object]] = []
    start_time: int | None = None

    while True:
        params: dict[str, int] = {"limit": 1000}
        if start_time is not None:
            params["startTime"] = start_time

        try:
            batch = client.get_withdraw_history(**params)
        except BinanceAPIException:
            break

        if not batch:
            break

        for entry in batch:
            if not _is_success_status(entry):
                continue

            coin = str(entry["coin"])
            menge = float(entry.get("amount", 0))
            fee = float(entry.get("transactionFee", 0) or 0)
            total_coin = menge + fee
            price = _asset_price_in_eur(coin, tickers)
            wert_eur = total_coin * price if price is not None else 0.0
            tx_id = str(entry.get("txId", entry.get("id", "")))
            apply_time = int(entry.get("applyTime", entry.get("completeTime", 0)))
            datum = datetime.fromtimestamp(apply_time / 1000, tz=timezone.utc).isoformat()

            rows.append(
                {
                    "id": f"crypto-out-{tx_id}-{coin}-{apply_time}",
                    "typ": TYP_CRYPTO_AUS,
                    "coin": coin,
                    "datum": datum,
                    "menge": menge,
                    "wert_eur": wert_eur,
                    "richtung": "out",
                }
            )

        if len(batch) < 1000:
            break
        start_time = int(batch[-1]["applyTime"]) + 1

    return rows


def sync_inflows_from_binance() -> tuple[bool, str, int]:
    """Holt Ein- und Auszahlungen von Binance (keine Spot-Käufe, keine internen Transfers)."""
    client_result = create_authenticated_client()
    if not client_result.ok:
        return False, client_result.message, 0

    assert client_result.client is not None
    client = client_result.client
    tickers = _build_ticker_map(client)

    seen = _load_seen_ids()
    new_rows: list[dict[str, object]] = []

    fetchers = (
        _fetch_fiat_deposits,
        _fetch_fiat_withdrawals,
        _fetch_fiat_card_payments,
        _fetch_crypto_deposits,
        _fetch_crypto_withdrawals,
    )
    for fetch in fetchers:
        for record in fetch(client, tickers):
            record_id = str(record.pop("id"))
            if record_id in seen:
                continue
            seen.add(record_id)
            new_rows.append(record)

    _append_rows(new_rows)
    _save_seen_ids(seen)

    total = len(load_zufluesse_csv())
    message = (
        f"Kapitalflüsse aktualisiert: {len(new_rows)} neue Einträge "
        f"(insgesamt {total} in der Historie)."
    )
    return True, message, len(new_rows)


def _signed_eur(row: pd.Series) -> float:
    amount = abs(float(row["wert_eur"]))
    richtung = str(row.get("richtung", _richtung_for_typ(str(row["typ"]))))
    return -amount if richtung == "out" else amount


def compute_inflow_summary() -> InflowSummary:
    """Summiert Einzahlungen: Bank, Karte und externe Krypto-Wallets."""
    df = load_zufluesse_csv()
    transfers = df[df["typ"].isin(INFLOW_TYPES)] if not df.empty else df

    if transfers.empty:
        return InflowSummary(0.0, 0.0, 0.0, 0.0, 0)

    fiat = float(transfers.loc[transfers["typ"] == TYP_FIAT, "wert_eur"].sum())
    karte = float(transfers.loc[transfers["typ"] == TYP_FIAT_KARTE, "wert_eur"].sum())
    crypto = float(transfers.loc[transfers["typ"] == TYP_CRYPTO, "wert_eur"].sum())

    return InflowSummary(
        total_eur=fiat + karte + crypto,
        fiat_eur=fiat,
        fiat_karte_eur=karte,
        crypto_deposits_eur=crypto,
        anzahl_eintraege=len(transfers),
    )


def compute_capital_flow_summary() -> CapitalFlowSummary:
    """Netto eingezahltes Kapital (Einzahlungen minus Auszahlungen)."""
    df = load_zufluesse_csv()
    flows = df[df["typ"].isin(CAPITAL_FLOW_TYPES)] if not df.empty else df

    if flows.empty:
        return CapitalFlowSummary(0.0, 0.0, 0.0, 0, 0)

    signed = flows.apply(_signed_eur, axis=1)
    ein = float(signed[signed > 0].sum())
    aus = float(-signed[signed < 0].sum())
    return CapitalFlowSummary(
        netto_eur=float(signed.sum()),
        einzahlungen_eur=ein,
        auszahlungen_eur=aus,
        anzahl_ein=int((signed > 0).sum()),
        anzahl_aus=int((signed < 0).sum()),
    )


def capital_flow_timeseries() -> pd.DataFrame:
    """
    Chronologische Kapitalflüsse für den Graphen.

    Spalten: zeit, delta_eur, kapital_netto, typ, typ_label, coin, beschreibung
    """
    df = load_zufluesse_csv()
    flows = df[df["typ"].isin(CAPITAL_FLOW_TYPES)].copy()
    empty_cols = ["zeit", "delta_eur", "kapital_netto", "typ", "typ_label", "coin", "beschreibung"]
    if flows.empty:
        return pd.DataFrame(columns=empty_cols)

    flows["_sort"] = flows["datum"].map(_parse_utc_datum)
    flows = flows.sort_values("_sort")
    flows["delta_eur"] = flows.apply(_signed_eur, axis=1)
    flows["kapital_netto"] = flows["delta_eur"].cumsum()
    flows["typ_label"] = flows["typ"].map(TYPE_LABELS).fillna(flows["typ"])
    flows["beschreibung"] = flows.apply(
        lambda row: f"{'+' if row['delta_eur'] >= 0 else ''}{row['delta_eur']:.2f} EUR · {row['typ_label']}",
        axis=1,
    )
    flows["zeit"] = flows["_sort"].dt.tz_convert(LOCAL_TZ)
    return flows[
        ["zeit", "delta_eur", "kapital_netto", "typ", "typ_label", "coin", "beschreibung"]
    ].reset_index(drop=True)


CAPITAL_FLOW_RANGE_OPTIONS: dict[str, str] = {
    "1m": "Letzter Monat",
    "3m": "Letzte 3 Monate",
    "6m": "Letzte 6 Monate",
    "1y": "Letztes Jahr",
    "2y": "Letzte 2 Jahre",
    "max": "Gesamt",
}

_RANGE_OFFSETS: dict[str, pd.DateOffset] = {
    "1m": pd.DateOffset(months=1),
    "3m": pd.DateOffset(months=3),
    "6m": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1),
    "2y": pd.DateOffset(years=2),
}


def _capital_flow_range_cutoff(
    range_key: str,
    now: pd.Timestamp,
) -> pd.Timestamp | None:
    if range_key == "max":
        return None
    offset = _RANGE_OFFSETS.get(range_key)
    if offset is None:
        return None
    return now - offset


def to_altair_naive_local(ts: pd.Timestamp) -> pd.Timestamp:
    """Altair akzeptiert nur UTC oder naive lokale datetimes (ohne ZoneInfo)."""
    if ts.tzinfo is not None:
        return ts.tz_convert(LOCAL_TZ).tz_localize(None)
    return ts


def altair_capital_flow_frames(
    line_df: pd.DataFrame,
    points_df: pd.DataFrame,
    x_min: pd.Timestamp,
    x_max: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """Konvertiert Chart-Daten für Altair (naive lokale Zeitachse)."""
    line_out = line_df.copy()
    points_out = points_df.copy()
    if not line_out.empty:
        line_out["zeit"] = line_out["zeit"].map(to_altair_naive_local)
    if not points_out.empty:
        points_out["zeit"] = points_out["zeit"].map(to_altair_naive_local)
    return (
        line_out,
        points_out,
        to_altair_naive_local(x_min),
        to_altair_naive_local(x_max),
    )


@dataclass(frozen=True)
class CapitalFlowDonutView:
    """Daten für Donut-Chart neben dem Kapitalfluss-Zeitstrahl."""

    slices: pd.DataFrame
    center_eur: float
    center_subtitle: str
    total_netto_eur: float
    period_net_eur: float
    period_ein_eur: float
    period_aus_eur: float


def _now_local(now: pd.Timestamp | None = None) -> pd.Timestamp:
    now_ts = now or pd.Timestamp.now(tz=LOCAL_TZ)
    if now_ts.tzinfo is None:
        return now_ts.tz_localize(LOCAL_TZ)
    return now_ts.tz_convert(LOCAL_TZ)


def _flow_series_in_range(
    flow_series: pd.DataFrame,
    range_key: str,
    *,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Filtert Buchungen auf den gewählten Zeitraum (Gesamt = alle)."""
    if flow_series.empty:
        return flow_series.copy()

    now_ts = _now_local(now)
    series = flow_series.sort_values("zeit").reset_index(drop=True)
    cutoff = _capital_flow_range_cutoff(range_key, now_ts)
    if cutoff is None:
        return series
    return series[series["zeit"] >= cutoff].copy()


def prepare_capital_flow_donut(
    flow_series: pd.DataFrame,
    range_key: str,
    total_netto_eur: float,
    *,
    now: pd.Timestamp | None = None,
) -> CapitalFlowDonutView:
    """
    Donut: Kreis = immer Gesamt-Netto (100 %).
    Bei Teiltermin: Anteil des Zeitraums hell, Rest gedimmt.
    Mitte: Netto im Zeitraum bzw. Gesamt; Untertitel mit %-Anteil.
    """
    visible = _flow_series_in_range(flow_series, range_key, now=now)
    ein = float(visible.loc[visible["delta_eur"] > 0, "delta_eur"].sum()) if not visible.empty else 0.0
    aus = float(-visible.loc[visible["delta_eur"] < 0, "delta_eur"].sum()) if not visible.empty else 0.0
    period_net = ein - aus
    total_abs = abs(total_netto_eur)

    if range_key == "max":
        center_eur = total_netto_eur
        center_subtitle = "Netto eingezahlt (Gesamt)"
        highlight = total_abs
        rest = 0.0
    else:
        center_eur = period_net
        if abs(total_netto_eur) > 1e-9:
            pct = period_net / total_netto_eur * 100.0
            center_subtitle = f"{pct:.1f} % vom Gesamt ({total_netto_eur:,.0f} €)"
        else:
            center_subtitle = "Kein Gesamt-Kapital zum Vergleich"
        period_abs = abs(period_net)
        highlight = min(period_abs, total_abs) if total_abs > 1e-9 else period_abs
        rest = max(total_abs - highlight, 0.0)

    if total_abs <= 1e-9:
        slices = pd.DataFrame([{"Kategorie": "Kein Kapital", "value": 1.0, "hell": True}])
    elif highlight <= 1e-9:
        slices = pd.DataFrame([{"Kategorie": "Rest", "value": rest, "hell": False}])
    elif rest <= 1e-9:
        label = "Gesamt" if range_key == "max" else "Zeitraum"
        slices = pd.DataFrame([{"Kategorie": label, "value": highlight, "hell": True}])
    else:
        slices = pd.DataFrame(
            [
                {"Kategorie": "Zeitraum", "value": highlight, "hell": True},
                {"Kategorie": "Rest", "value": rest, "hell": False},
            ]
        )

    return CapitalFlowDonutView(
        slices=slices,
        center_eur=center_eur,
        center_subtitle=center_subtitle,
        total_netto_eur=total_netto_eur,
        period_net_eur=period_net,
        period_ein_eur=ein,
        period_aus_eur=aus,
    )


def prepare_capital_flow_chart_data(
    flow_series: pd.DataFrame,
    range_key: str,
    *,
    now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    """
    Bereitet Linien- und Punkt-Daten für den Kapitalfluss-Chart vor.

    Die kumulierte Höhe bleibt korrekt: vor dem Fenster wird ein Ankerpunkt
    mit dem Kapitalstand zum Schnittzeitpunkt gesetzt.
    """
    empty = pd.DataFrame(
        columns=["zeit", "kapital_netto", "beschreibung", "typ_label", "delta_eur", "typ", "coin"]
    )
    if flow_series.empty:
        now_ts = _now_local(now)
        return empty, empty, now_ts, now_ts

    now_ts = _now_local(now)
    series = flow_series.sort_values("zeit").reset_index(drop=True)
    cutoff = _capital_flow_range_cutoff(range_key, now_ts)

    if cutoff is None:
        x_min = series["zeit"].iloc[0] - pd.Timedelta(seconds=1)
        x_max = now_ts
        baseline = 0.0
        visible = series
    else:
        x_min = cutoff
        x_max = now_ts
        before = series[series["zeit"] < cutoff]
        baseline = float(before["kapital_netto"].iloc[-1]) if not before.empty else 0.0
        visible = series[series["zeit"] >= cutoff].copy()

    anchor = pd.DataFrame(
        [
            {
                "zeit": x_min,
                "kapital_netto": baseline,
                "beschreibung": "Start Zeitraum",
                "typ_label": "",
                "delta_eur": 0.0,
                "typ": "",
                "coin": "",
            }
        ]
    )

    line_df = pd.concat([anchor, visible], ignore_index=True)
    return line_df, visible, x_min, x_max


def _parse_utc_datum(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_datum_local(value: str) -> str:
    return _parse_utc_datum(value).astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")


def _rows_to_display_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    columns = ["Typ", "Richtung", "Coin", "Datum", "Menge", "Betrag (EUR)", "Kapital netto (EUR)"]
    if not rows:
        return pd.DataFrame(columns=columns)

    out = pd.DataFrame(rows)
    out["_sort"] = out["datum"].map(_parse_utc_datum)
    out = out.sort_values("_sort")
    out["delta_eur"] = out.apply(_signed_eur, axis=1)
    out["Kapital netto (EUR)"] = out["delta_eur"].cumsum()
    out["Typ"] = out["typ"].map(TYPE_LABELS).fillna(out["typ"])
    out["Richtung"] = out["richtung"].map({"in": "Ein", "out": "Aus"}).fillna(out["richtung"])
    out["Datum"] = out["datum"].map(_format_datum_local)
    out["Betrag (EUR)"] = out["delta_eur"]
    out = out.sort_values("_sort", ascending=False)
    return out.rename(
        columns={
            "coin": "Coin",
            "menge": "Menge",
        }
    )[columns]


def capital_flows_to_dataframe(
    types: set[str] | None = None,
) -> pd.DataFrame:
    """Kapitalflüsse (Ein- und Auszahlung) – filterbar nach Typ."""
    allowed = types if types is not None else CAPITAL_FLOW_TYPES
    rows: list[dict[str, object]] = []

    df = load_zufluesse_csv()
    if not df.empty:
        for _, row in df[df["typ"].isin(CAPITAL_FLOW_TYPES & allowed)].iterrows():
            rows.append(row.to_dict())

    return _rows_to_display_df(rows) if rows else _rows_to_display_df([])


def transfers_to_dataframe() -> pd.DataFrame:
    """Nur Einzahlungen von außen (ohne Spot-Markt)."""
    return capital_flows_to_dataframe(types=INFLOW_TYPES)


inflows_to_dataframe = transfers_to_dataframe

__all__ = [
    "CAPITAL_FLOW_TYPES",
    "CapitalFlowSummary",
    "INFLOW_TYPES",
    "OUTFLOW_TYPES",
    "InflowSummary",
    "DISPLAY_TYPES",
    "TRANSFER_TYPES",
    "TYPE_LABELS",
    "TYP_CRYPTO",
    "TYP_CRYPTO_AUS",
    "TYP_FIAT",
    "TYP_FIAT_AUS",
    "TYP_FIAT_KARTE",
    "CapitalFlowDonutView",
    "CAPITAL_FLOW_RANGE_OPTIONS",
    "capital_flow_timeseries",
    "capital_flows_to_dataframe",
    "compute_capital_flow_summary",
    "compute_inflow_summary",
    "inflows_to_dataframe",
    "load_zufluesse_csv",
    "prepare_capital_flow_chart_data",
    "prepare_capital_flow_donut",
    "sync_inflows_from_binance",
    "to_altair_naive_local",
    "altair_capital_flow_frames",
    "transfers_to_dataframe",
]
