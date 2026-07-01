"""Zeitstempel der letzten Binance-Synchronisation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from core.storage import binance_dir

SYNC_META = binance_dir() / "sync_meta.json"


def _read_meta() -> dict[str, str]:
    if not SYNC_META.exists():
        return {}
    try:
        return json.loads(SYNC_META.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError):
        return {}


def get_last_trade_sync() -> datetime | None:
    """Wann Trade-Historie (Käufe/Verkäufe) zuletzt von Binance geholt wurde."""
    raw = _read_meta().get("trades_synced_at")
    if raw:
        return datetime.fromisoformat(raw)

    # Fallback: CSV existiert schon, aber Meta-Datei noch nicht
    kaeufe = SYNC_META.parent / "kaeufe.csv"
    if kaeufe.exists():
        mtime = datetime.fromtimestamp(kaeufe.stat().st_mtime, tz=timezone.utc)
        return mtime
    return None


def mark_trades_synced() -> None:
    """Speichert den aktuellen Zeitpunkt als letzte Trade-Synchronisation."""
    SYNC_META.parent.mkdir(parents=True, exist_ok=True)
    meta = _read_meta()
    meta["trades_synced_at"] = datetime.now(timezone.utc).isoformat()
    SYNC_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def format_sync_time(value: datetime | None) -> str:
    """Menschenlesbare Anzeige für die UI."""
    if value is None:
        return "noch nie von Binance geladen"
    local = value.astimezone()
    return local.strftime("%d.%m.%Y %H:%M")
