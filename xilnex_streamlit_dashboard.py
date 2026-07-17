import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import json
import time
import concurrent.futures
from datetime import date, datetime

try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

st.title("My Xilnex Sales Dashboard")
st.write("**Real Data from Xilnex**")

# Credentials are read from Streamlit's secrets manager, not hardcoded.
# Locally: create a file at .streamlit/secrets.toml (see instructions).
# On Streamlit Community Cloud: set these in the app's "Secrets" settings.
try:
    appid = st.secrets["XILNEX_APPID"]
    token = st.secrets["XILNEX_TOKEN"]
    auth = st.secrets.get("XILNEX_AUTH", "5")
except (KeyError, FileNotFoundError):
    st.error(
        "Missing Xilnex credentials. Add XILNEX_APPID, XILNEX_TOKEN, and XILNEX_AUTH "
        "to .streamlit/secrets.toml (locally) or to your app's Secrets settings (on Streamlit Cloud)."
    )
    st.stop()

MAX_PAGES = 200  # safety cap so a bug can't loop forever

st.sidebar.success("✅ Credentials Loaded")

st.sidebar.write("Date range")
col_from, col_to = st.sidebar.columns(2)
with col_from:
    from_date = st.date_input("From", value=date.today(), key="from_date")
with col_to:
    to_date = st.date_input("To", value=date.today(), key="to_date")

if to_date < from_date:
    st.sidebar.error("'To' date is before 'From' date — please fix the range.")
    st.stop()

datefrom = f"{from_date.isoformat()}T00:00:00.000Z"
dateto = f"{to_date.isoformat()}T23:59:59.000Z"

def date_label():
    if from_date == to_date:
        return from_date.strftime('%d %b %Y')
    return f"{from_date.strftime('%d %b %Y')} – {to_date.strftime('%d %b %Y')}"

st.sidebar.markdown("---")
live_mode = st.sidebar.toggle("🔴 Live mode (auto-refresh)", value=False)

if live_mode and not AUTOREFRESH_AVAILABLE:
    st.sidebar.error("Run `pip install streamlit-autorefresh` to enable live mode.")
    live_mode = False

refresh_seconds = 60
if live_mode:
    refresh_seconds = st.sidebar.slider("Refresh every (seconds)", 15, 300, 60, step=15)
    st_autorefresh(interval=refresh_seconds * 1000, key="live_refresh")

manual_load = st.sidebar.button("🔄 Refresh Now")

# Data loads automatically whenever the page opens or a setting changes —
# no button click required. The button above is just an optional manual
# refresh if you want the very latest numbers without waiting.
run_now = True

if live_mode:
    st.sidebar.caption(f"Auto-refreshing every {refresh_seconds}s")

@st.cache_data(ttl=20, show_spinner=False)
def fetch_all_sales(datefrom, dateto, appid, token, auth, limit=100, max_pages=200, max_workers=8):
    """Fetches every page of sales/search for the given date range, in parallel.
    Cached for 20 seconds so quick repeat loads (reruns, live-mode ticks that
    land close together) don't re-hit the API at all."""
    headers = {"appid": appid, "token": token, "auth": auth, "Accept": "application/json"}
    base_url = f"https://api.xilnex.com/logic/v2/sales/search?sort=id:desc&datefrom={datefrom}&dateto={dateto}"

    def fetch_page(offset, max_retries=3, timeout=30):
        url = f"{base_url}&offset={offset}&limit={limit}"
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                return requests.get(url, headers=headers, timeout=timeout)
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(1.5 * attempt)
        raise last_error

    errors = []

    # Page 1 first (sequential) — we need it to find out totalSize before
    # we know how many more pages to request in parallel.
    try:
        first_resp = fetch_page(0)
    except requests.exceptions.RequestException as e:
        return [], None, [f"Network error: {e}"]

    if first_resp.status_code != 200:
        return [], None, [f"HTTP {first_resp.status_code}: {first_resp.text[:300]}"]

    first_data = first_resp.json().get("data", {}) or {}
    all_sales = list(first_data.get("sales", []) or [])
    total_size = first_data.get("totalSize")

    if not all_sales or total_size is None or len(all_sales) >= total_size:
        return all_sales, total_size, errors

    remaining_offsets = list(range(limit, min(total_size, limit * max_pages), limit))

    def safe_fetch(offset):
        try:
            resp = fetch_page(offset)
            if resp.status_code != 200:
                return offset, [], f"HTTP {resp.status_code} at offset {offset}"
            page_data = resp.json().get("data", {}) or {}
            return offset, page_data.get("sales", []) or [], None
        except requests.exceptions.RequestException as e:
            return offset, [], f"Network error at offset {offset}: {e}"

    # Fire off all remaining pages at once instead of one-by-one.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(safe_fetch, off) for off in remaining_offsets]
        for future in concurrent.futures.as_completed(futures):
            offset, sales, err = future.result()
            if err:
                errors.append(err)
            if sales:
                all_sales.extend(sales)

    return all_sales, total_size, errors


if run_now:
    try:
        with st.spinner("Loading sales data..."):
            all_sales, total_size, fetch_errors = fetch_all_sales(datefrom, dateto, appid, token, auth)

        for err in fetch_errors:
            st.warning(f"Some data may be incomplete: {err}")

        if not all_sales:
            st.warning(f"No sales records found for {date_label()}.")
        else:
            # --- Build a store-level breakdown from the 'collections' entries ---
            store_rows = []
            for sale in all_sales:
                collections = sale.get("collections") or []
                for c in collections:
                    store_rows.append({
                        "outlet": c.get("outlet"),
                        "amount": c.get("amount"),
                        "method": c.get("method"),
                        "paymentDate": c.get("paymentDate"),
                        "invoiceId": c.get("invoiceId"),
                    })

            if store_rows:
                df_stores = pd.DataFrame(store_rows)
                grand_total = df_stores["amount"].sum()

                # --- Highlighted total, shown first at the top ---
                st.markdown(
                    f"""
                    <div style="
                        background-color:#FFD54A;
                        color:#000000;
                        padding:16px 22px;
                        border-radius:8px;
                        display:inline-block;
                        margin-bottom:18px;
                    ">
                        <div style="font-size:14px; font-weight:600; opacity:0.75;">
                            Total Sales — {date_label()}
                        </div>
                        <div style="font-size:34px; font-weight:700; line-height:1.3;">
                            RM {grand_total:,.2f}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                store_totals = (
                    df_stores.groupby("outlet")["amount"]
                    .sum()
                    .reset_index()
                    .rename(columns={"outlet": "Store", "amount": "Total Sales (RM)"})
                    .sort_values("Total Sales (RM)", ascending=False)
                )
                st.subheader(f"Sales Summary — {date_label()}")
                st.dataframe(
                    store_totals.style.format({"Total Sales (RM)": "RM {:,.2f}"}),
                    use_container_width=True,
                    hide_index=True
                )

                fig = px.bar(store_totals, x="Store", y="Total Sales (RM)", title=f"Sales by Store — {date_label()}")
                st.plotly_chart(fig, use_container_width=True)

                print("Store totals (all pages):\n", store_totals.to_string(index=False))
            else:
                st.warning("Sales records found, but no 'collections'/payment entries inside them.")

            # --- Line items are hidden by default now; flip this to True if you want them back ---
            SHOW_LINE_ITEMS = False
            if SHOW_LINE_ITEMS:
                all_items = []
                for sale in all_sales:
                    for li in (sale.get("items") or []):
                        all_items.append(li)
                if all_items:
                    df_items = pd.DataFrame(all_items)
                    st.subheader("Line Items (products sold)")
                    st.dataframe(df_items, use_container_width=True)

    except Exception as e:
        st.error(f"❌ An error occurred: {str(e)}")
        st.exception(e)
