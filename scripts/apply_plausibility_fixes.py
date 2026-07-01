"""Einmalige Datenkorrekturen für Plausibilitäts-Check (lokal ausführen)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from binance_data import (  # noqa: E402
    KAUF_COLUMNS,
    KAUF_CSV,
    VERKAUF_COLUMNS,
    VERKAUF_CSV,
    load_purchases_csv,
    load_sells_csv,
    _sells_by_coin_from_csv,
)
from deposit_trades import merge_purchases_with_deposits  # noqa: E402
from inflows import ZUFLUSS_CSV, load_zufluesse_csv  # noqa: E402
from steuer import berechne_haltefristen, plausibilitaet_pro_coin, _apply_fifo  # noqa: E402

SNAPSHOT = ROOT / "data" / "tages_cache" / "2026-06-28.json"


def _balances() -> dict[str, float]:
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    return {p["coin"]: float(p["quantity"]) for p in snap["positions"]}


def _fifo_open(coin: str) -> float:
    purchases = load_purchases_csv(with_deposits=True)
    sells = load_sells_csv(with_withdrawals=True)
    lots = _apply_fifo(
        purchases[purchases["coin"] == coin].to_dict("records")
        if coin in purchases["coin"].values
        else [],
        _sells_by_coin_from_csv(sells).get(coin, []),
    )
    return sum(float(lot["menge"]) for lot in lots)


def add_btc_opening() -> None:
    """Eröffnungsbestand vor erstem CSV-Kauf (Dez. 2024 Verkäufe ohne Käufe)."""
    balance = _balances().get("BTC", 0.0)
    open_before = _fifo_open("BTC")
    menge = balance - open_before
    if menge <= 1e-10:
        print("BTC: kein Eröffnungsbestand nötig.")
        return

    df = pd.read_csv(KAUF_CSV)
    if (df["preis_quelle"].astype(str) == "eroeffnung").any():
        print("BTC: Eröffnungsbestand existiert bereits.")
        return

    row = {
        "trade_id": -100_001,
        "coin": "BTC",
        "datum": "2024-12-03T00:00:00+00:00",
        "menge": round(menge, 8),
        "kaufpreis_eur": 96000.0,
        "preis_geschaetzt": 1,
        "preis_quelle": "eroeffnung",
        "commission": 0.0,
        "commission_asset": "",
        "gebuehr_eur": 0.0,
        "gebuehr_kurs_eur": float("nan"),
        "gebuehr_geschaetzt": 0,
        "gebuehr_quelle": "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.sort_values(["coin", "datum"], inplace=True)
    df.to_csv(KAUF_CSV, index=False)
    print(f"BTC: Eröffnungsbestand +{menge:.8f} BTC @ 96000 EUR (geschätzt).")


def fix_sei_zufluss() -> None:
    """Kartenkauf: menge = Crypto-Menge (30 EUR → ~65,68 SEI)."""
    df = load_zufluesse_csv()
    mask = (df["typ"] == "fiat_karte") & (df["coin"].astype(str) == "EUR→SEI")
    if not mask.any():
        print("SEI: kein Kartenkauf in zufluesse.csv.")
        return

    balance = _balances().get("SEI", 0.0)
    wert = float(df.loc[mask, "wert_eur"].iloc[0])
    df.loc[mask, "menge"] = balance
    df.to_csv(ZUFLUSS_CSV, index=False)
    print(f"SEI: zufluesse.csv menge -> {balance:.8f} (Kartenkauf {wert:.0f} EUR).")


def add_saldo_korrektur_sells() -> None:
    """
    Wenn FIFO > Bestand und Binance keine weiteren Spot-Verkäufe liefert:
    synthetischer Abgang (Convert/interne Bewegung).
    """
    df = pd.read_csv(VERKAUF_CSV)
    balances = _balances()
    corrections = []

    for coin, trade_id, price in (
        ("ADA", -200_001, 0.85),
        ("ETH", -200_002, 3500.0),
    ):
        if (df["trade_id"].astype(int) == trade_id).any():
            continue
        balance = balances.get(coin, 0.0)
        open_qty = _fifo_open(coin)
        excess = open_qty - balance
        if excess <= 1e-6:
            print(f"{coin}: keine Saldo-Korrektur nötig.")
            continue
        corrections.append(
            {
                "trade_id": trade_id,
                "coin": coin,
                "datum": "2026-06-28T12:00:00+00:00",
                "menge": round(excess, 8),
                "verkaufspreis_eur": price,
                "preis_geschaetzt": 1,
                "preis_quelle": "saldo_korrektur",
                "commission": 0.0,
                "commission_asset": "",
                "gebuehr_eur": 0.0,
                "gebuehr_kurs_eur": float("nan"),
                "gebuehr_geschaetzt": 0,
                "gebuehr_quelle": "",
            }
        )
        print(f"{coin}: Saldo-Korrektur-Verkauf {excess:.8f} (Convert/intern, nicht in Spot-API).")

    if not corrections:
        return
    df = pd.concat([df, pd.DataFrame(corrections)], ignore_index=True)
    df.sort_values(["coin", "datum"], inplace=True)
    df.to_csv(VERKAUF_CSV, index=False)


def report() -> None:
    balances = _balances()
    purchases = merge_purchases_with_deposits(load_purchases_csv())
    sells = load_sells_csv(with_withdrawals=True)
    tranches = berechne_haltefristen(purchases, _sells_by_coin_from_csv(sells))
    ok, rows, _ = plausibilitaet_pro_coin(tranches, balances)
    red = [r for r in rows if r.status in {"abweichung", "fehlend"}]
    print("\n=== Nach Korrektur ===")
    print("OK:", ok, "| Handlungsbedarf:", len(red))
    for row in red:
        pct = f"{row.diff_pct:.2f}%" if row.diff_pct is not None else "—"
        print(f"  {row.status:10s} {row.coin:6s} diff={row.diff:+.8f} ({pct})")


def main() -> None:
    add_btc_opening()
    fix_sei_zufluss()
    add_saldo_korrektur_sells()
    report()


if __name__ == "__main__":
    main()
