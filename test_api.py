"""Kurzer Test: Binance API-Key aus .env prüfen."""
from dotenv import load_dotenv
import os
import sys

from binance.client import Client

load_dotenv(override=True)
api_key = os.getenv("BINANCE_API_KEY", "")
api_secret = os.getenv("BINANCE_API_SECRET", "")

if not api_key or not api_secret:
    print("FEHLER: BINANCE_API_KEY oder BINANCE_API_SECRET fehlt in .env")
    sys.exit(1)

print(f"Key geladen (Länge: {len(api_key.strip())}), Secret geladen (Länge: {len(api_secret.strip())})")

if api_key != api_key.strip() or api_secret != api_secret.strip():
    print("WARNUNG: Leerzeichen am Anfang/Ende – bitte in .env entfernen")

try:
    client = Client(api_key.strip(), api_secret.strip())
    account = client.get_account()
    print("OK: Verbindung erfolgreich!")
    print(f"Kontotyp: {account['accountType']}")
    balances = [
        b for b in account["balances"]
        if float(b["free"]) + float(b["locked"]) > 0
    ]
    print(f"Assets mit Guthaben: {len(balances)}")
    for b in balances[:5]:
        total = float(b["free"]) + float(b["locked"])
        print(f"  {b['asset']}: {total}")
except Exception as e:
    print(f"FEHLER: {type(e).__name__}: {e}")
    sys.exit(1)
