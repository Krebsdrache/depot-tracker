"""Tages-Cache für Kurse und Depot-Snapshot (1× pro Tag)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from binance_data import PortfolioResult, Position
from core.storage import binance_dir

CACHE_DIR = binance_dir() / "tages_cache"


def _today_key() -> str:
    return date.today().isoformat()


def _snapshot_path(day: str | None = None) -> Path:
    return CACHE_DIR / f"{day or _today_key()}.json"


def has_today_snapshot() -> bool:
    return _snapshot_path().exists()


def has_any_snapshot() -> bool:
    return CACHE_DIR.exists() and any(CACHE_DIR.glob("*.json"))


def save_today_snapshot(result: PortfolioResult) -> str:
    """Speichert Depot + Kurse für heute auf der Festplatte."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    loaded_at = datetime.now(timezone.utc).astimezone().isoformat()
    payload = {
        "day": _today_key(),
        "loaded_at": loaded_at,
        "total_value_eur": result.total_value_eur,
        "message": result.message,
        "positions": [asdict(pos) for pos in result.positions],
    }
    _snapshot_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return loaded_at


def _load_snapshot_file(path: Path) -> tuple[PortfolioResult, str, str] | None:
    """Liest eine Snapshot-Datei. Rückgabe: Ergebnis, Anzeige-Zeit, Tag (YYYY-MM-DD)."""
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError, KeyError):
        return None

    day_key = str(payload.get("day", path.stem))
    positions = [Position(**item) for item in payload.get("positions", [])]
    result = PortfolioResult(
        ok=True,
        message=str(payload.get("message", "Depot aus Tages-Cache geladen.")),
        positions=positions,
        total_value_eur=float(payload.get("total_value_eur", 0.0)),
    )
    loaded_at_raw = str(payload.get("loaded_at", ""))
    if loaded_at_raw:
        loaded_at = datetime.fromisoformat(loaded_at_raw).astimezone().strftime(
            "%d.%m.%Y %H:%M:%S"
        )
    else:
        loaded_at = day_key
    return result, loaded_at, day_key


def load_today_snapshot() -> tuple[PortfolioResult, str] | None:
    """Lädt den heutigen Snapshot von der Festplatte."""
    loaded = _load_snapshot_file(_snapshot_path())
    if loaded is None:
        return None
    result, loaded_at, _day = loaded
    return result, loaded_at


def load_latest_snapshot() -> tuple[PortfolioResult, str, str] | None:
    """Lädt den neuesten verfügbaren Snapshot (auch von einem früheren Tag)."""
    if not CACHE_DIR.exists():
        return None
    files = sorted(CACHE_DIR.glob("*.json"), key=lambda path: path.stem, reverse=True)
    if not files:
        return None
    return _load_snapshot_file(files[0])


def format_loaded_at_display(loaded_at: str, from_cache: bool, day_key: str | None = None) -> str:
    """Kurzer Hinweis für die UI."""
    source = "Tages-Cache" if from_cache else "Binance (heute gespeichert)"
    text = f"{loaded_at} · {source}"
    if day_key and day_key != _today_key():
        try:
            day_label = datetime.fromisoformat(day_key).strftime("%d.%m.%Y")
        except ValueError:
            day_label = day_key
        text += f" · Stand vom {day_label} (nicht heute)"
    return text


def clear_today_snapshot() -> None:
    """Löscht den heutigen Snapshot."""
    path = _snapshot_path()
    if path.exists():
        path.unlink()
