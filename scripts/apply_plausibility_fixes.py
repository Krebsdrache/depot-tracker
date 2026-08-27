"""Einmalige Datenkorrekturen für den Plausibilitäts-Check (nur lokal ausführen).

Konfiguration in ``plausibility_fixes.local.json`` (nicht versioniert).
Vorlage: ``plausibility_fixes.example.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "plausibility_fixes.local.json"
EXAMPLE_PATH = Path(__file__).resolve().parent / "plausibility_fixes.example.json"

sys.path.insert(0, str(ROOT / "src"))

from binance_data import (  # noqa: E402
    KAUF_CSV,
    VERKAUF_CSV,
    load_purchases_csv,
    load_sells_csv,
    _sells_by_coin_from_csv,
)
from deposit_trades import merge_purchases_with_deposits  # noqa: E402
from inflows import ZUFLUSS_CSV, load_zufluesse_csv  # noqa: E402
from steuer import berechne_haltefristen, plausibilitaet_pro_coin, _apply_fifo  # noqa: E402


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(
            f"Keine lokale Konfiguration: {CONFIG_PATH.name}\n"
            f"Kopiere {EXAMPLE_PATH.name} nach {CONFIG_PATH.name} "
            "und passe die Werte an (Datei wird nicht ins Git übernommen)."
        )
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _snapshot_path(config: dict) -> Path:
    rel = str(config.get("snapshot", "")).strip()
    if not rel:
        print("Konfiguration: Feld 'snapshot' fehlt.")
        sys.exit(1)
    path = ROOT / rel
    if not path.exists():
        print(f"Snapshot nicht gefunden: {path}")
        sys.exit(1)
    return path


def _balances(snapshot: Path) -> dict[str, float]:
    snap = json.loads(snapshot.read_text(encoding="utf-8"))
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


def _trade_row(
    *,
    trade_id: int,
    coin: str,
    datum: str,
    menge: float,
    price_eur: float,
    price_column: str,
    preis_quelle: str,
) -> dict[str, object]:
    return {
        "trade_id": trade_id,
        "coin": coin,
        "datum": datum,
        "menge": round(menge, 8),
        price_column: price_eur,
        "preis_geschaetzt": 1,
        "preis_quelle": preis_quelle,
        "commission": 0.0,
        "commission_asset": "",
        "gebuehr_eur": 0.0,
        "gebuehr_kurs_eur": float("nan"),
        "gebuehr_geschaetzt": 0,
        "gebuehr_quelle": "",
    }


def apply_opening_balances(config: dict, balances: dict[str, float]) -> None:
    for entry in config.get("opening_balances", []):
        coin = str(entry["coin"])
        preis_quelle = str(entry.get("preis_quelle", "eroeffnung"))
        balance = balances.get(coin, 0.0)
        open_before = _fifo_open(coin)
        menge = balance - open_before
        if menge <= 1e-10:
            print(f"{coin}: kein Eröffnungsbestand nötig.")
            continue

        df = pd.read_csv(KAUF_CSV)
        if (df["preis_quelle"].astype(str) == preis_quelle).any():
            print(f"{coin}: Eröffnungsbestand existiert bereits.")
            continue

        row = _trade_row(
            trade_id=int(entry["trade_id"]),
            coin=coin,
            datum=str(entry["datum"]),
            menge=menge,
            price_eur=float(entry["kaufpreis_eur"]),
            price_column="kaufpreis_eur",
            preis_quelle=preis_quelle,
        )
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.sort_values(["coin", "datum"], inplace=True)
        df.to_csv(KAUF_CSV, index=False)
        print(f"{coin}: Eröffnungsbestand +{menge:.8f} @ {entry['kaufpreis_eur']} EUR.")


def apply_card_deposit_fixes(config: dict, balances: dict[str, float]) -> None:
    for entry in config.get("card_deposit_fixes", []):
        coin_label = str(entry["coin_label"])
        typ = str(entry.get("typ", "fiat_karte"))
        df = load_zufluesse_csv()
        mask = (df["typ"] == typ) & (df["coin"].astype(str) == coin_label)
        if not mask.any():
            print(f"{coin_label}: kein passender Eintrag in zufluesse.csv.")
            continue

        target_coin = coin_label.split("→", 1)[-1].strip()
        balance = balances.get(target_coin, 0.0)
        wert = float(df.loc[mask, "wert_eur"].iloc[0])
        df.loc[mask, "menge"] = balance
        df.to_csv(ZUFLUSS_CSV, index=False)
        print(f"{target_coin}: zufluesse.csv menge -> {balance:.8f} ({wert:.0f} EUR).")


def apply_balance_corrections(config: dict, balances: dict[str, float]) -> None:
    df = pd.read_csv(VERKAUF_CSV)
    corrections: list[dict[str, object]] = []

    for entry in config.get("balance_corrections", []):
        coin = str(entry["coin"])
        trade_id = int(entry["trade_id"])
        if (df["trade_id"].astype(int) == trade_id).any():
            continue

        balance = balances.get(coin, 0.0)
        open_qty = _fifo_open(coin)
        excess = open_qty - balance
        if excess <= 1e-6:
            print(f"{coin}: keine Saldo-Korrektur nötig.")
            continue

        corrections.append(
            _trade_row(
                trade_id=trade_id,
                coin=coin,
                datum=str(entry["datum"]),
                menge=excess,
                price_eur=float(entry["verkaufspreis_eur"]),
                price_column="verkaufspreis_eur",
                preis_quelle=str(entry.get("preis_quelle", "saldo_korrektur")),
            )
        )
        print(f"{coin}: Saldo-Korrektur-Verkauf {excess:.8f}.")

    if not corrections:
        return
    df = pd.concat([df, pd.DataFrame(corrections)], ignore_index=True)
    df.sort_values(["coin", "datum"], inplace=True)
    df.to_csv(VERKAUF_CSV, index=False)


def report(balances: dict[str, float]) -> None:
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
    config = _load_config()
    snapshot = _snapshot_path(config)
    balances = _balances(snapshot)
    apply_opening_balances(config, balances)
    apply_card_deposit_fixes(config, balances)
    apply_balance_corrections(config, balances)
    report(balances)


if __name__ == "__main__":
    main()
