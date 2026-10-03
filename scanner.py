"""
Nifty 500 R2 Scanner - V1

Manual scanner. No database, no scheduler, no Streamlit.

Logic:
    x  = (H - L) / 4
    R1 = C + x
    R2 = C + 2*x
    R3 = C + 3*x
    S1 = C - x
    S2 = C - 2*x
    S3 = C - 3*x

Signal:
    Current Price > R2

Only matching instruments are displayed and exported.

Data:
    - Nifty 500 constituent list: Nifty Indices
    - Market data: Upstox V3 OHLC Quotes API

Important:
    Upstox's V3 OHLC endpoint accepts multiple instrument keys.
    We therefore fetch the daily quote for up to 500 instruments
    per API request instead of making one historical request per stock.

For interval=1d, the API response contains:
    - last_price
    - prev_ohlc
    - live_ohlc

The scanner uses prev_ohlc as the previous-session OHLC and
last_price as the current price.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata")

UPSTOX_BASE_URL = "https://api.upstox.com/v3"

NIFTY_500_CSV_URL = (
    "https://www.niftyindices.com/IndexConstituent/"
    "ind_nifty500list.csv"
)

UPSTOX_INSTRUMENT_MASTER_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/"
    "complete.json.gz"
)

OUTPUT_DIR = Path("output")
REQUEST_TIMEOUT = 30
MAX_INSTRUMENTS_PER_REQUEST = 500

# Set to True to save the complete internal scan data.
# False means only R2 matches are saved.
SAVE_FULL_SCAN = False


# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------

def get_access_token() -> str:
    token = os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

    if not token:
        raise RuntimeError(
            "\nUPSTOX_ACCESS_TOKEN is not set.\n\n"
            "PowerShell:\n"
            "  $env:UPSTOX_ACCESS_TOKEN='YOUR_ACCESS_TOKEN'\n\n"
            "Command Prompt:\n"
            "  set UPSTOX_ACCESS_TOKEN=YOUR_ACCESS_TOKEN\n\n"
            "Linux/macOS:\n"
            "  export UPSTOX_ACCESS_TOKEN='YOUR_ACCESS_TOKEN'\n"
        )

    return token


def make_session(token: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Nifty500-R2-Scanner/1.0",
        }
    )
    return session


# ---------------------------------------------------------------------
# Nifty 500 universe
# ---------------------------------------------------------------------

def load_nifty500_symbols() -> pd.DataFrame:
    """
    Download the current Nifty 500 constituent CSV.

    Nifty Indices publishes the current Index Constituent file.
    The exact constituent count can change; we use whatever the
    official file returns rather than assuming exactly 500 rows.
    """
    print("1/5  Loading current Nifty 500 constituents...")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/154 Safari/537.36"
        ),
        "Accept": "*/*",
    }

    response = requests.get(
        NIFTY_500_CSV_URL,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    # Nifty's CSV can be UTF-8/Latin-1 depending on publication.
    try:
        text = response.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = response.content.decode("latin-1")

    df = pd.read_csv(io.StringIO(text))

    # Locate Symbol column robustly.
    symbol_col = None
    for col in df.columns:
        normalized = re.sub(r"[^a-z0-9]", "", str(col).lower())
        if normalized == "symbol":
            symbol_col = col
            break

    if symbol_col is None:
        raise RuntimeError(
            f"Could not find 'Symbol' column in Nifty file. "
            f"Columns received: {list(df.columns)}"
        )

    df["symbol"] = (
        df[symbol_col]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df = df[df["symbol"].notna()]
    df = df[df["symbol"] != ""]
    df = df[df["symbol"] != "NAN"]
    df = df.drop_duplicates("symbol").reset_index(drop=True)

    print(f"     Loaded {len(df)} Nifty constituents.")
    return df[["symbol"]]


# ---------------------------------------------------------------------
# Upstox instrument master
# ---------------------------------------------------------------------

def load_upstox_equity_instruments() -> dict:
    print("2/5  Loading Upstox NSE equity instrument master...")

    response = requests.get(
        UPSTOX_INSTRUMENT_MASTER_URL,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    raw = response.content

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
        instrument_key = item.get("instrument_key")

        if not symbol or not instrument_key:
            continue

        instruments[symbol] = {
            "instrument_key": instrument_key,
            "name": item.get("name", ""),
            "isin": item.get("isin", ""),
        }

    print(f"     Loaded {len(instruments)} NSE equity instruments.")
    return instruments


def build_universe(
    nifty_df: pd.DataFrame,
    instruments: dict,
) -> pd.DataFrame:

    rows = []
    missing = []

    for symbol in nifty_df["symbol"]:
        item = instruments.get(symbol)

        if item is None:
            missing.append(symbol)
            continue

        rows.append(
            {
                "symbol": symbol,
                "instrument_key": item["instrument_key"],
                "name": item["name"],
                "isin": item["isin"],
            }
        )

    universe = pd.DataFrame(rows)

    if missing:
        print(
            f"     Warning: {len(missing)} Nifty symbols "
            f"were not found in Upstox NSE_EQ."
        )
        if len(missing) <= 30:
            print("     Missing:", ", ".join(missing))

    print(f"     Mapped to Upstox: {len(universe)} stocks.")

    if universe.empty:
        raise RuntimeError("No Nifty constituents could be mapped to Upstox.")

    return universe


# ---------------------------------------------------------------------
# Optimized market data
# ---------------------------------------------------------------------

def fetch_daily_quotes(
    session: requests.Session,
    instrument_keys: list[str],
) -> dict:
    """
    Fetch daily OHLC + current LTP for multiple instruments.

    Upstox V3 accepts a comma-separated list of instrument keys,
    with a documented maximum of 500 instruments per request.

    For >500 instruments, we automatically split into batches.
    """
    quotes = {}

    total = len(instrument_keys)

    for start in range(0, total, MAX_INSTRUMENTS_PER_REQUEST):
        batch = instrument_keys[
            start:start + MAX_INSTRUMENTS_PER_REQUEST
        ]

        print(
            f"     Market-data request "
            f"{start + 1}-{start + len(batch)} of {total}..."
        )

        params = {
            "instrument_key": ",".join(batch),
            "interval": "1d",
        }

        response = session.get(
            f"{UPSTOX_BASE_URL}/market-quote/ohlc",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:
            raise RuntimeError(
                "Upstox OHLC request failed.\n"
                f"HTTP {response.status_code}\n"
                f"{response.text[:1000]}"
            )

        payload = response.json()

        if payload.get("status") != "success":
            raise RuntimeError(
                f"Unexpected Upstox response: {payload}"
            )

        quotes.update(payload.get("data", {}))

    return quotes


# ---------------------------------------------------------------------
# R/S calculations
# ---------------------------------------------------------------------

def calculate_levels(
    high: float,
    low: float,
    close: float,
) -> dict:

    x = (high - low) / 4.0

    return {
        "x": x,
        "r1": close + x,
        "r2": close + (2.0 * x),
        "r3": close + (3.0 * x),
        "s1": close - x,
        "s2": close - (2.0 * x),
        "s3": close - (3.0 * x),
    }


def build_scan_dataframe(
    universe: pd.DataFrame,
    quotes: dict,
) -> pd.DataFrame:

    rows = []

    # Upstox returns data keys in the form NSE_EQ:SYMBOL.
    # We primarily use instrument_token from the payload to
    # match back to the universe's instrument_key.
    by_instrument_key = {}

    for key, quote in quotes.items():
        instrument_token = quote.get("instrument_token")
        if instrument_token:
            by_instrument_key[instrument_token] = quote

    for row in universe.itertuples(index=False):

        quote = by_instrument_key.get(row.instrument_key)

        if quote is None:
            # Some responses can use exchange:symbol as the key.
            # Fall back to constructing that key.
            quote = quotes.get(f"NSE_EQ:{row.symbol}")

        if quote is None:
            continue

        last_price = quote.get("last_price")
        prev_ohlc = quote.get("prev_ohlc")

        if last_price is None or not prev_ohlc:
            continue

        try:
            current_price = float(last_price)
            open_price = float(prev_ohlc["open"])
            high = float(prev_ohlc["high"])
            low = float(prev_ohlc["low"])
            close = float(prev_ohlc["close"])
        except (KeyError, TypeError, ValueError):
            continue

        if high <= 0 or low <= 0 or close <= 0:
            continue

        levels = calculate_levels(high, low, close)

        r2 = levels["r2"]

        # THE ONLY SIGNAL FILTER.
        if current_price <= r2:
            continue

        percent_above_r2 = (
            (current_price - r2) / r2
        ) * 100.0

        rows.append(
            {
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
                "Current Price": current_price,
                "% Above R2": percent_above_r2,
            }
        )

    result = pd.DataFrame(rows)

    if not result.empty:
        result = result.sort_values(
            "% Above R2",
            ascending=False,
        ).reset_index(drop=True)

    return result


# ---------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------

def format_for_display(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    output = df.copy()

    money_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "R1",
        "R2",
        "R3",
        "S1",
        "S2",
        "S3",
        "Current Price",
    ]

    for col in money_columns:
        output[col] = output[col].map(
            lambda x: f"{x:.2f}"
        )

    output["% Above R2"] = output["% Above R2"].map(
        lambda x: f"{x:.2f}%"
    )

    return output


def save_results(
    result: pd.DataFrame,
    scan_time: datetime,
) -> Path | None:

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = scan_time.strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"r2_scanner_{timestamp}.csv"

    result.to_csv(path, index=False)

    return path


def print_results(
    result: pd.DataFrame,
    scan_time: datetime,
    universe_count: int,
    quote_count: int,
) -> None:

    print("\n" + "=" * 150)
    print("NIFTY 500 R2 SCANNER")
    print("=" * 150)
    print(
        f"Scan time:       "
        f"{scan_time.strftime('%Y-%m-%d %H:%M:%S')} IST"
    )
    print(f"Universe:        {universe_count}")
    print(f"Quotes received: {quote_count}")
    print(f"Stocks > R2:     {len(result)}")
    print("=" * 150)

    if result.empty:
        print("\nNo Nifty 500 stocks are currently above R2.\n")
        return

    display_columns = [
        "Symbol",
        "Close",
        "R1",
        "R2",
        "R3",
        "S1",
        "S2",
        "S3",
        "Current Price",
        "% Above R2",
    ]

    print(
        format_for_display(
            result[display_columns]
        ).to_string(index=False)
    )

    print("=" * 150)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    started = time.perf_counter()
    scan_time = datetime.now(IST)

    print("\n" + "=" * 80)
    print("NIFTY 500 R2 SCANNER - V1")
    print("=" * 80)
    print(
        f"Started: {scan_time.strftime('%Y-%m-%d %H:%M:%S')} IST"
    )
    print()

    try:
        token = get_access_token()
        session = make_session(token)

        # 1. Universe
        nifty = load_nifty500_symbols()

        # 2. Upstox instrument mapping
        instruments = load_upstox_equity_instruments()
        universe = build_universe(nifty, instruments)

        # 3. ONE batched market-data operation (or two if universe > 500)
        print(
            "3/5  Fetching previous-session OHLC + current LTP..."
        )
        quotes = fetch_daily_quotes(
            session,
            universe["instrument_key"].tolist(),
        )

        print(f"     Quotes received: {len(quotes)}")

        # 4. Calculate and filter
        print("4/5  Calculating R/S levels and filtering LTP > R2...")
        result = build_scan_dataframe(
            universe,
            quotes,
        )

        # 5. Output
        print("5/5  Displaying results...")
        print_results(
            result,
            scan_time,
            len(universe),
            len(quotes),
        )

        csv_path = save_results(
            result,
            scan_time,
        )

        print(
            f"\nCSV saved: {csv_path.resolve()}"
        )

        elapsed = time.perf_counter() - started

        print(
            f"Total runtime: {elapsed:.2f} seconds"
        )
        print("=" * 80)

        return 0

    except requests.RequestException as exc:
        print(f"\nNetwork error: {exc}", file=sys.stderr)
        return 1

    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
