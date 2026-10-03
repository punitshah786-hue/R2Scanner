# Nifty 500 R2 Scanner — V1

A lightweight Python scanner for the Nifty 500 using the Upstox API.

## Strategy

For every Nifty 500 stock, using the previous trading session's OHLC:

```text
x  = (H - L) / 4

R1 = C + x
R2 = C + 2x
R3 = C + 3x

S1 = C - x
S2 = C - 2x
S3 = C - 3x
```

The scanner displays **only** instruments where:

```text
Current Price > R2
```

It also displays:

- Previous-day OHLC
- R1, R2, R3
- S1, S2, S3
- Current Price
- % Above R2

Results are sorted by `% Above R2`, highest first.

## Architecture

```text
Nifty 500 official constituent list
            |
            v
     Upstox instrument map
            |
            v
   Upstox V3 OHLC Quotes API
            |
            +---- previous-session OHLC
            |
            +---- current LTP
            |
            v
       R/S calculation
            |
            v
       Current Price > R2
            |
            v
       Terminal + CSV
```

There is:

- No database
- No scheduler
- No Streamlit
- No email yet

Run it manually first and validate the scanner.

## Why the OHLC request is optimized

The scanner does **not** make one historical API request per stock.

It uses the Upstox V3 Market Quote OHLC endpoint with multiple instrument keys in a single request.

The API supports comma-separated instrument keys and a maximum of 500 instruments per request. If the Nifty universe contains more than 500 instruments, the code automatically creates additional batches.

For `interval=1d`, the scanner uses the response's `prev_ohlc` as the previous session OHLC and `last_price` as the current price.

This means the normal Nifty 500 scan needs only a very small number of market-data requests instead of ~500 individual requests.

## Requirements

- Python 3.10+
- Upstox account/API access
- Upstox access token
- Internet connection

## Installation

### Windows PowerShell

```powershell
git clone <YOUR_REPO_URL>
cd nifty500-r2-scanner

python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### Linux/macOS

```bash
git clone <YOUR_REPO_URL>
cd nifty500-r2-scanner

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

## Configure Upstox token

Do NOT put your token in `scanner.py`.

### Windows PowerShell

```powershell
$env:UPSTOX_ACCESS_TOKEN="YOUR_UPSTOX_ACCESS_TOKEN"
```

### Windows Command Prompt

```cmd
set UPSTOX_ACCESS_TOKEN=YOUR_UPSTOX_ACCESS_TOKEN
```

### Linux/macOS

```bash
export UPSTOX_ACCESS_TOKEN="YOUR_UPSTOX_ACCESS_TOKEN"
```

## Run

```bash
python scanner.py
```

## Output

Example:

```text
====================================================================================================
NIFTY 500 R2 SCANNER
====================================================================================================
Scan time:       2026-10-03 09:30:01 IST
Universe:        500
Quotes received: 500
Stocks > R2:     6
====================================================================================================
Symbol   Close    R1    R2    R3    S1    S2    S3    Current Price   % Above R2
HFCL     66.20   ...   ...   ...   ...   ...   ...      70.10           2.71%
...
====================================================================================================
```

A CSV is also created in:

```text
output/
```

## GitHub

The repository intentionally contains no credentials.

Do not commit:

- Upstox access tokens
- client secrets
- Gmail credentials
- `.env` files containing secrets

The included `.gitignore` protects common secret files.

## Important data note

The official Nifty Indices constituent list is downloaded at runtime, so the universe is not hard-coded.

## Next versions

Potential V2/V3 additions:

1. Gmail alert
2. Streamlit dashboard
3. Official NSE holiday calendar
4. Manual refresh button
5. R2 breakout ranking
6. Volume expansion
7. 20/50/200 EMA
8. Relative Strength
9. ATR
10. Market-regime filter
11. Historical signal tracking
12. Backtesting
