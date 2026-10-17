# Nifty 500 R/S Level Scanner — V2

Two panels:
1. **Stocks above R1/R2/R3** selected from the sidebar dropdown.
2. **All scanned stocks** with usable OHLC/current price and R1/R2/R3/S1/S2/S3.

Both tables paginate 10 rows per page. Includes metrics and diagnostics so you can tell whether data was fetched even if no stocks break a resistance.

## Calculation
Uses previous-session OHLC:
- x = (High - Low) / 4
- R1 = Close + x; R2 = Close + 2x; R3 = Close + 3x
- S1 = Close - x; S2 = Close - 2x; S3 = Close - 3x

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```
Configure `UPSTOX_ACCESS_TOKEN` in `.streamlit/secrets.toml` or as an environment variable.

## Streamlit Community Cloud
Push files to GitHub, deploy `app.py`, then add this under App → Settings → Secrets:
```toml
UPSTOX_ACCESS_TOKEN = "YOUR_REAL_UPSTOX_ACCESS_TOKEN"
```
Never commit a real token. The app has no database, scheduler, or email; run manually.

## Diagnostics
The top metrics show Nifty symbols, mapped instruments, quote records received, and usable OHLC rows. Open **Data diagnostics** to see skipped-row reasons and sample quote records. Outside market hours, `last_price` may be the last traded price.
