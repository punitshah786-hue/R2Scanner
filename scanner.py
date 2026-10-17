from __future__ import annotations
import gzip, io, json, re
from typing import Callable, Optional
import pandas as pd
import requests

UPSTOX_BASE_URL = "https://api.upstox.com/v3"
NIFTY_500_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
UPSTOX_INSTRUMENT_MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
REQUEST_TIMEOUT = 30
MAX_INSTRUMENTS_PER_REQUEST = 500

def load_nifty500_symbols():
    r = requests.get(NIFTY_500_CSV_URL, headers={"User-Agent":"Mozilla/5.0","Accept":"*/*"}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    try: text = r.content.decode("utf-8-sig")
    except UnicodeDecodeError: text = r.content.decode("latin-1")
    df = pd.read_csv(io.StringIO(text))
    col = next((c for c in df.columns if re.sub(r"[^a-z0-9]","",str(c).lower()) == "symbol"), None)
    if col is None: raise RuntimeError(f"Symbol column not found in Nifty 500 CSV. Columns: {list(df.columns)}")
    syms = df[col].astype(str).str.strip().str.upper()
    out = pd.DataFrame({"symbol":syms})
    return out[(out.symbol != "") & (out.symbol != "NAN")].drop_duplicates().reset_index(drop=True)

def load_upstox_equity_instruments():
    r = requests.get(UPSTOX_INSTRUMENT_MASTER_URL, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    raw = r.content
    try: raw = gzip.decompress(raw)
    except gzip.BadGzipFile: pass
    data = json.loads(raw.decode("utf-8"))
    out = {}
    for item in data:
        if item.get("segment") != "NSE_EQ" or item.get("instrument_type") != "EQ": continue
        sym = str(item.get("trading_symbol","")).strip().upper()
        key = item.get("instrument_key")
        if sym and key:
            out[sym] = {"instrument_key":key,"name":item.get("name",""),"isin":item.get("isin","")}
    return out

def build_universe(nifty, instruments):
    rows = []
    for sym in nifty["symbol"]:
        item = instruments.get(sym)
        if item:
            rows.append({"symbol":sym,"instrument_key":item["instrument_key"],"name":item["name"],"isin":item["isin"]})
    out = pd.DataFrame(rows, columns=["symbol","instrument_key","name","isin"])
    if out.empty: raise RuntimeError("No Nifty 500 symbols mapped to Upstox instruments.")
    return out

def calculate_levels(high, low, close):
    x = (high-low)/4.0
    return {"R1":close+x,"R2":close+2*x,"R3":close+3*x,"S1":close-x,"S2":close-2*x,"S3":close-3*x}

def fetch_daily_quotes(session, keys, progress_callback=None):
    quotes = {}
    total = len(keys)
    for start in range(0,total,MAX_INSTRUMENTS_PER_REQUEST):
        batch = keys[start:start+MAX_INSTRUMENTS_PER_REQUEST]
        if progress_callback:
            pct = min(70,40+int(((start+len(batch))/max(total,1))*30))
            progress_callback(pct,f"Fetching Upstox OHLC quotes: {start+1}-{start+len(batch)} of {total}...")
        r = session.get(f"{UPSTOX_BASE_URL}/market-quote/ohlc",params={"instrument_key":",".join(batch),"interval":"1d"},timeout=REQUEST_TIMEOUT)
        if r.status_code != 200: raise RuntimeError(f"Upstox OHLC API failed: HTTP {r.status_code}\n{r.text[:1500]}")
        payload = r.json()
        if payload.get("status") != "success": raise RuntimeError(f"Unexpected Upstox response: {payload}")
        quotes.update(payload.get("data") or {})
    return quotes

def as_float(value):
    try: return float(value) if value is not None else None
    except (TypeError,ValueError): return None

def build_all_stocks(universe, quotes):
    by_token = {}
    for key, quote in quotes.items():
        token = quote.get("instrument_token")
        if token: by_token[str(token)] = quote
    rows = []
    skipped = {"quote_not_matched_to_instrument":0,"missing_last_price":0,"missing_prev_ohlc":0,"invalid_ohlc_values":0}
    for row in universe.itertuples(index=False):
        quote = by_token.get(str(row.instrument_key)) or quotes.get(row.instrument_key) or quotes.get(f"NSE_EQ:{row.symbol}")
        if quote is None:
            skipped["quote_not_matched_to_instrument"] += 1; continue
        current = as_float(quote.get("last_price"))
        if current is None or current <= 0:
            skipped["missing_last_price"] += 1; continue
        prev = quote.get("prev_ohlc")
        if not isinstance(prev,dict):
            skipped["missing_prev_ohlc"] += 1; continue
        o,h,l,c = [as_float(prev.get(k)) for k in ("open","high","low","close")]
        if any(v is None or v <= 0 for v in (o,h,l,c)) or h < l:
            skipped["invalid_ohlc_values"] += 1; continue
        levels = calculate_levels(h,l,c)
        rows.append({"Symbol":row.symbol,"Company":row.name,"Instrument Key":row.instrument_key,"Open":o,"High":h,"Low":l,"Close":c,"Current Price":current,**levels})
    cols = ["Symbol","Company","Instrument Key","Open","High","Low","Close","Current Price","R1","R2","R3","S1","S2","S3"]
    out = pd.DataFrame(rows,columns=cols)
    if not out.empty: out = out.sort_values("Symbol").reset_index(drop=True)
    return out, skipped

def scan_nifty500(access_token: str, progress_callback: Optional[Callable] = None):
    if not access_token: raise ValueError("Upstox access token is empty.")
    session = requests.Session()
    session.headers.update({"Accept":"application/json","Authorization":f"Bearer {access_token}","User-Agent":"Nifty500-RS-Scanner/2.0"})
    if progress_callback: progress_callback(5,"Loading current Nifty 500 constituents...")
    nifty = load_nifty500_symbols()
    if progress_callback: progress_callback(20,"Loading Upstox instrument master...")
    instruments = load_upstox_equity_instruments()
    if progress_callback: progress_callback(30,"Mapping Nifty 500 symbols to Upstox...")
    universe = build_universe(nifty,instruments)
    if progress_callback: progress_callback(40,f"Mapped {len(universe)} stocks. Fetching OHLC quotes...")
    quotes = fetch_daily_quotes(session,universe["instrument_key"].tolist(),progress_callback)
    if progress_callback: progress_callback(75,"Parsing quotes and calculating R/S levels...")
    all_stocks, skipped = build_all_stocks(universe,quotes)
    if progress_callback: progress_callback(95,f"Parsed {len(all_stocks)} usable stocks from {len(quotes)} quote records.")
    r2 = all_stocks.loc[all_stocks["Current Price"] > all_stocks["R2"]].copy()
    if not r2.empty:
        r2["% Above R2"] = (r2["Current Price"]-r2["R2"])/r2["R2"]*100
        r2 = r2.sort_values("% Above R2",ascending=False).reset_index(drop=True)
    return {
        "all_stocks":all_stocks,"results":r2,
        "nifty_symbols_count":len(nifty),"universe_count":len(universe),
        "quotes_received":len(quotes),
        "diagnostics":f"Received {len(quotes)} quote records; parsed {len(all_stocks)} usable rows from {len(universe)} mapped instruments.",
        "diagnostics_detail":{
            "nifty_symbols_count":int(len(nifty)),"mapped_to_upstox":int(len(universe)),
            "quote_records_returned":int(len(quotes)),"usable_ohlc_rows":int(len(all_stocks)),
            "skipped_rows":skipped,"quote_response_keys_sample":list(quotes.keys())[:10]
        },
        "raw_quote_samples":{k:{"instrument_token":v.get("instrument_token"),"last_price":v.get("last_price"),"prev_ohlc":v.get("prev_ohlc"),"live_ohlc":v.get("live_ohlc")} for k,v in list(quotes.items())[:5]}
    }
