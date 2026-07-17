import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import json
import time
from datetime import date, datetime

try:
    from streamlit_autorefresh import st_autorefresh
    AUTOREFRESH_AVAILABLE = True
except ImportError:
    AUTOREFRESH_AVAILABLE = False

st.title("My Xilnex Sales Dashboard")
st.write("**Real Data from Xilnex**")

# Your Credentials
# TODO: move these to environment variables before sharing/deploying this file
appid = "vfWC58Dcqc3n0ZzEXb5sZOxUFhE5Ubgx"
token = "v5_YZVv7KhRel5XUCO0tf+Ac9CzZfJn/YfpPuiw02LwLcM="
auth = "5"

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

manual_load = st.sidebar.button(f"Load Sales for {date_label()}")

# In live mode, the page runs automatically (no button needed).
# Otherwise, it only runs when the button is clicked.
run_now = manual_load or live_mode

if live_mode:
    st.sidebar.caption(f"Auto-refreshing every {refresh_seconds}s")

if run_now:
    fetch_time = datetime.now().strftime('%H:%M:%S')
    st.info(f"Fetching sales for {date_label()} (all pages)... last checked {fetch_time}")

    headers = {
        "appid": appid,
        "token": token,
        "auth": auth,
        "Accept": "application/json"
    }

    try:
        limit = 100
        offset = 0
        base_url = f"https://api.xilnex.com/logic/v2/sales/search?sort=id:desc&datefrom={datefrom}&dateto={dateto}"

        all_sales = []
        page_count = 0
        total_size = None
        status_placeholder = st.empty()
        stopped_early = False

        def fetch_page(page_url, max_retries=3, timeout=30):
            last_error = None
            for attempt in range(1, max_retries + 1):
                try:
                    return requests.get(page_url, headers=headers, timeout=timeout)
                except requests.exceptions.RequestException as e:
                    last_error = e
                    print(f"Attempt {attempt}/{max_retries} failed for {page_url}: {e}")
                    if attempt < max_retries:
                        time.sleep(2 * attempt)  # backoff: 2s, 4s, ...
            raise last_error

        while page_count < MAX_PAGES:
            url = f"{base_url}&offset={offset}&limit={limit}"

            try:
                response = fetch_page(url)
            except requests.exceptions.RequestException as e:
                st.warning(
                    f"Network error at offset {offset} after retries ({e}). "
                    f"Showing summary from the {len(all_sales)} records fetched so far."
                )
                print(f"Giving up at offset {offset} after retries: {e}")
                stopped_early = True
                break

            if response.status_code != 200:
                st.error(f"Error {response.status_code} at offset {offset}")
                print(f"Error {response.status_code} at offset {offset}")
                print("URL that failed:", url)
                print(response.text[:2000])
                st.json(response.json() if response.text else {})
                break

            result = response.json()
            data = result.get("data", {}) or {}
            page_sales = data.get("sales", []) or []
            all_sales.extend(page_sales)

            page_count += 1
            total_size = data.get("totalSize", total_size)

            status_placeholder.info(
                f"Fetched page {page_count} (offset {offset}) — {len(all_sales)}"
                + (f" of {total_size}" if total_size else "")
                + " records loaded so far..."
            )
            print(f"Fetched page {page_count} (offset {offset}), running total: {len(all_sales)}"
                  + (f" of {total_size}" if total_size else ""))

            # Stop once a page comes back empty, or once we've collected
            # everything totalSize says exists.
            if not page_sales:
                break
            if total_size is not None and len(all_sales) >= total_size:
                break

            offset += limit
            time.sleep(0.15)  # small pause so we don't hammer the API

        if page_count >= MAX_PAGES:
            st.warning(f"Stopped after {MAX_PAGES} pages (safety limit) — there may be more data.")

        if stopped_early:
            status_placeholder.warning(f"⚠️ Loaded {len(all_sales)} sales records across {page_count} page(s) before a network error stopped the fetch.")
        else:
            status_placeholder.success(f"✅ Loaded {len(all_sales)} sales records across {page_count} page(s) — updated {datetime.now().strftime('%H:%M:%S')}")

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
else:
    st.write("👈 Click **Load Sales**, or turn on **Live mode** in the sidebar to auto-refresh.")
