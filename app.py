import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from scanner import scan_nifty500

IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(
    page_title="Nifty 500 R2 Scanner",
    page_icon="📈",
    layout="wide",
)

def get_access_token() -> str:
    try:
        token = st.secrets["UPSTOX_ACCESS_TOKEN"]
        if token:
            return str(token).strip()
    except Exception:
        pass
    return os.getenv("UPSTOX_ACCESS_TOKEN", "").strip()

def format_results(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for col in [
        "Close", "R1", "R2", "R3", "S1", "S2", "S3", "Current Price"
    ]:
        if col in result.columns:
            result[col] = result[col].map(
                lambda x: f"₹{x:,.2f}" if pd.notna(x) else ""
            )
    if "% Above R2" in result.columns:
        result["% Above R2"] = result["% Above R2"].map(
            lambda x: f"{x:.2f}%" if pd.notna(x) else ""
        )
    return result

st.title("📈 Nifty 500 R2 Scanner")
st.caption(
    "Shows only Nifty 500 stocks where Current Price > R2."
)

with st.sidebar:
    st.header("Scanner")
    st.info(
        "x = (H-L)/4\n\n"
        "R1 = C+x\n"
        "R2 = C+2x\n"
        "R3 = C+3x\n\n"
        "S1 = C-x\n"
        "S2 = C-2x\n"
        "S3 = C-3x"
    )
    scan_button = st.button(
        "🔄 Run Scanner",
        type="primary",
        use_container_width=True,
    )
    st.divider()
    st.write("Data source: Upstox")
    st.write("Universe: Current Nifty 500")
    st.write("Signal: Current Price > R2")
    st.write("Database: None")
    st.write("Scheduler: None")

if scan_button:
    token = get_access_token()
    if not token:
        st.error(
            "UPSTOX_ACCESS_TOKEN is not configured. "
            "Add it under Streamlit App → Settings → Secrets."
        )
        st.stop()

    progress = st.progress(0)
    status = st.empty()

    def update_progress(percent, message):
        progress.progress(percent)
        status.info(message)

    try:
        result = scan_nifty500(
            access_token=token,
            progress_callback=update_progress,
        )
        progress.progress(100)
        status.success("Scan completed.")
        st.session_state["scan_result"] = result
        st.session_state["scan_time"] = datetime.now(IST)
    except Exception as exc:
        progress.empty()
        status.empty()
        st.error("Scanner failed.")
        with st.expander("Technical error"):
            st.exception(exc)
        st.stop()

if "scan_result" not in st.session_state:
    st.info("Click **Run Scanner** to scan the current Nifty 500.")
    st.markdown("""
### Scanner logic

For each Nifty 500 stock using the previous trading session:

```text
x  = (H - L) / 4

R1 = C + x
R2 = C + 2x
R3 = C + 3x

S1 = C - x
S2 = C - 2x
S3 = C - 3x
```

Only this condition is used:

```text
Current Price > R2
```

Results are sorted by **% Above R2**, highest first.
""")
    st.stop()

scan = st.session_state["scan_result"]
scan_time = st.session_state["scan_time"]
results = scan["results"]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Nifty 500 Scanned", scan["universe_count"])
c2.metric("Quotes Received", scan["quotes_received"])
c3.metric("Stocks > R2", len(results))
c4.metric("Scan Time", scan_time.strftime("%H:%M:%S"))

st.divider()

if results.empty:
    st.success("No Nifty 500 stocks are currently above R2.")
else:
    st.subheader("R2 Breakouts")

    columns = [
        "Symbol", "Company", "Close", "R1", "R2", "R3",
        "S1", "S2", "S3", "Current Price", "% Above R2"
    ]
    display = results[columns].copy()

    st.dataframe(
        format_results(display),
        use_container_width=True,
        hide_index=True,
    )

    st.download_button(
        "⬇️ Download Results CSV",
        data=results.to_csv(index=False).encode("utf-8"),
        file_name=(
            f"r2_scanner_{scan_time.strftime('%Y%m%d_%H%M%S')}.csv"
        ),
        mime="text/csv",
    )

st.caption(
    f"Last scan: {scan_time.strftime('%Y-%m-%d %H:%M:%S')} IST"
)
