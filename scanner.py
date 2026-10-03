"""
Nifty 500 R2 Scanner engine.

No database, scheduler, or email.

Uses Upstox V3 multi-instrument OHLC:
- last_price = current price
- prev_ohlc = previous-session OHLC

Only Current Price > R2 is returned.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from typing import Callable, Optional

import pandas as pd
import requests

UPSTOX_BASE_URL = "https://api.upstox.com/v3"
NIFTY_500_CSV_URL = (
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
)
UPSTOX_INSTRUMENT_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/"
    "complete.json.gz"
)
REQUEST_TIMEOUT = 30
MAX_INSTRUMENTS_PER_REQUEST = 500

def _headers():
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/154 Safari/537.36"
        ),
        "Accept": "*/*",
    }

def load_nifty500_symbols():
    r = requests.get(
        NIFTY_500_CSV_URL,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    try:
        text = r.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = r.content.decode("latin-1")
    df = pd.read_csv(io.StringIO(text))

    symbol_col = None
    for col in df.columns:
        if re.sub(r"[^a-z0-9]", "", str(col).lower()) == "symbol":
            symbol_col = col
            break
    if symbol_col is None:
        raise RuntimeError(
            f"Could not find Symbol column. Columns: {list(df.columns)}"
        )

    df["symbol"] = (
        df[symbol_col].astype(str).str.strip().str.upper()
    )
    return (
        df.loc[
            df["symbol"].notna()
            & (df["symbol"] != "")
            & (df["symbol"] != "NAN"),
            ["symbol"],
        ]
        .drop_duplicates()
        .reset_index(drop=True)
    )

def load_upstox_equity_instruments():
    r = requests.get(
        UPSTOX_INSTRUMENT_MASTER_URL,
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    raw = r.content
    try:
        raw = gzip.decompress(raw)
    except gzip.BadGzipFile:
        pass

    data = json.loads(raw.decode("utf-8"))
    instruments = {}

    for item in data:
        if item.get("segment") != "NSE_EQ":
            continue
        if item.get("instrument_type") != "EQ":
            continue

        symbol = str(item.get("trading_symbol", "")).strip().upper()
        key = item.get("instrument_key")
        if not symbol or not key:
            continue

        instruments[symbol] = {
            "instrument_key": key,
            "name": item.get("name", ""),
            "isin": item.get("isin", ""),
        }

    return instruments

def build_universe(nifty, instruments):
    rows = []
    for symbol in nifty["symbol"]:
        item = instruments.get(symbol)
        if item:
            rows.append({
                "symbol": symbol,
                "instrument_key": item["instrument_key"],
                "name": item["name"],
                "isin": item["isin"],
            })

    universe = pd.DataFrame(rows)
    if universe.empty:
        raise RuntimeError("No Nifty 500 stocks mapped to Upstox.")
    return universe

def calculate_levels(high, low, close):
    x = (high - low) / 4.0
    return {
        "r1": close + x,
        "r2": close + 2.0 * x,
        "r3": close + 3.0 * x,
        "s1": close - x,
        "s2": close - 2.0 * x,
        "s3": close - 3.0 * x,
    }

def fetch_daily_quotes(
    session,
    instrument_keys,
    progress_callback: Optional[Callable] = None,
):
    quotes = {}
    total = len(instrument_keys)

    for start in range(0, total, MAX_INSTRUMENTS_PER_REQUEST):
        batch = instrument_keys[
            start:start + MAX_INSTRUMENTS_PER_REQUEST
        ]

        if progress_callback:
            percent = min(
                70,
                40 + int(((start + len(batch)) / total) * 30),
            )
            progress_callback(
                percent,
                f"Fetching market data: {start + 1}-"
                f"{start + len(batch)} of {total}..."
            )

        r = session.get(
            f"{UPSTOX_BASE_URL}/market-quote/ohlc",
            params={
                "instrument_key": ",".join(batch),
                "interval": "1d",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Upstox OHLC API failed: HTTP {r.status_code}\n"
                f"{r.text[:1000]}"
            )

        payload = r.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Unexpected Upstox response: {payload}")

        quotes.update(payload.get("data", {}))

    return quotes

def build_results(universe, quotes):
    by_key = {}
    for _, quote in quotes.items():
        token = quote.get("instrument_token")
        if token:
            by_key[token] = quote

    rows = []

    for row in universe.itertuples(index=False):
        quote = by_key.get(row.instrument_key)
        if quote is None:
            quote = quotes.get(f"NSE_EQ:{row.symbol}")
        if quote is None:
            continue

        last_price = quote.get("last_price")
        prev = quote.get("prev_ohlc")
        if last_price is None or not prev:
            continue

        try:
            current = float(last_price)
            open_price = float(prev["open"])
            high = float(prev["high"])
            low = float(prev["low"])
            close = float(prev["close"])
        except (KeyError, TypeError, ValueError):
            continue

        if min(current, open_price, high, low, close) <= 0:
            continue

        levels = calculate_levels(high, low, close)
        r2 = levels["r2"]

        # ONLY SIGNAL FILTER.
        if current <= r2:
            continue

        pct = ((current - r2) / r2) * 100.0

        rows.append({
            "Symbol": row.symbol,
            "Company": row.name,
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "R1": levels["r1"],
            "R2": levels["r2"],
            "R3": levels["r3"],
            "S1": levels["s1"],
            "S2": levels["s2"],
            "S3": levels["s3"],
            "Current Price": current,
            "% Above R2": pct,
        })

    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(
            "% Above R2",
            ascending=False,
        ).reset_index(drop=True)
    return result

def scan_nifty500(
    access_token: str,
    progress_callback: Optional[Callable] = None,
):
    if not access_token:
        raise ValueError("Upstox access token is empty.")

    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Nifty500-R2-Scanner/1.0",
    })

    if progress_callback:
        progress_callback(5, "Loading current Nifty 500...")
    nifty = load_nifty500_symbols()

    if progress_callback:
        progress_callback(20, "Loading Upstox instrument master...")
    instruments = load_upstox_equity_instruments()

    if progress_callback:
        progress_callback(30, "Mapping Nifty 500 to Upstox...")
    universe = build_universe(nifty, instruments)

    if progress_callback:
        progress_callback(
            40,
            f"Mapped {len(universe)} stocks. Fetching market data..."
        )

    quotes = fetch_daily_quotes(
        session,
        universe["instrument_key"].tolist(),
        progress_callback,
    )

    if progress_callback:
        progress_callback(
            75,
            "Calculating R/S levels and filtering Current Price > R2...",
        )

    results = build_results(universe, quotes)

    if progress_callback:
        progress_callback(
            95,
            f"Found {len(results)} stocks above R2.",
        )

    return {
        "results": results,
        "universe_count": len(universe),
        "quotes_received": len(quotes),
    }
