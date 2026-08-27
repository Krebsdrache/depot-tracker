# Depot-Tracker

Lokales **Streamlit-Dashboard** zur Analyse eines **Binance Spot-Depots** — nur Lesen, keine Trades. Bestände und Kurse kommen live von der Binance-API; Trade-Historie, Einzahlungen und Auswertungen liegen lokal in CSV-Dateien auf der Festplatte.

## Funktionen

- **Depot-Übersicht** — Bestände, Kurse, FIFO-Einstand, unrealisierter Gewinn/Verlust, Wertverteilung als Diagramm
- **Kapitalfluss & Entwicklung** — netto eingezahltes Kapital (Bank, Karte, externe Wallets) und historischer Depotwert im Zeitverlauf
- **Was-wäre-wenn?** — Kurs-Szenarien und ATH-Vergleiche auf Basis der aktuellen Bestände
- **Bilanz** — realisierte Gewinne/Verluste nach FIFO, Coin- und Jahresübersicht, CSV-Export
- **Handelsgebühren** — Auswertung in EUR, inkl. historischer Kurse zum Trade-Zeitpunkt
- **Steuer-Haltefrist** — deutsche 1-Jahres-Regel, FIFO-Tranchen, Frist-Kalender (Orientierung, keine Steuerberatung)
- **Preis-Zonen** — Strategie-Tabellen mit Live-Kursen in EUR/USD

## Screenshots

Ordner `docs/` für App-Ansichten vorgesehen:

| Ansicht | Datei |
|---------|--------|
| Depot-Übersicht | `docs/depot.png` |
| Depot-Entwicklung | `docs/entwicklung.png` |
| Steuer-Haltefrist | `docs/steuer.png` |

Nach dem Hinzufügen der PNGs im README einbinden, z. B. `![Depot-Übersicht](docs/depot.png)`.

## Technischer Aufbau

```
Binance REST (python-binance, nur Lesen)
        ↓
binance_data.py / inflows.py  →  CSV in data/
        ↓
fifo.py  →  bilanz.py, steuer.py, gebuehren.py, portfolio_history.py
        ↓
app.py (Streamlit + Altair)
```

- **UI:** Streamlit-Dashboard mit sieben Bereichen (Navigation in `src/app.py`)
- **Datenanbindung:** Binance Spot REST — Bestände, Kurse, Trades, Ein-/Auszahlungen; kein WebSocket, kein Trading
- **Persistenz:** Lokale CSVs und JSON-Caches unter `data/` (Trade-Historie, Kapitalflüsse, Tages-Snapshots, Gebühren-Kurse)
- **FIFO:** Eigenes Modul `fifo.py`, gemeinsame Logik für Bilanz, Steuer und Einstandspreise
- **Architektur:** Provider-basiert — abstrakte Basisklasse `Provider`, konkreter `BinanceProvider`, neutrales Domänenmodell in `core/model.py` (erweiterbar für weitere Depots)
- **Visualisierung:** Altair-Charts (Linien, Flächen, Donuts, Balken)
- **Tests:** 110 Unit-Tests in 19 Testdateien (`unittest discover -s tests`)
- **Umfang:** 48 Python-Module, ~10.000 Zeilen Anwendungscode

### Stack

| Komponente | Technologie |
|------------|-------------|
| Sprache | Python 3.11+ |
| UI | Streamlit |
| Daten | pandas |
| API | python-binance (Binance Spot REST) |
| Diagramme | Altair |
| Konfiguration | python-dotenv (`.env`) |

## Installation

**Voraussetzungen:** Python 3.11+ (getestet mit 3.14), Binance-API-Key mit **Enable Reading** (kein Trading nötig)

```powershell
cd depot-tracker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install altair
```

## API-Keys (`.env`)

Lege im Projektroot eine Datei `.env` an:

```env
BINANCE_API_KEY=dein_api_key
BINANCE_API_SECRET=dein_api_secret
```

Die Datei wird nicht ins Git übernommen (siehe `.gitignore`). Trage echte Keys nur lokal ein — nie ins Repository committen.

## App starten

### Einfach (empfohlen)

**Doppelklick** auf `start.bat` im Projektordner (nach `cd depot-tracker`).

- Schwarzes Fenster **offen lassen** (sonst ist die App wieder weg)
- Browser öffnet sich unter **http://localhost:8501**
- Beenden: im Fenster **Strg+C**

### Manuell (Terminal)

```powershell
cd depot-tracker
.\.venv\Scripts\python.exe -m streamlit run src\app.py
```

## Erste Schritte

1. **`.env`** mit Binance-Keys anlegen
2. App starten
3. Oben rechts **„Daten von Binance aktualisieren“** klicken (Trades, Kapitalflüsse, fehlende Preise/Gebühren — kann **2–4 Minuten** dauern)
4. Optional in der Sidebar: **„Kurse & Bestände jetzt laden“** für aktuelle Spot-Kurse

## Datenspeicherung (`data/`)

Alle Historie liegt lokal auf der Festplatte — beim Schließen der App bleibt alles erhalten:

| Datei / Ordner | Inhalt |
|----------------|--------|
| `kaeufe.csv` / `verkaeufe.csv` | Trade-Historie |
| `zufluesse.csv` | Ein- und Auszahlungen (Bank, Karte, Krypto) |
| `settings.json` | App-Einstellungen (Kurs-Modus, UI) |
| `preis_zonen.json` | Preis-Zonen-Schwellen |
| `fee_rate_cache.json` | Cache historischer Kurse (Gebühren) |
| `portfolio_price_cache.json` | Cache Tageskurse (Depot-Entwicklung) |
| `tages_cache/` | Tages-Snapshots Bestand + Kurse |

Übersicht auch in der App unter Sidebar → **Datenspeicherung**.

## Sidebar: Kurs-Modus

- **Feste Daten** — kein Auto-Reload (schnell zum Entwickeln)
- **Live (1×/Tag)** — Kurse beim ersten Start des Tages von Binance, danach Tages-Cache

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

## Hinweise

- Krypto-Deposits aus `zufluesse.csv` werden für FIFO, Bilanz und Steuer automatisch als Käufe behandelt (Einstand = Wert bei Einzahlung).
- Nur **Binance Spot**; Earn, Staking, Futures etc. sind nicht enthalten.
- Steuer-Auswertung ist eine Orientierung, kein Steuerberater-Ersatz.
- Einstand bei externen Wallet-Einzahlungen: Kurs zum Deposit-Zeitpunkt aus dem Sync (geschätzt, wenn kein EUR-Paar).
