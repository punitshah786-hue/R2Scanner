import os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st
from scanner import scan_nifty500

IST = ZoneInfo("Asia/Kolkata")
st.set_page_config(page_title="Nifty 500 R/S Scanner", page_icon="📈", layout="wide")

def get_access_token():
    try:
        token = st.secrets["UPSTOX_ACCESS_TOKEN"]
        if token:
            return str(token).strip()
    except Exception:
        pass
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

def fmt_prices(df):
    out = df.copy()
    for col in ["Open","High","Low","Close","Current Price","R1","R2","R3","S1","S2","S3"]:
        if col in out:
            out[col] = out[col].map(lambda v: f"₹{v:,.2f}" if pd.notna(v) else "—")
    if "% Above Level" in out:
        out["% Above Level"] = out["% Above Level"].map(lambda v: f"{v:.2f}%" if pd.notna(v) else "—")
    return out

def paged_table(df, prefix, page_size=10):
    if df.empty:
        st.info("No rows to display.")
        return
    key = f"{prefix}_page"
    pages = max(1, (len(df) + page_size - 1) // page_size)
    st.session_state[key] = min(st.session_state.get(key, 1), pages)
    a, b, c = st.columns([1, 2, 1])
    with a:
        if st.button("◀ Previous", key=f"{prefix}_prev", disabled=st.session_state[key] <= 1, use_container_width=True):
            st.session_state[key] -= 1
            st.rerun()
    with b:
        st.markdown(f"<div style='text-align:center;padding-top:8px'>Page <b>{st.session_state[key]}</b> of <b>{pages}</b> · {len(df)} stocks</div>", unsafe_allow_html=True)
    with c:
        if st.button("Next ▶", key=f"{prefix}_next", disabled=st.session_state[key] >= pages, use_container_width=True):
            st.session_state[key] += 1
            st.rerun()
    start = (st.session_state[key] - 1) * page_size
    st.dataframe(fmt_prices(df.iloc[start:start + page_size]), use_container_width=True, hide_index=True)

st.title("📈 Nifty 500 R/S Level Scanner")
st.caption("Two panels: stocks above a selectable resistance level, and all stocks successfully scanned.")

with st.sidebar:
    st.header("Scanner settings")
    level = st.selectbox("Panel 1: Show stocks above", ["R1", "R2", "R3"], index=1)
    st.info("Using previous-session OHLC\n\nx = (High − Low) / 4\nR1 = Close + x\nR2 = Close + 2x\nR3 = Close + 3x\nS1 = Close − x\nS2 = Close − 2x\nS3 = Close − 3x")
    run_scan = st.button("🔄 Run / Refresh Scanner", type="primary", use_container_width=True)
    st.write("Source: Upstox · Universe: current Nifty 500")
    st.write("Manual scan · No database or scheduler")

if run_scan:
    token = get_access_token()
    if not token:
        st.error("UPSTOX_ACCESS_TOKEN is not configured. Add it under Streamlit App → Settings → Secrets.")
        st.stop()
    progress, status = st.progress(0), st.empty()
    def update_progress(pct, message):
        progress.progress(pct)
        status.info(message)
    try:
        scan = scan_nifty500(token, progress_callback=update_progress)
        st.session_state["scan_result"] = scan
        st.session_state["scan_time"] = datetime.now(IST)
        progress.progress(100)
        status.success("Scan completed.")
    except Exception as exc:
        st.error("Scanner failed.")
        with st.expander("Technical error", expanded=True):
            st.exception(exc)
        st.stop()

if "scan_result" not in st.session_state:
    st.warning("No scan has run in this session. Click **Run / Refresh Scanner**. After a scan, the metrics and Panel 2 help verify whether data was fetched even if no stock is above a resistance level.")
    st.stop()

scan = st.session_state["scan_result"]
scan_time = st.session_state["scan_time"]
all_stocks = scan["all_stocks"].copy()

cols = st.columns(5)
cols[0].metric("Nifty 500 symbols", scan["nifty_symbols_count"])
cols[1].metric("Mapped to Upstox", scan["universe_count"])
cols[2].metric("Quotes returned", scan["quotes_received"])
cols[3].metric("Usable OHLC rows", len(all_stocks))
cols[4].metric("Last scan IST", scan_time.strftime("%H:%M:%S"))
st.caption(scan.get("diagnostics", ""))
st.divider()

st.subheader(f"Panel 1 — Stocks trading above {level}")
breakouts = all_stocks.loc[all_stocks["Current Price"] > all_stocks[level]].copy()
breakouts["% Above Level"] = ((breakouts["Current Price"] - breakouts[level]) / breakouts[level]) * 100
breakouts = breakouts.sort_values("% Above Level", ascending=False).reset_index(drop=True)
if breakouts.empty:
    st.warning(f"No stocks currently satisfy Current Price > {level}. Check Panel 2 and the quote counts to confirm the scan fetched data.")
else:
    st.success(f"Found {len(breakouts)} stocks above {level}.")
    paged_table(breakouts[["Symbol","Company","Current Price",level,"% Above Level","R1","R2","R3"]], "breakouts")
    st.download_button(f"Download stocks above {level} CSV", breakouts.to_csv(index=False).encode("utf-8"), f"nifty500_above_{level.lower()}.csv", "text/csv")

st.divider()
st.subheader("Panel 2 — All scanned stocks and calculated levels")
st.write("Every Nifty 500 stock with usable current-price and previous-session OHLC data is shown here, whether or not it is above R1/R2/R3.")
if all_stocks.empty:
    st.error("Upstox returned no usable OHLC rows. Expand Data diagnostics below.")
else:
    all_cols = ["Symbol","Company","Open","High","Low","Close","Current Price","R1","R2","R3","S1","S2","S3"]
    paged_table(all_stocks[all_cols], "all_stocks")
    st.download_button("Download all scanned stocks CSV", all_stocks.to_csv(index=False).encode("utf-8"), "nifty500_all_scanned_stocks.csv", "text/csv")

with st.expander("Data diagnostics — verify fetching and scanning"):
    st.json(scan.get("diagnostics_detail", {}))
    if not all_stocks.empty:
        st.write("Sample of first 10 usable rows (unformatted values)")
        st.dataframe(all_stocks.head(10), use_container_width=True, hide_index=True)
    elif scan.get("raw_quote_samples"):
        st.write("Sample quote records returned by Upstox")
        st.json(scan["raw_quote_samples"])
    st.caption("If quote records are 0, check the API/token. If quotes are returned but usable rows are 0, inspect mapping and skipped-row reasons.")
st.caption(f"Last successful scan: {scan_time:%Y-%m-%d %H:%M:%S} IST")
