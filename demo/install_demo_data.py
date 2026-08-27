"""Demo-Daten installieren (fiktives Plus-Portfolio, keine echten Binance-Daten).

Kopiert CSVs und Snapshot nach data/depots/binance/ und erzeugt Kurs-Cache
für die Depot-Entwicklung. Überschreibt nur Demo-relevante Dateien.

Usage:
    .venv\\Scripts\\python.exe demo/install_demo_data.py
"""

from __future__ import annotations

import json
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
ROOT = DEMO_DIR.parent
DATA = ROOT / "data"
BINANCE = DATA / "depots" / "binance"
CACHE_DIR = BINANCE / "tages_cache"
PRICE_CACHE = DATA / "portfolio_price_cache.json"
SETTINGS = DATA / "settings.json"

# Monatliche Demo-Kurse (EUR, fiktiv, steigender Trend)
MONTHLY_PRICES: dict[str, list[tuple[str, float]]] = {
    "BTC": [
        ("2024-03-01", 45000),
        ("2024-06-01", 52000),
        ("2024-09-01", 58000),
        ("2024-12-01", 62000),
        ("2025-03-01", 68000),
        ("2025-06-01", 75000),
        ("2025-09-01", 82000),
        ("2025-12-01", 88000),
        ("2026-03-01", 90000),
        ("2026-08-01", 92000),
    ],
    "ETH": [
        ("2024-03-01", 1800),
        ("2024-06-01", 2100),
        ("2024-09-01", 2400),
        ("2024-12-01", 2600),
        ("2025-03-01", 2800),
        ("2025-06-01", 3000),
        ("2025-09-01", 3200),
        ("2025-12-01", 3300),
        ("2026-03-01", 3350),
        ("2026-08-01", 3400),
    ],
    "SOL": [
        ("2024-03-01", 60),
        ("2024-06-01", 75),
        ("2024-09-01", 95),
        ("2024-12-01", 110),
        ("2025-03-01", 120),
        ("2025-06-01", 130),
        ("2025-09-01", 138),
        ("2025-12-01", 142),
        ("2026-03-01", 144),
        ("2026-08-01", 145),
    ],
    "LINK": [
        ("2024-03-01", 8.0),
        ("2024-06-01", 10.0),
        ("2024-09-01", 11.5),
        ("2024-12-01", 13.0),
        ("2025-03-01", 14.5),
        ("2025-06-01", 16.0),
        ("2025-09-01", 17.0),
        ("2025-12-01", 17.5),
        ("2026-03-01", 17.8),
        ("2026-08-01", 18.0),
    ],
}


def _copy_csv(name: str) -> None:
    shutil.copy2(DEMO_DIR / name, BINANCE / name)


def _write_meta(name: str, payload: dict) -> None:
    (BINANCE / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _install_snapshot() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    raw = json.loads((DEMO_DIR / "demo_snapshot.json").read_text(encoding="utf-8"))
    raw["day"] = today
    raw["loaded_at"] = datetime.now(timezone.utc).astimezone().isoformat()
    (CACHE_DIR / f"{today}.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")


def _install_price_cache() -> None:
    entries: dict[str, float] = {}
    for asset, series in MONTHLY_PRICES.items():
        for day_str, price in series:
            entries[f"{asset}@{day_str}"] = float(price)
    DATA.mkdir(parents=True, exist_ok=True)
    PRICE_CACHE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _install_settings() -> None:
    payload = {
        "price_mode": "frozen",
        "ui": {
            "main_nav": "depot",
            "capital_flow_range": "max",
            "portfolio_history_range": "max",
        },
    }
    DATA.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    BINANCE.mkdir(parents=True, exist_ok=True)
    for csv_name in ("kaeufe.csv", "verkaeufe.csv", "zufluesse.csv"):
        _copy_csv(csv_name)
    _write_meta("kaeufe_meta.json", {"trade_ids": []})
    _write_meta("verkaeufe_meta.json", {"trade_ids": []})
    _write_meta("zufluesse_meta.json", {"seen_ids": []})
    _install_snapshot()
    _install_price_cache()
    _install_settings()
    print("Demo-Daten installiert.")
    print(f"  Ziel: {BINANCE}")
    print("  Modus: Feste Daten (kein Binance-Sync nötig für Screenshots)")
    print("  Portfolio: ~33.200 EUR (fiktiv, im Plus)")
    print("  Netto eingezahlt: 25.000 EUR")
    print()
    print("App starten: start.bat")
    print("Wichtig: Nicht auf „Daten von Binance aktualisieren“ klicken.")


if __name__ == "__main__":
    main()
