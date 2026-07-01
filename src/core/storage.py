"""Zentrale Auflösung aller Datei-Pfade – pro Depot getrennt.

Layout:
    data/                      globale App-Daten (Einstellungen, geteilte Kurs-Caches)
    data/depots/<depot_id>/    depot-eigene Daten (Trades, Ein-/Auszahlungen, Snapshots)

Die bisher im Wurzel-Ordner ``data/`` liegenden Binance-Daten werden beim ersten
Zugriff **einmalig kopiert** (nicht verschoben) nach ``data/depots/binance/``.
Die Originale bleiben als Sicherung liegen; eine Markerdatei verhindert doppelte
Migration.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_ROOT = PROJECT_ROOT / "data"
DEPOTS_ROOT = DATA_ROOT / "depots"

BINANCE_DEPOT_ID = "binance"
_MIGRATION_MARKER = ".migrated_from_root"

# Depot-eigene Dateien/Ordner, die historisch direkt unter data/ lagen.
_BINANCE_DEPOT_ITEMS: tuple[str, ...] = (
    "kaeufe.csv",
    "kaeufe_meta.json",
    "verkaeufe.csv",
    "verkaeufe_meta.json",
    "zufluesse.csv",
    "zufluesse_meta.json",
    "sync_meta.json",
    "tages_cache",
)


def data_root() -> Path:
    """Globaler Datenordner (App-Einstellungen, geteilte Caches)."""
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return DATA_ROOT


def depot_dir(depot_id: str) -> Path:
    """Datenordner eines Depots; wird bei Bedarf angelegt."""
    target = DEPOTS_ROOT / depot_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def migrate_legacy(
    legacy_root: Path,
    target: Path,
    items: Iterable[str],
    *,
    marker: str = _MIGRATION_MARKER,
) -> list[str]:
    """Kopiert Alt-Daten einmalig in ``target``. Idempotent über eine Markerdatei.

    Kopiert (nie verschiebt) und überschreibt nichts Vorhandenes. Gibt die Namen
    der tatsächlich migrierten Einträge zurück.
    """
    target.mkdir(parents=True, exist_ok=True)
    marker_path = target / marker
    if marker_path.exists():
        return []

    migrated: list[str] = []
    for name in items:
        src = legacy_root / name
        dst = target / name
        if not src.exists() or dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        migrated.append(name)

    marker_path.write_text(
        datetime.now(timezone.utc).isoformat(),
        encoding="utf-8",
    )
    return migrated


def binance_dir() -> Path:
    """Datenordner des Binance-Depots (mit einmaliger Migration der Alt-Daten)."""
    target = DEPOTS_ROOT / BINANCE_DEPOT_ID
    migrate_legacy(DATA_ROOT, target, _BINANCE_DEPOT_ITEMS)
    return target
