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

## Historical OHLC fix

Previous-session OHLC is now fetched from Upstox V3 Historical Candle Data:
`/v3/historical-candle/{instrument_key}/days/1/{to_date}/{from_date}`.
The app requests a 10-calendar-day window ending today, then selects the latest candle strictly before today's date in IST. This handles weekends and exchange holidays. The bulk market-quote endpoint is used only for current/last price. Historical candles require one API call per instrument, executed with a small thread pool. Data diagnostics include historical candle counts and sample errors.

## V4 change: Cloudflare HTTP 429 / Error 1015 protection

Historical candle requests are now sent sequentially with a 1-second delay instead of using a concurrent thread pool. Rate-limited responses use exponential backoff, respect `Retry-After` where available, and trigger an additional cooldown. This reduces request bursts that can lead to Cloudflare Error 1015. It does not bypass provider limits; if rate limiting persists, stop the scan and retry later or contact Upstox support. A full 500-stock history scan may take several minutes.

## V5 change: persistent historical-candle cache

Historical daily candles are cached in `.cache/historical_daily_candles.json`. The scanner reuses the latest cached candle strictly before the current IST date and fetches history only for instruments without a suitable cached candle. Each successful candle is saved immediately so a later HTTP 429 does not lose the earlier progress. The cache is pruned to the latest 14 calendar days and is excluded from Git.

**Hosting note:** this local JSON cache survives reruns and app restarts only if the deployment filesystem persists. Some Streamlit Community Cloud restarts/redeployments may reset local files. If persistence is not retained by the host, the scanner will still work but may fetch candles again after a restart.
