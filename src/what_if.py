"""Was-wäre-wenn-Szenarien: Kursänderungen auf das aktuelle Depot."""

from __future__ import annotations

from dataclasses import dataclass

from binance_data import PortfolioResult, Position


@dataclass(frozen=True)
class ScenarioPosition:
    """Ein Coin im Szenario-Vergleich."""

    coin: str
    quantity: float
    current_price_eur: float | None
    scenario_price_eur: float | None
    current_value_eur: float | None
    scenario_value_eur: float | None
    entry_price_eur: float | None
    current_pl_eur: float | None
    scenario_pl_eur: float | None
    price_change_pct: float


@dataclass(frozen=True)
class ScenarioSummary:
    """Aggregiertes Szenario-Ergebnis."""

    total_current_eur: float
    total_scenario_eur: float
    delta_eur: float
    delta_pct: float | None
    total_current_pl_eur: float | None
    total_scenario_pl_eur: float | None
    pl_delta_eur: float | None
    positions: tuple[ScenarioPosition, ...]
    priced_coin_count: int


def _change_pct_for_coin(
    coin: str,
    global_change_pct: float,
    coin_changes_pct: dict[str, float] | None,
) -> float:
    if coin_changes_pct and coin in coin_changes_pct:
        return float(coin_changes_pct[coin])
    return float(global_change_pct)


def _scenario_price(current_price: float | None, change_pct: float) -> float | None:
    if current_price is None or current_price <= 0:
        return None
    return current_price * (1.0 + change_pct / 100.0)


def scale_target_prices_eur(
    base_prices_eur: dict[str, float],
    premium_pct: float,
) -> dict[str, float]:
    """Skaliert Zielkurse um premium_pct über der Basis (z. B. ATH +50 %)."""
    factor = 1.0 + float(premium_pct) / 100.0
    return {
        coin: price * factor
        for coin, price in base_prices_eur.items()
        if price > 0
    }


def scale_target_prices_eur_by_coin(
    base_prices_eur: dict[str, float],
    *,
    default_premium_pct: float,
    coin_premium_pct: dict[str, float] | None = None,
) -> dict[str, float]:
    """Skaliert je Coin mit eigenem Premium über ATH (Fallback: default_premium_pct)."""
    overrides = coin_premium_pct or {}
    return {
        coin: price * (1.0 + float(overrides.get(coin, default_premium_pct)) / 100.0)
        for coin, price in base_prices_eur.items()
        if price > 0
    }


ATH_LEVEL_TODAY = 0.0
ATH_LEVEL_ATH = 100.0
ATH_MAX_PREMIUM_PCT = 300.0
ATH_LEVEL_MAX = ATH_LEVEL_ATH + ATH_MAX_PREMIUM_PCT


def target_from_ath_level(today_eur: float, ath_eur: float, level: float) -> float:
    """
    Regler-Skala je Coin:
    0 = heute, 100 = ATH, 400 = ATH + 300 %.
    """
    clamped = max(ATH_LEVEL_TODAY, min(ATH_LEVEL_MAX, float(level)))
    if clamped <= ATH_LEVEL_ATH:
        t = clamped / ATH_LEVEL_ATH if ATH_LEVEL_ATH else 0.0
        return today_eur + t * (ath_eur - today_eur)
    premium_pct = clamped - ATH_LEVEL_ATH
    return ath_eur * (1.0 + premium_pct / 100.0)


def ath_ceiling_eur(ath_base_eur: float, premium_pct: float = ATH_MAX_PREMIUM_PCT) -> float:
    """Obergrenze bei +300 % über ATH."""
    return ath_base_eur * (1.0 + float(premium_pct) / 100.0)


def compute_ath_target_prices(
    positions: list[Position],
    ath_bases: dict[str, float],
    *,
    coin_level_pct: dict[str, float] | None = None,
    default_level_pct: float = ATH_LEVEL_ATH,
) -> dict[str, float]:
    """Zielkurse je Coin: Regler 0 (heute) … 100 (ATH) … 400 (ATH +300 %)."""
    levels = coin_level_pct or {}
    targets: dict[str, float] = {}
    for pos in positions:
        coin = pos.coin
        if coin not in ath_bases:
            continue
        today = pos.current_price_eur
        if today is None or today <= 0:
            continue
        level = float(levels.get(coin, default_level_pct))
        targets[coin] = target_from_ath_level(today, ath_bases[coin], level)
    return targets


def _resolve_change_and_scenario_price(
    pos: Position,
    *,
    global_change_pct: float,
    coin_changes_pct: dict[str, float] | None,
    coin_target_prices_eur: dict[str, float] | None,
) -> tuple[float, float | None]:
    if coin_target_prices_eur and pos.coin in coin_target_prices_eur:
        target = coin_target_prices_eur[pos.coin]
        if target > 0 and pos.current_price_eur is not None and pos.current_price_eur > 0:
            change_pct = (target / pos.current_price_eur - 1.0) * 100.0
            return change_pct, target
        return 0.0, target if target > 0 else None

    change_pct = _change_pct_for_coin(pos.coin, global_change_pct, coin_changes_pct)
    return change_pct, _scenario_price(pos.current_price_eur, change_pct)


def compute_price_scenario(
    positions: list[Position],
    *,
    global_change_pct: float = 0.0,
    coin_changes_pct: dict[str, float] | None = None,
    coin_target_prices_eur: dict[str, float] | None = None,
) -> ScenarioSummary:
    """
    Simuliert Kursänderungen auf dem aktuellen Depot (Mengen & Einstand unverändert).

    `coin_changes_pct` überschreibt `global_change_pct` je Coin (Werte in Prozent).
    `coin_target_prices_eur` setzt absolute Zielkurse je Coin (z. B. ATH).
    """
    scenario_rows: list[ScenarioPosition] = []
    total_current = 0.0
    total_scenario = 0.0
    total_current_pl = 0.0
    total_scenario_pl = 0.0
    pl_known = True
    priced_count = 0

    for pos in positions:
        change_pct, scen_price = _resolve_change_and_scenario_price(
            pos,
            global_change_pct=global_change_pct,
            coin_changes_pct=coin_changes_pct,
            coin_target_prices_eur=coin_target_prices_eur,
        )
        scen_value = (
            pos.quantity * scen_price
            if scen_price is not None and pos.quantity > 0
            else None
        )

        scen_pl: float | None = None
        if (
            scen_value is not None
            and pos.entry_known
            and pos.avg_entry_price_eur is not None
            and pos.avg_entry_price_eur > 0
        ):
            cost = pos.quantity * pos.avg_entry_price_eur
            scen_pl = scen_value - cost
        elif pos.profit_loss_eur is not None and pos.current_value_eur not in (None, 0):
            # Einstand unbekannt: G/V proportional zum Kurs skalieren
            ratio = (1.0 + change_pct / 100.0) if pos.current_value_eur else 1.0
            scen_pl = pos.profit_loss_eur * ratio
        elif pos.profit_loss_eur is not None:
            scen_pl = pos.profit_loss_eur

        if pos.current_value_eur is not None:
            total_current += pos.current_value_eur
            priced_count += 1
        if scen_value is not None:
            total_scenario += scen_value
        if pos.profit_loss_eur is not None:
            total_current_pl += pos.profit_loss_eur
        else:
            pl_known = False
        if scen_pl is not None:
            total_scenario_pl += scen_pl
        elif pos.profit_loss_eur is not None:
            pl_known = False

        scenario_rows.append(
            ScenarioPosition(
                coin=pos.coin,
                quantity=pos.quantity,
                current_price_eur=pos.current_price_eur,
                scenario_price_eur=scen_price,
                current_value_eur=pos.current_value_eur,
                scenario_value_eur=scen_value,
                entry_price_eur=pos.avg_entry_price_eur if pos.entry_known else None,
                current_pl_eur=pos.profit_loss_eur,
                scenario_pl_eur=scen_pl,
                price_change_pct=change_pct,
            )
        )

    delta = total_scenario - total_current
    delta_pct = (delta / total_current * 100.0) if total_current > 1e-12 else None
    pl_delta = (
        (total_scenario_pl - total_current_pl)
        if pl_known and total_current_pl is not None
        else None
    )

    return ScenarioSummary(
        total_current_eur=total_current,
        total_scenario_eur=total_scenario,
        delta_eur=delta,
        delta_pct=delta_pct,
        total_current_pl_eur=total_current_pl if pl_known else None,
        total_scenario_pl_eur=total_scenario_pl if pl_known else None,
        pl_delta_eur=pl_delta,
        positions=tuple(scenario_rows),
        priced_coin_count=priced_count,
    )


def scenario_from_portfolio(
    result: PortfolioResult,
    *,
    global_change_pct: float = 0.0,
    coin_changes_pct: dict[str, float] | None = None,
    coin_target_prices_eur: dict[str, float] | None = None,
) -> ScenarioSummary:
    """Hilfsfunktion für die UI."""
    return compute_price_scenario(
        result.positions,
        global_change_pct=global_change_pct,
        coin_changes_pct=coin_changes_pct,
        coin_target_prices_eur=coin_target_prices_eur,
    )


def scenario_positions_dataframe(summary: ScenarioSummary):
    """DataFrame für die Coin-Tabelle (lazy import pandas)."""
    import pandas as pd

    rows: list[dict[str, object]] = []
    for row in summary.positions:
        if row.current_value_eur is None and row.scenario_value_eur is None:
            continue
        rows.append(
            {
                "Coin": row.coin,
                "Kursänderung %": row.price_change_pct,
                "Wert heute (EUR)": row.current_value_eur,
                "Wert Szenario (EUR)": row.scenario_value_eur,
                "Δ Wert (EUR)": (
                    (row.scenario_value_eur or 0.0) - (row.current_value_eur or 0.0)
                    if row.scenario_value_eur is not None
                    and row.current_value_eur is not None
                    else None
                ),
                "G/V heute (EUR)": row.current_pl_eur,
                "G/V Szenario (EUR)": row.scenario_pl_eur,
            }
        )
    if not rows:
        return pd.DataFrame(
            columns=[
                "Coin",
                "Kursänderung %",
                "Wert heute (EUR)",
                "Wert Szenario (EUR)",
                "Δ Wert (EUR)",
                "G/V heute (EUR)",
                "G/V Szenario (EUR)",
            ]
        )
    df = pd.DataFrame(rows)
    return df.sort_values("Wert heute (EUR)", ascending=False, na_position="last").reset_index(
        drop=True
    )
