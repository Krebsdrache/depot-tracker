"""Binance-Daten laden: Bestände, EUR-Preise und Einstandspreise (nur Lesen)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv

from fee_rates import (
    HistoricalRateCache,
    trade_fee_eur_at_time,
    trade_price_eur_at_time,
    valid_symbols_from_exchange,
)
from fifo import fifo_avg_entry_from_local

from core.storage import binance_dir

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
_DEPOT_DIR = binance_dir()
KAUF_CSV = _DEPOT_DIR / "kaeufe.csv"
KAUF_META = _DEPOT_DIR / "kaeufe_meta.json"
VERKAUF_CSV = _DEPOT_DIR / "verkaeufe.csv"
VERKAUF_META = _DEPOT_DIR / "verkaeufe_meta.json"
KAUF_COLUMNS = [
    "trade_id",
    "coin",
    "datum",
    "menge",
    "kaufpreis_eur",
    "preis_geschaetzt",
    "preis_quelle",
    "commission",
    "commission_asset",
    "gebuehr_eur",
    "gebuehr_kurs_eur",
    "gebuehr_geschaetzt",
    "gebuehr_quelle",
]
VERKAUF_COLUMNS = [
    "trade_id",
    "coin",
    "datum",
    "menge",
    "verkaufspreis_eur",
    "preis_geschaetzt",
    "preis_quelle",
    "commission",
    "commission_asset",
    "gebuehr_eur",
    "gebuehr_kurs_eur",
    "gebuehr_geschaetzt",
    "gebuehr_quelle",
]

# Reihenfolge, in der wir Handelspaare für die Trade-Historie probieren.
PREFERRED_QUOTE_ASSETS = ("EUR", "USDT", "USDC", "BTC", "BNB")


@dataclass(frozen=True)
class Position:
    """Eine offene Spot-Position (Coin mit Menge > 0)."""

    coin: str
    quantity: float
    current_price_eur: float | None
    current_value_eur: float | None
    avg_entry_price_eur: float | None
    entry_known: bool
    profit_loss_eur: float | None
    profit_loss_pct: float | None


@dataclass
class PortfolioResult:
    """Ergebnis eines kompletten Depot-Ladevorgangs."""

    ok: bool
    message: str
    positions: list[Position] = field(default_factory=list)
    total_value_eur: float = 0.0


@dataclass
class ClientResult:
    """Ergebnis beim Erstellen eines authentifizierten Binance-Clients."""

    ok: bool
    message: str
    client: Client | None = None
    exchange_info: dict | None = None
    tickers: dict[str, float] | None = None


@dataclass
class PurchaseSyncResult:
    """Ergebnis beim Synchronisieren der Kauf-CSV."""

    ok: bool
    message: str
    new_count: int = 0
    total_count: int = 0


@dataclass(frozen=True)
class TradeDataStatus:
    """Fehlende Preise/Gebühren in den lokalen Trade-CSV-Dateien."""

    buys_total: int
    sells_total: int
    buys_missing_fee: int
    sells_missing_price: int

    @property
    def needs_backfill(self) -> bool:
        return self.sells_missing_price > 0 or self.buys_missing_fee > 0


@dataclass
class TradeSyncOutcome:
    ok: bool
    message: str
    new_buys: int = 0
    new_sells: int = 0
    buys_backfilled: int = 0
    sells_backfilled: int = 0


def _load_credentials() -> tuple[str, str] | tuple[None, None]:
    """Liest API-Key und Secret aus der .env im Projektordner."""
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        return None, None
    if api_key.startswith("hier_") or api_secret.startswith("hier_"):
        return None, None
    return api_key, api_secret


def _build_ticker_map(client: Client) -> dict[str, float]:
    """Alle aktuellen Kurse von Binance als Dictionary symbol -> Preis."""
    return {item["symbol"]: float(item["price"]) for item in client.get_all_tickers()}


def _quote_to_eur_rate(quote_asset: str, tickers: dict[str, float]) -> float | None:
    """Wandelt 1 Einheit Quote-Währung in EUR um (z. B. USDT -> EUR)."""
    if quote_asset == "EUR":
        return 1.0

    direct = f"{quote_asset}EUR"
    if direct in tickers and tickers[direct] > 0:
        return tickers[direct]

    inverse = f"EUR{quote_asset}"
    if inverse in tickers and tickers[inverse] > 0:
        return 1.0 / tickers[inverse]

    via_usdt = f"{quote_asset}USDT"
    if via_usdt in tickers and tickers[via_usdt] > 0:
        usdt_eur = _quote_to_eur_rate("USDT", tickers)
        if usdt_eur is not None:
            return tickers[via_usdt] * usdt_eur

    return None


def _asset_price_in_eur(asset: str, tickers: dict[str, float]) -> float | None:
    """Aktueller Preis von 1 Coin-Einheit in EUR (Spot-Ticker, mehrere Umrechnungswege)."""
    price, _source = asset_price_in_eur_detailed(asset, tickers)
    return price


def asset_price_in_eur_detailed(asset: str, tickers: dict[str, float]) -> tuple[float | None, str]:
    """EUR-Preis plus Kurzbeschreibung des Umrechnungswegs."""
    asset = asset.strip().upper()
    if asset == "EUR":
        return 1.0, "direkt (EUR-Paar)"

    direct = f"{asset}EUR"
    if direct in tickers and tickers[direct] > 0:
        return tickers[direct], "direkt (EUR-Paar)"

    inverse = f"EUR{asset}"
    if inverse in tickers and tickers[inverse] > 0:
        return 1.0 / tickers[inverse], "direkt (EUR-Paar)"

    if asset in {"USDT", "USDC"}:
        for symbol in (f"{asset}EUR",):
            if symbol in tickers and tickers[symbol] > 0:
                return tickers[symbol], "direkt (EUR-Paar)"
        inverse_stable = f"EUR{asset}"
        if inverse_stable in tickers and tickers[inverse_stable] > 0:
            return 1.0 / tickers[inverse_stable], "direkt (EUR-Paar)"

    if asset == "USD":
        usdc_eur = _asset_price_in_eur("USDC", tickers)
        if usdc_eur is None:
            return None, "—"
        peg = tickers.get("USDCUSD")
        if peg and peg > 0:
            return peg * usdc_eur, "USD→USDC→EUR"
        return usdc_eur, "USD→USDC→EUR"

    via_usdc = f"{asset}USDC"
    if via_usdc in tickers and tickers[via_usdc] > 0:
        usdc_eur = _asset_price_in_eur("USDC", tickers)
        if usdc_eur is not None:
            return tickers[via_usdc] * usdc_eur, "USDC→EUR"

    via_usdt = f"{asset}USDT"
    if via_usdt in tickers and tickers[via_usdt] > 0:
        usdt_eur = _quote_to_eur_rate("USDT", tickers)
        if usdt_eur is not None:
            return tickers[via_usdt] * usdt_eur, "USDT→EUR"

    via_btc = f"{asset}BTC"
    if via_btc in tickers and tickers[via_btc] > 0:
        btc_eur = _asset_price_in_eur("BTC", tickers)
        if btc_eur is not None:
            return tickers[via_btc] * btc_eur, "BTC→EUR"

    return None, "—"


def fetch_spot_tickers() -> tuple[dict[str, float] | None, str]:
    """Aktuelle Binance-Spot-Ticker (symbol → Preis)."""
    client_result = create_authenticated_client()
    if not client_result.ok or client_result.client is None:
        return None, client_result.message
    tickers = client_result.tickers or _build_ticker_map(client_result.client)
    return tickers, ""


def usd_to_eur_rate(tickers: dict[str, float]) -> float | None:
    """Aktueller Kurs: 1 USD (≈ USDT) in EUR."""
    rate, _source = asset_price_in_eur_detailed("USDT", tickers)
    return rate


def asset_price_in_usd_detailed(asset: str, tickers: dict[str, float]) -> tuple[float | None, str]:
    """USD-Preis plus Kurzbeschreibung des Umrechnungswegs (primär USDT/USDC-Paare)."""
    asset = asset.strip().upper()
    if asset in {"USD", "USDT", "USDC", "BUSD"}:
        return 1.0, "direkt (USD)"

    for quote, label in (
        ("USDT", "direkt (USDT-Paar)"),
        ("USDC", "direkt (USDC-Paar)"),
        ("USD", "direkt (USD-Paar)"),
        ("BUSD", "direkt (USD-Paar)"),
    ):
        direct = f"{asset}{quote}"
        if direct in tickers and tickers[direct] > 0:
            return tickers[direct], label

        inverse = f"{quote}{asset}"
        if inverse in tickers and tickers[inverse] > 0:
            return 1.0 / tickers[inverse], label

    via_btc = f"{asset}BTC"
    if via_btc in tickers and tickers[via_btc] > 0:
        btc_usd, _ = asset_price_in_usd_detailed("BTC", tickers)
        if btc_usd is not None:
            return tickers[via_btc] * btc_usd, "BTC→USDT"

    direct_eur = f"{asset}EUR"
    if direct_eur in tickers and tickers[direct_eur] > 0:
        usd_eur = usd_to_eur_rate(tickers)
        if usd_eur is not None and usd_eur > 0:
            return tickers[direct_eur] / usd_eur, "EUR→USD"

    inverse_eur = f"EUR{asset}"
    if inverse_eur in tickers and tickers[inverse_eur] > 0:
        usd_eur = usd_to_eur_rate(tickers)
        if usd_eur is not None and usd_eur > 0:
            return (1.0 / tickers[inverse_eur]) / usd_eur, "EUR→USD"

    return None, "—"


def _position_from_balance(
    coin: str,
    quantity: float,
    tickers: dict[str, float],
    *,
    avg_entry: float | None,
) -> Position:
    """Eine Spot-Position mit aktuellem EUR-Kurs und optionalem Einstand."""
    price_eur = _asset_price_in_eur(coin, tickers)
    value_eur = quantity * price_eur if price_eur is not None else None
    entry_known = avg_entry is not None

    profit_loss_eur: float | None = None
    profit_loss_pct: float | None = None
    if entry_known and price_eur is not None and avg_entry is not None and avg_entry > 0:
        profit_loss_eur = (price_eur - avg_entry) * quantity
        profit_loss_pct = ((price_eur / avg_entry) - 1.0) * 100.0

    return Position(
        coin=coin,
        quantity=quantity,
        current_price_eur=price_eur,
        current_value_eur=value_eur,
        avg_entry_price_eur=avg_entry,
        entry_known=entry_known,
        profit_loss_eur=profit_loss_eur,
        profit_loss_pct=profit_loss_pct,
    )


def _sort_positions(positions: list[Position]) -> list[Position]:
    return sorted(
        positions,
        key=lambda pos: pos.current_value_eur if pos.current_value_eur is not None else -1.0,
        reverse=True,
    )


def _portfolio_total_eur(positions: list[Position]) -> float:
    return sum(pos.current_value_eur or 0.0 for pos in positions)


def _refresh_portfolio_live_prices(
    result: PortfolioResult,
    *,
    use_local_history: bool = True,
) -> tuple[PortfolioResult, str | None]:
    """
    Aktualisiert Bestände, Kurse und Gesamtwert live von Binance.

    Einstandspreise kommen weiterhin aus der lokalen CSV (schnell, kein Trade-Abruf).
    """
    client_result = create_authenticated_client()
    if not client_result.ok:
        return result, None

    assert client_result.client is not None
    tickers = client_result.tickers or _build_ticker_map(client_result.client)
    balances = _account_balances_from_client(client_result.client)
    purchases = load_purchases_csv(with_deposits=True) if use_local_history else None
    sells = load_sells_csv(with_withdrawals=True) if use_local_history else None

    positions: list[Position] = []
    for coin, quantity in balances.items():
        avg_entry: float | None = None
        if use_local_history and purchases is not None:
            avg_entry = fifo_avg_entry_from_local(
                coin,
                purchases,
                sells if sells is not None else pd.DataFrame(),
            )
        positions.append(
            _position_from_balance(coin, quantity, tickers, avg_entry=avg_entry)
        )

    positions = _sort_positions(positions)
    loaded_at = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")
    return (
        PortfolioResult(
            ok=True,
            message=result.message,
            positions=positions,
            total_value_eur=_portfolio_total_eur(positions),
        ),
        loaded_at,
    )


def _spot_symbols_for_asset(exchange_info: dict, asset: str) -> list[str]:
    """Findet Spot-Symbole, in denen der Coin als Basis-Asset gehandelt wird."""
    symbols: list[str] = []
    for entry in exchange_info["symbols"]:
        if entry.get("status") != "TRADING":
            continue
        if not entry.get("isSpotTradingAllowed", True):
            continue
        if entry["baseAsset"] != asset:
            continue
        symbols.append(entry["symbol"])

    def _priority(symbol: str) -> tuple[int, str]:
        quote = next(
            item["quoteAsset"]
            for item in exchange_info["symbols"]
            if item["symbol"] == symbol
        )
        try:
            return PREFERRED_QUOTE_ASSETS.index(quote), symbol
        except ValueError:
            return len(PREFERRED_QUOTE_ASSETS), symbol

    return sorted(symbols, key=_priority)


def _fetch_all_trades(client: Client, symbol: str) -> list[dict]:
    """Lädt alle eigenen Trades für ein Symbol (mit Paginierung)."""
    trades: list[dict] = []
    from_id: int | None = None

    while True:
        params: dict[str, int | str] = {"symbol": symbol, "limit": 1000}
        if from_id is not None:
            params["fromId"] = from_id

        batch = client.get_my_trades(**params)
        if not batch:
            break

        trades.extend(batch)
        if len(batch) < 1000:
            break
        from_id = batch[-1]["id"] + 1

    return trades


def _trade_price_in_eur(trade: dict, symbol_meta: dict, tickers: dict[str, float]) -> float | None:
    """Rechnet den Trade-Preis (pro Coin) in EUR um."""
    quote_asset = symbol_meta["quoteAsset"]
    quote_rate = _quote_to_eur_rate(quote_asset, tickers)
    if quote_rate is None:
        return None
    return float(trade["price"]) * quote_rate


def _average_entry_price_eur(
    trades: list[dict],
    symbol_meta_by_symbol: dict[str, dict],
    tickers: dict[str, float],
) -> float | None:
    """
    Berechnet den durchschnittlichen Einstandspreis in EUR.

    Verkäufe reduzieren Menge und Kostenbasis proportional (Average-Cost-Methode).
    Später kann hier FIFO ergänzt werden.
    """
    if not trades:
        return None

    remaining_qty = 0.0
    remaining_cost_eur = 0.0

    for trade in sorted(trades, key=lambda item: item["time"]):
        symbol = trade["symbol"]
        meta = symbol_meta_by_symbol.get(symbol)
        if meta is None:
            continue

        price_eur = _trade_price_in_eur(trade, meta, tickers)
        if price_eur is None:
            continue

        qty = float(trade["qty"])
        if trade["isBuyer"]:
            remaining_qty += qty
            remaining_cost_eur += qty * price_eur
            continue

        if remaining_qty <= 0:
            continue

        sell_qty = min(qty, remaining_qty)
        avg_cost = remaining_cost_eur / remaining_qty
        remaining_cost_eur -= sell_qty * avg_cost
        remaining_qty -= sell_qty

    if remaining_qty <= 0 or remaining_cost_eur <= 0:
        return None
    return remaining_cost_eur / remaining_qty


def _collect_trades(
    client: Client,
    asset: str,
    exchange_info: dict,
    symbol_meta_by_symbol: dict[str, dict],
) -> list[dict]:
    """Sammelt Trades über alle relevanten Spot-Paare eines Coins."""
    all_trades: list[dict] = []
    for symbol in _spot_symbols_for_asset(exchange_info, asset):
        try:
            all_trades.extend(_fetch_all_trades(client, symbol))
        except BinanceAPIException:
            continue
    return all_trades


def create_authenticated_client() -> ClientResult:
    """Erstellt einen Binance-Client und lädt Basis-Marktdaten."""
    api_key, api_secret = _load_credentials()
    if not api_key or not api_secret:
        return ClientResult(
            ok=False,
            message=(
                "API-Keys fehlen oder sind noch Platzhalter. "
                f"Bitte BINANCE_API_KEY und BINANCE_API_SECRET in {PROJECT_ROOT / '.env'} eintragen."
            ),
        )

    try:
        client = Client(api_key, api_secret)
        exchange_info = client.get_exchange_info()
        tickers = _build_ticker_map(client)
    except BinanceAPIException as exc:
        return ClientResult(
            ok=False,
            message=f"Binance hat die Anfrage abgelehnt: {exc.message} (Code {exc.code})",
        )
    except Exception as exc:
        return ClientResult(
            ok=False,
            message=f"Verbindung zu Binance fehlgeschlagen: {type(exc).__name__}: {exc}",
        )

    return ClientResult(
        ok=True,
        message="Binance-Verbindung steht.",
        client=client,
        exchange_info=exchange_info,
        tickers=tickers,
    )


def _load_seen_trade_ids() -> set[int]:
    """Liest bereits gespeicherte Binance-Trade-IDs für Käufe."""
    return _load_meta_trade_ids(KAUF_META)


def _save_seen_trade_ids(trade_ids: set[int]) -> None:
    """Speichert bekannte Kauf-Trade-IDs."""
    _save_meta_trade_ids(KAUF_META, trade_ids)


def _ensure_trade_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    string_cols = {
        "coin",
        "datum",
        "commission_asset",
        "gebuehr_quelle",
        "preis_quelle",
    }
    for column in columns:
        if column not in out.columns:
            out[column] = "" if column in string_cols else float("nan")
    return out


def load_purchases_csv(*, with_deposits: bool = False) -> pd.DataFrame:
    """Lädt Käufe aus data/kaeufe.csv; optional inkl. Krypto-Deposits (FIFO/Steuer)."""
    if not KAUF_CSV.exists():
        base = pd.DataFrame(columns=KAUF_COLUMNS)
    else:
        base = _ensure_trade_columns(pd.read_csv(KAUF_CSV), KAUF_COLUMNS)
    if not with_deposits:
        return base
    from deposit_trades import merge_purchases_with_deposits

    return merge_purchases_with_deposits(base)


def load_sells_csv(*, with_withdrawals: bool = False) -> pd.DataFrame:
    """Lädt Verkäufe aus data/verkaeufe.csv; optional inkl. Krypto-Withdrawals (FIFO)."""
    if not VERKAUF_CSV.exists():
        base = pd.DataFrame(columns=VERKAUF_COLUMNS)
    else:
        base = _ensure_trade_columns(pd.read_csv(VERKAUF_CSV), VERKAUF_COLUMNS)
    if not with_withdrawals:
        return base
    from deposit_trades import merge_sells_with_withdrawals

    return merge_sells_with_withdrawals(base)


def _csv_cell_missing(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    return False


def trade_data_status() -> TradeDataStatus:
    """Prüft, ob in kaeufe.csv / verkaeufe.csv noch Preise oder Gebühren fehlen."""
    purchases = load_purchases_csv()
    sells = load_sells_csv()

    buys_missing_fee = 0
    if not purchases.empty:
        for _, row in purchases.iterrows():
            if _csv_cell_missing(row.get("gebuehr_eur")):
                buys_missing_fee += 1

    sells_missing_price = 0
    if not sells.empty:
        for _, row in sells.iterrows():
            if _csv_cell_missing(row.get("verkaufspreis_eur")):
                sells_missing_price += 1

    return TradeDataStatus(
        buys_total=len(purchases),
        sells_total=len(sells),
        buys_missing_fee=buys_missing_fee,
        sells_missing_price=sells_missing_price,
    )


def _sells_by_coin_from_csv(sells_df: pd.DataFrame) -> dict[str, list[dict[str, object]]]:
    """Wandelt die Verkaufs-CSV in das Format für die FIFO-Berechnung um."""
    sells_by_coin: dict[str, list[dict[str, object]]] = {}
    if sells_df.empty:
        return sells_by_coin

    for coin, group in sells_df.groupby("coin"):
        entries: list[dict[str, object]] = []
        for _, row in group.iterrows():
            entries.append(
                {
                    "menge": float(row["menge"]),
                    "zeit": datetime.fromisoformat(str(row["datum"])),
                }
            )
        sells_by_coin[str(coin)] = entries
    return sells_by_coin


def _parse_csv_zeit(value: object) -> datetime:
    zeit = datetime.fromisoformat(str(value))
    if zeit.tzinfo is None:
        return zeit.replace(tzinfo=timezone.utc)
    return zeit


def average_entry_from_local(coin: str, purchases: pd.DataFrame, sells: pd.DataFrame) -> float | None:
    """Einstand offener Lots per FIFO (Alias für fifo_avg_entry_from_local)."""
    return fifo_avg_entry_from_local(coin, purchases, sells)


def _row_needs_backfill(row: pd.Series, columns: list[str]) -> bool:
    for col in columns:
        if col not in row.index:
            return True
        value = row[col]
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return True
    return False


def _match_api_trade_index(
    row: pd.Series,
    candidates: list[dict[str, object]],
    *,
    time_tolerance_sec: float = 2.0,
) -> int | None:
    raw_trade_id = row.get("trade_id")
    if raw_trade_id is not None and not (isinstance(raw_trade_id, float) and pd.isna(raw_trade_id)):
        try:
            row_trade_id = int(raw_trade_id)
            for index, trade in enumerate(candidates):
                if int(trade.get("trade_id", -1)) == row_trade_id:
                    return index
        except (TypeError, ValueError):
            pass

    row_time = _parse_csv_zeit(row["datum"])
    row_qty = float(row["menge"])
    for index, trade in enumerate(candidates):
        if abs(float(trade["menge"]) - row_qty) > 1e-6:
            continue
        trade_time = trade.get("zeit")
        if not isinstance(trade_time, datetime):
            trade_time = _parse_csv_zeit(trade["datum"])
        if abs((row_time - trade_time).total_seconds()) <= time_tolerance_sec:
            return index
    return None


def _backfill_dataframe_from_trades(
    df: pd.DataFrame,
    api_trades: list[dict[str, object]],
    fill_columns: dict[str, str],
    *,
    always_refresh: set[str] | None = None,
) -> tuple[pd.DataFrame, int]:
    """Ergänzt fehlende Spalten in der CSV anhand passender Binance-Trades."""
    if df.empty or not api_trades:
        return df, 0

    out = df.copy()
    for column in fill_columns:
        if column not in out.columns:
            string_cols = {"commission_asset", "gebuehr_quelle", "preis_quelle"}
            out[column] = "" if column in string_cols else float("nan")

    pool = list(api_trades)
    updated = 0
    refresh = always_refresh or set()
    for index, row in out.iterrows():
        missing = _row_needs_backfill(row, list(fill_columns.keys()))
        if not missing and not refresh:
            continue
        match_index = _match_api_trade_index(row, pool)
        if match_index is None:
            continue
        trade = pool.pop(match_index)
        changed = False
        for csv_col, api_col in fill_columns.items():
            if csv_col in refresh or _row_needs_backfill(row, [csv_col]):
                out.at[index, csv_col] = trade[api_col]
                changed = True
        if changed:
            updated += 1

    return out, updated


def _coins_for_trade_sync(client: Client) -> list[str]:
    """Coins aus Bestand, Käufen und Verkäufen in den CSV-Dateien."""
    account = client.get_account()
    coins = {
        entry["asset"]
        for entry in account["balances"]
        if float(entry["free"]) + float(entry["locked"]) > 0
    }

    if KAUF_CSV.exists():
        existing_buys = load_purchases_csv()
        if not existing_buys.empty:
            coins.update(existing_buys["coin"].astype(str).tolist())

    if VERKAUF_CSV.exists():
        existing_sells = load_sells_csv()
        if not existing_sells.empty:
            coins.update(existing_sells["coin"].astype(str).tolist())

    return sorted(coins)


def backfill_trade_csv_from_binance() -> tuple[int, int, str]:
    """
    Ergänzt fehlende Preise/Gebühren in bestehenden CSV-Zeilen von Binance.

    Returns:
        (kaeufe_aktualisiert, verkaeufe_aktualisiert, status_message)
    """
    outcome = _run_full_trade_sync(add_new_rows=False, backfill_existing=True)
    if not outcome.ok:
        return 0, 0, outcome.message
    return outcome.buys_backfilled, outcome.sells_backfilled, outcome.message


_FEE_COLUMNS = {
    "trade_id": "trade_id",
    "commission": "commission",
    "commission_asset": "commission_asset",
    "gebuehr_eur": "gebuehr_eur",
    "gebuehr_kurs_eur": "gebuehr_kurs_eur",
    "gebuehr_geschaetzt": "gebuehr_geschaetzt",
    "gebuehr_quelle": "gebuehr_quelle",
}
_BUY_PRICE_COLUMNS = {
    "kaufpreis_eur": "kaufpreis_eur",
    "preis_geschaetzt": "preis_geschaetzt",
    "preis_quelle": "preis_quelle",
}
_SELL_PRICE_COLUMNS = {
    "verkaufspreis_eur": "verkaufspreis_eur",
    "preis_geschaetzt": "preis_geschaetzt",
    "preis_quelle": "preis_quelle",
}
_FEE_REFRESH = set(_FEE_COLUMNS.keys())
_BUY_REFRESH = _FEE_REFRESH | set(_BUY_PRICE_COLUMNS.keys())
_SELL_REFRESH = _FEE_REFRESH | set(_SELL_PRICE_COLUMNS.keys())


def _trade_record_from_api(
    trade: dict,
    coin: str,
    meta: dict,
    tickers: dict[str, float],
    *,
    is_buy: bool,
    client: Client,
    fee_cache: HistoricalRateCache,
    valid_symbols: set[str],
) -> dict[str, object] | None:
    price_eur, preis_geschaetzt, preis_quelle = trade_price_eur_at_time(
        trade, meta, client, tickers, fee_cache, valid_symbols
    )
    if is_buy and price_eur is None:
        return None

    fee_eur, fee_estimated, fee_source, commission, commission_asset, fee_kurs = (
        trade_fee_eur_at_time(trade, client, tickers, fee_cache, valid_symbols)
    )
    traded_at = datetime.fromtimestamp(trade["time"] / 1000, tz=timezone.utc)
    record: dict[str, object] = {
        "trade_id": int(trade["id"]),
        "coin": coin,
        "datum": traded_at.isoformat(),
        "zeit": traded_at,
        "menge": float(trade["qty"]),
        "commission": commission,
        "commission_asset": commission_asset,
        "gebuehr_eur": fee_eur if fee_eur is not None else float("nan"),
        "gebuehr_kurs_eur": fee_kurs if fee_kurs is not None else float("nan"),
        "gebuehr_geschaetzt": 1 if fee_estimated else 0,
        "gebuehr_quelle": fee_source,
        "preis_geschaetzt": 1 if preis_geschaetzt else 0,
        "preis_quelle": preis_quelle,
    }
    if is_buy:
        record["kaufpreis_eur"] = price_eur
        return record
    record["verkaufspreis_eur"] = price_eur if price_eur is not None else float("nan")
    return record


def _run_full_trade_sync(
    *,
    add_new_rows: bool = True,
    backfill_existing: bool = True,
) -> TradeSyncOutcome:
    """Ein Binance-Durchlauf: neue Trades speichern und/oder CSV-Zeilen ergänzen."""
    client_result = create_authenticated_client()
    if not client_result.ok:
        return TradeSyncOutcome(ok=False, message=client_result.message)

    assert client_result.client is not None
    client = client_result.client
    exchange_info = client_result.exchange_info or client.get_exchange_info()
    tickers = client_result.tickers or _build_ticker_map(client)
    symbol_meta_by_symbol = {entry["symbol"]: entry for entry in exchange_info["symbols"]}
    valid_symbols = valid_symbols_from_exchange(exchange_info)
    fee_cache = HistoricalRateCache()

    buy_seen = _load_seen_trade_ids()
    sell_seen = _load_meta_trade_ids(VERKAUF_META)
    new_buy_rows: list[dict[str, object]] = []
    new_sell_rows: list[dict[str, object]] = []
    all_buy_records: list[dict[str, object]] = []
    all_sell_records: list[dict[str, object]] = []

    for coin in _coins_for_trade_sync(client):
        for trade in _collect_trades(client, coin, exchange_info, symbol_meta_by_symbol):
            meta = symbol_meta_by_symbol.get(trade["symbol"])
            if meta is None:
                continue

            trade_id = int(trade["id"])
            if trade.get("isBuyer"):
                record = _trade_record_from_api(
                    trade,
                    coin,
                    meta,
                    tickers,
                    is_buy=True,
                    client=client,
                    fee_cache=fee_cache,
                    valid_symbols=valid_symbols,
                )
                if record is None:
                    continue
                all_buy_records.append(record)
                if add_new_rows and trade_id not in buy_seen:
                    buy_seen.add(trade_id)
                    new_buy_rows.append({col: record[col] for col in KAUF_COLUMNS})
            else:
                record = _trade_record_from_api(
                    trade,
                    coin,
                    meta,
                    tickers,
                    is_buy=False,
                    client=client,
                    fee_cache=fee_cache,
                    valid_symbols=valid_symbols,
                )
                all_sell_records.append(record)
                if add_new_rows and trade_id not in sell_seen:
                    sell_seen.add(trade_id)
                    new_sell_rows.append({col: record[col] for col in VERKAUF_COLUMNS})

    _DEPOT_DIR.mkdir(parents=True, exist_ok=True)

    if add_new_rows and new_buy_rows:
        new_df = pd.DataFrame(new_buy_rows, columns=KAUF_COLUMNS)
        if KAUF_CSV.exists():
            combined = pd.concat([load_purchases_csv(), new_df], ignore_index=True)
        else:
            combined = new_df
        combined.sort_values(["coin", "datum"], inplace=True)
        combined.to_csv(KAUF_CSV, index=False)
        _save_seen_trade_ids(buy_seen)

    if add_new_rows and new_sell_rows:
        new_df = pd.DataFrame(new_sell_rows, columns=VERKAUF_COLUMNS)
        if VERKAUF_CSV.exists():
            combined = pd.concat([load_sells_csv(), new_df], ignore_index=True)
        else:
            combined = new_df
        combined.sort_values(["coin", "datum"], inplace=True)
        combined.to_csv(VERKAUF_CSV, index=False)
        _save_meta_trade_ids(VERKAUF_META, sell_seen)

    buys_backfilled = 0
    sells_backfilled = 0

    if backfill_existing and KAUF_CSV.exists():
        purchases = load_purchases_csv()
        buy_fill = {**_BUY_PRICE_COLUMNS, **_FEE_COLUMNS}
        filled, buys_backfilled = _backfill_dataframe_from_trades(
            purchases,
            all_buy_records,
            buy_fill,
            always_refresh=_BUY_REFRESH,
        )
        if buys_backfilled:
            filled.sort_values(["coin", "datum"], inplace=True)
            filled.to_csv(KAUF_CSV, index=False)

    if backfill_existing and VERKAUF_CSV.exists():
        sells = load_sells_csv()
        sell_fill = {**_SELL_PRICE_COLUMNS, **_FEE_COLUMNS}
        filled, sells_backfilled = _backfill_dataframe_from_trades(
            sells,
            all_sell_records,
            sell_fill,
            always_refresh=_SELL_REFRESH,
        )
        if sells_backfilled:
            filled.sort_values(["coin", "datum"], inplace=True)
            filled.to_csv(VERKAUF_CSV, index=False)

    fee_cache.save()

    messages: list[str] = []
    if add_new_rows:
        buy_total = len(load_purchases_csv()) if KAUF_CSV.exists() else 0
        sell_total = len(load_sells_csv()) if VERKAUF_CSV.exists() else 0
        if new_buy_rows:
            messages.append(f"{len(new_buy_rows)} neue Käufe (insgesamt {buy_total}).")
        else:
            messages.append(f"Keine neuen Käufe ({buy_total} Einträge).")
        if new_sell_rows:
            messages.append(f"{len(new_sell_rows)} neue Verkäufe (insgesamt {sell_total}).")
        else:
            messages.append(f"Keine neuen Verkäufe ({sell_total} Einträge).")

    if backfill_existing:
        if buys_backfilled or sells_backfilled:
            messages.append(
                f"{buys_backfilled} Kauf-Zeile/n und {sells_backfilled} Verkauf-Zeile/n "
                "mit Preis/Gebühr ergänzt."
            )
        elif not add_new_rows:
            messages.append("Keine fehlenden Preise/Gebühren in den CSV-Dateien.")

    if not messages:
        messages.append("Trade-Historie synchronisiert.")

    return TradeSyncOutcome(
        ok=True,
        message=" ".join(messages),
        new_buys=len(new_buy_rows),
        new_sells=len(new_sell_rows),
        buys_backfilled=buys_backfilled,
        sells_backfilled=sells_backfilled,
    )


def sync_trade_history_from_binance() -> PurchaseSyncResult:
    """Synchronisiert Käufe und Verkäufe von Binance in die lokalen CSV-Dateien."""
    outcome = _run_full_trade_sync(add_new_rows=True, backfill_existing=True)
    if not outcome.ok:
        return PurchaseSyncResult(ok=False, message=outcome.message)

    buy_total = len(load_purchases_csv()) if KAUF_CSV.exists() else 0
    sell_total = len(load_sells_csv()) if VERKAUF_CSV.exists() else 0
    return PurchaseSyncResult(
        ok=True,
        message=outcome.message,
        new_count=outcome.new_buys + outcome.new_sells,
        total_count=buy_total + sell_total,
    )


def _load_meta_trade_ids(meta_path: Path) -> set[int]:
    if not meta_path.exists():
        return set()
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        return {int(item) for item in payload.get("trade_ids", [])}
    except (json.JSONDecodeError, TypeError, ValueError):
        return set()


def _save_meta_trade_ids(meta_path: Path, trade_ids: set[int]) -> None:
    meta_path.write_text(
        json.dumps({"trade_ids": sorted(trade_ids)}, indent=2),
        encoding="utf-8",
    )


def collect_sell_trades(
    client: Client,
    coins: list[str],
) -> dict[str, list[dict[str, object]]]:
    """Sammelt Verkaufs-Trades je Coin für die FIFO-Berechnung (Binance-API)."""
    exchange_info = client.get_exchange_info()
    symbol_meta_by_symbol = {
        entry["symbol"]: entry for entry in exchange_info["symbols"]
    }

    sells_by_coin: dict[str, list[dict[str, object]]] = {}
    for coin in coins:
        coin_sells: list[dict[str, object]] = []
        for trade in _collect_trades(client, coin, exchange_info, symbol_meta_by_symbol):
            if trade.get("isBuyer"):
                continue
            coin_sells.append(
                {
                    "menge": float(trade["qty"]),
                    "zeit": datetime.fromtimestamp(trade["time"] / 1000, tz=timezone.utc),
                }
            )
        sells_by_coin[coin] = coin_sells

    return sells_by_coin


def _account_balances_from_client(client: Client) -> dict[str, float]:
    """Aktuelle Spot-Bestände je Coin (free + locked)."""
    account = client.get_account()
    return {
        entry["asset"]: float(entry["free"]) + float(entry["locked"])
        for entry in account["balances"]
        if float(entry["free"]) + float(entry["locked"]) > 0
    }


def _fetch_portfolio_from_binance(use_local_history: bool = True) -> PortfolioResult:
    """Holt Bestände und Kurse live von Binance (ohne Tages-Cache)."""
    client_result = create_authenticated_client()
    if not client_result.ok:
        return PortfolioResult(ok=False, message=client_result.message)

    assert client_result.client is not None
    assert client_result.exchange_info is not None
    assert client_result.tickers is not None

    tickers = client_result.tickers
    account = client_result.client.get_account()
    purchases = load_purchases_csv(with_deposits=True)
    sells = load_sells_csv(with_withdrawals=True)

    symbol_meta_by_symbol = {
        entry["symbol"]: entry for entry in client_result.exchange_info["symbols"]
    }

    positions: list[Position] = []

    for balance in account["balances"]:
        quantity = float(balance["free"]) + float(balance["locked"])
        if quantity <= 0:
            continue

        coin = balance["asset"]
        if use_local_history:
            avg_entry = fifo_avg_entry_from_local(coin, purchases, sells)
        else:
            trades = _collect_trades(
                client_result.client,
                coin,
                client_result.exchange_info,
                symbol_meta_by_symbol,
            )
            avg_entry = _average_entry_price_eur(trades, symbol_meta_by_symbol, tickers)

        positions.append(
            _position_from_balance(coin, quantity, tickers, avg_entry=avg_entry)
        )

    positions = _sort_positions(positions)

    source = "lokale CSV" if use_local_history else "Binance-API"
    return PortfolioResult(
        ok=True,
        message=f"Depot geladen ({source}): {len(positions)} Coins mit Bestand.",
        positions=positions,
        total_value_eur=_portfolio_total_eur(positions),
    )


def load_portfolio(
    use_local_history: bool = True,
    price_mode: str = "live_daily",
    force_live: bool = False,
) -> tuple[PortfolioResult, str, bool, str | None]:
    """
    Lädt das Depot mit Tages-Cache für Kurse.

    price_mode:
      - frozen       → immer letzter Snapshot, kein Auto-Reload (Entwicklung)
      - live_daily   → max. 1× pro Tag live von Binance, danach Tages-Cache

    force_live=True → immer live von Binance holen und Snapshot aktualisieren.
    """
    from daily_cache import load_latest_snapshot, load_today_snapshot, save_today_snapshot

    frozen_modes = {"frozen", "cache_only"}

    if force_live:
        result = _fetch_portfolio_from_binance(use_local_history=use_local_history)
        if not result.ok:
            return result, "", False, None
        save_today_snapshot(result)
        display = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")
        return result, display, False, date.today().isoformat()

    if price_mode in frozen_modes:
        latest = load_latest_snapshot()
        if latest is not None:
            result, loaded_at, day_key = latest
            return (
                PortfolioResult(
                    ok=True,
                    message=f"{result.message} (fester Snapshot – kein Auto-Reload)",
                    positions=result.positions,
                    total_value_eur=result.total_value_eur,
                ),
                loaded_at,
                True,
                day_key,
            )
        return (
            PortfolioResult(
                ok=False,
                message=(
                    "Noch kein gespeicherter Snapshot. "
                    "Klicke links auf „Kurse & Bestände jetzt laden“ (einmalig)."
                ),
            ),
            "",
            False,
            None,
        )

    cached = load_today_snapshot()
    if cached is not None:
        result, loaded_at = cached
        refreshed, live_at = _refresh_portfolio_live_prices(
            result,
            use_local_history=use_local_history,
        )
        if live_at is not None:
            return refreshed, live_at, False, date.today().isoformat()
        return result, loaded_at, True, date.today().isoformat()

    result = _fetch_portfolio_from_binance(use_local_history=use_local_history)
    if not result.ok:
        return result, "", False, None

    save_today_snapshot(result)
    display = datetime.now(timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M:%S")
    return result, display, False, date.today().isoformat()


