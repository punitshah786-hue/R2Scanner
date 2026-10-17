
from __future__ import annotations
import concurrent.futures, gzip, io, json, re, time
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
import pandas as pd
import requests

BASE="https://api.upstox.com/v3"
NIFTY_URL="https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"
MASTER_URL="https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
TIMEOUT=30
IST=ZoneInfo("Asia/Kolkata")

def load_nifty500_symbols():
    r=requests.get(NIFTY_URL,headers={"User-Agent":"Mozilla/5.0","Accept":"*/*"},timeout=TIMEOUT); r.raise_for_status()
    try: txt=r.content.decode("utf-8-sig")
    except UnicodeDecodeError: txt=r.content.decode("latin-1")
    df=pd.read_csv(io.StringIO(txt))
    col=next((c for c in df.columns if re.sub(r"[^a-z0-9]","",str(c).lower())=="symbol"),None)
    if col is None: raise RuntimeError(f"Nifty CSV has no Symbol column: {list(df.columns)}")
    syms=df[col].astype(str).str.strip().str.upper()
    return pd.DataFrame({"symbol":syms})[(syms!="")&(syms!="NAN")].drop_duplicates().reset_index(drop=True)

def load_upstox_equity_instruments():
    r=requests.get(MASTER_URL,timeout=TIMEOUT); r.raise_for_status(); raw=r.content
    try: raw=gzip.decompress(raw)
    except gzip.BadGzipFile: pass
    out={}
    for x in json.loads(raw.decode("utf-8")):
        if x.get("segment")!="NSE_EQ" or x.get("instrument_type")!="EQ": continue
        s=str(x.get("trading_symbol","")).strip().upper(); k=x.get("instrument_key")
        if s and k: out[s]={"instrument_key":k,"name":x.get("name",""),"isin":x.get("isin","")}
    return out

def build_universe(nifty,instruments):
    rows=[]
    for s in nifty.symbol:
        x=instruments.get(s)
        if x: rows.append({"symbol":s,"instrument_key":x["instrument_key"],"name":x["name"],"isin":x["isin"]})
    df=pd.DataFrame(rows,columns=["symbol","instrument_key","name","isin"])
    if df.empty: raise RuntimeError("No Nifty 500 symbols mapped to Upstox.")
    return df

def calculate_levels(h,l,c):
    x=(h-l)/4
    return {"R1":c+x,"R2":c+2*x,"R3":c+3*x,"S1":c-x,"S2":c-2*x,"S3":c-3*x}

def session_for(token):
    s=requests.Session(); s.headers.update({"Accept":"application/json","Authorization":f"Bearer {token}","User-Agent":"Nifty500-RS-Scanner/3.0"}); return s

def fetch_live_quotes(session,keys,progress_callback=None):
    out={}; total=len(keys)
    for start in range(0,total,500):
        batch=keys[start:start+500]
        if progress_callback: progress_callback(min(25,15+int((start+len(batch))/total*10)),f"Fetching current prices: {start+1}-{start+len(batch)} of {total}...")
        r=session.get(f"{BASE}/market-quote/ohlc",params={"instrument_key":",".join(batch),"interval":"1d"},timeout=TIMEOUT)
        if r.status_code!=200: raise RuntimeError(f"Upstox market quote failed HTTP {r.status_code}: {r.text[:1000]}")
        p=r.json()
        if p.get("status")!="success": raise RuntimeError(f"Unexpected quote response: {p}")
        out.update(p.get("data") or {})
    return out

def as_float(x):
    try: return float(x) if x is not None else None
    except (TypeError,ValueError): return None

def match_quotes(universe,quotes):
    by_token={str(q.get("instrument_token")):q for q in quotes.values() if q.get("instrument_token")}
    found={}
    for r in universe.itertuples(index=False):
        q=by_token.get(str(r.instrument_key)) or quotes.get(r.instrument_key) or quotes.get(f"NSE_EQ:{r.symbol}")
        if q is not None: found[r.instrument_key]=q
    return found

def get_previous_candle(token,key,today,from_date):
    url=f"{BASE}/historical-candle/{quote(key,safe='|')}/days/1/{today}/{from_date}"
    s=session_for(token); error=None
    for attempt in range(3):
        try:
            r=s.get(url,timeout=TIMEOUT)
            if r.status_code==429 or r.status_code>=500:
                error=f"HTTP {r.status_code}: {r.text[:200]}"
                if attempt<2: time.sleep(.4*(attempt+1)); continue
                return None,error
            if r.status_code!=200: return None,f"HTTP {r.status_code}: {r.text[:250]}"
            p=r.json()
            if p.get("status")!="success": return None,f"API status: {p}"
            candles=(p.get("data") or {}).get("candles") or []; valid=[]
            for c in candles:
                try:
                    dt=datetime.fromisoformat(str(c[0]).replace("Z","+00:00"))
                    if dt.tzinfo: dt=dt.astimezone(IST)
                    vals=[float(c[i]) for i in range(1,5)]
                    if dt.date()<today and min(vals)>0 and vals[1]>=vals[2]:
                        valid.append((dt.date(),vals))
                except (ValueError,TypeError,IndexError): pass
            if not valid: return None,"No candle before today's date"
            day,v=max(valid,key=lambda x:x[0])
            return {"date":day.isoformat(),"open":v[0],"high":v[1],"low":v[2],"close":v[3]},None
        except (requests.RequestException,ValueError) as e:
            error=str(e)
            if attempt<2: time.sleep(.4*(attempt+1)); continue
    return None,error or "Unknown error"

def fetch_history(universe,token,progress_callback=None):
    today=datetime.now(IST).date(); to_date=today.isoformat(); from_date=(today-timedelta(days=10)).isoformat()
    result={}; errors={}; total=len(universe)
    def worker(row): return row.instrument_key,*get_previous_candle(token,row.instrument_key,today,from_date)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures=[pool.submit(worker,row) for row in universe.itertuples(index=False)]
        for n,f in enumerate(concurrent.futures.as_completed(futures),1):
            key,candle,error=f.result()
            if candle: result[key]=candle
            elif error: errors[key]=error
            if progress_callback and (n%10==0 or n==total):
                progress_callback(min(80,25+int(n/total*55)),f"Historical candles fetched: {n}/{total}; found {len(result)}...")
    return result,errors,from_date,to_date

def build_all_stocks(universe,live_by_key,history):
    rows=[]; skipped={"missing_current_quote":0,"missing_previous_daily_candle":0,"invalid_current_price":0}
    for r in universe.itertuples(index=False):
        q=live_by_key.get(r.instrument_key)
        if q is None: skipped["missing_current_quote"]+=1; continue
        price=as_float(q.get("last_price"))
        if price is None or price<=0: skipped["invalid_current_price"]+=1; continue
        c=history.get(r.instrument_key)
        if c is None: skipped["missing_previous_daily_candle"]+=1; continue
        levels=calculate_levels(c["high"],c["low"],c["close"])
        rows.append({"Symbol":r.symbol,"Company":r.name,"Instrument Key":r.instrument_key,"Previous Candle Date":c["date"],"Open":c["open"],"High":c["high"],"Low":c["low"],"Close":c["close"],"Current Price":price,**levels})
    cols=["Symbol","Company","Instrument Key","Previous Candle Date","Open","High","Low","Close","Current Price","R1","R2","R3","S1","S2","S3"]
    df=pd.DataFrame(rows,columns=cols)
    if not df.empty: df=df.sort_values("Symbol").reset_index(drop=True)
    return df,skipped

def scan_nifty500(access_token,progress_callback=None):
    if not access_token: raise ValueError("Upstox access token is empty.")
    if progress_callback: progress_callback(5,"Loading current Nifty 500...")
    nifty=load_nifty500_symbols()
    if progress_callback: progress_callback(10,"Loading Upstox instrument master...")
    instruments=load_upstox_equity_instruments()
    universe=build_universe(nifty,instruments)
    if progress_callback: progress_callback(14,f"Mapped {len(universe)} instruments. Fetching live prices...")
    live=fetch_live_quotes(session_for(access_token),universe.instrument_key.tolist(),progress_callback)
    live_by_key=match_quotes(universe,live)
    if progress_callback: progress_callback(25,"Fetching previous-session OHLC from historical-candle API...")
    history,history_errors,from_date,to_date=fetch_history(universe,access_token,progress_callback)
    if progress_callback: progress_callback(85,"Calculating R/S levels...")
    all_stocks,skipped=build_all_stocks(universe,live_by_key,history)
    r2=all_stocks.loc[all_stocks["Current Price"]>all_stocks["R2"]].copy()
    if not r2.empty:
        r2["% Above R2"]=(r2["Current Price"]-r2["R2"])/r2["R2"]*100
        r2=r2.sort_values("% Above R2",ascending=False).reset_index(drop=True)
    detail={"nifty_symbols_count":len(nifty),"mapped_to_upstox":len(universe),"live_quote_records_returned":len(live),"live_quotes_matched":len(live_by_key),"historical_candles_found":len(history),"historical_candle_errors":len(history_errors),"historical_date_window":{"from_date":from_date,"to_date":to_date},"usable_ohlc_rows":len(all_stocks),"skipped_rows":skipped,"sample_historical_errors":dict(list(history_errors.items())[:10]),"live_quote_response_keys_sample":list(live.keys())[:10]}
    raw={k:{"instrument_token":v.get("instrument_token"),"last_price":v.get("last_price")} for k,v in list(live.items())[:5]}
    if progress_callback: progress_callback(100,f"Completed: {len(all_stocks)} usable stocks.")
    return {"all_stocks":all_stocks,"results":r2,"nifty_symbols_count":len(nifty),"universe_count":len(universe),"quotes_received":len(live),"diagnostics":f"Live quotes: {len(live)}; matched: {len(live_by_key)}; historical candles: {len(history)}; usable rows: {len(all_stocks)}.","diagnostics_detail":detail,"raw_quote_samples":raw}
