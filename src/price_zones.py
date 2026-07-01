"""Preis-Zonen-Tabelle: Schwellenwerte aus JSON, Live-Kurse von Binance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal

import binance_data

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ZONES_JSON = PROJECT_ROOT / "data" / "preis_zonen.json"

DisplayCurrency = Literal["EUR", "USD"]

DIRECT_EUR_SOURCES = {"direkt (EUR-Paar)"}
DIRECT_USD_SOURCES = {
    "direkt (USD)",
    "direkt (USDT-Paar)",
    "direkt (USDC-Paar)",
    "direkt (USD-Paar)",
}


@dataclass(frozen=True)
class CoinZoneState:
    symbol: str
    thresholds: tuple[float, ...]
    live_price: float | None
    price_source: str
    active_zone_index: int | None


@dataclass(frozen=True)
class PriceZoneTable:
    display_currency: DisplayCurrency
    threshold_currency: str
    exchange_rate_note: str
    usd_eur_rate: float | None
    zones: list[dict]
    categories: list[dict]
    coin_states: dict[str, CoinZoneState]


def load_price_zone_config(path: Path | None = None) -> dict:
    config_path = path or ZONES_JSON
    return json.loads(config_path.read_text(encoding="utf-8"))


def convert_thresholds_to_eur(
    thresholds: list[float] | tuple[float, ...],
    usd_eur_rate: float,
) -> tuple[float, ...]:
    """Rechnet USD-Schwellen mit live USD/EUR-Kurs in EUR um."""
    return tuple(float(value) * usd_eur_rate for value in thresholds)


def format_usd_eur_rate(rate: float) -> str:
    return f"{rate:.4f}".replace(".", ",")


def closest_zone_index(price: float, thresholds: list[float] | tuple[float, ...]) -> int:
    """Markiert die Zone, deren Schwellenwert dem Live-Kurs am nächsten liegt."""
    if not thresholds:
        return 0
    best_index = 0
    best_distance = abs(price - thresholds[0])
    for index, threshold in enumerate(thresholds[1:], start=1):
        distance = abs(price - threshold)
        if distance < best_distance - 1e-12:
            best_distance = distance
            best_index = index
        elif abs(distance - best_distance) <= 1e-12 and index < best_index:
            best_index = index
    return best_index


def format_threshold(value: float, currency: DisplayCurrency) -> str:
    suffix = " €" if currency == "EUR" else " $"
    if value >= 1000:
        text = f"{value:,.1f}"
        return text.replace(",", " ") + suffix
    if value >= 1:
        return f"{value:g}{suffix}"
    if value >= 0.01:
        return f"{value:.4f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.5f}".rstrip("0").rstrip(".") + suffix


def format_live_price(value: float, currency: DisplayCurrency) -> str:
    suffix = " €" if currency == "EUR" else " $"
    if value >= 1000:
        return f"{value:,.2f}{suffix}".replace(",", " ")
    if value >= 1:
        return f"{value:,.4f}{suffix}".replace(",", " ")
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{text}{suffix}"


def build_price_zone_table(
    tickers: dict[str, float] | None,
    config: dict | None = None,
    *,
    display_currency: DisplayCurrency = "EUR",
) -> PriceZoneTable:
    cfg = config or load_price_zone_config()
    threshold_currency = str(cfg.get("threshold_currency", "USD")).upper()
    usd_eur_rate: float | None = None
    if tickers is not None and display_currency == "EUR" and threshold_currency == "USD":
        usd_eur_rate = binance_data.usd_to_eur_rate(tickers)

    coin_states: dict[str, CoinZoneState] = {}

    for category in cfg["categories"]:
        for coin in category["coins"]:
            symbol = str(coin["symbol"]).upper()
            raw_thresholds = tuple(float(v) for v in coin["thresholds"])

            if display_currency == "USD":
                thresholds = raw_thresholds
            elif threshold_currency == "USD" and usd_eur_rate is not None:
                thresholds = convert_thresholds_to_eur(raw_thresholds, usd_eur_rate)
            else:
                thresholds = raw_thresholds

            if tickers is None:
                live_price = None
                source = "—"
                active_index = None
            elif display_currency == "USD":
                live_price, source = binance_data.asset_price_in_usd_detailed(symbol, tickers)
                active_index = (
                    closest_zone_index(live_price, thresholds) if live_price is not None else None
                )
            else:
                live_price, source = binance_data.asset_price_in_eur_detailed(symbol, tickers)
                active_index = (
                    closest_zone_index(live_price, thresholds) if live_price is not None else None
                )

            coin_states[symbol] = CoinZoneState(
                symbol=symbol,
                thresholds=thresholds,
                live_price=live_price,
                price_source=source,
                active_zone_index=active_index,
            )

    return PriceZoneTable(
        display_currency=display_currency,
        threshold_currency=threshold_currency,
        exchange_rate_note=str(cfg.get("exchange_rate_note", "")),
        usd_eur_rate=usd_eur_rate,
        zones=list(cfg.get("zones", [])),
        categories=list(cfg.get("categories", [])),
        coin_states=coin_states,
    )


def _cell_class(active: bool) -> str:
    return "pz-cell pz-active" if active else "pz-cell"


def _direct_sources(currency: DisplayCurrency) -> set[str]:
    return DIRECT_USD_SOURCES if currency == "USD" else DIRECT_EUR_SOURCES


def conversion_badge_label(source: str) -> str:
    """Kurzlabel für Umrechnungs-Badge."""
    if "→" in source:
        return source.split("→", maxsplit=1)[0]
    if source.startswith("direkt"):
        return source.replace("direkt (", "").replace(")", "")
    return "≈"


def _render_live_price_cell(state: CoinZoneState, currency: DisplayCurrency) -> str:
    if state.live_price is None:
        return '<td class="pz-live-cell"><span class="pz-live-price">—</span></td>'

    price_text = escape(format_live_price(state.live_price, currency))
    indirect = state.price_source not in _direct_sources(currency)
    if indirect:
        badge = escape(conversion_badge_label(state.price_source))
        title = escape(f"Umrechnung: {state.price_source}")
        badge_html = f'<span class="pz-conv-badge" title="{title}">{badge}</span>'
        return (
            f'<td class="pz-live-cell">'
            f'<span class="pz-live-price">{price_text}</span>{badge_html}'
            f"</td>"
        )
    return f'<td class="pz-live-cell"><span class="pz-live-price">{price_text}</span></td>'


def _coin_columns(table: PriceZoneTable) -> list[tuple[str, bool]]:
    """(symbol, category_start) – category_start = Trennlinie vor neuer Kategorie."""
    columns: list[tuple[str, bool]] = []
    for cat_index, category in enumerate(table.categories):
        for index, coin in enumerate(category["coins"]):
            columns.append(
                (
                    str(coin["symbol"]).upper(),
                    cat_index > 0 and index == 0,
                )
            )
    return columns


def _meta_line(table: PriceZoneTable) -> str:
    if table.display_currency == "USD":
        return (
            "Währung: USD · Schwellenwerte wie in der Strategie-Tabelle (ohne Umrechnung) · "
            "Live-Kurse direkt in USD von Binance"
        )
    if table.threshold_currency == "USD" and table.usd_eur_rate is not None:
        rate_text = format_usd_eur_rate(table.usd_eur_rate)
        return (
            f"Währung: EUR · Schwellen (USD) umgerechnet · "
            f"1 USD = {rate_text} EUR (Binance live)"
        )
    if table.threshold_currency == "USD":
        return (
            f"Währung: EUR · {escape(table.exchange_rate_note)} · USD/EUR-Kurs nicht verfügbar"
        )
    return f"Währung: EUR · {escape(table.exchange_rate_note)}"


def render_price_zone_table_html(table: PriceZoneTable) -> str:
    zones = table.zones
    columns = _coin_columns(table)
    total_cols = len(columns)
    currency = table.display_currency
    meta_line = _meta_line(table)

    parts: list[str] = [
        "<style>",
        ".pz-wrap { overflow-x: auto; margin-top: 0.5rem; }",
        ".pz-table { border-collapse: collapse; width: 100%; font-size: 0.8rem; color: #0f172a; }",
        ".pz-table th, .pz-table td {",
        "  border: 1px solid #64748b; padding: 5px 7px; text-align: center;",
        "  color: #0f172a !important;",
        "}",
        ".pz-meta { text-align: left; background: #fff; font-weight: 600; }",
        ".pz-cat-header { font-weight: 700; font-size: 0.84rem; color: #0f172a !important; }",
        ".pz-ticker { font-weight: 700; background: #e2e8f0; }",
        ".pz-cat-start { border-left: 3px solid #334155 !important; }",
        ".pz-live-row td { background: #f8fafc; }",
        ".pz-live-cell { vertical-align: middle; }",
        ".pz-live-price { display: block; font-size: 0.78rem; font-weight: 700; color: #0f172a !important; }",
        ".pz-conv-badge {",
        "  display: inline-block; margin-top: 3px; padding: 1px 6px; border-radius: 999px;",
        "  background: #1d4ed8; color: #fff !important; font-size: 0.62rem; font-weight: 700;",
        "  letter-spacing: 0.02em;",
        "}",
        ".pz-zone-label {",
        "  width: 2.4rem; font-weight: 700; font-size: 0.72rem; background: rgba(255,255,255,0.55);",
        "}",
        ".pz-value { font-weight: 600; font-variant-numeric: tabular-nums; }",
        ".pz-legend { font-size: 0.76rem; text-align: left; padding: 6px 10px; color: #0f172a !important; }",
        ".pz-legend-code { width: 2.4rem; font-weight: 700; text-align: center; }",
        ".pz-active {",
        "  box-shadow:",
        "    inset 0 1px 0 rgba(255,255,255,0.95),",
        "    inset 0 -1px 0 rgba(100,116,139,0.45),",
        "    inset 0 0 10px rgba(255,255,255,0.55),",
        "    0 0 0 3px #f8fafc,",
        "    0 0 0 5px #cbd5e1,",
        "    0 0 0 7px #64748b,",
        "    0 0 16px 3px rgba(148,163,184,0.95),",
        "    0 0 28px 8px rgba(226,232,240,0.85);",
        "  outline: 2px solid #ffffff;",
        "  outline-offset: -1px;",
        "  position: relative; z-index: 3; font-weight: 800;",
        "}",
        "</style>",
        '<div class="pz-wrap">',
        '<table class="pz-table">',
        "<thead>",
        "<tr>",
        f'<th colspan="{total_cols + 1}" class="pz-meta">{meta_line}</th>',
        "</tr>",
        "<tr>",
        '<th class="pz-zone-label"></th>',
    ]

    for cat_index, category in enumerate(table.categories):
        span = len(category["coins"])
        color = escape(str(category.get("header_color", "#ddd")))
        name = escape(str(category["name"]))
        cat_class = "pz-cat-header pz-cat-start" if cat_index > 0 else "pz-cat-header"
        parts.append(
            f'<th colspan="{span}" class="{cat_class}" style="background:{color};">{name}</th>'
        )
    parts.append('</tr><tr><th class="pz-zone-label">Live</th>')

    for symbol, category_start in columns:
        ticker_class = "pz-ticker pz-cat-start" if category_start else "pz-ticker"
        parts.append(f'<th class="{ticker_class}">{escape(symbol)}</th>')
    parts.append('</tr></thead><tbody><tr class="pz-live-row"><td class="pz-zone-label">Kurs</td>')

    for symbol, category_start in columns:
        state = table.coin_states[symbol]
        cell_html = _render_live_price_cell(state, currency)
        if category_start:
            cell_html = cell_html.replace(
                '<td class="pz-live-cell">',
                '<td class="pz-live-cell pz-cat-start">',
                1,
            )
        parts.append(cell_html)
    parts.append("</tr>")

    for zone_index, zone in enumerate(zones):
        bg = escape(str(zone.get("color", "#fff")))
        zone_code = escape(f"Z{zone_index + 1}")
        parts.append("<tr>")
        parts.append(
            f'<td class="pz-zone-label" style="background:{bg};">{zone_code}</td>'
        )
        for symbol, category_start in columns:
            state = table.coin_states[symbol]
            threshold = state.thresholds[zone_index]
            active = state.active_zone_index == zone_index
            cell_class = _cell_class(active)
            if category_start:
                cell_class += " pz-cat-start"
            value_text = escape(format_threshold(threshold, currency))
            parts.append(
                f'<td class="{cell_class} pz-value" style="background:{bg};">{value_text}</td>'
            )
        parts.append("</tr>")

    parts.append("</tbody></table>")
    parts.append('<table class="pz-table" style="margin-top:4px;">')
    for zone_index, zone in enumerate(zones):
        bg = escape(str(zone.get("color", "#fff")))
        label = escape(str(zone.get("label", "")))
        zone_code = escape(f"Z{zone_index + 1}")
        parts.append(
            f'<tr><td class="pz-legend pz-legend-code" style="background:{bg};">{zone_code}</td>'
            f'<td class="pz-legend" style="background:{bg};">{label}</td></tr>'
        )
    parts.append("</table></div>")
    return "".join(parts)


REST_CATEGORY_LABEL = "Kategorie 4: Rest"
REST_CATEGORY_COLOR = "#94a3b8"
CASH_RESERVE_CATEGORY_LABEL = "Kategorie 5: Cashreserve"
CASH_RESERVE_CATEGORY_COLOR = "#475569"
CASH_RESERVE_ASSETS = frozenset({"EUR", "USD", "USDT", "USDC", "BUSD", "FDUSD"})
CATEGORY_BREAKDOWN_TOTAL_LABEL = "Insgesamt"
CATEGORY_PIE_START_ANGLE_RAD = 2 * 3.141592653589793 / 3  # 4 Uhr (120° von 12 Uhr, im Uhrzeigersinn)


@dataclass(frozen=True)
class CategoryAllocationRow:
    """Ein Segment im Kategorie-Kreisdiagramm."""

    label: str
    color: str
    value_eur: float
    share_pct: float
    coins: tuple[str, ...]


def coin_category_index_map(config: dict) -> dict[str, int]:
    """Ordnet Coin-Symbole den Kategorien 1–3 zu (Indizes 0–2 in der JSON)."""
    mapping: dict[str, int] = {}
    for index, category in enumerate(config.get("categories", [])[:3]):
        for coin in category.get("coins", []):
            mapping[str(coin["symbol"]).upper()] = index
    return mapping


def _category_name(config: dict, index: int) -> str:
    categories = config.get("categories", [])
    if index < len(categories):
        return str(categories[index].get("name", f"Kategorie {index + 1}"))
    return f"Kategorie {index + 1}"


def _category_header_color(config: dict, index: int) -> str:
    categories = config.get("categories", [])
    if index < len(categories):
        return str(categories[index].get("header_color", "#cccccc"))
    return "#cccccc"


def is_cash_reserve_asset(coin: str) -> bool:
    """Spot-Währungen / Stablecoins für die Cashreserve-Kategorie."""
    return str(coin).upper() in CASH_RESERVE_ASSETS


def portfolio_value_totals_eur(positions: list) -> tuple[float, float]:
    """
    Gesamtwerte des Depots in EUR.

    Returns:
        (mit Cashreserve, ohne Cashreserve)
    """
    total_mit = 0.0
    total_ohne = 0.0
    for pos in positions:
        coin = str(getattr(pos, "coin", "")).upper()
        value = getattr(pos, "current_value_eur", None)
        if not coin or value is None or value <= 0:
            continue
        amount = float(value)
        total_mit += amount
        if not is_cash_reserve_asset(coin):
            total_ohne += amount
    return total_mit, total_ohne


def _rest_bucket_index(*, include_cash_reserve: bool) -> int:
    return 3


def _cash_reserve_bucket_index() -> int:
    return 4


def build_category_allocation(
    positions: list,
    config: dict | None = None,
    *,
    include_cash_reserve: bool = False,
) -> list[CategoryAllocationRow]:
    """
    Teilt das Depot in Strategie-Kategorien.

    include_cash_reserve=False (4 Kategorien):
        JSON 1–3 + Rest (nur Krypto). Cashreserve (EUR, USDT, …) ausgeblendet.

    include_cash_reserve=True (5 Kategorien):
        JSON 1–3 + Rest (nur Krypto) + Cashreserve (EUR, USDT, USDC, …).
    """
    cfg = config or load_price_zone_config()
    cat_map = coin_category_index_map(cfg)
    rest_index = _rest_bucket_index(include_cash_reserve=include_cash_reserve)
    bucket_count = 5 if include_cash_reserve else 4
    bucket_coins: list[list[str]] = [[] for _ in range(bucket_count)]
    bucket_values = [0.0] * bucket_count

    for pos in positions:
        coin = str(getattr(pos, "coin", "")).upper()
        value = getattr(pos, "current_value_eur", None)
        if not coin or value is None or value <= 0:
            continue

        if include_cash_reserve and is_cash_reserve_asset(coin):
            bucket_index = _cash_reserve_bucket_index()
        elif not include_cash_reserve and is_cash_reserve_asset(coin):
            continue
        else:
            bucket_index = cat_map.get(coin, rest_index)

        bucket_coins[bucket_index].append(coin)
        bucket_values[bucket_index] += float(value)

    labels = [
        _category_name(cfg, 0),
        _category_name(cfg, 1),
        _category_name(cfg, 2),
        REST_CATEGORY_LABEL,
    ]
    colors = [
        _category_header_color(cfg, 0),
        _category_header_color(cfg, 1),
        _category_header_color(cfg, 2),
        REST_CATEGORY_COLOR,
    ]
    if include_cash_reserve:
        labels.append(CASH_RESERVE_CATEGORY_LABEL)
        colors.append(CASH_RESERVE_CATEGORY_COLOR)

    total = sum(bucket_values)

    rows: list[CategoryAllocationRow] = []
    for index in range(bucket_count):
        share = (bucket_values[index] / total * 100.0) if total > 1e-12 else 0.0
        rows.append(
            CategoryAllocationRow(
                label=labels[index],
                color=colors[index],
                value_eur=bucket_values[index],
                share_pct=share,
                coins=tuple(sorted(set(bucket_coins[index]))),
            )
        )
    return rows


def category_labels_in_order(
    config: dict | None = None,
    *,
    include_cash_reserve: bool = False,
) -> list[str]:
    """Fest sortierte Kategorie-Labels (1 → 4 bzw. 1 → 5)."""
    cfg = config or load_price_zone_config()
    labels = [
        _category_name(cfg, 0),
        _category_name(cfg, 1),
        _category_name(cfg, 2),
        REST_CATEGORY_LABEL,
    ]
    if include_cash_reserve:
        labels.append(CASH_RESERVE_CATEGORY_LABEL)
    return labels


def category_coin_breakdown_dataframe(
    positions: list,
    category_label: str,
    config: dict | None = None,
    *,
    include_cash_reserve: bool = False,
):
    """Coin-Anteile innerhalb einer Kategorie (für Drill-down)."""
    import pandas as pd

    cfg = config or load_price_zone_config()
    labels = category_labels_in_order(cfg, include_cash_reserve=include_cash_reserve)
    if category_label not in labels:
        return pd.DataFrame(
            columns=[
                "Coin",
                "Wert (EUR)",
                "Anteil in Kategorie %",
                "Anteil in Gesamt ohne Cashreserve %",
                "Anteil in Gesamt mit Cashreserve %",
            ]
        )

    cat_map = coin_category_index_map(cfg)
    target_index = labels.index(category_label)
    rest_index = _rest_bucket_index(include_cash_reserve=include_cash_reserve)
    per_coin: dict[str, float] = {}

    for pos in positions:
        coin = str(getattr(pos, "coin", "")).upper()
        value = getattr(pos, "current_value_eur", None)
        if not coin or value is None or value <= 0:
            continue

        if include_cash_reserve and is_cash_reserve_asset(coin):
            bucket_index = _cash_reserve_bucket_index()
        elif not include_cash_reserve and is_cash_reserve_asset(coin):
            continue
        else:
            bucket_index = cat_map.get(coin, rest_index)

        if bucket_index != target_index:
            continue
        per_coin[coin] = per_coin.get(coin, 0.0) + float(value)

    if not per_coin:
        return pd.DataFrame(
            columns=[
                "Coin",
                "Wert (EUR)",
                "Anteil in Kategorie %",
                "Anteil in Gesamt ohne Cashreserve %",
                "Anteil in Gesamt mit Cashreserve %",
            ]
        )

    category_total = sum(per_coin.values())
    total_mit, total_ohne = portfolio_value_totals_eur(positions)
    rows = [
        {
            "Coin": coin,
            "Wert (EUR)": value,
            "Anteil in Kategorie %": value / category_total * 100.0 if category_total > 0 else 0.0,
            "Anteil in Gesamt ohne Cashreserve %": (
                value / total_ohne * 100.0 if total_ohne > 0 else 0.0
            ),
            "Anteil in Gesamt mit Cashreserve %": (
                value / total_mit * 100.0 if total_mit > 0 else 0.0
            ),
        }
        for coin, value in sorted(per_coin.items(), key=lambda item: item[1], reverse=True)
    ]
    df = pd.DataFrame(rows)
    df = pd.concat(
        [
            df,
            pd.DataFrame(
                [
                    {
                        "Coin": CATEGORY_BREAKDOWN_TOTAL_LABEL,
                        "Wert (EUR)": category_total,
                        "Anteil in Kategorie %": 100.0,
                        "Anteil in Gesamt ohne Cashreserve %": (
                            category_total / total_ohne * 100.0 if total_ohne > 0 else 0.0
                        ),
                        "Anteil in Gesamt mit Cashreserve %": (
                            category_total / total_mit * 100.0 if total_mit > 0 else 0.0
                        ),
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    return df


def category_allocation_dataframe(
    positions: list,
    config: dict | None = None,
    *,
    include_cash_reserve: bool = False,
):
    """DataFrame für Kreisdiagramm und Tabelle (nur Segmente mit Wert > 0)."""
    import pandas as pd

    cfg = config or load_price_zone_config()
    ordered_labels = category_labels_in_order(cfg, include_cash_reserve=include_cash_reserve)
    label_order = {label: index + 1 for index, label in enumerate(ordered_labels)}
    rows = build_category_allocation(
        positions,
        config,
        include_cash_reserve=include_cash_reserve,
    )
    data = [
        {
            "Kategorie": row.label,
            "Reihenfolge": label_order.get(row.label, 99),
            "Wert (EUR)": row.value_eur,
            "Anteil %": row.share_pct,
            "Farbe": row.color,
            "Coins": ", ".join(row.coins) if row.coins else "—",
        }
        for row in rows
        if row.value_eur > 1e-9
    ]
    if not data:
        return pd.DataFrame(
            columns=["Kategorie", "Reihenfolge", "Wert (EUR)", "Anteil %", "Farbe", "Coins"]
        )
    df = pd.DataFrame(data)
    return df.sort_values("Reihenfolge").reset_index(drop=True)


def load_price_zone_tables_with_tickers(
    tickers: dict[str, float] | None = None,
) -> tuple[PriceZoneTable, PriceZoneTable, str]:
    """Lädt EUR- und USD-Tabelle; holt Ticker von Binance, wenn nicht übergeben."""
    if tickers is None:
        tickers, message = binance_data.fetch_spot_tickers()
        if tickers is None:
            empty = build_price_zone_table(None)
            return empty, build_price_zone_table(None, display_currency="USD"), message
        return (
            build_price_zone_table(tickers, display_currency="EUR"),
            build_price_zone_table(tickers, display_currency="USD"),
            "",
        )
    return (
        build_price_zone_table(tickers, display_currency="EUR"),
        build_price_zone_table(tickers, display_currency="USD"),
        "",
    )


def load_price_zone_table_with_tickers(
    tickers: dict[str, float] | None = None,
) -> tuple[PriceZoneTable, str]:
    """Kompatibilität: nur EUR-Tabelle."""
    eur_table, _usd_table, message = load_price_zone_tables_with_tickers(tickers)
    return eur_table, message
