"""Einstellungen des Depot-Trackers (lokal gespeichert)."""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Feste Daten: nie automatisch neu laden (ideal zum Programmieren)
PRICE_MODE_FROZEN = "frozen"
# Live: beim ersten Start an einem Tag einmal Binance, danach Tages-Cache
PRICE_MODE_LIVE_DAILY = "live_daily"
# Altname – wird als „frozen“ behandelt
PRICE_MODE_CACHE_ONLY = "cache_only"

PRICE_MODE_LABELS = {
    PRICE_MODE_FROZEN: "Feste Daten (Entwicklung)",
    PRICE_MODE_LIVE_DAILY: "Live (max. 1× pro Tag)",
}


def _read_settings() -> dict[str, str]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, TypeError):
        return {}


def get_price_mode() -> str:
    """Liest den Kurs-/Bestands-Modus. Standard: feste Daten (schnell)."""
    mode = _read_settings().get("price_mode", PRICE_MODE_FROZEN)
    if mode == PRICE_MODE_CACHE_ONLY:
        return PRICE_MODE_FROZEN
    if mode not in PRICE_MODE_LABELS:
        return PRICE_MODE_FROZEN
    return mode


def is_frozen_mode() -> bool:
    """True = Bestände & Kurse nur aus gespeichertem Snapshot, kein Auto-Reload."""
    return get_price_mode() == PRICE_MODE_FROZEN


def save_price_mode(mode: str) -> None:
    """Speichert die Modus-Einstellung dauerhaft."""
    if mode == PRICE_MODE_CACHE_ONLY:
        mode = PRICE_MODE_FROZEN
    if mode not in PRICE_MODE_LABELS:
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings = _read_settings()
    settings["price_mode"] = mode
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def get_ui_pref(key: str, default: str = "") -> str:
    """Liest UI-Einstellung (bleibt nach App-Neustart erhalten)."""
    ui = _read_settings().get("ui", {})
    if not isinstance(ui, dict):
        return default
    value = ui.get(key, default)
    return str(value) if value is not None else default


def save_ui_pref(key: str, value: str) -> None:
    """Speichert UI-Einstellung in settings.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings = _read_settings()
    ui = settings.setdefault("ui", {})
    if not isinstance(ui, dict):
        ui = {}
        settings["ui"] = ui
    ui[key] = value
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def persisted_data_files() -> list[tuple[str, str]]:
    """Übersicht der lokal gespeicherten Dateien (Pfad relativ zu data/)."""
    return [
        ("kaeufe.csv", "Käufe (Trade-Historie)"),
        ("verkaeufe.csv", "Verkäufe"),
        ("zufluesse.csv", "Ein- und Auszahlungen"),
        ("preis_zonen.json", "Preis-Zonen (USD-Schwellen)"),
        ("settings.json", "App-Einstellungen"),
        ("sync_meta.json", "Letzter Binance-Sync"),
        ("fee_rate_cache.json", "Gebühren-Kurs-Cache"),
        ("portfolio_price_cache.json", "Depot-Historie Kurs-Cache"),
        ("tages_cache/", "Tages-Depot-Snapshots"),
    ]
