# Depot-Tracker

Lokales **Streamlit-Dashboard** für dein **Binance Spot-Depot** (nur Lesen, keine Trades).

## Voraussetzungen

- Python 3.11+ (getestet mit 3.14)
- Binance-API-Key mit **Enable Reading** (kein Trading nötig)

## Installation

```powershell
cd depot-tracker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
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

**Tipp:** Rechtsklick auf `start.bat` → **An Pin anheften** oder **Senden an → Desktop (Verknüpfung erstellen)** — dann immer griffbereit.

### Manuell (Terminal)

```powershell
cd depot-tracker
.\.venv\Scripts\python.exe -m streamlit run src\app.py
```

Browser öffnet sich automatisch (Standard: `http://localhost:8501`).

## Erste Schritte

1. **`.env`** mit Binance-Keys anlegen  
2. App starten  
3. Oben rechts **„Daten von Binance aktualisieren“** klicken (Trades, Kapitalflüsse, fehlende Preise/Gebühren – kann **2–4 Minuten** dauern)  
4. Optional in der Sidebar: **„Kurse & Bestände jetzt laden“** für aktuelle Spot-Kurse  

## Datenspeicherung (`data/`)

Alle Historie liegt lokal auf der Festplatte – beim Schließen der App **bleibt alles erhalten**:

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

## Funktionen (Kurzüberblick)

- **Depot-Übersicht** – Bestände, Kurse, Einstand (**FIFO**, wie Bilanz/Steuer), unrealisierter G/V  
- **Kapitalfluss** – Netto eingezahltes Kapital (Bank, Karte, externe Wallets)  
- **Depot-Entwicklung** – historischer Depotwert vs. eingezahltes Kapital  
- **Bilanz** – realisierte G/V (FIFO), Coin-Übersicht, **Jahresübersicht**, CSV-Export  
- **Handelsgebühren** – Auswertung inkl. historischer EUR-Kurse  
- **Steuer-Haltefrist** – DE 1-Jahres-Regel, Frist-Kalender  
- **Preis-Zonen** – Strategie-Tabellen mit Live-Kursen (EUR/USD)  

## Sidebar: Kurs-Modus

- **Feste Daten** – kein Auto-Reload (schnell zum Entwickeln)  
- **Live (1×/Tag)** – Kurse beim ersten Start des Tages von Binance, danach Tages-Cache  

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

## Hinweise

- Krypto-Deposits aus `zufluesse.csv` werden für **FIFO, Bilanz und Steuer** automatisch als Käufe behandelt (Einstand = Wert bei Einzahlung).  
- Nur **Binance Spot**; Earn, Staking, Futures etc. sind nicht enthalten.  
- Steuer-Auswertung ist eine **Orientierung**, kein Steuerberater-Ersatz.  
- Einstand bei externen Wallet-Einzahlungen: **Kurs zum Deposit-Zeitpunkt** aus dem Sync (geschätzt, wenn kein EUR-Paar).  
