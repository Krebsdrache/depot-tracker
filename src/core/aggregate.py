"""Zusammenführen mehrerer Depots zu einer Gesamtübersicht.

Fasst die Bestände mehrerer ``DepotSnapshot``s je Instrument zusammen:
Mengen und Werte werden addiert, der Einstand mengen-gewichtet gemittelt.
"""

from __future__ import annotations

from datetime import datetime

from core.model import DepotSnapshot, Holding, Instrument

GESAMT_ID = "gesamt"


def _combine_group(instrument: Instrument, holdings: list[Holding]) -> Holding:
    total_qty = sum(h.quantity for h in holdings)

    values = [h.value_eur for h in holdings if h.value_eur is not None]
    total_value = sum(values) if values else None

    prices = [h.price_eur for h in holdings if h.price_eur is not None]
    price = prices[0] if prices else None

    # Einstand mengen-gewichtet über die Lots mit bekanntem Einstand.
    entry_qty = sum(h.quantity for h in holdings if h.avg_entry_price_eur is not None)
    entry_cost = sum(
        h.quantity * h.avg_entry_price_eur
        for h in holdings
        if h.avg_entry_price_eur is not None
    )
    avg_entry = entry_cost / entry_qty if entry_qty > 0 else None

    pls = [h.profit_loss_eur for h in holdings if h.profit_loss_eur is not None]
    profit_loss_eur = sum(pls) if pls else None

    profit_loss_pct = None
    if price is not None and avg_entry is not None and avg_entry > 0:
        profit_loss_pct = ((price / avg_entry) - 1.0) * 100.0

    return Holding(
        instrument=instrument,
        quantity=total_qty,
        price_eur=price,
        value_eur=total_value,
        avg_entry_price_eur=avg_entry,
        profit_loss_eur=profit_loss_eur,
        profit_loss_pct=profit_loss_pct,
    )


def combine_holdings(holdings: list[Holding]) -> list[Holding]:
    """Fasst Holdings gleicher Instrumente zusammen (nach Symbol)."""
    grouped: dict[str, list[Holding]] = {}
    instruments: dict[str, Instrument] = {}
    for holding in holdings:
        symbol = holding.instrument.symbol
        grouped.setdefault(symbol, []).append(holding)
        instruments.setdefault(symbol, holding.instrument)

    combined = [_combine_group(instruments[sym], group) for sym, group in grouped.items()]
    combined.sort(
        key=lambda h: h.value_eur if h.value_eur is not None else -1.0,
        reverse=True,
    )
    return combined


def combine_snapshots(
    snapshots: list[DepotSnapshot],
    *,
    depot_id: str = GESAMT_ID,
    provider_id: str = GESAMT_ID,
) -> DepotSnapshot:
    """Erzeugt aus mehreren Depot-Snapshots einen aggregierten Gesamt-Snapshot."""
    all_holdings: list[Holding] = []
    for snapshot in snapshots:
        all_holdings.extend(snapshot.holdings)

    combined = combine_holdings(all_holdings)
    total_value = sum(h.value_eur for h in combined if h.value_eur is not None)

    loaded_times = [s.loaded_at for s in snapshots if s.loaded_at is not None]
    loaded_at: datetime | None = max(loaded_times) if loaded_times else None
    ok = all(s.ok for s in snapshots) if snapshots else True

    return DepotSnapshot(
        depot_id=depot_id,
        provider_id=provider_id,
        holdings=combined,
        total_value_eur=total_value,
        loaded_at=loaded_at,
        ok=ok,
        message=f"Gesamt aus {len(snapshots)} Depot(s).",
    )
