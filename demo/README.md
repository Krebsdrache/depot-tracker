# Demo-Daten für Screenshots (fiktiv)

Alle Dateien in diesem Ordner sind **erfundene Beispieldaten** — kein echtes Portfolio.

## Installation

```powershell
cd C:\Users\samuk\Desktop\Cursor\depot-tracker
.\.venv\Scripts\python.exe demo\install_demo_data.py
```

Das Skript kopiert CSVs nach `data/depots/binance/`, legt einen Tages-Snapshot an und
aktiviert **Feste Daten** in `settings.json`.

## Demo-Portfolio (Überblick)

| Kennzahl | Wert (fiktiv) |
|----------|----------------|
| Gesamtwert | ~33.200 EUR |
| Netto eingezahlt | 25.000 EUR |
| Coins | BTC, ETH, SOL, LINK + EUR-Cash |
| Performance | Alle Krypto-Positionen im Plus |

## Screenshots

1. Demo installieren (siehe oben)
2. `start.bat` starten
3. **Nicht** „Daten von Binance aktualisieren“ klicken
4. Ansichten fotografieren → `docs/depot.png`, `docs/entwicklung.png`, `docs/steuer.png`

## Echte Daten wiederherstellen

Vorher Backup anlegen:

```powershell
Copy-Item -Recurse data data_backup
```

Nach den Screenshots:

```powershell
Remove-Item -Recurse -Force data
Copy-Item -Recurse data_backup data
```
