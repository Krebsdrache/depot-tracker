"""Binance-Verbindung: Keys aus .env laden und Kontostände abrufen."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from binance.client import Client
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class BinanceResult:
    ok: bool
    message: str
    balances: list[dict[str, float | str]] | None = None


def _load_credentials() -> tuple[str, str] | tuple[None, None]:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return None, None
    if api_key.startswith("hier_") or api_secret.startswith("hier_"):
        return None, None
    return api_key, api_secret


def fetch_binance_balances() -> BinanceResult:
    """Verbindet mit Binance und liefert alle Assets mit Guthaben > 0."""
    api_key, api_secret = _load_credentials()
    if not api_key or not api_secret:
        return BinanceResult(
            ok=False,
            message=(
                "Keine gültigen API-Keys gefunden. "
                "Trage BINANCE_API_KEY und BINANCE_API_SECRET in die .env ein "
                f"({PROJECT_ROOT / '.env'})."
            ),
        )

    try:
        client = Client(api_key, api_secret)
        account = client.get_account()
    except Exception as exc:
        return BinanceResult(
            ok=False,
            message=f"Binance-Verbindung fehlgeschlagen: {type(exc).__name__}: {exc}",
        )

    rows: list[dict[str, float | str]] = []
    for entry in account["balances"]:
        free = float(entry["free"])
        locked = float(entry["locked"])
        total = free + locked
        if total <= 0:
            continue
        rows.append(
            {
                "Asset": entry["asset"],
                "Verfügbar": free,
                "Reserviert": locked,
                "Gesamt": total,
            }
        )

    rows.sort(key=lambda row: float(row["Gesamt"]), reverse=True)
    return BinanceResult(
        ok=True,
        message=f"Verbunden (Kontotyp: {account['accountType']})",
        balances=rows,
    )
