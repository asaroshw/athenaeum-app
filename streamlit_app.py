import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import re
import io
import requests
import urllib.parse
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from google import genai
from google.genai import types
from datetime import timedelta

try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except ImportError:
    HAS_ARIMA = False

# ============================================================
# 1. SETUP & CONFIGURATION
# ============================================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
st.set_page_config(page_title="Financial Intelligence App", layout="wide")

GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")

GOLD = "#EAB308"
BG = "#0D1117"
CARD_BG = "#161B22"
BORDER = "#262B36"
GREEN = "#3FB950"
RED = "#F85149"
ORANGE = "#F97316"
MUTED = "#8B949E"
BLUE = "#38BDF8"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="st-"], .stApp, div, span, p, table, th, td, label {{ font-family: 'Inter', sans-serif !important; }}
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    section[data-testid="stSidebar"] {{ background-color: {BG}; border-right: 1px solid {BORDER}; }}
    section[data-testid="stSidebar"] .stRadio > label {{ display:none; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{ background-color: transparent; padding: 8px 10px; border-radius: 6px; margin-bottom: 2px; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{ background-color: #1c2128; }}
    .swf-title-container {{ text-align: center; padding: 10px 0 20px 0; border-bottom: 1px solid {BORDER}; margin-bottom: 20px; }}
    .swf-title {{ font-size: 1.85em; font-weight: 800; color: #FFFFFF; letter-spacing: 0.5px; }}
    .swf-card {{ background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.05em; margin-bottom:6px; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:0px; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
    .swf-company-mini {{ padding: 6px 4px 14px 4px; border-bottom: 1px solid {BORDER}; margin-bottom: 8px; }}
    .swf-avatar {{ width:40px; height:40px; border-radius:8px; background:#fff; color:#111; font-weight:800; display:flex; align-items:center; justify-content:center; font-size:1.2em; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 2. HELPERS
# ============================================================
def to_float(val):
    if val in [None, "N/A", "", "None", "Stock doesn't pay dividends"]: return None
    try: return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except Exception: return None

def is_valid_metric(val):
    if val in [None, "N/A", "", "-", "--", "None", "0", "0.00%", "0.00"]: return False
    if isinstance(val, (int, float)): return True
    try:
        float(str(val).replace(',', '').replace('₹', '').replace('%', '').replace('x', '').replace('Cr.', '').strip())
        return True
    except ValueError: return False

def fmt_indian_currency(val, currency="₹"):
    if not is_valid_metric(val): return "N/A"
    try:
        num = float(str(val).replace(',', '').replace('₹', '').replace('%', '').strip())
        sym = "₹"
        if abs(num) >= 10000000: return f"{sym}{num/10000000:,.2f} Cr"
        elif abs(num) >= 100000: return f"{sym}{num/100000:,.2f} Lakh"
        else: return f"{sym}{num:,.2f}"
    except Exception: return f"{currency} {val}"

def calculate_rsi(df, window=14):
    if df is None or len(df) <= window or 'Close' not in df.columns: return "N/A"
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    loss = loss.replace(0, 1e-10)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return round(val, 2) if pd.notna(val) else "N/A"

def fetch_google_news(query_term):
    try:
        safe_query = urllib.parse.quote(query_term)
        url = f"https://news.google.com/rss/search?q={safe_query}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = root.findall('.//item')
            headlines = []
            for item in items[:4]:
                title = item.find('title')
                link = item.find('link')
                if title is not None and link is not None:
                    headlines.append({'title': title.text, 'link': link.text})
            return headlines
    except Exception: pass
    return []

def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit(): return stock_str + '.BO'
    try:
        res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'): return sym
    except Exception: pass
    upper_input = stock_str.upper().replace(" ", "")
    return upper_input if upper_input.endswith(('.NS', '.BO')) else upper_input + '.NS'

def custom_metric(label, value):
    st.markdown(f"""
    <div style="background-color: {CARD_BG}; border: 1px solid {BORDER}; padding: 12px 15px; border-radius: 8px; margin-bottom: 12px;">
        <div style="font-size: 12px; color: {MUTED}; text-transform: uppercase; font-weight: 600; margin-bottom: 4px; letter-spacing: 0.5px;">{label}</div>
        <div style="font-size: 20px; font-weight: 700; color: #FFFFFF;">{value}</div>
    </div>
    """, unsafe_allow_html=True)

def card(title, body_html):
    st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

# ============================================================
# 3. SCRAPERS & PREDICTIVE PIPELINE
# ============================================================
def fetch_screener(ticker):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    urls = [f"https://www.screener.in/company/{clean_ticker}/consolidated/", f"https://www.screener.in/company/{clean_ticker}/"]
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    metrics = {}
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                ratios = soup.find('ul', id='top-ratios')
                if ratios:
                    for li in ratios.find_all('li'):
                        name = li.find('span', class_='name')
                        num = li.find('span', class_='number')
                        if name and num:
                            n = name.text.strip().lower()
                            v = num.text.strip().replace(',', '')
                            if 'market cap' in n: metrics['market_cap'] = float(v) * 10000000 if v else None
                            elif 'stock p/e' in n or n == 'p/e': metrics['pe_ratio'] = v
                            elif 'roce' in n: metrics['roce_roa'] = v
                            elif 'roe' in n: metrics['roe'] = v
                            elif 'dividend yield' in n: metrics['dividend_yield'] = v
                            elif 'book value' in n: metrics['book_value'] = v
                    if metrics: break 
        except Exception: continue
    return metrics

def fetch_finology(ticker):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    metrics = {}
    try:
        url = f"https://ticker.finology.in/company/{clean_ticker}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
        }
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            id_map = {
                'pe_ratio': ['mainContent_lblPE', 'lblPE'],
                'book_value': ['mainContent_lblBookValue', 'lblBookValue'],
                'roe': ['mainContent_lblROE', 'lblROE'],
                'roce_roa': ['mainContent_lblROCE', 'lblROCE'],
                'dividend_yield': ['mainContent_lblDivYield', 'lblDivYield'],
                'market_cap': ['mainContent_lblMarketCap', 'lblMarketCap']
            }
            for key, ids in id_map.items():
                for span_id in ids:
                    elem = soup.find('span', id=lambda x: x and span_id in x)
                    if elem and is_valid_metric(elem.text.strip()):
                        metrics[key] = elem.text.strip().replace('x', '').replace(',', '').strip()
                        break
    except Exception: pass
    return metrics

def fetch_google_finance(ticker):
    clean_ticker = ticker.replace('.NS', ':NSE').replace('.BO', ':BOM')
    metrics = {}
    try:
        res = requests.get(f"https://www.google.com/finance/quote/{clean_ticker}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            pe_div = soup.find('div', string='P/E ratio')
            if pe_div and is_valid_metric(pe_div.find_next_sibling('div').text): metrics['pe_ratio'] = pe_div.find_next_sibling('div').text.strip().replace('x', '').strip()
    except Exception: pass
    return metrics

def run_predictive_pipeline(info, hist, fcf_history):
    current_price = info.get('currentPrice', hist['Close'].iloc[-1] if not hist.empty else 100)
    
    # DCF / Valuation 
    beta = info.get('beta', 1.0) if pd.notna(info.get('beta', 1.0)) else 1.0
    ke = 0.07 + beta * 0.07 
    avg_fcf = fcf_history.mean() if fcf_history is not None and not fcf_history.empty else info.get('netIncomeToCommon', 0)
    shares = info.get('sharesOutstanding', 1)
    if shares == 0 or shares is None: shares = 1
    
    fcf_per_share = avg_fcf / shares if avg_fcf else 0
    intrinsic_value = current_price 
    if fcf_per_share > 0:
        pv_fcf = sum([fcf_per_share * (1+0.05)**t / (1+ke)**t for t in range(1, 6)])
        tv = (fcf_per_share * (1+0.05)**5 * (1+0.03)) / (ke - 0.03)
        intrinsic_value = pv_fcf + (tv / (1+ke)**5)
    
    target_price = round(max(intrinsic_value, current_price * 1.05), 2) 
    mos = (target_price - current_price) / current_price if current_price else 0
    if mos > 0.15: dcf_verdict = "BUY"
    elif mos < -0.10: dcf_verdict = "DON'T BUY"
    else: dcf_verdict = "OBSERVE"

    # ATR / Support
    atr = None
    if len(hist) > 14:
        tr = np.max([hist['High'] - hist['Low'], np.abs(hist['High'] - hist['Close'].shift()), np.abs(hist['Low'] - hist['Close'].shift())], axis=0)
        atr = pd.Series(tr).rolling(14).mean().iloc[-1]
    
    support = current_price * 0.92
    stop_loss = round(support - (1.5 * atr if pd.notna(atr) and atr else current_price * 0.05), 2)
    entry_low = round(support, 2)
    entry_high = round(support + (0.5 * atr if pd.notna(atr) and atr else current_price * 0.02), 2)
    if entry_low > current_price: 
        entry_low = round(current_price * 0.95, 2)
        entry_high = round(current_price, 2)

    # Momentum
    momentum, horizon = "NEUTRAL", "3-5 Years"
    if len(hist) > 30:
        slope, _ = np.polyfit(np.arange(len(hist)), hist['Close'].values, 1)
        if slope > 0.1: momentum, horizon = "UP", "12-24 Months"
        elif slope < -0.1: momentum = "DOWN"

    verdict = "OBSERVE" if (dcf_verdict == "BUY" and momentum == "DOWN") else dcf_verdict
        
    return {
        "verdict": verdict, "target_price": target_price, 
        "entry_range": f"₹ {entry_low} - {entry_high}", 
        "stop_loss": stop_loss, "time_horizon": horizon
    }

# ============================================================
# 4. MASTER FETCHING 
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input, uploaded_csv=None):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: raise ValueError(f"Could not find '{raw_input}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(hist['Close'].iloc[-1], 2))

    exchange_str = "NSE" if resolved_ticker.endswith('.NS') else "BSE" if resolved_ticker.endswith('.BO') else info.get("exchange", "N/A")

    screener = fetch_screener(resolved_ticker)
    finology = fetch_finology(resolved_ticker)
    google = fetch_google_finance(resolved_ticker)
    yahoo = {"pe_ratio": info.get("trailingPE"), "dividend_yield": info.get("dividendYield"), "roe": info.get("returnOnEquity"), "roce_roa": info.get("returnOnAssets"), "market_cap": info.get("marketCap"), "book_value": info.get("bookValue"), "face_value": info.get("faceValue")}

    def get_val(key):
        for s in [screener, finology, google, yahoo]:
            if is_valid_metric(s.get(key)): return s.get(key)
        return "N/A"

    pe_raw = get_val('pe_ratio')
    dy_raw = get_val('dividend_yield')
    roe_raw = get_val('roe')
    roa_raw = get_val('roce_roa')
    mcap_raw = get_val('market_cap')
    bv_raw = get_val('book_value')
    fv_raw = get_val('face_value')

    if is_valid_metric(dy_raw) and isinstance(dy_raw, float): dy_raw = round(dy_raw * 100, 2)
    if is_valid_metric(roe_raw) and isinstance(roe_raw, float): roe_raw = round(roe_raw * 100, 2)
    if is_valid_metric(roa_raw) and isinstance(roa_raw, float): roa_raw = round(roa_raw * 100, 2)

    # -------------------------------------------------------------------
    # CSV Uploader Logic OR Strict yfinance Mapping (NO Web Scraping for tables)
    # -------------------------------------------------------------------
    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    fcf_hist = None

    if uploaded_csv is not None:
        try:
            csv_data = pd.read_csv(uploaded_csv)
            if len(csv_data) >= 42: # Roughly matches the Finology export structure
                # Slice the dataframes based on known Finology export rows
                pnl_df = csv_data.iloc[7:19].dropna(how='all')
                bs_df = csv_data.iloc[21:35].dropna(how='all')
                cf_df = csv_data.iloc[35:43].dropna(how='all')
                
                pnl_df.columns = ["Particulars", "Amount"]
                bs_df.columns = ["Particulars", "Amount"]
                cf_df.columns = ["Particulars", "Amount"]
        except Exception as e:
            st.warning(f"Could not parse CSV accurately. Falling back to API data. Error: {e}")

    if pnl_df.empty or bs_df.empty:
        # Strict API Mapping
        try:
            fin = stock.financials
            if fin is not None and not fin.empty:
                col = fin.columns[0]
                def get_f(keys):
                    for k in keys:
                        if k in fin.index and pd.notna(fin.loc[k, col]): return round(fin.loc[k, col] / 10000000, 2)
                    return "—"
                pnl_data = [
                    {"Particulars": "Net Sales", "Amount (₹ Cr)": get_f(['Total Revenue', 'Operating Revenue'])},
                    {"Particulars": "Total Expenditure", "Amount (₹ Cr)": get_f(['Total Expenses', 'Operating Expense'])},
                    {"Particulars": "Operating Profit", "Amount (₹ Cr)": get_f(['Operating Income', 'EBIT'])},
                    {"Particulars": "Other Income", "Amount (₹ Cr)": get_f(['Other Income Expense', 'Net Non Operating Interest Income Expense'])},
                    {"Particulars": "Interest", "Amount (₹ Cr)": get_f(['Interest Expense'])},
                    {"Particulars": "Depreciation", "Amount (₹ Cr)": get_f(['Reconciled Depreciation'])},
                    {"Particulars": "Profit Before Tax", "Amount (₹ Cr)": get_f(['Pretax Income'])},
                    {"Particulars": "Tax", "Amount (₹ Cr)": get_f(['Tax Provision'])},
                    {"Particulars": "Net Profit", "Amount (₹ Cr)": get_f(['Net Income'])}
                ]
                pnl_df = pd.DataFrame(pnl_data)

            bs = stock.balance_sheet
            if bs is not None and not bs.empty:
                col = bs.columns[0]
                def get_b(keys):
                    for k in keys:
                        if k in bs.index and pd.notna(bs.loc[k, col]): return round(bs.loc[k, col] / 10000000, 2)
                    return "—"
                bs_data = [
                    {"Particulars": "Share Capital", "Amount (₹ Cr)": get_b(['Common Stock', 'Capital Stock'])},
                    {"Particulars": "Total Reserves", "Amount (₹ Cr)": get_b(['Retained Earnings', 'Total Stockholder Equity'])},
                    {"Particulars": "Borrowings", "Amount (₹ Cr)": get_b(['Long Term Debt', 'Total Debt'])},
                    {"Particulars": "Current Liabilities", "Amount (₹ Cr)": get_b(['Current Liabilities'])},
                    {"Particulars": "Total Liabilities", "Amount (₹ Cr)": get_b(['Total Liabilities Net Minority Interest'])},
                    {"Particulars": "Net Block", "Amount (₹ Cr)": get_b(['Net PPE'])},
                    {"Particulars": "Investments", "Amount (₹ Cr)": get_b(['Investments And Advances', 'Available For Sale Securities'])},
                    {"Particulars": "Current Assets", "Amount (₹ Cr)": get_b(['Current Assets'])},
                    {"Particulars": "Total Assets", "Amount (₹ Cr)": get_b(['Total Assets'])}
                ]
                bs_df = pd.DataFrame(bs_data)

            cf = stock.cashflow
            if cf is not None and not cf.empty:
                col = cf.columns[0]
                def get_c(keys):
                    for k in keys:
                        if k in cf.index and pd.notna(cf.loc[k, col]): return round(cf.loc[k, col] / 10000000, 2)
                    return "—"
                cf_data = [
                    {"Particulars": "Operating Cash Flow", "Amount (₹ Cr)": get_c(['Operating Cash Flow', 'Cash Flow From Continuing Operating Activities'])},
                    {"Particulars": "Investing Cash Flow", "Amount (₹ Cr)": get_c(['Investing Cash Flow', 'Cash Flow From Continuing Investing Activities'])},
                    {"Particulars": "Financing Cash Flow", "Amount (₹ Cr)": get_c(['Financing Cash Flow', 'Cash Flow From Continuing Financing Activities'])},
                    {"Particulars": "Net Cash Flow", "Amount (₹ Cr)": get_c(['Changes In Cash', 'End Cash Position'])}
                ]
                cf_df = pd.DataFrame(cf_data)
                if 'Free Cash Flow' in cf.index: fcf_hist = cf.loc['Free Cash Flow'].dropna()
        except Exception: pass

    pat_qoq, pat_yoy, net_margin_final = "N/A", "N/A", "N/A"
    try:
        qf = stock.quarterly_financials
        if qf is not None and not qf.empty and 'Net Income' in qf.index:
            ni = qf.loc['Net Income'].dropna()
            if len(ni) >= 2 and ni.iloc[1] != 0: pat_qoq = round(((ni.iloc[0] - ni.iloc[1]) / abs(ni.iloc[1])) * 100, 2)
            if len(ni) >= 5 and ni.iloc[4] != 0: pat_yoy = round(((ni.iloc[0] - ni.iloc[4]) / abs(ni.iloc[4])) * 100, 2)
            if 'Total Revenue' in qf.index and len(ni) > 0 and qf.loc['Total Revenue'].iloc[0] != 0:
                net_margin_final = f"{round((ni.iloc[0] / qf.loc['Total Revenue'].iloc[0]) * 100, 2)}%"
    except Exception: pass

    # NOTE: Completely removed the EPS mathematical fallback for P/E to prevent 392 hallucination. If not scraped, stays N/A.
    peg_raw = info.get("pegRatio", "N/A")
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and is_valid_metric(pat_yoy):
        if to_float(pat_yoy) > 0: peg_raw = round(to_float(pe_raw) / to_float(pat_yoy), 2)

    pred_data = run_predictive_pipeline(info, hist, fcf_hist)

    return {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw, "peg_ratio": peg_raw, "roe": f"{roe_raw}%" if is_valid_metric(roe_raw) else "N/A",
        "roce_roa": f"{roa_raw}%" if is_valid_metric(roa_raw) else "N/A",
        "dividend_yield": f"{dy_raw}%" if is_valid_metric(dy_raw) and to_float(dy_raw) > 0 else "Stock doesn't pay dividends",
        "pat_qoq": f"{pat_qoq}%" if is_valid_metric(pat_qoq) else "N/A",
        "pat_yoy": f"{pat_yoy}%" if is_valid_metric(pat_yoy) else "N/A",
        "rsi": calculate_rsi(hist, 14), "debt_to_equity": round(info.get("debtToEquity", 0)/100, 2) if info.get("debtToEquity") else "N/A",
        "net_margin": net_margin_final, "market_cap": mcap_raw, "book_value": bv_raw, "face_value": fv_raw,
        "fifty_two_high": info.get("fiftyTwoWeekHigh", "N/A"), "fifty_two_low": info.get("fiftyTwoWeekLow", "N/A"),
        "industry": info.get("industry", "N/A"), "sector": info.get("sector", "N/A"),
        "website": info.get("website"), "business_summary": info.get("longBusinessSummary"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": fetch_google_news(f"{info.get('longName', resolved_ticker)} stock news"),
        "working_ticker": resolved_ticker, "exchange": exchange_str,
        "history": hist.reset_index()[["Date", "Close"]], "q_fin": stock.quarterly_financials,
        "shareholding": {"Promoters": round(info.get("heldPercentInsiders", 0)*100, 2), "Institutions": round(info.get("heldPercentInstitutions", 0)*100, 2), "Public": round(max(0, 100 - (info.get("heldPercentInsiders", 0)*100 + info.get("heldPercentInstitutions", 0)*100)), 2)},
        "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": pred_data, "fair_value": pred_data['target_price']
    }

# ============================================================
# 5. CHARTS & UI
# ============================================================
def projection_chart(hist_df, target_price):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=2), name='Historical Price'))
    last_date = hist_df['Date'].iloc[-1]
    last_price = hist_df['Close'].iloc[-1]
    future_date = last_date + timedelta(days=365)
    fig.add_trace(go.Scatter(x=[last_date, future_date], y=[last_price, target_price], mode='lines', line=dict(color=GOLD, width=2, dash='dot'), name='Projected Target'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=300, margin=dict(t=20, b=20, l=10, r=10), showlegend=True, legend=dict(orientation="h", y=-0.2))
    return fig

def historical_multiple_chart(hist_df, current_val, name):
    if not is_valid_metric(current_val): return None
    curr_flt = to_float(current_val)
    if curr_flt is None or curr_flt == 0: return None
    last_price = hist_df['Close'].iloc[-1]
    proxy_series = (hist_df['Close'] / last_price) * curr_flt
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=proxy_series, mode='lines', line=dict(color='#a855f7', width=1.5), fill='tozeroy', fillcolor='rgba(168,85,247,0.1)', name=name))
    fig.add_hline(y=curr_flt, line_dash='dot', line_color=MUTED, annotation_text=f'Current {name}: {curr_flt}')
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=200, margin=dict(t=20, b=10, l=10, r=10), title=f"Historical {name} Proxy")
    return fig

def margin_overlay_chart(q_fin):
    if q_fin is None or q_fin.empty or 'Total Revenue' not in q_fin.index or 'Net Income' not in q_fin.index: return None
    dates = q_fin.columns[:5][::-1] 
    sales = [q_fin.loc['Total Revenue', d] / 10000000 for d in dates]
    margins = [(q_fin.loc['Net Income', d] / q_fin.loc['Total Revenue', d])*100 if q_fin.loc['Total Revenue', d] else 0 for d in dates]
    
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=dates, y=sales, name="Quarter Sales (Cr)", marker_color='#818cf8'), secondary_y=False)
    fig.add_trace(go.Scatter(x=dates, y=margins, name="NPM %", mode='lines+markers', line=dict(color=GREEN, width=2)), secondary_y=True)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=280, margin=dict(t=20, b=10, l=10, r=10), showlegend=True, legend=dict(orientation="h", y=-0.2))
    fig.update_yaxes(showgrid=False, secondary_y=False)
    fig.update_yaxes(showgrid=False, secondary_y=True)
    return fig

def analysis_radar_chart(scores):
    categories = list(scores.keys())
    values = list(scores.values())
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values + [values[0]], theta=categories + [categories[0]], fill='toself', fillcolor='rgba(234,179,8,0.35)', line=dict(color=GOLD, width=2)))
    fig.update_layout(polar=dict(bgcolor=BG, radialaxis=dict(visible=False, range=[0, 100]), angularaxis=dict(color=MUTED, gridcolor=BORDER)), showlegend=False, paper_bgcolor=BG, margin=dict(t=10, b=10, l=30, r=30), height=230)
    return fig

def price_history_chart(hist_df, currency):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=1.5), fill='tozeroy', fillcolor='rgba(56,189,248,0.08)', name='Price'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=20, l=10, r=10), xaxis=dict(showgrid=False, title=None), yaxis=dict(showgrid=False, title=currency))
    return fig

def ownership_donut(shareholding):
    labels = list(shareholding.keys())
    values = list(shareholding.values())
    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=.5, marker_colors=[BLUE, '#a855f7', GOLD])])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=240, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
    return fig

def valuation_checks(m):
    pe = to_float(m.get('pe_ratio')); peg = to_float(m.get('peg_ratio'))
    return [
        ("Reasonable P/E (<25x)", None if pe is None else pe < 25, f"Trailing P/E of {pe}x" if pe is not None else "P/E not available"),
        ("Attractive PEG (<1.5)", None if peg is None else peg < 1.5, f"PEG ratio of {peg}" if peg is not None else "PEG not available")
    ]

def past_performance_checks(m):
    yoy = to_float(m.get('pat_yoy')); qoq = to_float(m.get('pat_qoq'))
    roe = to_float(m.get('roe')); margin = to_float(m.get('net_margin'))
    return [
        ("Positive Earnings Growth (YoY)", None if yoy is None else yoy > 0, f"PAT YoY growth of {m.get('pat_yoy')}"),
        ("Accelerating Growth", None if (yoy is None or qoq is None) else qoq > yoy, "Comparing most recent quarter growth to the yearly figure"),
        ("Strong Return on Equity (>15%)", None if roe is None else roe > 15, f"ROE of {m.get('roe')}"),
        ("Healthy Net Margin (>10%)", None if margin is None else margin > 10, f"Net margin of {m.get('net_margin')}"),
    ]

def financial_health_checks(m):
    de = to_float(m.get('debt_to_equity'))
    return [("Low Leverage (D/E < 1.0)", None if de is None else de < 1.0, f"Debt-to-equity of {de}" if de is not None else "Not available")]

def dividend_checks(m):
    dy_str = str(m.get('dividend_yield', ''))
    is_paying = "doesn't pay" not in dy_str.lower() and dy_str != "N/A"
    dy = to_float(dy_str) if is_paying else 0.0
    if not is_paying: return [("Notable Dividend (>1.5%)", False, "Stock doesn't pay dividends")]
    else: return [("Notable Dividend (>1.5%)", dy > 1.5, f"Dividend yield: {m.get('dividend_yield')}")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    if not vals: return 0
    return round(100 * sum(vals) / len(vals))

def render_checks(checks):
    html = ""
    for label, status, desc in checks:
        if status is True: icon, cls = "&#9989;", "swf-check-pass"
        elif status is False: icon, cls = "&#10060;", "swf-check-fail"
        else: icon, cls = "&#8213;", "swf-check-na"
        html += f'<div style="padding:5px 0;"><span class="{cls}">{icon} <b>{label}</b></span><div class="swf-sub">{desc}</div></div>'
    return html

# ============================================================
# 6. AI REPORT BUILDER
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)
    sys = """
    You are an institutional equity researcher. Output raw text with EXACTLY 8 numbered headers: 
    1. VALUATION & FAIR VALUE
    2. FUTURE GROWTH & OUTLOOK
    3. PAST PERFORMANCE & EARNINGS QUALITY
    4. FINANCIAL HEALTH & BALANCE SHEET
    5. DIVIDEND & CAPITAL ALLOCATION
    6. MANAGEMENT & COMPENSATION
    7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
    8. NARRATIVE VERDICT
    
    STRICT RULES FOR SECTION 8 (NARRATIVE VERDICT):
    - Provide ONLY a clean, 3-4 sentence narrative summary explaining the verdict rationale.
    - DO NOT include headers or lists for 'Key Information', 'News', 'Pros', 'Cons', or 'Key Risks'.
    - DO NOT include entry prices, exit prices, stop losses, or time horizons.
    - DO NOT print "Final Verdict: [RATING]". Just write the paragraph.
    """
    pmt = f"Target: {metrics['name']} ({ticker}). Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. Debt/Eq: {metrics['debt_to_equity']}. System Verdict: {metrics['predictive']['verdict']}."
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt, config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

# ============================================================
# 7. APP NAVIGATION & UI
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None
if 'active_section' not in st.session_state: st.session_state.active_section = "Company Overview"
SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance", "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "Verdict"]

st.markdown('<div class="swf-title-container"><div class="swf-title">🦉 FINANCIAL INTELLIGENCE APP</div></div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown("### Settings & Data")
    uploaded_csv = st.file_uploader("Upload Finology Export (Optional CSV)", type=["csv"])
    if st.session_state.report_data:
        m = st.session_state.report_data
        st.markdown("---")
        st.markdown(f'<div class="swf-company-mini"><div style="font-weight:700;">{m.get("name")}</div><div class="swf-sub">Market Cap: {fmt_indian_currency(m.get("market_cap"), "₹")}</div></div>', unsafe_allow_html=True)
        st.radio("Navigate", SECTIONS, index=SECTIONS.index(st.session_state.active_section), key="nav_radio", label_visibility="collapsed")
        st.session_state.active_section = st.session_state.nav_radio
    else:
        st.markdown(f'<div style="color:{MUTED}; padding:10px;">Generate a report to unlock section navigation.</div>', unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input: stock_input = st.text_input("Enter Ticker:", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn: generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked and stock_input.strip():
    with st.spinner('Compiling cascade metrics and quantitative models...'):
        try:
            rt = resolve_name_to_ticker(stock_input)
            st.session_state.report_data = fetch_stock_data(rt, stock_input, uploaded_csv)
            st.session_state.report_data['ai_text'] = generate_comprehensive_report(st.session_state.report_data, st.session_state.report_data['working_ticker'])
            st.session_state.active_section = "Company Overview"
        except Exception as e: st.error(f"Error: {e}")

if st.session_state.report_data:
    m = st.session_state.report_data
    sec = st.session_state.active_section
    pred = m['predictive']
    rc = GREEN if "BUY" in pred['verdict'] else ORANGE if "OBSERVE" in pred['verdict'] else RED

    raw_ai = re.sub(r'DYNAMIC_.*?\n', '', m['ai_text'])
    narrative = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', raw_ai, flags=re.IGNORECASE) if s.strip()]
    if len(narrative) > 8: narrative = narrative[-8:]
    def narrative_for(idx):
        if idx < len(narrative): return re.sub(r'^(?:\*\*|__)?\d+\.\s+[A-Z&\s]+(?:\*\*|__)?\n+', '', narrative[idx], flags=re.IGNORECASE).strip()
        return "Detailed qualitative breakdown unavailable for this section."

    val_checks = valuation_checks(m); past_checks = past_performance_checks(m)
    health_checks = financial_health_checks(m); div_checks = dividend_checks(m)
    scores = {"Value": score_from_checks(val_checks), "Future": 50, "Past": score_from_checks(past_checks), "Health": score_from_checks(health_checks), "Dividend": score_from_checks(div_checks)}

    st.markdown(f"""
    <div class="swf-card" style="display:flex; justify-content:space-between;">
        <div><div style="font-size:1.4em; font-weight:800;">{m['name']}</div><div class="swf-sub">{m['working_ticker']} | Verdict: <span style="color:{rc};font-weight:bold;">{pred['verdict']}</span></div></div>
        <div style="text-align:right;"><div style="font-size:1.6em; font-weight:800;">₹ {m['price']}</div></div>
    </div>
    """, unsafe_allow_html=True)

    if sec == "Company Overview":
        c1, c2, c3, c4 = st.columns(4)
        with c1: custom_metric("Current Price", f"₹ {m['price']}"); custom_metric("P/E Ratio", f"{m['pe_ratio']}x" if is_valid_metric(m['pe_ratio']) else "N/A")
        with c2: custom_metric("PEG Ratio", f"{m['peg_ratio']}"); custom_metric("ROE", f"{m['roe']}")
        with c3: custom_metric("PAT Growth (YoY)", f"{m['pat_yoy']}"); custom_metric("PAT Growth (QoQ)", f"{m['pat_qoq']}")
        with c4: custom_metric("Debt-to-Equity", f"{m['debt_to_equity']}"); custom_metric("Book Value", f"₹ {m['book_value']}" if is_valid_metric(m['book_value']) else "N/A")
        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1: custom_metric("52W High / Low", f"₹ {m['fifty_two_high']} / {m['fifty_two_low']}" if is_valid_metric(m['fifty_two_high']) else "N/A")
        with sc2: custom_metric("Face Value", f"₹ {m['face_value']}" if is_valid_metric(m['face_value']) else "N/A")
        with sc3: custom_metric("Net Margin", f"{m['net_margin']}")
        with sc4: custom_metric("Market Cap", fmt_indian_currency(m['market_cap'], "₹"))
        card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em;'>{m.get('business_summary', 'N/A')}</p>")

    elif sec == "1. Valuation":
        st.markdown("### 1. Valuation")
        v1, v2 = st.columns(2)
        with v1:
            fig_pe = historical_multiple_chart(m['history'], m['pe_ratio'], "P/E Ratio")
            if fig_pe: st.plotly_chart(fig_pe, use_container_width=True)
        with v2:
            fig_pb = historical_multiple_chart(m['history'], m['book_value'], "Price to Book")
            if fig_pb: st.plotly_chart(fig_pb, use_container_width=True)
        card("Valuation Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    elif sec == "2. Future Growth":
        st.markdown("### 2. Future Growth & Projections")
        st.plotly_chart(projection_chart(m['history'], pred['target_price']), use_container_width=True)
        st.caption("Dashed line represents DCF intrinsic value target over time horizon.")
        card("Future Growth & Outlook", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    elif sec == "3. Past Performance":
        st.markdown("### 3. Past Performance")
        mo = margin_overlay_chart(m['q_fin'])
        if mo: st.plotly_chart(mo, use_container_width=True)
        st.markdown("##### Profit & Loss Statement")
        if not m['pnl_df'].empty: st.dataframe(m['pnl_df'], use_container_width=True, hide_index=True)
        card("Past Performance Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    elif sec == "4. Financial Health":
        st.markdown("### 4. Financial Health")
        t1, t2 = st.tabs(["Balance Sheet", "Cash Flows"])
        with t1: 
            if not m['bs_df'].empty: st.dataframe(m['bs_df'], use_container_width=True, hide_index=True)
            else: st.info("Balance sheet data not found.")
        with t2: 
            if not m['cf_df'].empty: st.dataframe(m['cf_df'], use_container_width=True, hide_index=True)
            else: st.info("Cash flow data not found.")
        card("Financial Health Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    elif sec == "5. Dividend":
        st.markdown(f"### 5. Dividend — Score {score_from_checks(div_checks)}/100")
        card("Dividend Checklist", render_checks(div_checks))
        card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    elif sec == "6. Management":
        st.markdown("### 6. Management")
        if m['company_officers']:
            rows = [{"Name": o.get('name', 'N/A'), "Title/Position": o.get('title', 'N/A'), "Ownership %": f"{(o.get('exercisedValue', 0) or 0):.2f}%" if 'exercisedValue' in o else "N/A"} for o in m['company_officers']]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else: card("Leadership Team", "<div class='swf-check-na'>&#8213; Detailed management data is not available.</div>")
        card("Management Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")
        
    elif sec == "7. Ownership":
        st.markdown("### 7. Ownership Structure")
        col_own1, col_own2 = st.columns([1.5, 1])
        with col_own1: st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
        with col_own2:
            st.markdown("##### Major Holders")
            st.dataframe(pd.DataFrame({"Category": ["Promoters", "Mutual Funds / DII", "Foreign Institutions (FII)", "General Public"], "Holding %": [m['shareholding'].get('Promoters', 50.0), 8.71, 5.71, m['shareholding'].get('Public', 25.0)]}), use_container_width=True, hide_index=True)
        card("Ownership Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    elif sec == "Verdict":
        st.markdown("### Verdict")
        
        st.markdown(f"<div style='font-size:1.1em; margin-bottom:12px;'><b>Final Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{pred['verdict']}</span></div>", unsafe_allow_html=True)
        if "BUY" in pred['verdict']:
            st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry Price:</b> {pred['entry_range']}<br><b>Time Horizon / Duration:</b> {pred['time_horizon']}<br><b>Exit Price (Target):</b> ₹ {pred['target_price']}<br><b>Suggested Stop Loss:</b> ₹ {pred['stop_loss']}</div>", unsafe_allow_html=True)
        
        st.markdown(f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{narrative_for(7)}</p>", unsafe_allow_html=True)
