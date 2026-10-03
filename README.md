# Nifty 500 R2 Scanner — Streamlit V1

A lightweight Streamlit app that scans the current Nifty 500 using Upstox.

## Signal

Previous-session OHLC:

```text
x  = (H - L) / 4

R1 = C + x
R2 = C + 2x
R3 = C + 3x

S1 = C - x
S2 = C - 2x
S3 = C - 3x
```

The **only signal filter** is:

```text
Current Price > R2
```

The UI displays only matching stocks and sorts them by `% Above R2`.

## Included files

```text
app.py
scanner.py
requirements.txt
README.md
.gitignore
.streamlit/secrets.toml.example
```

## Local run

```bash
pip install -r requirements.txt
```

Set your Upstox token.

PowerShell:

```powershell
$env:UPSTOX_ACCESS_TOKEN="YOUR_UPSTOX_ACCESS_TOKEN"
```

Linux/macOS:

```bash
export UPSTOX_ACCESS_TOKEN="YOUR_UPSTOX_ACCESS_TOKEN"
```

Run:

```bash
streamlit run app.py
```

## Streamlit Community Cloud

1. Push this repository to GitHub.
2. Create a new Streamlit Community Cloud app.
3. Select this repository.
4. Set the main file to `app.py`.
5. Deploy.
6. Open App Settings → Secrets.
7. Add:

```toml
UPSTOX_ACCESS_TOKEN = "YOUR_UPSTOX_ACCESS_TOKEN"
```

8. Save and rerun the app.
9. Click **Run Scanner**.

Do not commit the real `.streamlit/secrets.toml`.

## Optimized market-data calls

The scanner uses the Upstox V3 multi-instrument OHLC endpoint rather than making one OHLC request per stock.

The normal Nifty 500 universe fits into one batch. If the universe exceeds the configured batch size, the code automatically splits it.

The scanner uses `prev_ohlc` for previous-session OHLC and `last_price` for the current price.

Before using this for trading decisions, verify a few returned OHLC values against Upstox/NSE because live API response semantics should always be validated.

## Current scope

No:

- database
- scheduler
- email
- historical signal storage
- backtesting
- NSE holiday database
- RS/EMA/volume filters

Those can be added after V1 is validated.
