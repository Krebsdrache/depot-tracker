"""Depot-Tracker – Streamlit-Dashboard mit echten Binance-Daten (nur Anzeige)."""

from __future__ import annotations

import importlib

import altair as alt
import pandas as pd
import streamlit as st

from core import registry as registry_mod

import bilanz as bilanz_mod
import binance_data as binance_data_mod
import fee_rates as fee_rates_mod
import gebuehren as gebuehren_mod
import inflows as inflows_mod
import portfolio_history as portfolio_history_mod
import steuer as steuer_mod
import what_if as what_if_mod
import ath_prices as ath_prices_mod

# Streamlit hot-reload can leave outdated modules in sys.modules.
fee_rates_mod = importlib.reload(fee_rates_mod)
binance_data_mod = importlib.reload(binance_data_mod)
bilanz_mod = importlib.reload(bilanz_mod)
gebuehren_mod = importlib.reload(gebuehren_mod)
inflows_mod = importlib.reload(inflows_mod)
portfolio_history_mod = importlib.reload(portfolio_history_mod)
steuer_mod = importlib.reload(steuer_mod)
what_if_mod = importlib.reload(what_if_mod)
ath_prices_mod = importlib.reload(ath_prices_mod)
import price_zones as price_zones_mod

price_zones_mod = importlib.reload(price_zones_mod)
inflows = inflows_mod

SteuerResult = steuer_mod.SteuerResult
berechne_frist_kalender = steuer_mod.berechne_frist_kalender
haltefrist_status_text = steuer_mod.haltefrist_status_text
load_steuer_uebersicht = steuer_mod.load_steuer_uebersicht

capital_flow_timeseries = inflows_mod.capital_flow_timeseries
compute_capital_flow_summary = inflows_mod.compute_capital_flow_summary
compute_inflow_summary = inflows_mod.compute_inflow_summary
sync_inflows_from_binance = inflows_mod.sync_inflows_from_binance

PortfolioResult = binance_data_mod.PortfolioResult
backfill_trade_csv_from_binance = binance_data_mod.backfill_trade_csv_from_binance
load_portfolio = binance_data_mod.load_portfolio
sync_trade_history_from_binance = binance_data_mod.sync_trade_history_from_binance
trade_data_status = binance_data_mod.trade_data_status


def transfers_to_dataframe() -> pd.DataFrame:
    fn = getattr(inflows, "transfers_to_dataframe", None) or getattr(
        inflows, "inflows_to_dataframe", None
    )
    if fn is not None:
        return fn()
    return capital_flows_to_dataframe(
        getattr(inflows, "TRANSFER_TYPES", {"fiat_einzahlung", "fiat_karte", "krypto_deposit"})
    )


def capital_flows_to_dataframe(types: set[str] | None = None) -> pd.DataFrame:
    fn = getattr(inflows, "capital_flows_to_dataframe", None)
    if fn is not None:
        return fn(types)
    return transfers_to_dataframe()


from daily_cache import format_loaded_at_display, has_any_snapshot, load_latest_snapshot
from settings import (
    PRICE_MODE_FROZEN,
    PRICE_MODE_LIVE_DAILY,
    get_price_mode,
    get_ui_pref,
    is_frozen_mode,
    persisted_data_files,
    save_price_mode,
    save_ui_pref,
)
from sync_meta import format_sync_time, get_last_trade_sync, mark_trades_synced



def _render_capital_flow_donut(donut: inflows_mod.CapitalFlowDonutView) -> None:
    """Donut: Gesamt-Kapital (100 %), Zeitraumsanteil hell hervorgehoben."""
    color_scale = alt.Scale(
        domain=[True, False],
        range=["#2563eb", "#dbeafe"],
    )
    arc = (
        alt.Chart(donut.slices)
        .mark_arc(innerRadius=72, outerRadius=112, padAngle=0.015)
        .encode(
            theta=alt.Theta("value:Q", stack=True),
            color=alt.Color(
                "hell:N",
                scale=color_scale,
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Kategorie:N", title="Anteil"),
                alt.Tooltip("value:Q", title="EUR", format=",.2f"),
            ],
            order=alt.Order("hell:O", sort="descending"),
        )
    )

    center_df = pd.DataFrame(
        [
            {
                "amount": f"{donut.center_eur:,.0f} €",
                "subtitle": donut.center_subtitle,
            }
        ]
    )
    amount = (
        alt.Chart(center_df)
        .mark_text(
            radius=0,
            theta=0,
            size=20,
            fontWeight="bold",
            align="center",
            baseline="middle",
            dy=-8,
        )
        .encode(text="amount:N")
    )
    subtitle = (
        alt.Chart(center_df)
        .mark_text(
            radius=0,
            theta=0,
            size=11,
            color="#64748b",
            align="center",
            baseline="middle",
            dy=14,
        )
        .encode(text="subtitle:N")
    )

    chart = (
        alt.layer(arc, amount, subtitle)
        .properties(width=320, height=320, padding={"left": 24, "right": 24, "top": 16, "bottom": 16})
        .configure_view(strokeWidth=0)
    )
    st.altair_chart(chart, width="content")


def _capital_flow_x_encoding(
    range_key: str,
    x_min: pd.Timestamp,
    x_max: pd.Timestamp,
) -> alt.X:
    """Zeitachse passend zum gewählten Fenster (deutsche Datums-Labels, weniger Überlappung)."""
    span_days = max((x_max - x_min) / pd.Timedelta(days=1), 1.0)
    pad = pd.Timedelta(days=max(1, int(span_days * 0.04)))
    x_scale = alt.Scale(domain=[x_min - pad * 0.05, x_max + pad], nice=False)

    axis_base = dict(
        title=None,
        grid=True,
        gridOpacity=0.22,
        domainColor="#94a3b8",
        labelColor="#334155",
        tickColor="#94a3b8",
        labelPadding=6,
        tickSize=4,
    )

    if range_key == "1m" or span_days <= 35:
        axis = alt.Axis(format="%d.%m.", tickCount=5, labelAngle=0, **axis_base)
    elif range_key in {"3m", "6m"} or span_days <= 200:
        axis = alt.Axis(
            format="%d.%m.",
            tickCount=6,
            labelAngle=-35,
            labelOverlap="greedy",
            **axis_base,
        )
    elif range_key in {"1y", "2y"} or span_days <= 800:
        axis = alt.Axis(format="%m.%Y", tickCount=6, labelAngle=0, **axis_base)
    else:
        axis = alt.Axis(
            format="%m.%Y",
            tickCount=min(10, max(4, int(span_days / 120))),
            labelAngle=-35,
            labelOverlap="greedy",
            **axis_base,
        )

    return alt.X("zeit:T", scale=x_scale, axis=axis)


def _render_capital_flow_chart(flow_series: pd.DataFrame, total_netto_eur: float) -> None:
    """Linienchart: kumuliertes Netto-Kapital über die Zeit (filterbar)."""
    if flow_series.empty:
        st.info(
            "Noch keine Kapitalflüsse in der Historie. "
            "Klicke oben rechts auf **„Daten von Binance aktualisieren“**."
        )
        return

    range_options = inflows_mod.CAPITAL_FLOW_RANGE_OPTIONS
    range_keys = list(range_options.keys())
    saved_range = get_ui_pref("capital_flow_range", "max")
    if saved_range not in range_keys:
        saved_range = "max"
    if st.session_state.get("capital_flow_range") not in range_keys:
        st.session_state["capital_flow_range"] = saved_range

    selected_range = st.radio(
        "Zeitraum",
        options=range_keys,
        format_func=lambda key: range_options[key],
        horizontal=True,
        key="capital_flow_range",
    )
    if selected_range != saved_range:
        save_ui_pref("capital_flow_range", selected_range)

    line_df, points_df, x_min, x_max = inflows_mod.prepare_capital_flow_chart_data(
        flow_series,
        selected_range,
    )
    donut_view = inflows_mod.prepare_capital_flow_donut(
        flow_series,
        selected_range,
        total_netto_eur,
    )
    line_df, points_df, x_min, x_max = inflows_mod.altair_capital_flow_frames(
        line_df,
        points_df,
        x_min,
        x_max,
    )

    x_encoding = _capital_flow_x_encoding(selected_range, x_min, x_max)

    line = (
        alt.Chart(line_df)
        .mark_line(color="#2563eb", strokeWidth=2)
        .encode(
            x=x_encoding,
            y=alt.Y("kapital_netto:Q", title="Netto eingezahlt (EUR)", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("zeit:T", title="Zeit", format="%d.%m.%Y %H:%M"),
                alt.Tooltip("kapital_netto:Q", title="Kapital netto", format=",.2f"),
            ],
        )
    )

    if points_df.empty:
        line_chart = line
    else:
        points = (
            alt.Chart(points_df)
            .mark_circle(size=70)
            .encode(
                x=x_encoding,
                y="kapital_netto:Q",
                color=alt.Color(
                    "typ_label:N",
                    title="Typ",
                ),
                tooltip=[
                    alt.Tooltip("zeit:T", title="Zeit", format="%d.%m.%Y %H:%M"),
                    alt.Tooltip("beschreibung:N", title="Buchung"),
                    alt.Tooltip("kapital_netto:Q", title="Kapital netto", format=",.2f"),
                ],
            )
        )
        line_chart = line + points

    line_col, donut_col = st.columns([2.2, 1.15], gap="medium")
    with line_col:
        st.altair_chart(line_chart.properties(height=360), width="stretch")
        if points_df.empty and selected_range != "max":
            st.caption(
                "In diesem Zeitraum keine Buchungen – Linie zeigt den Kapitalstand am Periodenstart."
            )
    with donut_col:
        st.caption("Anteil am Gesamt-Kapital")
        _render_capital_flow_donut(donut_view)
        st.caption(
            f"Gesamt: **{donut_view.total_netto_eur:,.0f} €** · "
            "Dunkelblau = gewählter Zeitraum, Hellblau = übriges Kapital"
        )

    st.caption(
        f"Zeitraum: **{range_options[selected_range]}** · "
        "Nur echte Ein- und Auszahlungen (Bank, Karte, externe Wallets). "
        "Spot-Trades, Earn-Zinsen und interne Wallet-Umschichtungen sind nicht enthalten."
    )


def _render_portfolio_history_chart(
    flow_series: pd.DataFrame,
    total_value_eur: float,
) -> None:
    """Depotwert über die Zeit, optional gegen Netto-Kapital."""
    range_options = portfolio_history_mod.CAPITAL_FLOW_RANGE_OPTIONS
    range_keys = list(range_options.keys())
    saved_range = get_ui_pref("portfolio_history_range", "max")
    if saved_range not in range_keys:
        saved_range = "max"
    if st.session_state.get("portfolio_history_range") not in range_keys:
        st.session_state["portfolio_history_range"] = saved_range

    selected_range = st.radio(
        "Zeitraum",
        options=range_keys,
        format_func=lambda key: range_options[key],
        horizontal=True,
        key="portfolio_history_range",
    )
    if selected_range != saved_range:
        save_ui_pref("portfolio_history_range", selected_range)

    sources_stamp = portfolio_history_mod.sources_fingerprint()
    history_version = int(st.session_state.get("history_version", 0))
    portfolio_series = _cached_portfolio_timeseries(
        sources_stamp,
        history_version,
        float(total_value_eur),
    )

    if portfolio_series.empty:
        st.info(
            "Noch keine Daten für die Depot-Entwicklung. "
            "Synchronisiere **Trades** und **Kapitalflüsse** über „Daten von Binance aktualisieren“."
        )
        return

    chart_view = portfolio_history_mod.prepare_portfolio_chart_data(
        portfolio_series,
        flow_series,
        selected_range,
    )
    if chart_view.line_df.empty:
        st.info("Keine Datenpunkte im gewählten Zeitraum.")
        return

    x_encoding = _capital_flow_x_encoding(
        selected_range,
        chart_view.x_min,
        chart_view.x_max,
    )
    y_scale = alt.Scale(zero=False)

    gain_area = (
        alt.Chart(chart_view.line_df[chart_view.line_df["gewinn"]])
        .mark_area(opacity=0.28, color="#16a34a")
        .encode(
            x=x_encoding,
            y=alt.Y("y0:Q", scale=y_scale, title="EUR"),
            y2="y1:Q",
        )
    )
    loss_area = (
        alt.Chart(chart_view.line_df[~chart_view.line_df["gewinn"]])
        .mark_area(opacity=0.28, color="#dc2626")
        .encode(
            x=x_encoding,
            y=alt.Y("y0:Q", scale=y_scale),
            y2="y1:Q",
        )
    )

    layers: list[alt.Chart] = [gain_area, loss_area]

    if chart_view.has_capital:
        capital_line = (
            alt.Chart(chart_view.line_df)
            .mark_line(color="#64748b", strokeWidth=2, strokeDash=[6, 4])
            .encode(
                x=x_encoding,
                y=alt.Y("kapital_netto:Q", scale=y_scale),
                tooltip=[
                    alt.Tooltip("zeit:T", title="Datum", format="%d.%m.%Y"),
                    alt.Tooltip("kapital_netto:Q", title="Netto-Kapital", format=",.2f"),
                ],
            )
        )
        layers.append(capital_line)

    depot_line = (
        alt.Chart(chart_view.line_df)
        .mark_line(color="#2563eb", strokeWidth=2.5)
        .encode(
            x=x_encoding,
            y=alt.Y("depotwert_eur:Q", scale=y_scale),
            tooltip=[
                alt.Tooltip("zeit:T", title="Datum", format="%d.%m.%Y"),
                alt.Tooltip("depotwert_eur:Q", title="Depotwert", format=",.2f"),
                alt.Tooltip(
                    "kapital_netto:Q",
                    title="Netto-Kapital",
                    format=",.2f",
                ),
                alt.Tooltip(
                    "performance_eur:Q",
                    title="Kursgewinn/-verlust",
                    format="+,.2f",
                ),
            ],
        )
    )
    layers.append(depot_line)

    chart = alt.layer(*layers).resolve_scale(y="shared")
    st.altair_chart(chart.properties(height=380), width="stretch")

    last = chart_view.line_df.iloc[-1]
    perf = float(last["performance_eur"])
    kapital = float(last["kapital_netto"])
    perf_pct = (perf / kapital * 100.0) if abs(kapital) > 1e-9 else 0.0
    st.caption(
        f"Zeitraum: **{range_options[selected_range]}** · "
        f"Blau = **Depotwert**, gestrichelt = **Netto-Kapital** · "
        f"Grün/Rot = Kursgewinn/-verlust vs. eingezahltem Kapital · "
        f"Stand Ende: **{float(last['depotwert_eur']):,.0f} €** "
        f"({perf:+,.0f} € vs. Kapital, {perf_pct:+.1f} %)"
    )
    if chart_view.missing_price_days > 0:
        st.caption(
            f"Hinweis: An **{chart_view.missing_price_days}** Tag(en) fehlten historische Kurse – "
            "diese Coins wurden dort nicht bewertet."
        )


_CATEGORY20 = [
    "#1f77b4",
    "#aec7e8",
    "#ff7f0e",
    "#ffbb78",
    "#2ca02c",
    "#98df8a",
    "#d62728",
    "#ff9896",
    "#9467bd",
    "#c5b0d5",
    "#8c564b",
    "#c49c94",
    "#e377c2",
    "#f7b6d2",
    "#7f7f7f",
    "#c7c7c7",
    "#bcbd22",
    "#dbdb8d",
    "#17becf",
    "#9edae5",
]


def _portfolio_share_colors(coins: list[str]) -> tuple[alt.Scale, dict[str, str]]:
    repeats = (len(coins) // len(_CATEGORY20)) + 1
    palette = (_CATEGORY20 * repeats)[: len(coins)]
    color_map = dict(zip(coins, palette))
    scale = alt.Scale(domain=coins, range=palette)
    return scale, color_map


PIE_LABEL_MIN_SHARE_PCT = 3.0


def _coin_pie_label(coin: str, *, other_label: str = "Sonstige (< 1 %)") -> str:
    """Kurzlabel für Coin-Segmente im Kreisdiagramm."""
    if coin == other_label:
        return "Sonst."
    return str(coin).upper()


def _pie_segment_label(
    coin: str,
    anteil_pct: float,
    *,
    other_label: str = "Sonstige (< 1 %)",
    min_label_pct: float = PIE_LABEL_MIN_SHARE_PCT,
) -> str:
    """Label im Ring nur ab min_label_pct – dünne Segmente bleiben leer."""
    if anteil_pct + 1e-12 < min_label_pct:
        return ""
    return _coin_pie_label(coin, other_label=other_label)


def _portfolio_pie_dataframe(
    chart_df: pd.DataFrame,
    *,
    min_share_pct: float = 1.0,
    other_label: str = "Sonstige (< 1 %)",
) -> pd.DataFrame:
    """Für Kreisdiagramm: Anteile unter min_share_pct zu einem Segment zusammenfassen."""
    major = chart_df[chart_df["Anteil %"] >= min_share_pct].copy()
    minor = chart_df[chart_df["Anteil %"] < min_share_pct]
    if minor.empty:
        major = major.reset_index(drop=True)
        major["PieLabel"] = major.apply(
            lambda row: _pie_segment_label(row["Coin"], row["Anteil %"], other_label=other_label),
            axis=1,
        )
        return major

    other_row = pd.DataFrame(
        [
            {
                "Coin": other_label,
                "Aktueller Wert (EUR)": float(minor["Aktueller Wert (EUR)"].sum()),
                "Anteil %": float(minor["Anteil %"].sum()),
            }
        ]
    )
    result = (
        pd.concat([major, other_row], ignore_index=True)
        .sort_values("Aktueller Wert (EUR)", ascending=False)
        .reset_index(drop=True)
    )
    result["PieLabel"] = result.apply(
        lambda row: _pie_segment_label(row["Coin"], row["Anteil %"], other_label=other_label),
        axis=1,
    )
    return result


def _portfolio_pie_color_scale(
    pie_df: pd.DataFrame,
    color_map: dict[str, str],
    *,
    other_label: str = "Sonstige (< 1 %)",
    other_color: str = "#9ca3af",
) -> alt.Scale:
    pie_coins = pie_df["Coin"].tolist()
    palette = [color_map.get(coin, other_color) if coin != other_label else other_color for coin in pie_coins]
    return alt.Scale(domain=pie_coins, range=palette)


def _style_share_color_column(df: pd.DataFrame, color_map: dict[str, str]):
    def _row_style(row: pd.Series) -> list[str]:
        color = color_map[row["Coin"]]
        styles = [""] * len(row)
        styles[0] = f"background-color: {color}; color: {color};"
        return styles

    return df.style.apply(_row_style, axis=1)


def _init_session_state() -> None:
    if "history_version" not in st.session_state:
        st.session_state["history_version"] = 0
    if "capital_flow_range" not in st.session_state:
        st.session_state["capital_flow_range"] = get_ui_pref("capital_flow_range", "max")
    nav_default = get_ui_pref("main_nav", "depot")
    if nav_default not in NAV_SECTIONS:
        nav_default = "depot"
    if st.session_state.get("main_nav") not in NAV_SECTIONS:
        st.session_state["main_nav"] = nav_default


def _apply_nav_request() -> None:
    """Tab-Leiste → Sidebar: vor dem Radio-Widget anwenden."""
    pending = st.session_state.pop("nav_request", None)
    if pending in NAV_SECTIONS:
        st.session_state["main_nav"] = pending
        save_ui_pref("main_nav", pending)


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_steuer(
    _history_version: int,
    balances_tuple: tuple[tuple[str, float], ...],
) -> SteuerResult:
    """Haltefrist aus lokaler CSV – ohne Binance-Abruf für Bestände."""
    return load_steuer_uebersicht(
        use_local_history=True,
        balances_by_coin=dict(balances_tuple),
    )


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_portfolio_timeseries(
    sources_stamp: str,
    history_version: int,
    today_value: float,
) -> pd.DataFrame:
    """Depot-Zeitreihe – unabhängig vom Chart-Zeitraum-Filter (nur bei Datenänderung neu)."""
    _ = history_version
    return portfolio_history_mod.build_portfolio_timeseries(
        today_value_override=today_value,
    )


def _tranches_to_dataframe(result: SteuerResult) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for tranche in result.tranches:
        rows.append(
            {
                "Coin": tranche.coin,
                "Kaufdatum": tranche.kaufdatum.isoformat(),
                "Menge": tranche.menge,
                "Steuerfrei ab": tranche.steuerfrei_ab.isoformat(),
                "Tage verbleibend": tranche.tage_verbleibend,
                "Status": haltefrist_status_text(tranche.steuerfrei_ab),
            }
        )
    return pd.DataFrame(rows)


def _style_haltefrist(df: pd.DataFrame):
    def _row_style(row: pd.Series) -> list[str]:
        status = str(row["Status"])
        if status.startswith("steuerfrei"):
            style = "background-color: #e8f8ee; color: #0a7a2f; font-weight: 600;"
        elif status.startswith("bald steuerfrei"):
            style = "background-color: #fff8e6; color: #9a6700; font-weight: 600;"
        else:
            style = "background-color: #fdeeee; color: #b42318; font-weight: 600;"
        return [style] * len(row)

    return df.reset_index(drop=True).style.apply(_row_style, axis=1)


def _positions_to_dataframe(result: PortfolioResult) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for pos in result.positions:
        rows.append(
            {
                "Coin": pos.coin,
                "Menge": pos.quantity,
                "Aktueller Kurs (EUR)": pos.current_price_eur,
                "Aktueller Wert (EUR)": pos.current_value_eur,
                "Einstandspreis (EUR)": pos.avg_entry_price_eur if pos.entry_known else None,
                "G/V (EUR)": pos.profit_loss_eur,
                "G/V (%)": pos.profit_loss_pct,
            }
        )
    return pd.DataFrame(rows)


def _style_profit_loss(df: pd.DataFrame):
    def _color_cell(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, str):
            return ""
        if float(value) > 0:
            return "color: #0a7a2f; font-weight: 600;"
        if float(value) < 0:
            return "color: #c0392b; font-weight: 600;"
        return ""

    return df.style.map(_color_cell, subset=["G/V (EUR)", "G/V (%)"])


def _balances_from_portfolio(result: PortfolioResult) -> dict[str, float]:
    return {pos.coin: pos.quantity for pos in result.positions}


def _prices_from_portfolio(result: PortfolioResult) -> dict[str, float | None]:
    return {pos.coin: pos.current_price_eur for pos in result.positions}


def _format_coin_mengen(mengen: dict[str, float]) -> str:
    if not mengen:
        return "—"
    parts = []
    for coin, menge in sorted(mengen.items()):
        if menge >= 1:
            parts.append(f"{coin} {menge:,.4f}".rstrip("0").rstrip("."))
        else:
            parts.append(f"{coin} {menge:.8f}".rstrip("0").rstrip("."))
    return " · ".join(parts)


def _frist_kalender_status_df(kalender) -> pd.DataFrame:
    rows = [
        {
            "Coin": row.coin,
            "Steuerfreie Menge": row.steuerfrei_menge,
            "Noch gesperrte Menge": row.gesperrt_menge,
            "Steuerfreier Wert (EUR)": row.steuerfreier_wert_eur,
            "Gesperrter Wert (EUR)": row.gesperrter_wert_eur,
        }
        for row in kalender.coin_rows
    ]
    return pd.DataFrame(rows)


def _frist_kalender_monats_df(kalender) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for monat in kalender.monate:
        if monat.details:
            coins_text = ", ".join(
                f"{coin} {menge:.8f}".rstrip("0").rstrip(".")
                for coin, menge, _wert in monat.details
            )
        else:
            coins_text = "—"
        rows.append(
            {
                "Monat": monat.label,
                "Coins + Mengen": coins_text,
                "Wert (EUR)": monat.wert_eur if monat.details else 0.0,
            }
        )
    return pd.DataFrame(rows)


_PLAUSIBILITY_STATUS_ORDER = {
    "abweichung": 0,
    "fehlend": 1,
    "rundung": 2,
    "ok": 3,
    "ignoriert": 4,
}

_PLAUSIBILITY_STATUS_ICON = {
    "ok": "🟢",
    "rundung": "🟡",
    "abweichung": "🔴",
    "fehlend": "🔴",
    "ignoriert": "⚪",
}


def _plausibilitaet_to_dataframe(rows) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=[
                "Status",
                "Coin",
                "Bestand",
                "Tranchen (FIFO)",
                "Diff",
                "Diff %",
            ]
        )

    table_rows = []
    for row in rows:
        diff_pct = (
            f"{row.diff_pct:.2f} %"
            if row.diff_pct is not None
            else "—"
        )
        icon = _PLAUSIBILITY_STATUS_ICON.get(row.status, "")
        table_rows.append(
            {
                "Status": f"{icon} {row.status_label}",
                "Coin": row.coin,
                "Bestand": row.bestand,
                "Tranchen (FIFO)": row.tranchen,
                "Diff": row.diff,
                "Diff %": diff_pct,
                "_sort": _PLAUSIBILITY_STATUS_ORDER.get(row.status, 99),
            }
        )

    df = pd.DataFrame(table_rows)
    df.sort_values(["_sort", "Coin"], inplace=True)
    return df.drop(columns=["_sort"]).reset_index(drop=True)


def _style_plausibilitaet(df: pd.DataFrame):
    def _row_style(row: pd.Series) -> list[str]:
        status = str(row.get("Status", ""))
        if status.startswith("🟢"):
            style = "background-color: #ecfdf3; color: #027a48;"
        elif status.startswith("🟡"):
            style = "background-color: #fff8e6; color: #9a6700;"
        elif status.startswith("🔴"):
            style = "background-color: #fdeeee; color: #b42318; font-weight: 600;"
        elif status.startswith("⚪"):
            style = "background-color: #f3f4f6; color: #6b7280;"
        else:
            style = ""
        return [style] * len(row)

    if "Status" not in df.columns or df.empty:
        return df.style
    return df.style.apply(_row_style, axis=1)


def _render_plausibilitaet_check(kalender) -> None:
    rows = getattr(kalender, "plausibility_rows", None) or []
    counts = {
        key: sum(1 for row in rows if row.status == key)
        for key in ("abweichung", "fehlend", "rundung", "ok", "ignoriert")
    }
    actionable = counts["abweichung"] + counts["fehlend"]

    st.markdown("**Plausibilitäts-Check**")
    st.caption(
        "Vergleicht Binance-Bestand mit offenen FIFO-Tranchen (Käufe, Deposits, Kartenkäufe). "
        "🟢 OK · 🟡 Rundung ≤ 2 % · 🔴 Handlungsbedarf · ⚪ Fiat/Stable/Fee/Dust (ignoriert)."
    )

    if kalender.plausibility_ok:
        st.success(
            f"Passt: {counts['ok']} Coins OK"
            + (f", {counts['rundung']} nur Rundung" if counts["rundung"] else "")
            + (
                f", {counts['ignoriert']} ignoriert (Fiat/Stable/BNB)."
                if counts["ignoriert"]
                else "."
            )
        )
    else:
        st.warning(
            f"{actionable} Coin(s) mit Handlungsbedarf "
            f"({counts['abweichung']} Abweichung, {counts['fehlend']} fehlende Tranchen). "
            "Trade-Sync, zufluesse.csv und ggf. Eröffnungsbestand prüfen."
        )

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("🔴 Abweichung", counts["abweichung"])
    with m2:
        st.metric("🔴 Fehlend", counts["fehlend"])
    with m3:
        st.metric("🟡 Rundung", counts["rundung"])
    with m4:
        st.metric("🟢 OK", counts["ok"])
    with m5:
        st.metric("⚪ Ignoriert", counts["ignoriert"])

    plausi_df = _plausibilitaet_to_dataframe(rows)
    show_all = st.checkbox("Alle Coins anzeigen (inkl. OK & ignoriert)", value=False)
    if not show_all:
        plausi_df = plausi_df[
            plausi_df["Status"].str.startswith(("🔴", "🟡"))
        ].reset_index(drop=True)

    if plausi_df.empty:
        st.info("Keine relevanten Abweichungen – alles passt (ohne ignorierte Coins).")
    else:
        st.dataframe(
            _style_plausibilitaet(plausi_df),
            width="stretch",
            hide_index=True,
            column_config={
                "Bestand": st.column_config.NumberColumn(format="%.8f"),
                "Tranchen (FIFO)": st.column_config.NumberColumn(format="%.8f"),
                "Diff": st.column_config.NumberColumn(format="%+.8f"),
            },
        )


def _render_frist_kalender(steuer: SteuerResult, result: PortfolioResult) -> None:
    """Frist-Kalender: aktueller Status + 13-Monats-Zeitstrahl."""
    st.markdown("#### Frist-Kalender")
    st.caption(
        "Basierend auf FIFO-Tranchen aus Käufen **und Krypto-Deposits** (zufluesse.csv). "
        "EUR-Werte = Menge × heutiger Kurs."
    )

    if not steuer.tranches and not steuer.coin_summaries:
        st.warning(
            "Keine Käufe in data/kaeufe.csv. "
            'Klicke oben auf „Daten von Binance aktualisieren“.'
        )
        return

    kalender = berechne_frist_kalender(
        steuer.tranches,
        _balances_from_portfolio(result),
        _prices_from_portfolio(result),
    )

    st.markdown("**Teil 1 – Aktueller Status**")
    k1, k2 = st.columns(2)
    with k1:
        st.metric(
            "Heute steuerfrei verfügbar",
            f"{kalender.heute_freier_wert_eur:,.2f} EUR",
        )
        st.caption(_format_coin_mengen(kalender.heute_freie_mengen))
    with k2:
        st.metric("Noch gesperrt", f"{kalender.gesperrter_wert_eur:,.2f} EUR")

    status_df = _frist_kalender_status_df(kalender)
    if not status_df.empty:
        st.dataframe(
            status_df,
            width="stretch",
            hide_index=True,
            column_config={
                "Steuerfreie Menge": st.column_config.NumberColumn(format="%.8f"),
                "Noch gesperrte Menge": st.column_config.NumberColumn(format="%.8f"),
                "Steuerfreier Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Gesperrter Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            },
        )

    _render_plausibilitaet_check(kalender)

    st.markdown("**Teil 2 – Zeitstrahl (nächste 13 Monate)**")
    st.caption(
        "Gesperrte Tranchen werden dem Monat zugeordnet, in dem ihr "
        "„steuerfrei ab“-Datum liegt (aktueller Monat + 12 Folgemonate). "
        "Monate ohne Freigabe bleiben leer."
    )

    chart_df = pd.DataFrame(
        [{"Monat": m.label, "Wert (EUR)": m.wert_eur} for m in kalender.monate]
    )
    chart = (
        alt.Chart(chart_df)
        .mark_bar(color="#2ca02c")
        .encode(
            x=alt.X("Monat:N", sort=list(chart_df["Monat"]), title=None),
            y=alt.Y("Wert (EUR):Q", title="Steuerfrei werdender Wert (EUR)"),
            tooltip=[
                alt.Tooltip("Monat:N", title="Monat"),
                alt.Tooltip("Wert (EUR):Q", title="Wert (EUR)", format=",.2f"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, width="stretch")

    monats_df = _frist_kalender_monats_df(kalender)
    st.dataframe(
        monats_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
        },
    )

    if kalender.message and "Kein EUR-Kurs" in kalender.message:
        st.info(kalender.message)


def _bilanz_fn(name: str):
    """Resolve bilanz helper; reload once if Streamlit cached an old module."""
    global bilanz_mod
    fn = getattr(bilanz_mod, name, None)
    if fn is not None:
        return fn
    bilanz_mod = importlib.reload(bilanz_mod)
    fn = getattr(bilanz_mod, name, None)
    if fn is None:
        raise AttributeError(f"module 'bilanz' has no attribute {name!r}")
    return fn


def _bilanz_verkaeufe_df(bilanz_result) -> pd.DataFrame:
    return _bilanz_fn("verkaeufe_to_dataframe")(bilanz_result.verkaeufe)


def _style_bilanz_steuer_status(df: pd.DataFrame):
    def _row_style(row: pd.Series) -> list[str]:
        status = str(row.get("Steuer-Status", ""))
        if status == "steuerfrei":
            style = "background-color: #e8f8ee; color: #0a7a2f; font-weight: 600;"
        elif status == "steuerpflichtig":
            style = "background-color: #fdeeee; color: #b42318; font-weight: 600;"
        elif status == "gemischt":
            style = "background-color: #fff8e6; color: #9a6700; font-weight: 600;"
        else:
            style = ""
        return [style] * len(row)

    if "Steuer-Status" not in df.columns:
        return df.style
    return df.reset_index(drop=True).style.apply(_row_style, axis=1)


def _style_bilanz_gv(df: pd.DataFrame):
    def _color_gv(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, str):
            if not value.strip():
                return ""
            try:
                number = float(value)
            except ValueError:
                return ""
        else:
            number = float(value)
        if number > 0:
            return "color: #0a7a2f; font-weight: 600;"
        if number < 0:
            return "color: #c0392b; font-weight: 600;"
        return ""

    color_cols = [
        c
        for c in (
            "G/V (EUR)",
            "G/V (%)",
            "Real. G/V (EUR)",
            "Real. G/V (%)",
            "Steuerfrei (EUR)",
            "Steuerpflichtig (EUR)",
            "Netto realisiert (EUR)",
            "Real. Gewinne (EUR)",
            "Real. Verluste (EUR)",
        )
        if c in df.columns
    ]
    if not color_cols:
        return df.style
    return df.style.map(_color_gv, subset=color_cols)


def _style_unrealisiert_gv(df: pd.DataFrame):
    def _color_cell(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, str):
            return ""
        if float(value) > 0:
            return "color: #0a7a2f; font-weight: 600;"
        if float(value) < 0:
            return "color: #c0392b; font-weight: 600;"
        return ""

    subset = [c for c in ("Unreal. G/V (EUR)", "Unreal. G/V (%)") if c in df.columns]
    if not subset:
        return df.style
    return df.style.map(_color_cell, subset=subset)


def _render_bilanz(result: PortfolioResult) -> None:
    """Bilanz: Gesamtübersicht, Verkäufe pro Coin und Einzelverkäufe."""
    st.subheader("Bilanz")
    st.warning(
        "Keine Steuerberatung – Angaben ohne Gewähr, bitte mit Steuerberater prüfen."
    )

    bilanz_result = _bilanz_fn("berechne_bilanz")()

    if not bilanz_result.ok:
        st.error(bilanz_result.message)
        return

    if bilanz_result.message and bilanz_result.message != "Bilanz berechnet.":
        st.info(bilanz_result.message)

    if bilanz_result.unbekannter_erloes_anzahl > 0:
        st.warning(
            f"Du hast **{len(bilanz_result.verkaeufe)} Verkäufe** in der Historie, "
            f"aber bei **{bilanz_result.unbekannter_erloes_anzahl}** fehlen noch "
            "Verkaufspreis und/oder Gebühr. Deshalb können realisierte "
            "Gewinne/Verluste nicht berechnet werden. "
            "Nutze unten **„Preise & Gebühren nachladen“** (dauert ca. 2–3 Minuten)."
        )
    elif not bilanz_result.verkaeufe:
        st.info("In deiner Trade-Historie sind noch keine Verkäufe erfasst.")

    trade_status = trade_data_status()
    if trade_status.needs_backfill:
        st.error(
            f"**Daten unvollständig:** {trade_status.sells_missing_price} Verkäufe ohne Preis, "
            f"{trade_status.buys_missing_fee} Käufe ohne Gebühr. "
            "Bilanz und Gebühren können erst danach berechnet werden."
        )
        if st.button(
            "Preise & Gebühren nachladen (ca. 2–3 Min.)",
            type="primary",
            key="backfill_trade_data",
        ):
            with st.spinner(
                "Hole alle Trades von Binance und ergänze Preise/Gebühren … "
                "**Bitte warten, das kann 2–3 Minuten dauern.**"
            ):
                buys, sells, msg = backfill_trade_csv_from_binance()
            st.session_state["history_version"] = int(st.session_state["history_version"]) + 1
            st.cache_data.clear()
            st.success(msg)
            st.rerun()
    elif bilanz_result.netto_realisiert_eur != 0 or bilanz_result.realisierte_gewinne_eur != 0:
        st.caption(
            f"Realisierte Bilanz aus {trade_status.sells_total} Verkäufen "
            f"({sum(1 for v in bilanz_result.verkaeufe if v.vollstaendig_berechenbar)} vollständig berechenbar)."
        )

    if bilanz_result.unvollstaendige_verkaeufe:
        with st.expander("Verkäufe mit unbekanntem Einstand"):
            for line in bilanz_result.unvollstaendige_verkaeufe[:20]:
                st.caption(f"• {line}")

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Realisierte Gewinne", f"{bilanz_result.realisierte_gewinne_eur:,.2f} EUR")
    with k2:
        st.metric("Realisierte Verluste", f"{bilanz_result.realisierte_verluste_eur:,.2f} EUR")
    with k3:
        st.metric("Netto realisiert", f"{bilanz_result.netto_realisiert_eur:,.2f} EUR")
    with k4:
        rendite_text = (
            f"{bilanz_result.rendite_auf_verkauftes_pct:.2f} %"
            if bilanz_result.rendite_auf_verkauftes_pct is not None
            else "—"
        )
        st.metric("Rendite auf Verkauftes", rendite_text)
    with k5:
        st.metric(
            "Verkaufsgebühren (Bilanz)",
            f"{bilanz_result.gebuehren_gesamt_eur:,.2f} EUR",
            help="Nur Gebühren aus Verkäufen, die in der Bilanz-Tabelle stehen.",
        )

    jahres_df = _bilanz_fn("jahres_bilanz_dataframe")(bilanz_result)
    if not jahres_df.empty:
        st.markdown("**Jahresübersicht (realisiert)**")
        st.caption(
            "Summen je Kalenderjahr nach **Verkaufsdatum** (FIFO, netto nach Gebühren). "
            "Steuerfrei/pflichtig nach DE-Haltefrist."
        )
        jahres_display = jahres_df[jahres_df["Jahr"] != "Gesamt"].copy()
        if not jahres_display.empty:
            st.dataframe(
                _style_bilanz_gv(jahres_display),
                width="stretch",
                hide_index=True,
                column_config={
                    "Verkäufe": st.column_config.NumberColumn(format="%d"),
                    "Real. Gewinne (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Real. Verluste (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Netto realisiert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Steuerfrei (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Steuerpflichtig (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Gebühren (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Rendite (%)": st.column_config.NumberColumn(format="%.2f %%"),
                },
            )
        gesamt_row = jahres_df[jahres_df["Jahr"] == "Gesamt"]
        if not gesamt_row.empty:
            g = gesamt_row.iloc[0]
            st.caption(
                f"**Gesamt:** Netto {g['Netto realisiert (EUR)']:,.2f} € · "
                f"steuerfrei {g['Steuerfrei (EUR)']:,.2f} € · "
                f"steuerpflichtig {g['Steuerpflichtig (EUR)']:,.2f} €"
            )

    verkaeufe_df = _bilanz_verkaeufe_df(bilanz_result)
    verkauf_coin_df = _bilanz_fn("coin_verkauf_summary_dataframe")(bilanz_result)
    gesamt_df = _bilanz_fn("coin_gesamtuebersicht_dataframe")(result, bilanz_result)
    to_csv = _bilanz_fn("dataframe_to_csv_bytes")

    with st.expander("CSV-Export", expanded=False):
        st.caption("Downloads für Excel/LibreOffice (UTF-8, Komma).")
        ex1, ex2, ex3, ex4 = st.columns(4)
        with ex1:
            if not jahres_df.empty:
                st.download_button(
                    "Jahresübersicht",
                    data=to_csv(jahres_df),
                    file_name="bilanz_jahresuebersicht.csv",
                    mime="text/csv",
                    width="stretch",
                )
        with ex2:
            if not verkaeufe_df.empty:
                st.download_button(
                    "Alle Verkäufe",
                    data=to_csv(verkaeufe_df),
                    file_name="bilanz_verkaeufe.csv",
                    mime="text/csv",
                    width="stretch",
                )
        with ex3:
            if not verkauf_coin_df.empty:
                st.download_button(
                    "Pro Coin (Verkäufe)",
                    data=to_csv(verkauf_coin_df),
                    file_name="bilanz_verkaeufe_pro_coin.csv",
                    mime="text/csv",
                    width="stretch",
                )
        with ex4:
            if not gesamt_df.empty:
                st.download_button(
                    "Coin-Übersicht",
                    data=to_csv(gesamt_df),
                    file_name="bilanz_coin_uebersicht.csv",
                    mime="text/csv",
                    width="stretch",
                )

    if not gesamt_df.empty:
        st.markdown("**Gesamtübersicht pro Coin**")
        st.caption("Gekauft / Verkauft / Im Depot – wie die Depot-Übersicht, mit Handels-Historie.")
        st.dataframe(
            _style_unrealisiert_gv(gesamt_df),
            width="stretch",
            hide_index=True,
            column_config={
                "Gekauft gesamt": st.column_config.NumberColumn(format="%.8f"),
                "Verkauft gesamt": st.column_config.NumberColumn(format="%.8f"),
                "Im Depot": st.column_config.NumberColumn(format="%.8f"),
                "Aktueller Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Unreal. G/V (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Unreal. G/V (%)": st.column_config.NumberColumn(format="%.2f %%"),
            },
        )

    if not verkauf_coin_df.empty:
        st.markdown("**Verkaufsübersicht pro Coin**")
        st.caption(
            "Zusammengefasst: wie viel je Coin verkauft wurde, realisierter G/V "
            "und ob steuerfrei oder steuerpflichtig."
        )
        st.dataframe(
            _style_bilanz_steuer_status(verkauf_coin_df),
            width="stretch",
            hide_index=True,
            column_config={
                "Gekauft gesamt": st.column_config.NumberColumn(format="%.8f"),
                "Verkauft gesamt": st.column_config.NumberColumn(format="%.8f"),
                "Real. G/V (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Real. G/V (%)": st.column_config.NumberColumn(format="%.2f %%"),
                "Steuerfrei (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Steuerpflichtig (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            },
        )

    if verkaeufe_df.empty:
        st.info(
            "Noch keine vollständig berechenbaren Verkäufe. "
            "Klicke oben auf „Daten von Binance aktualisieren“ (Preis + Gebühr)."
        )
        return

    summen = {
        "Coin": "Summe",
        "Verkaufsdatum": None,
        "Menge verkauft": None,
        "Einstand (EUR)": verkaeufe_df["Einstand (EUR)"].sum(),
        "Erlös (EUR)": verkaeufe_df["Erlös (EUR)"].sum(),
        "Gebühr (EUR)": verkaeufe_df["Gebühr (EUR)"].fillna(0).sum(),
        "G/V (EUR)": verkaeufe_df["G/V (EUR)"].sum(),
        "G/V (%)": None,
        "Steuer-Status": None,
        "Steuerfrei (EUR)": verkaeufe_df["Steuerfrei (EUR)"].sum(),
        "Steuerpflichtig (EUR)": verkaeufe_df["Steuerpflichtig (EUR)"].sum(),
    }
    display_df = pd.concat([verkaeufe_df, pd.DataFrame([summen])], ignore_index=True)

    st.markdown("**Alle Verkäufe im Detail**")
    st.dataframe(
        _style_bilanz_gv(display_df),
        width="stretch",
        hide_index=True,
        column_config={
            "Menge verkauft": st.column_config.NumberColumn(format="%.8f"),
            "Einstand (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "Erlös (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "Gebühr (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "G/V (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "G/V (%)": st.column_config.NumberColumn(format="%.2f %%"),
            "Steuerfrei (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "Steuerpflichtig (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
        },
    )
    st.caption(
        f"{len(verkaeufe_df)} Verkäufe · "
        "🟢 steuerfrei · 🔴 steuerpflichtig · 🟡 gemischt · Gewinn grün, Verlust rot"
    )


def _style_gebuehren_status(df: pd.DataFrame):
    def _row_style(row: pd.Series) -> list[str]:
        status = str(row.get("Status", ""))
        if status == "fehlend":
            style = "background-color: #fdeeee; color: #b42318; font-weight: 600;"
        elif status == "geschätzt":
            style = "background-color: #fff8e6; color: #9a6700; font-weight: 600;"
        else:
            style = ""
        return [style] * len(row)

    if "Status" not in df.columns:
        return df.style
    return df.reset_index(drop=True).style.apply(_row_style, axis=1)


def _render_gebuehren() -> None:
    """Handelsgebühren: Kennzahlen, Charts und Einzelliste."""
    st.subheader("Handelsgebühren")
    st.caption(
        "Gebühren und Kurse werden **zum Trade-Zeitpunkt** per Binance-Kerze umgerechnet. "
        "Kaufgebühren fließen auch in den Depot-Einstand ein."
    )

    btn_left, btn_right = st.columns([1, 3])
    with btn_left:
        if st.button("Gebühren neu berechnen", type="primary", key="recompute_fees"):
            with st.spinner(
                "Hole Trades von Binance und rechne Gebühren/Kurse historisch … "
                "**Bitte 2–4 Minuten warten.**"
            ):
                buys, sells, msg = backfill_trade_csv_from_binance()
            st.session_state["history_version"] = int(st.session_state["history_version"]) + 1
            st.cache_data.clear()
            st.success(msg)
            st.rerun()
    with btn_right:
        st.caption(
            "Lädt alle Trades neu und überschreibt Gebühren, Kurse und trade_id in den CSV-Dateien."
        )

    summary = gebuehren_mod.summarize_gebuehren()
    plausi = gebuehren_mod.pruefe_gebuehren_plausibilitaet()
    fee_list = gebuehren_mod.gebuehren_liste_dataframe(include_missing=True)

    g1, g2, g3, g4, g5, g6 = st.columns(6)
    with g1:
        st.metric("Gebühren Käufe", f"{summary.kauf_gebuehren_eur:,.2f} EUR")
    with g2:
        st.metric("Gebühren Verkäufe", f"{summary.verkauf_gebuehren_eur:,.2f} EUR")
    with g3:
        st.metric("Gebühren gesamt", f"{summary.gesamt_eur:,.2f} EUR")
    with g4:
        exakt_pct = (
            f"{summary.exakt_anteil_pct:.1f} %"
            if summary.exakt_anteil_pct is not None
            else "—"
        )
        st.metric(
            "Exakt (EUR)",
            f"{summary.exakt_eur:,.2f} EUR",
            delta=exakt_pct,
            delta_color="off",
            help=f"{summary.exakt_anzahl} Trades mit EUR-Gebühr oder historischem Kurs.",
        )
    with g5:
        geschaetzt_pct = (
            f"{summary.geschaetzt_anteil_pct:.1f} %"
            if summary.geschaetzt_anteil_pct is not None
            else "—"
        )
        st.metric(
            "Geschätzt (EUR)",
            f"{summary.geschaetzt_eur:,.2f} EUR",
            delta=geschaetzt_pct,
            delta_color="off",
            help=f"{summary.geschaetzt_anzahl} Trades mit Live-Kurs-Fallback.",
        )
    with g6:
        st.metric("Ohne Gebühr", f"{summary.fehlend_anzahl}")

    if plausi.ok:
        st.success(plausi.message)
    else:
        st.warning(plausi.message)
        if plausi.auffaelligkeiten:
            with st.expander("Auffällige Trades"):
                for line in plausi.auffaelligkeiten:
                    st.caption(f"• {line}")

    if summary.fehlend_anzahl and fee_list.empty:
        st.warning(
            "Noch keine berechenbaren Gebühren. "
            "Klicke **„Gebühren neu berechnen“** oder **„Daten von Binance aktualisieren“**."
        )
        return

    if fee_list.empty:
        return

    chart_left, chart_right = st.columns(2)

    coin_df = gebuehren_mod.gebuehren_nach_coin_dataframe(fee_list).head(12)
    with chart_left:
        st.markdown("**Gebühren pro Coin (Top 12)**")
        coin_chart = (
            alt.Chart(coin_df)
            .mark_bar(color="#4c78a8")
            .encode(
                x=alt.X("Gebühr (EUR):Q", title="Gebühr (EUR)"),
                y=alt.Y("Coin:N", sort="-x", title=None),
                tooltip=[
                    alt.Tooltip("Coin:N", title="Coin"),
                    alt.Tooltip("Gebühr (EUR):Q", title="Summe (EUR)", format=",.2f"),
                    alt.Tooltip("Anzahl:Q", title="Anzahl Trades"),
                ],
            )
            .properties(height=max(220, 28 * len(coin_df)))
        )
        st.altair_chart(coin_chart, width="stretch")

    month_df = gebuehren_mod.gebuehren_nach_monat_dataframe(fee_list)
    with chart_right:
        st.markdown("**Gebühren pro Monat**")
        month_long = month_df.melt(
            id_vars=["Monat", "Trades gesamt", "Trades geschätzt"],
            value_vars=["Kauf (EUR)", "Verkauf (EUR)"],
            var_name="Art",
            value_name="Gebühr (EUR)",
        )
        month_long["Art"] = month_long["Art"].str.replace(" \\(EUR\\)", "", regex=True)
        month_chart = (
            alt.Chart(month_long)
            .mark_bar()
            .encode(
                x=alt.X("Monat:N", title="Monat"),
                y=alt.Y("Gebühr (EUR):Q", title="Gebühr (EUR)", stack=True),
                color=alt.Color("Art:N", title="Art"),
                tooltip=[
                    alt.Tooltip("Monat:N", title="Monat"),
                    alt.Tooltip("Art:N", title="Art"),
                    alt.Tooltip("Gebühr (EUR):Q", title="Gebühr (EUR)", format=",.2f"),
                    alt.Tooltip("Trades gesamt:Q", title="Trades gesamt"),
                    alt.Tooltip("Trades geschätzt:Q", title="Trades geschätzt"),
                ],
            )
            .properties(height=280)
        )
        st.altair_chart(month_chart, width="stretch")

    st.markdown("**Alle Gebühren im Detail**")
    st.caption("Rot = fehlende Gebühr · Gelb = geschätzter Kurs-Fallback")
    st.dataframe(
        _style_gebuehren_status(fee_list),
        width="stretch",
        hide_index=True,
        column_config={
            "EUR-Kurs am Trade": st.column_config.NumberColumn(format="%.4f EUR"),
            "Gebühr (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "Gebühr (% vom Handelswert)": st.column_config.NumberColumn(format="%.3f %%"),
        },
    )


def _render_price_zones(
    tickers: dict[str, float] | None = None,
    result: PortfolioResult | None = None,
) -> None:
    """Preis-Zonen: Kategorie-Kreisdiagramm + EUR/USD-Tabellen."""
    st.subheader("Preis-Zonen")

    if result is not None and result.positions:
        category_view = st.radio(
            "Kategorie-Ansicht",
            options=("four", "five"),
            format_func=lambda key: (
                "4 Kategorien (ohne Cashreserve)"
                if key == "four"
                else "5 Kategorien (Cashreserve separat)"
            ),
            horizontal=True,
            key="pz_category_view",
        )
        include_cash_reserve = category_view == "five"
        alloc_df = price_zones_mod.category_allocation_dataframe(
            result.positions,
            include_cash_reserve=include_cash_reserve,
        )
        if alloc_df.empty:
            st.info(
                "Keine bewerteten Coins für das Kategorie-Diagramm — "
                "Depot-Kurse laden oder Snapshot aktualisieren."
            )
        else:
            st.markdown("#### Portfolio nach Strategie-Kategorien")
            if include_cash_reserve:
                st.caption(
                    "Kategorien 1–3 entsprechen `preis_zonen.json`. "
                    "**Kategorie 4** = übrige Krypto-Coins. "
                    "**Kategorie 5** = Cashreserve (EUR, USDT, USDC, …). "
                    "Start bei **4 Uhr**, im Uhrzeigersinn. **Segment anklicken** für Aufschlüsselung."
                )
            else:
                st.caption(
                    "Kategorien 1–3 entsprechen `preis_zonen.json`. "
                    "**Kategorie 4** = übrige Krypto-Coins. "
                    "**Cashreserve** (EUR, USDT, USDC, …) ist in dieser Ansicht ausgeblendet. "
                    "Start bei **4 Uhr**, im Uhrzeigersinn. **Segment anklicken** für Coin-Aufschlüsselung."
                )
            ordered = alloc_df.sort_values("Reihenfolge")
            domain = ordered["Kategorie"].tolist()
            colors = ordered["Farbe"].tolist()

            category_select = alt.selection_point(name="category_select", fields=["Kategorie"])
            pie = (
                alt.Chart(alloc_df)
                .mark_arc(
                    innerRadius=55,
                    outerRadius=150,
                    padAngle=0.01,
                )
                .encode(
                    theta=alt.Theta(
                        "Wert (EUR):Q",
                        stack=True,
                        sort=alt.EncodingSortField(field="Reihenfolge", order="ascending"),
                    ),
                    color=alt.Color(
                        "Kategorie:N",
                        sort=alt.EncodingSortField(field="Reihenfolge", order="ascending"),
                        scale=alt.Scale(domain=domain, range=colors),
                        legend=alt.Legend(title="Kategorie"),
                    ),
                    order=alt.Order("Reihenfolge:Q"),
                    opacity=alt.condition(category_select, alt.value(1.0), alt.value(0.45)),
                    strokeWidth=alt.condition(category_select, alt.value(2.5), alt.value(0.5)),
                    stroke=alt.value("white"),
                    tooltip=[
                        alt.Tooltip("Kategorie:N", title="Kategorie"),
                        alt.Tooltip("Wert (EUR):Q", format=",.2f", title="Wert (EUR)"),
                        alt.Tooltip("Anteil %:Q", format=".1f", title="Anteil %"),
                        alt.Tooltip("Coins:N", title="Coins"),
                    ],
                )
                .add_params(category_select)
                .configure_arc(startAngle=price_zones_mod.CATEGORY_PIE_START_ANGLE_RAD)
                .properties(height=380)
            )

            col_pie, col_detail = st.columns([1, 1], gap="large")
            with col_pie:
                pie_event = st.altair_chart(
                    pie,
                    on_select="rerun",
                    key=f"pz_category_pie_{category_view}",
                    width="stretch",
                )

            selected_category: str | None = None
            if pie_event and pie_event.selection:
                points = pie_event.selection.get("category_select", [])
                if points:
                    selected_category = points[0].get("Kategorie")

            with col_detail:
                if selected_category:
                    coin_df = price_zones_mod.category_coin_breakdown_dataframe(
                        result.positions,
                        selected_category,
                        include_cash_reserve=include_cash_reserve,
                    )
                    cat_color = ordered.loc[
                        ordered["Kategorie"] == selected_category,
                        "Farbe",
                    ].iloc[0]
                    st.markdown(f"**{selected_category}** — Anteil je Coin in dieser Kategorie")
                    if coin_df.empty:
                        st.info("Keine Coins in dieser Kategorie.")
                    else:
                        bar_df = coin_df[
                            coin_df["Coin"] != price_zones_mod.CATEGORY_BREAKDOWN_TOTAL_LABEL
                        ]
                        bar_height = max(220, len(bar_df) * 36)
                        bar = (
                            alt.Chart(bar_df)
                            .mark_bar(cornerRadiusEnd=4)
                            .encode(
                                x=alt.X(
                                    "Anteil in Kategorie %:Q",
                                    title="Anteil in Kategorie",
                                    scale=alt.Scale(domain=[0, 100]),
                                ),
                                y=alt.Y(
                                    "Coin:N",
                                    sort=alt.EncodingSortField(
                                        field="Anteil in Kategorie %",
                                        order="descending",
                                    ),
                                ),
                                color=alt.value(cat_color),
                                tooltip=[
                                    alt.Tooltip("Coin:N", title="Coin"),
                                    alt.Tooltip("Wert (EUR):Q", format=",.2f", title="Wert (EUR)"),
                                    alt.Tooltip(
                                        "Anteil in Kategorie %:Q",
                                        format=".1f",
                                        title="Anteil in Kategorie",
                                    ),
                                    alt.Tooltip(
                                        "Anteil in Gesamt ohne Cashreserve %:Q",
                                        format=".1f",
                                        title="Anteil gesamt ohne Cashreserve",
                                    ),
                                    alt.Tooltip(
                                        "Anteil in Gesamt mit Cashreserve %:Q",
                                        format=".1f",
                                        title="Anteil gesamt mit Cashreserve",
                                    ),
                                ],
                            )
                            .properties(height=bar_height)
                        )
                        st.altair_chart(bar, width="stretch")
                        st.dataframe(
                            coin_df,
                            width="stretch",
                            hide_index=True,
                            column_config={
                                "Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                                "Anteil in Kategorie %": st.column_config.NumberColumn(
                                    format="%.1f %%"
                                ),
                                "Anteil in Gesamt ohne Cashreserve %": st.column_config.NumberColumn(
                                    format="%.1f %%"
                                ),
                                "Anteil in Gesamt mit Cashreserve %": st.column_config.NumberColumn(
                                    format="%.1f %%"
                                ),
                            },
                        )
                else:
                    st.info(
                        "Klicke auf ein Segment im Kreisdiagramm, "
                        "um die Coin-Aufschlüsselung zu sehen."
                    )

            table_df = alloc_df.drop(columns=["Farbe", "Reihenfolge"]).copy()
            st.dataframe(
                table_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                    "Anteil %": st.column_config.NumberColumn(format="%.1f %%"),
                },
            )

    st.divider()

    if tickers is None:
        eur_table, usd_table, ticker_error = price_zones_mod.load_price_zone_tables_with_tickers()
    else:
        eur_table = price_zones_mod.build_price_zone_table(tickers, display_currency="EUR")
        usd_table = price_zones_mod.build_price_zone_table(tickers, display_currency="USD")
        ticker_error = ""

    if ticker_error:
        st.warning(f"Live-Kurse nicht verfügbar: {ticker_error}")

    st.markdown("#### EUR (Schwellen umgerechnet, Live-Kurse in €)")
    st.caption("Metallischer Rahmen = Zone mit dem nächsten Schwellenwert in EUR.")
    st.markdown(price_zones_mod.render_price_zone_table_html(eur_table), unsafe_allow_html=True)

    st.markdown("#### USD (Original-Schwellen, Live-Kurse in $)")
    st.caption(
        "Schwellenwerte exakt wie in der Strategie-Tabelle · "
        "Live-Kurse direkt von Binance in USD (z. B. BTC/USDT)."
    )
    st.markdown(price_zones_mod.render_price_zone_table_html(usd_table), unsafe_allow_html=True)

    st.caption(
        "Schwellenwerte in **USD** in `data/preis_zonen.json`. "
        "Blaues Badge = Kurs nicht direkt über das Standard-Paar (EUR bzw. USDT)."
    )


NAV_SECTIONS: dict[str, str] = {
    "depot": "Depot",
    "entwicklung": "Entwicklung",
    "was_wenn": "Was wäre wenn?",
    "bilanz": "Bilanz",
    "gebuehren": "Gebühren",
    "steuer": "Steuer",
    "preis_zonen": "Preis-Zonen",
}


def _style_scenario_delta(df: pd.DataFrame):
    def _color_cell(value: object) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if isinstance(value, str):
            return ""
        if float(value) > 0:
            return "color: #0a7a2f; font-weight: 600;"
        if float(value) < 0:
            return "color: #c0392b; font-weight: 600;"
        return ""

    cols = [c for c in ("Δ Wert (EUR)", "G/V Szenario (EUR)") if c in df.columns]
    if not cols:
        return df.style
    return df.style.map(_color_cell, subset=cols)


_SKIP_WHAT_IF_COINS = frozenset({"EUR"})
PORTFOLIO_DEPENDENT_TABS = frozenset({"depot", "entwicklung", "was_wenn"})


def _what_if_positions(result: PortfolioResult) -> list:
    """Alle Depot-Coins für Szenarien (Menge > 0, ohne EUR)."""
    positions = [
        p
        for p in result.positions
        if p.quantity > 1e-12 and p.coin not in _SKIP_WHAT_IF_COINS
    ]
    positions.sort(
        key=lambda p: (p.current_value_eur is None, -(p.current_value_eur or 0.0)),
    )
    return positions


def _what_if_ath_base_prices(
    result: PortfolioResult,
    ath_prices: dict[str, float],
) -> dict[str, float]:
    """Nur echte ATH-Kurse — kein Fallback auf den heutigen Kurs."""
    return {coin: price for coin, price in ath_prices.items() if price > 0}


def _clear_what_if_coin_overrides() -> None:
    """Entfernt gespeicherte Einzel-Coin-Abweichungen (nach Preset / globalem Slider)."""
    for key in list(st.session_state.keys()):
        if key.startswith("what_if_coin_"):
            del st.session_state[key]


def _sync_what_if_ath_levels_from_global() -> None:
    """Alle Coin-Regler auf 100 + globales Premium (ATH bis +300 %)."""
    premium = float(st.session_state.get("what_if_ath_premium_pct", 0.0))
    level = min(what_if_mod.ATH_LEVEL_MAX, what_if_mod.ATH_LEVEL_ATH + premium)
    coins = st.session_state.get("what_if_ath_coins")
    if not coins:
        coins = list((st.session_state.get("what_if_ath_prices") or {}).keys())
    for coin in coins:
        st.session_state[f"what_if_ath_level_{coin}"] = level


def _render_was_wenn_tab(result: PortfolioResult) -> None:
    """Kurs-Szenarien: Auswirkung auf Depotwert und G/V."""
    st.subheader("Was wäre wenn?")
    st.caption(
        "Simulation auf Basis der **aktuellen Bestände und Kurse** — Mengen und Einstand bleiben fix. "
        "Keine Prognose, nur Rechenmodell."
    )

    preset_cols = st.columns(7)
    presets = (-30, -20, -10, 0, 10, 20, 50)
    for col, preset in zip(preset_cols, presets):
        with col:
            if st.button(f"{preset:+d} %", key=f"wi_preset_{preset}", width="stretch"):
                st.session_state["what_if_global_pct"] = float(preset)
                _clear_what_if_coin_overrides()
                st.session_state.pop("what_if_use_ath", None)
                st.session_state.pop("what_if_ath_prices", None)
                st.session_state.pop("what_if_ath_premium_pct", None)
                st.session_state.pop("what_if_ath_coins", None)
                st.rerun()

    ath_col, _ = st.columns([1, 6])
    with ath_col:
        if st.button("ATH", key="wi_preset_ath", width="stretch", help="Jeder Coin auf sein All-Time-High (Binance Spot, Tages-Hoch in EUR)"):
            scenario_positions = _what_if_positions(result)
            coins = [pos.coin for pos in scenario_positions]
            try:
                with st.spinner("ATH-Kurse werden von Binance geladen … (kann 1–2 Min. dauern)"):
                    ath_result = ath_prices_mod.fetch_ath_prices_eur(coins)
            except ath_prices_mod.AthFetchError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"ATH-Abruf fehlgeschlagen: {exc}")
            else:
                if not ath_result.prices_eur:
                    st.error(
                        ath_result.message
                        or "Keine ATH-Kurse von Binance — ATH-Szenario nicht gestartet."
                    )
                else:
                    if ath_result.stale_cache or ath_result.errors:
                        st.warning(ath_result.message)
                    st.session_state["what_if_use_ath"] = True
                    st.session_state["what_if_ath_prices"] = ath_result.prices_eur
                    st.session_state["what_if_ath_coins"] = coins
                    st.session_state["what_if_ath_premium_pct"] = 0.0
                    for coin in coins:
                        st.session_state[f"what_if_ath_level_{coin}"] = 100.0
                    if ath_result.missing:
                        st.session_state["what_if_ath_missing"] = list(ath_result.missing)
                    else:
                        st.session_state.pop("what_if_ath_missing", None)
                    st.rerun()

    use_ath = bool(st.session_state.get("what_if_use_ath"))
    scenario_positions = _what_if_positions(result)
    if use_ath:
        all_coins = [p.coin for p in scenario_positions]
        if st.session_state.get("what_if_ath_coins") != all_coins:
            st.session_state["what_if_ath_coins"] = all_coins
            for coin in all_coins:
                st.session_state.setdefault(f"what_if_ath_level_{coin}", 100.0)
    if use_ath:
        st.info(
            "**ATH-Szenario aktiv** — Regler **0** = heute, **100** = ATH, **400** = ATH +300 %. "
            "Nach ATH-Klick starten alle bei **100**. "
            "Prozent-Preset oben klicken, um zurück zum manuellen Szenario zu wechseln."
        )
        missing_ath = st.session_state.get("what_if_ath_missing") or []
        if missing_ath:
            st.caption(f"Ohne ATH-Daten: {', '.join(missing_ath)}")

        st.markdown("**Über ATH hinaus**")
        ath_preset_cols = st.columns(7)
        ath_presets = (0, 10, 25, 50, 100, 200, 300)
        for col, premium in zip(ath_preset_cols, ath_presets):
            with col:
                label = "ATH" if premium == 0 else f"+{premium} %"
                if st.button(label, key=f"wi_ath_premium_{premium}", width="stretch"):
                    st.session_state["what_if_ath_premium_pct"] = float(premium)
                    _sync_what_if_ath_levels_from_global()
                    st.rerun()

        st.slider(
            "Premium über ATH (%) — alle Coins gleich setzen",
            min_value=0.0,
            max_value=300.0,
            value=min(
                300.0,
                float(st.session_state.get("what_if_ath_premium_pct", 0.0)),
            ),
            step=1.0,
            key="what_if_ath_premium_pct",
            on_change=_sync_what_if_ath_levels_from_global,
            help="Setzt alle Coin-Regler auf 100 + X (ATH + X %). Max. +300 % = Regler 400.",
        )

    global_pct = st.slider(
        "Kursänderung alle Coins (%)",
        min_value=-80.0,
        max_value=200.0,
        value=float(st.session_state.get("what_if_global_pct", 0.0)),
        step=1.0,
        key="what_if_global_pct",
        on_change=_clear_what_if_coin_overrides,
        help="Gilt für alle Coins mit EUR-Kurs; Einzel-Coins unten überschreiben.",
        disabled=use_ath,
    )

    coin_changes: dict[str, float] = {}
    coin_ath_levels: dict[str, float] = {}
    ath_prices = st.session_state.get("what_if_ath_prices") if use_ath else None
    expander_label = (
        "Einzelne Coins: Heute ↔ ATH"
        if use_ath
        else "Einzelne Coins anpassen (optional)"
    )
    with st.expander(expander_label, expanded=use_ath):
        if use_ath:
            st.caption(
                f"**{len(scenario_positions)} Coins** — Regler **0** = heute, **100** = ATH, "
                "**400** = ATH +300 %. Nach ATH-Start: alle bei **100**."
            )
        else:
            st.caption("Abweichung vom globalen Slider — nur geänderte Coins zählen.")
        if not scenario_positions:
            st.info("Keine Coins im Depot.")
        elif use_ath and isinstance(ath_prices, dict) and ath_prices:
            ath_bases = _what_if_ath_base_prices(result, ath_prices)
            for pos in scenario_positions:
                coin = pos.coin
                if coin not in ath_bases:
                    st.caption(f"{coin}: kein ATH von Binance — nur Coins mit ATH-Daten simulierbar.")
                    continue
                today = pos.current_price_eur or 0.0
                ath_val = ath_bases[coin]
                max_val = what_if_mod.ath_ceiling_eur(ath_val)
                level_val = float(st.session_state.get(f"what_if_ath_level_{coin}", 100.0))
                target_val = what_if_mod.target_from_ath_level(today, ath_val, level_val)
                if coin in ath_prices:
                    basis_hint = (
                        f"Heute: {today:,.4f} EUR | ATH: {ath_val:,.4f} EUR | "
                        f"Max (+300 %): {max_val:,.4f} EUR | Ziel: {target_val:,.4f} EUR"
                    )
                else:
                    basis_hint = f"Heute: {today:,.4f} EUR | Ziel: {target_val:,.4f} EUR"
                coin_ath_levels[coin] = st.slider(
                    f"{coin} — Ziel (0 = heute, 100 = ATH, 400 = ATH +300 %)",
                    min_value=0.0,
                    max_value=what_if_mod.ATH_LEVEL_MAX,
                    value=level_val,
                    step=1.0,
                    key=f"what_if_ath_level_{coin}",
                    help=basis_hint,
                )
        else:
            for pos in scenario_positions:
                override = st.slider(
                    f"{pos.coin} (global {global_pct:+.0f} %)",
                    min_value=-80.0,
                    max_value=200.0,
                    value=float(global_pct),
                    step=1.0,
                    key=f"what_if_coin_{pos.coin}",
                )
                if abs(override - global_pct) > 1e-9:
                    coin_changes[pos.coin] = override

    if use_ath and isinstance(ath_prices, dict) and ath_prices:
        ath_bases = _what_if_ath_base_prices(result, ath_prices)
        scaled_targets = what_if_mod.compute_ath_target_prices(
            result.positions,
            ath_bases,
            coin_level_pct=coin_ath_levels or None,
        )
        summary = what_if_mod.scenario_from_portfolio(
            result,
            coin_target_prices_eur=scaled_targets,
        )
    else:
        summary = what_if_mod.scenario_from_portfolio(
            result,
            global_change_pct=global_pct,
            coin_changes_pct=coin_changes or None,
        )

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("Depot heute", f"{summary.total_current_eur:,.2f} EUR")
    with m2:
        st.metric(
            "Depot Szenario",
            f"{summary.total_scenario_eur:,.2f} EUR",
            delta=f"{summary.delta_eur:+,.2f} EUR",
        )
    with m3:
        delta_pct = summary.delta_pct
        st.metric(
            "Δ Depot",
            f"{delta_pct:+.1f} %" if delta_pct is not None else "—",
        )
    with m4:
        if summary.total_current_pl_eur is not None and summary.total_scenario_pl_eur is not None:
            st.metric(
                "G/V Szenario",
                f"{summary.total_scenario_pl_eur:,.2f} EUR",
                delta=f"{summary.pl_delta_eur:+,.2f} EUR" if summary.pl_delta_eur is not None else None,
            )
        else:
            st.metric("G/V Szenario", "—")

    compare_df = pd.DataFrame(
        [
            {"Ansicht": "Heute", "Wert (EUR)": summary.total_current_eur},
            {"Ansicht": "Szenario", "Wert (EUR)": summary.total_scenario_eur},
        ]
    )
    compare_chart = (
        alt.Chart(compare_df)
        .mark_bar()
        .encode(
            x=alt.X("Ansicht:N", title=None, sort=["Heute", "Szenario"]),
            y=alt.Y("Wert (EUR):Q", title="Depotwert (EUR)"),
            color=alt.Color(
                "Ansicht:N",
                scale=alt.Scale(domain=["Heute", "Szenario"], range=["#4c78a8", "#2ca02c"]),
                legend=None,
            ),
            tooltip=[alt.Tooltip("Wert (EUR):Q", format=",.2f")],
        )
        .properties(height=280)
    )
    st.altair_chart(compare_chart, width="stretch")

    scen_df = what_if_mod.scenario_positions_dataframe(summary)
    if scen_df.empty:
        st.warning("Keine Coins mit EUR-Bewertung für die Simulation.")
    else:
        st.markdown("**Auswirkung je Coin**")
        st.dataframe(
            _style_scenario_delta(scen_df),
            width="stretch",
            hide_index=True,
            column_config={
                "Kursänderung %": st.column_config.NumberColumn(format="%+.0f %%"),
                "Wert heute (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Wert Szenario (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Δ Wert (EUR)": st.column_config.NumberColumn(format="%+.2f EUR"),
                "G/V heute (EUR)": st.column_config.NumberColumn(format="%+.2f EUR"),
                "G/V Szenario (EUR)": st.column_config.NumberColumn(format="%+.2f EUR"),
            },
        )

    with st.expander("Geplant (nächste Schritte)", expanded=False):
        st.markdown(
            "- **Preis-Zone:** Was passiert, wenn ein Coin die nächste Kauf/Verkaufs-Zone erreicht?\n"
            "- **Verkauf simulieren:** Wie viel G/V wäre realisiert?\n"
            "- **Kursziel:** Festes Ziel pro Coin (z. B. BTC 100.000 €)"
        )


def _render_depot_tab(result: PortfolioResult) -> None:
    """Spot-Bestand und Wertverteilung."""
    df = _positions_to_dataframe(result)
    st.subheader("Depot-Übersicht")
    st.caption(
        "Einstand = **FIFO** (wie Bilanz und Steuer). "
        "Leerer Einstandspreis = Kaufpreis in der Historie noch unbekannt."
    )
    st.dataframe(
        _style_profit_loss(df),
        width="stretch",
        hide_index=True,
        column_config={
            "Menge": st.column_config.NumberColumn(format="%.8f"),
            "Aktueller Kurs (EUR)": st.column_config.NumberColumn(format="%.4f EUR"),
            "Aktueller Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "Einstandspreis (EUR)": st.column_config.NumberColumn(format="%.4f EUR"),
            "G/V (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
            "G/V (%)": st.column_config.NumberColumn(format="%.2f %%"),
        },
    )

    chart_df = df[df["Aktueller Wert (EUR)"].notna()].copy()
    if chart_df.empty:
        st.warning("Für keinen Coin konnte ein EUR-Wert berechnet werden.")
        return

    st.subheader("Wertverteilung nach Coin")
    total_value = chart_df["Aktueller Wert (EUR)"].sum()
    chart_df = chart_df.sort_values("Aktueller Wert (EUR)", ascending=False).reset_index(drop=True)
    chart_df["Anteil %"] = chart_df["Aktueller Wert (EUR)"] / total_value * 100

    coins = chart_df["Coin"].tolist()
    color_map = _portfolio_share_colors(coins)[1]
    pie_df = _portfolio_pie_dataframe(chart_df)
    pie_color_scale = _portfolio_pie_color_scale(pie_df, color_map)
    value_sort = alt.EncodingSortField(field="Aktueller Wert (EUR)", order="descending")

    pie_col, list_col = st.columns([1.1, 1])
    with pie_col:
        pie_arc = (
            alt.Chart(pie_df)
            .mark_arc(innerRadius=55, outerRadius=150, padAngle=0.005)
            .encode(
                theta=alt.Theta("Aktueller Wert (EUR):Q", stack=True, sort=value_sort),
                color=alt.Color("Coin:N", sort=value_sort, scale=pie_color_scale, legend=None),
                order=alt.Order("Aktueller Wert (EUR):Q", sort="descending"),
                tooltip=[
                    alt.Tooltip("Coin:N", title="Coin"),
                    alt.Tooltip("Aktueller Wert (EUR):Q", title="Wert (EUR)", format=",.2f"),
                    alt.Tooltip("Anteil %:Q", title="Anteil", format=".1f"),
                ],
            )
        )
        pie_labels = (
            alt.Chart(pie_df[pie_df["PieLabel"].astype(str).str.len() > 0])
            .mark_text(
                radius=102,
                size=12,
                fontWeight="bold",
                fill="white",
                stroke="#1f2937",
                strokeWidth=1,
            )
            .encode(
                theta=alt.Theta("Aktueller Wert (EUR):Q", stack=True, sort=value_sort),
                text=alt.Text("PieLabel:N"),
                order=alt.Order("Aktueller Wert (EUR):Q", sort="descending"),
            )
        )
        pie = pie_arc + pie_labels
        st.altair_chart(pie.properties(height=380), width="stretch")
        st.caption(
            "Start bei 12 Uhr · größte Position zuerst · "
            "Coins unter 1 % als «Sonstige (< 1 %)» · "
            f"Kürzel im Ring ab {PIE_LABEL_MIN_SHARE_PCT:.0f} %"
        )

    with list_col:
        share_df = chart_df[["Coin", "Aktueller Wert (EUR)", "Anteil %"]].rename(
            columns={"Aktueller Wert (EUR)": "Wert (EUR)"}
        )
        share_df.insert(0, " ", "●")
        st.dataframe(
            _style_share_color_column(share_df, color_map),
            width="stretch",
            hide_index=True,
            column_config={
                " ": st.column_config.TextColumn(width="small"),
                "Wert (EUR)": st.column_config.NumberColumn(format="%.2f EUR"),
                "Anteil %": st.column_config.NumberColumn(format="%.1f %%"),
            },
        )
        st.caption(f"Gesamtwert: {total_value:,.2f} EUR · größte zuerst")


def _render_entwicklung_tab(
    flow_series: pd.DataFrame,
    capital_summary,
    inflow_summary,
    karte_eur: float,
    total_value_eur: float,
) -> None:
    """Kapitalfluss, Depot-Entwicklung und Buchungsdetails."""
    st.subheader("Kapitalfluss")
    _render_capital_flow_chart(flow_series, capital_summary.netto_eur)

    st.subheader("Depot-Entwicklung")
    _render_portfolio_history_chart(flow_series, total_value_eur)

    type_labels = getattr(inflows, "TYPE_LABELS", {})
    reverse_labels = {label: key for key, label in type_labels.items()}

    with st.expander("Details: Kapitalflüsse (Ein- und Auszahlungen)"):
        st.markdown(
            f"- **Bank (SEPA/Überweisung):** {inflow_summary.fiat_eur:,.2f} EUR  \n"
            f"- **Karte (Direktkauf):** {karte_eur:,.2f} EUR  \n"
            f"- **Krypto (externes Wallet):** {inflow_summary.crypto_deposits_eur:,.2f} EUR  \n"
            f"- **Auszahlungen gesamt:** {capital_summary.auszahlungen_eur:,.2f} EUR"
        )
        st.caption(
            "Nur Geld **von/nach außen** (Bank, Karte, externe Wallets). "
            "Zeiten in **deutscher Ortszeit**. "
            "Neue Daten: **„Daten von Binance aktualisieren“** oben rechts."
        )

        selected_labels = st.multiselect(
            "Anzeigen",
            options=list(type_labels.values()),
            default=list(type_labels.values()),
        )
        selected_types = {reverse_labels[label] for label in selected_labels if label in reverse_labels}
        flows_df = capital_flows_to_dataframe(selected_types or None)

        search = st.text_input(
            "Suche (Datum oder Betrag)",
            placeholder="z. B. 11.10.2025 oder 1000",
        )
        if search.strip() and not flows_df.empty:
            q = search.strip().replace(",", ".")
            flows_df = flows_df[
                flows_df.apply(
                    lambda r: q in str(r["Datum"])
                    or q in f"{r['Betrag (EUR)']:.2f}"
                    or q in f"{r['Betrag (EUR)']:.0f}",
                    axis=1,
                )
            ]

        if flows_df.empty:
            st.info(
                "Keine Einträge für den Filter. "
                "Klicke oben rechts auf „Daten von Binance aktualisieren“."
            )
        else:
            st.dataframe(
                flows_df,
                width="stretch",
                hide_index=True,
                column_config={
                    "Menge": st.column_config.NumberColumn(format="%.8f"),
                    "Betrag (EUR)": st.column_config.NumberColumn(format="+,.2f EUR"),
                    "Kapital netto (EUR)": st.column_config.NumberColumn(format=",.2f EUR"),
                },
            )
            st.caption(
                f"{len(flows_df)} Einträge · "
                f"Summe der angezeigten Buchungen: {flows_df['Betrag (EUR)'].sum():,.2f} EUR"
            )


def _resolve_portfolio_load(
    price_mode: str,
    force_live: bool,
) -> tuple[PortfolioResult, str, bool, str | None, str | None]:
    """
    Lädt Depot; bei Fehler Snapshot-Fallback oder leeres Ergebnis.

    Rückgabe: result, loaded_at, from_cache, cache_day, live_error (falls Live fehlschlug)
    """
    result, loaded_at, from_cache, cache_day = load_portfolio(
        use_local_history=True,
        price_mode=price_mode,
        force_live=force_live,
    )
    if result.ok:
        return result, loaded_at, from_cache, cache_day, None

    live_error = result.message
    fallback = load_latest_snapshot()
    if fallback is not None:
        snap_result, snap_loaded_at, snap_day = fallback
        return (
            PortfolioResult(
                ok=True,
                message=(
                    f"{snap_result.message} "
                    f"(Snapshot vom {snap_day} — Live-Depot nicht erreichbar)"
                ),
                positions=snap_result.positions,
                total_value_eur=snap_result.total_value_eur,
            ),
            snap_loaded_at,
            True,
            snap_day,
            live_error,
        )

    return (
        PortfolioResult(
            ok=True,
            message="Depot nicht geladen — Steuer/Bilanz nutzen lokale CSV-Daten.",
            positions=[],
            total_value_eur=0.0,
        ),
        "",
        False,
        None,
        live_error,
    )


def _render_steuer_tab(result: PortfolioResult, history_version: int) -> None:
    """Haltefrist-Kalender und FIFO-Tranchen."""
    st.subheader("Steuer-Haltefrist")
    st.warning(
        "Keine Steuerberatung – Angaben ohne Gewähr, bitte mit Steuerberater prüfen."
    )
    st.caption(
        "🟢 steuerfrei · 🟡 bald steuerfrei (≤ 30 Tage) · 🔴 gesperrt (> 30 Tage)"
    )

    balances = _balances_from_portfolio(result)
    if balances:
        steuer = _cached_steuer(history_version, tuple(sorted(balances.items())))
    else:
        steuer = load_steuer_uebersicht(use_local_history=True, balances_by_coin=None)

    if not steuer.ok:
        st.error(steuer.message)
        return

    if steuer.sync_info:
        st.caption(steuer.sync_info)

    _render_frist_kalender(steuer, result)

    tranche_df = _tranches_to_dataframe(steuer)
    if tranche_df.empty:
        return

    st.markdown("**Einzelne Kauf-Tranchen (FIFO)**")
    st.dataframe(
        _style_haltefrist(tranche_df),
        width="stretch",
        hide_index=True,
        column_config={
            "Menge": st.column_config.NumberColumn(format="%.8f"),
            "Tage verbleibend": st.column_config.NumberColumn(format="%d"),
        },
    )


def _render_price_zones_tab(result: PortfolioResult) -> None:
    tickers, _msg = binance_data_mod.fetch_spot_tickers()
    _render_price_zones(tickers, result)


def _render_depot_selector() -> str:
    """Zeigt aktive Depots; ab 2 Depots einen Umschalter. Gibt das aktive Depot zurück."""
    enabled = registry_mod.enabled_providers()
    if not enabled:
        st.caption("Kein Depot konfiguriert.")
        return registry_mod.GESAMT_ID

    if len(enabled) == 1:
        st.caption(f"Aktiv: {enabled[0].info().display_name}")
        return enabled[0].info().id

    options = [registry_mod.GESAMT_ID] + [p.info().id for p in enabled]
    labels = {registry_mod.GESAMT_ID: "Gesamt"}
    labels.update({p.info().id: p.info().display_name for p in enabled})

    saved = get_ui_pref("active_depot", registry_mod.GESAMT_ID)
    if saved not in options:
        saved = registry_mod.GESAMT_ID

    selected = st.radio(
        "Depot",
        options=options,
        format_func=lambda key: labels[key],
        index=options.index(saved),
        key="active_depot",
        label_visibility="collapsed",
    )
    if selected != saved:
        save_ui_pref("active_depot", selected)
    return selected


def main() -> None:
    st.set_page_config(page_title="Depot-Tracker", page_icon="📈", layout="wide")
    _init_session_state()
    _apply_nav_request()
    history_version = int(st.session_state["history_version"])

    with st.sidebar:
        st.subheader("Bestände & Kurse")
        frozen = st.checkbox(
            "Feste Daten (Entwicklung)",
            value=is_frozen_mode(),
            help=(
                "An: Bestände und Kurse werden NICHT automatisch neu geladen – "
                "auch nicht am nächsten Tag. Ideal zum schnellen Programmieren. "
                "Aus: Live-Modus (max. 1× pro Tag von Binance)."
            ),
        )
        new_mode = PRICE_MODE_FROZEN if frozen else PRICE_MODE_LIVE_DAILY
        if new_mode != get_price_mode():
            save_price_mode(new_mode)
        price_mode = new_mode

        if frozen:
            st.success("Kein Auto-Reload – Snapshot wird wiederverwendet.")
        else:
            st.caption("Kurse & Gesamtwert: **live bei jedem Start** · Einstand aus CSV · Trade-Sync max. 1×/Tag.")

        if st.button("Kurse & Bestände jetzt laden", width="stretch"):
            st.session_state["force_live_prices"] = True
            st.rerun()

        with st.expander("Datenspeicherung", expanded=False):
            st.caption(
                "Alle Depot-Daten liegen im Ordner **`data/`** auf deiner Festplatte. "
                "Beim Schließen der App geht **nichts verloren** – nur der laufende Server stoppt."
            )
            for display_path, path, label in persisted_data_files():
                mark = "✓" if path.exists() else "·"
                st.markdown(f"{mark} `{display_path}` — {label}")

        st.divider()
        st.subheader("Depots")
        _render_depot_selector()

        st.divider()
        st.subheader("Navigation")
        selected_nav = st.radio(
            "Bereich",
            options=list(NAV_SECTIONS.keys()),
            format_func=lambda key: NAV_SECTIONS[key],
            key="main_nav",
            label_visibility="collapsed",
        )
        saved_nav = get_ui_pref("main_nav", "depot")
        if selected_nav != saved_nav:
            save_ui_pref("main_nav", selected_nav)

    st.title("Depot-Tracker")
    st.caption("Dein Binance-Spot-Depot – nur Lesen, keine Trades")

    header_left, header_right = st.columns([3, 1])
    with header_right:
        if st.button("Daten von Binance aktualisieren", type="primary", width="stretch"):
            with st.spinner(
                "Hole Trade-Historie, Einzahlungen und ergänze Preise/Gebühren … "
                "**Kann 2–4 Minuten dauern – bitte warten.**"
            ):
                sync = sync_trade_history_from_binance()
                inflow_ok, inflow_msg, _ = sync_inflows_from_binance()
            if sync.ok:
                mark_trades_synced()
                st.session_state["history_version"] = history_version + 1
                st.cache_data.clear()
                st.success(sync.message)
                if inflow_ok:
                    st.info(inflow_msg)
                st.rerun()
            else:
                st.error(sync.message)

    trade_sync_text = format_sync_time(get_last_trade_sync())
    mode_label = "Feste Daten" if frozen else "Live (1×/Tag)"

    force_live = bool(st.session_state.pop("force_live_prices", False))

    with st.spinner("Lade Depot …"):
        result, loaded_at, from_cache, cache_day, portfolio_live_error = _resolve_portfolio_load(
            price_mode,
            force_live,
        )

    with header_left:
        if result.positions:
            st.caption(
                f"Kurse ({mode_label}): "
                f"{format_loaded_at_display(loaded_at, from_cache, cache_day)} · "
                f"Trade-Historie: {trade_sync_text}"
            )
        else:
            st.caption(f"Kurs-Modus: {mode_label} · Trade-Historie: {trade_sync_text}")

    if portfolio_live_error:
        st.error(portfolio_live_error)
        if not has_any_snapshot():
            if st.button("Kurse & Bestände jetzt von Binance laden", type="primary"):
                st.session_state["force_live_prices"] = True
                st.rerun()
            st.info("Oder links **„Kurse & Bestände jetzt laden“** klicken (einmalig).")
        else:
            st.info(
                "Steuer, Bilanz und Gebühren sind weiter nutzbar (CSV-Daten). "
                "Depot-Tabs benötigen Live-Kurse oder einen Snapshot."
            )

    if result.positions:
        st.success(result.message)

    inflow_summary = compute_inflow_summary()
    capital_summary = compute_capital_flow_summary()
    karte_eur = getattr(inflow_summary, "fiat_karte_eur", 0.0)

    if result.positions:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Gesamtwert", f"{result.total_value_eur:,.2f} EUR")
            st.caption("Summe aller Spot-Bestände in EUR (live Kurse von Binance).")
        with col2:
            st.metric("Netto eingezahlt", f"{capital_summary.netto_eur:,.2f} EUR")
            st.caption(
                f"Ein: {capital_summary.einzahlungen_eur:,.2f} · "
                f"Aus: {capital_summary.auszahlungen_eur:,.2f} EUR"
            )
        with col3:
            st.metric("Anzahl Coins", len(result.positions))
        with col4:
            known = sum(1 for pos in result.positions if pos.entry_known)
            st.metric("Einstand bekannt", f"{known} / {len(result.positions)}")

    active = st.session_state["main_nav"]
    nav_cols = st.columns(len(NAV_SECTIONS))
    for col, (key, label) in zip(nav_cols, NAV_SECTIONS.items()):
        with col:
            if st.button(
                label,
                key=f"main_nav_btn_{key}",
                type="primary" if key == active else "secondary",
                width="stretch",
            ):
                st.session_state["nav_request"] = key
                st.rerun()

    st.divider()

    if active in PORTFOLIO_DEPENDENT_TABS and not result.positions:
        st.warning(
            "**Depot-Daten nicht verfügbar.** "
            "Bitte Binance-Verbindung prüfen oder **„Kurse & Bestände jetzt laden“** "
            "in der Sidebar. Steuer, Bilanz und Gebühren funktionieren weiter mit CSV-Daten."
        )
    elif active == "depot":
        _render_depot_tab(result)
    elif active == "entwicklung":
        flow_series = capital_flow_timeseries()
        _render_entwicklung_tab(
            flow_series,
            capital_summary,
            inflow_summary,
            karte_eur,
            result.total_value_eur,
        )
    elif active == "was_wenn":
        _render_was_wenn_tab(result)
    elif active == "bilanz":
        _render_bilanz(result)
    elif active == "gebuehren":
        _render_gebuehren()
    elif active == "steuer":
        _render_steuer_tab(result, history_version)
    elif active == "preis_zonen":
        _render_price_zones_tab(result)


if __name__ == "__main__":
    main()
