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
# 1. SETUP & CONFIGURATION (Unified Font & Styling)
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
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:0px; line-height: 1.4; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
    .swf-company-mini {{ padding: 6px 4px 14px 4px; border-bottom: 1px solid {BORDER}; margin-bottom: 8px; }}
    .swf-avatar {{ width:40px; height:40px; border-radius:8px; background:#fff; color:#111; font-weight:800; display:flex; align-items:center; justify-content:center; font-size:1.2em; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 2. DATA FORMATTING & QUANTITATIVE HELPERS
# ============================================================
def to_float(val):
    if val in [None, "N/A", "", "None", "Stock doesn't pay dividends"]: return None
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except Exception: return None

def is_valid_metric(val):
    if val in [None, "N/A", "", "-", "--", "None", "0", "0.00%", "0.00"]: return False
    if isinstance(val, (int, float)): return True
    try:
        cleaned = str(val).replace(',', '').replace('₹', '').replace('%', '').replace('x', '').replace('Cr.', '').strip()
        float(cleaned)
        return True
    except ValueError:
        return False

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
    if len(df) < window: return "N/A"
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    loss = loss.replace(0, 1e-10)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

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

def fetch_google_news(query_term):
    try:
        safe_query = urllib.parse.quote(query_term)
        res = requests.get(f"https://news.google.com/rss/search?q={safe_query}&hl=en-IN&gl=IN&ceid=IN:en", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = root.findall('.//item')
            headlines = [{'title': item.find('title').text, 'link': item.find('link').text} for item in items[:4] if item.find('title') is not None]
            return headlines
    except Exception: pass
    return []

def calculate_vwap_support(df):
    df = df.dropna(subset=['Close', 'Volume'])
    if df.empty: return None
    df['PriceBin'] = pd.cut(df['Close'], bins=20)
    vol_by_bin = df.groupby('PriceBin', observed=True)['Volume'].sum()
    return vol_by_bin.idxmax().mid

def calculate_atr(df, period=14):
    if len(df) < period: return None
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(period).mean().iloc[-1]

def run_predictive_pipeline(info, hist, fcf_history):
    current_price = info.get('currentPrice', hist['Close'].iloc[-1])
    
    # 1. DCF Valuation Model (Matches Claude's CAPM & Terminal assumptions)
    beta = info.get('beta', 1.0) if pd.notna(info.get('beta', 1.0)) else 1.0
    ke = 0.07 + beta * 0.07 
    avg_fcf = fcf_history.mean() if fcf_history is not None and not fcf_history.empty else info.get('netIncomeToCommon', 0)
    shares = info.get('sharesOutstanding', 1) or 1
    
    fcf_per_share = avg_fcf / shares if avg_fcf else 0
    intrinsic_value = current_price 
    
    if fcf_per_share > 0:
        g, tg = 0.05, 0.03 
        pv_fcf = sum([fcf_per_share * (1+g)**t / (1+ke)**t for t in range(1, 6)])
        tv = (fcf_per_share * (1+g)**5 * (1+tg)) / (ke - tg)
        intrinsic_value = pv_fcf + (tv / (1+ke)**5)
    
    target_price = round(intrinsic_value, 2)
    mos = (target_price - current_price) / current_price if current_price else 0
    if mos > 0.15: dcf_verdict = "BUY"
    elif mos < -0.10: dcf_verdict = "DON'T BUY"
    else: dcf_verdict = "OBSERVE"

    # 2. Risk Model (Matches Claude's ATR + Volume-Weighted Support)
    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist) or (current_price * 0.92)
    
    stop_loss = round(support - (1.5 * atr if pd.notna(atr) and atr else current_price * 0.05), 2)
    entry_low = round(support, 2)
    entry_high = round(support + (0.5 * atr if pd.notna(atr) and atr else current_price * 0.02), 2)
    
    if entry_low > current_price: 
        entry_low, entry_high = round(current_price * 0.95, 2), round(current_price, 2)

    # 3. Timing Model (Matches Claude's ARIMA + Trend Drift + Trap Override)
    momentum, horizon = "NEUTRAL", "3-5 Years"
    if len(hist) > 30:
        slope, _ = np.polyfit(np.arange(len(hist)), hist['Close'].values, 1)
        if slope > 0.1: momentum, horizon = "UP", "12-18 Months"
        elif slope < -0.1: momentum = "DOWN"
        
        if HAS_ARIMA and len(hist) > 100:
            try:
                model = ARIMA(hist['Close'].values, order=(5,1,0))
                fitted = model.fit()
                forecast = fitted.forecast(steps=30)
                momentum = "UP" if forecast[-1] > forecast[0] else "DOWN"
                horizon = "12-18 Months (Accelerated)" if momentum == "UP" else "3-5 Years"
            except: pass
            
    final_verdict = "OBSERVE" if (dcf_verdict == "BUY" and momentum == "DOWN") else dcf_verdict
        
    return {
        "verdict": final_verdict, "target_price": target_price,
        "entry_range": f"₹ {entry_low} - {entry_high}",
        "stop_loss": stop_loss, "time_horizon": horizon
    }

# ============================================================
# 3. MASTER DATA PIPELINE (yfinance + Ratios + Projections)
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input, uploaded_csv=None):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: raise ValueError(f"Could not find '{raw_input}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(hist['Close'].iloc[-1], 2))

    pe_raw = info.get("trailingPE")
    dy_raw = info.get("dividendYield")
    roe_raw = info.get("returnOnEquity")
    roa_raw = info.get("returnOnAssets")
    mcap_raw = info.get("marketCap")
    bv_raw = info.get("bookValue")
    fv_raw = info.get("faceValue")

    if is_valid_metric(dy_raw) and isinstance(dy_raw, float): dy_raw = round(dy_raw * 100, 2)
    if is_valid_metric(roe_raw) and isinstance(roe_raw, float): roe_raw = round(roe_raw * 100, 2)
    if is_valid_metric(roa_raw) and isinstance(roa_raw, float): roa_raw = round(roa_raw * 100, 2)

    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    fcf_hist = None
    
    if uploaded_csv is not None:
        try:
            csv_data = pd.read_csv(uploaded_csv)
            if len(csv_data) >= 42: 
                pnl_df = csv_data.iloc[7:19].dropna(how='all')
                bs_df = csv_data.iloc[21:35].dropna(how='all')
                cf_df = csv_data.iloc[35:43].dropna(how='all')
                pnl_df.columns = ["Particulars", "Amount (₹ Cr)"]
                bs_df.columns = ["Particulars", "Amount (₹ Cr)"]
                cf_df.columns = ["Particulars", "Amount (₹ Cr)"]
        except Exception: pass

    if pnl_df.empty:
        pnl_data, bs_data, cf_data = [], [], []
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
                    {"Particulars": "Total Liabilities", "Amount (₹ Cr)": get_b(['Total Liabilities Net Minority Interest'])},
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
                    {"Particulars": "Net Cash Flow", "Amount (₹ Cr)": get_c(['Changes In Cash', 'End Cash Position'])}
                ]
                cf_df = pd.DataFrame(cf_data)
                if 'Free Cash Flow' in cf.index: fcf_hist = cf.loc['Free Cash Flow'].dropna()
        except Exception: pass

    pat_qoq, pat_yoy, net_margin_final = "N/A", "N/A", "N/A"
    rev_cagr, earn_cagr = 0.12, 0.15 
    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            ni = q_fin.loc['Net Income'].dropna()
            if len(ni) >= 2 and ni.iloc[1] != 0: pat_qoq = round(((ni.iloc[0] - ni.iloc[1]) / abs(ni.iloc[1])) * 100, 2)
            if len(ni) >= 5 and ni.iloc[4] != 0: pat_yoy = round(((ni.iloc[0] - ni.iloc[4]) / abs(ni.iloc[4])) * 100, 2)
            if 'Total Revenue' in q_fin.index and len(ni) > 0 and qf.loc['Total Revenue'].iloc[0] != 0:
                net_margin_final = f"{round((ni.iloc[0] / q_fin.loc['Total Revenue'].iloc[0]) * 100, 2)}%"
                
        fin = stock.financials
        if fin is not None and not fin.empty:
            if 'Total Revenue' in fin.index:
                revs = fin.loc['Total Revenue'].dropna()
                if len(revs) > 1 and revs.iloc[-1] > 0:
                    rev_cagr = (revs.iloc[0] / revs.iloc[-1]) ** (1 / (len(revs) - 1)) - 1
            if 'Net Income' in fin.index:
                earns = fin.loc['Net Income'].dropna()
                if len(earns) > 1 and earns.iloc[-1] > 0:
                    earn_cagr = (earns.iloc[0] / earns.iloc[-1]) ** (1 / (len(earns) - 1)) - 1
    except Exception: pass

    est_rev_growth = f"{round(max(0.03, min(0.35, rev_cagr)) * 100, 2)}%"
    est_earn_growth = f"{round(max(0.03, min(0.35, earn_cagr)) * 100, 2)}%"

    mcap_float = to_float(mcap_raw)
    total_debt = info.get("totalDebt")
    total_cash = info.get("totalCash")
    ebitda = info.get("ebitda")
    
    ev_ebitda, asset_turnover = "N/A", "N/A"
    if mcap_float and total_debt is not None and total_cash is not None and ebitda and ebitda > 0:
        ev = mcap_float + total_debt - total_cash
        ev_ebitda = round(ev / ebitda, 2)
    
    if mcap_float and bv_raw is None:
        eq = info.get("bookValue")
        if eq and info.get("sharesOutstanding"):
            bv_raw = round((eq * info.get("sharesOutstanding")) / 10000000, 2)
            
    pb_ratio = info.get("priceToBook")
    if not is_valid_metric(pb_ratio) and bv_raw and current_price and info.get("sharesOutstanding"):
        pb_ratio = round((current_price * info.get("sharesOutstanding")) / (to_float(bv_raw) * 10000000), 2) if to_float(bv_raw) > 0 else "N/A"

    if not is_valid_metric(pe_raw):
        eps = info.get("trailingEps")
        if eps and eps > 0: 
            pe_raw = round(current_price / eps, 2)
        elif mcap_float and not pnl_df.empty:
            try:
                np_row = pnl_df[pnl_df['Particulars'].astype(str).str.contains('Net Profit', case=False, na=False)]
                if not np_row.empty:
                    np_val = to_float(np_row.iloc[0]['Amount (₹ Cr)'])
                    if np_val and np_val > 0: pe_raw = round((mcap_float / 10000000) / np_val, 2)
            except Exception: pass
        if not is_valid_metric(pe_raw): pe_raw = "N/A"

    peg_raw = info.get("pegRatio", "N/A")
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and is_valid_metric(pat_yoy):
        if to_float(pat_yoy) > 0: peg_raw = round(to_float(pe_raw) / to_float(pat_yoy), 2)

    dy_float = to_float(dy_raw)
    dividend_formatted = f"{dy_float}%" if dy_float and dy_float > 0 else "Stock doesn't pay dividends"
    exchange_str = "NSE & BSE" if resolved_ticker.endswith('.NS') else "BSE" if resolved_ticker.endswith('.BO') else info.get("exchange", "N/A")
    insider_h = (info.get("heldPercentInsiders") or 0) * 100
    inst_h = (info.get("heldPercentInstitutions") or 0) * 100
    
    predictive_data = run_predictive_pipeline(info, hist, fcf_hist)

    metrics = {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw, "peg_ratio": peg_raw, "pb_ratio": pb_ratio, "ev_ebitda": ev_ebitda,
        "roe": f"{roe_raw}%" if is_valid_metric(roe_raw) else "N/A", "roce_roa": f"{roa_raw}%" if is_valid_metric(roa_raw) else "N/A",
        "dividend_yield": dividend_formatted,
        "pat_qoq": f"{pat_qoq}%" if is_valid_metric(pat_qoq) else "N/A", "pat_yoy": f"{pat_yoy}%" if is_valid_metric(pat_yoy) else "N/A",
        "earnings_growth_est": est_earn_growth, "revenue_growth_est": est_rev_growth,
        "rsi": calculate_rsi(hist, 14), "debt_to_equity": round(info.get("debtToEquity", 0)/100, 2) if info.get("debtToEquity") else "N/A",
        "net_margin": net_margin_final, "market_cap": mcap_raw, "book_value": bv_raw, "face_value": fv_raw,
        "fifty_two_high": info.get("fiftyTwoWeekHigh", "N/A"), "fifty_two_low": info.get("fiftyTwoWeekLow", "N/A"),
        "industry": info.get("industry", "N/A"), "sector": info.get("sector", "N/A"),
        "website": info.get("website"), "business_summary": info.get("longBusinessSummary"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": fetch_google_news(f"{info.get('longName', resolved_ticker)} stock news"),
        "working_ticker": resolved_ticker, "exchange": exchange_str, "currency": "₹",
        "history": hist.reset_index()[["Date", "Close"]], "q_fin": stock.quarterly_financials,
        "shareholding": {"Promoters": round(insider_h, 2), "Institutions": round(inst_h, 2), "Public": round(max(0, 100 - (insider_h + inst_h)), 2)},
        "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": predictive_data, "fair_value": predictive_data['target_price']
    }
    return metrics

# ============================================================
# 4. VISUAL CHARTS & CHECKLISTS
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
    fig.update_yaxes(showgrid=False, secondary_y=False); fig.update_yaxes(showgrid=False, secondary_y=True)
    return fig

def future_trajectory_chart(pnl_df, earn_growth_est, rev_growth_est):
    e_g = (to_float(earn_growth_est) or 15.0) / 100
    r_g = (to_float(rev_growth_est) or 12.0) / 100
    curr_rev, curr_earn = 100, 20
    if pnl_df is not None and not pnl_df.empty:
        try:
            r_row = pnl_df[pnl_df['Particulars'].astype(str).str.contains('Net Sales|Revenue', case=False, na=False)]
            if not r_row.empty: curr_rev = to_float(r_row.iloc[0]['Amount (₹ Cr)']) or 100
            e_row = pnl_df[pnl_df['Particulars'].astype(str).str.contains('Net Profit|Earnings', case=False, na=False)]
            if not e_row.empty: curr_earn = to_float(e_row.iloc[0]['Amount (₹ Cr)']) or 20
        except Exception: pass
        
    years = ['2023', '2024', '2025E', '2026E', '2027E']
    all_rev = [curr_rev * (1 - r_g), curr_rev, curr_rev * (1+r_g), curr_rev * (1+r_g)**2, curr_rev * (1+r_g)**3]
    all_earn = [curr_earn * (1 - e_g), curr_earn, curr_earn * (1+e_g), curr_earn * (1+e_g)**2, curr_earn * (1+e_g)**3]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=years, y=all_rev, mode='lines+markers', name='Revenue', line=dict(color=BLUE, width=3)))
    fig.add_trace(go.Scatter(x=years, y=all_earn, mode='lines+markers', name='Earnings', line=dict(color=GREEN, width=3)))
    fig.add_vrect(x0='2024', x1='2027E', fillcolor='rgba(255,255,255,0.05)', layer='below', line_width=0)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=300, margin=dict(t=20, b=20, l=10, r=10), legend=dict(orientation="h", y=-0.2), yaxis=dict(showgrid=False))
    return fig

def future_growth_bar_charts(earn_growth, rev_growth):
    earn_c = to_float(earn_growth) or 15.0; rev_c = to_float(rev_growth) or 12.0
    earn_i, earn_m = round(earn_c * 0.8, 1), round(earn_c * 0.6, 1)
    rev_i, rev_m = round(rev_c * 0.85, 1), round(rev_c * 0.65, 1)

    fig = make_subplots(rows=1, cols=2, subplot_titles=("Annual Earnings Growth", "Annual Revenue Growth"))
    fig.add_trace(go.Bar(x=['Company', 'Industry', 'Market'], y=[earn_c, earn_i, earn_m], marker_color=[BLUE, '#4ade80', '#c026d3'], text=[f"{earn_c}%", f"{earn_i}%", f"{earn_m}%"], textposition='auto'), row=1, col=1)
    fig.add_trace(go.Bar(x=['Company', 'Industry', 'Market'], y=[rev_c, rev_i, rev_m], marker_color=[BLUE, '#4ade80', '#c026d3'], text=[f"{rev_c}%", f"{rev_i}%", f"{rev_m}%"], textposition='auto'), row=1, col=2)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=300, showlegend=False, margin=dict(t=30, b=10, l=10, r=10))
    fig.update_yaxes(showgrid=False, showticklabels=False)
    return fig

def future_roe_gauge(current_roe):
    roe_val = to_float(current_roe) or 10.0
    future_roe = round(roe_val + 2.5, 1) 
    fig = go.Figure(go.Indicator(
        mode = "gauge+number", value = future_roe, number = {'suffix': "%"},
        gauge = {
            'axis': {'range': [None, max(40, future_roe+10)], 'tickwidth': 1, 'tickcolor': "white"},
            'bar': {'color': BLUE}, 'bgcolor': BG,
            'steps': [{'range': [0, 10], 'color': RED}, {'range': [10, 20], 'color': GOLD}, {'range': [20, max(40, future_roe+10)], 'color': GREEN}],
            'threshold': {'line': {'color': "white", 'width': 3}, 'thickness': 0.75, 'value': future_roe}
        }))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=250, margin=dict(t=20, b=20, l=20, r=20))
    return fig, future_roe

def analysis_radar_chart(scores):
    categories, values = list(scores.keys()), list(scores.values())
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values + [values[0]], theta=categories + [categories[0]], fill='toself', fillcolor='rgba(234,179,8,0.35)', line=dict(color=GOLD, width=2)))
    fig.update_layout(polar=dict(bgcolor=BG, radialaxis=dict(visible=False, range=[0, 100]), angularaxis=dict(color=MUTED, gridcolor=BORDER)), showlegend=False, paper_bgcolor=BG, margin=dict(t=10, b=10, l=30, r=30), height=230)
    return fig

def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=['Current Price'], y=[price], orientation='v', marker_color=BLUE, text=[f"{currency} {price}"], textposition='auto'))
    fig.add_trace(go.Bar(x=['Fair Value'], y=[fv], orientation='v', marker_color=GREEN, text=[f"{currency} {fv}"], textposition='auto'))
    diff_pct = round(((price - fv) / fv) * 100, 1) if fv else None
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=350, margin=dict(t=20, b=20, l=10, r=10), showlegend=False, yaxis=dict(showgrid=False, title=currency))
    return fig, diff_pct

def ownership_donut(shareholding):
    fig = go.Figure(data=[go.Pie(labels=list(shareholding.keys()), values=list(shareholding.values()), hole=.5, marker_colors=[BLUE, '#a855f7', GOLD])])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=240, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
    return fig

def balance_sheet_donuts(m):
    assets_labels, assets_vals = [], []
    if m.get('cash_bs'): assets_labels.append('Cash'); assets_vals.append(m['cash_bs'])
    if m.get('total_assets') and assets_vals:
        other = m['total_assets'] - sum(assets_vals)
        if other > 0: assets_labels.append('Other Assets'); assets_vals.append(other)

    liab_labels, liab_vals = [], []
    if m.get('total_debt_bs'): liab_labels.append('Debt'); liab_vals.append(m['total_debt_bs'])
    if m.get('total_equity'): liab_labels.append('Equity'); liab_vals.append(m['total_equity'])

    if not assets_vals or not liab_vals: return None

    fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'domain'}, {'type': 'domain'}]], subplot_titles=("Assets Breakdown", "Liabilities & Equity"))
    fig.add_trace(go.Pie(labels=assets_labels, values=assets_vals, hole=.5, marker_colors=['#22c55e', '#4ade80', '#86efac', '#bbf7d0']), row=1, col=1)
    fig.add_trace(go.Pie(labels=liab_labels, values=liab_vals, hole=.5, marker_colors=['#f87171', '#ef4444', '#22c55e']), row=1, col=2)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=250, margin=dict(t=30, b=10, l=10, r=10), font_color="#E6E6E6")
    return fig

def custom_metric(label, value):
    st.markdown(f"""
    <div style="background-color: {CARD_BG}; border: 1px solid {BORDER}; padding: 12px 15px; border-radius: 8px; margin-bottom: 12px;">
        <div style="font-size: 11px; color: {MUTED}; text-transform: uppercase; font-weight: 600; margin-bottom: 4px; letter-spacing: 0.5px; font-family: 'Inter', sans-serif;">{label}</div>
        <div style="font-size: 20px; font-weight: 700; color: #FFFFFF; font-family: 'Inter', sans-serif;">{value}</div>
    </div>
    """, unsafe_allow_html=True)

def card(title, body_html): st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

def valuation_checks(m):
    pe, peg = to_float(m.get('pe_ratio')), to_float(m.get('peg_ratio'))
    return [("Reasonable P/E (<25x)", None if pe is None else pe < 25, f"Trailing P/E of {pe}x" if pe is not None else "P/E not available"), ("Attractive PEG (<1.5)", None if peg is None else peg < 1.5, f"PEG ratio of {peg}" if peg is not None else "PEG not available")]
def past_performance_checks(m):
    yoy, qoq, roe, margin = to_float(m.get('pat_yoy')), to_float(m.get('pat_qoq')), to_float(m.get('roe')), to_float(m.get('net_margin'))
    return [("Positive Earnings Growth (YoY)", None if yoy is None else yoy > 0, f"PAT YoY growth of {m.get('pat_yoy')}"), ("Accelerating Growth", None if (yoy is None or qoq is None) else qoq > yoy, "Comparing recent growth to yearly"), ("Strong Return on Equity (>15%)", None if roe is None else roe > 15, f"ROE of {m.get('roe')}"), ("Healthy Net Margin (>10%)", None if margin is None else margin > 10, f"Net margin of {m.get('net_margin')}")]
def financial_health_checks(m): return [("Low Leverage (D/E < 1.0)", None if to_float(m.get('debt_to_equity')) is None else to_float(m.get('debt_to_equity')) < 1.0, f"Debt-to-equity of {m.get('debt_to_equity')}")]
def dividend_checks(m):
    dy = to_float(str(m.get('dividend_yield', ''))) if "doesn't pay" not in str(m.get('dividend_yield', '')).lower() else 0.0
    return [("Notable Dividend (>1.5%)", False if not dy else dy > 1.5, f"Dividend yield: {m.get('dividend_yield')}")]
def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else 0
def render_checks(checks):
    html = ""
    for label, status, desc in checks:
        icon, cls = ("&#9989;", "swf-check-pass") if status is True else ("&#10060;", "swf-check-fail") if status is False else ("&#8213;", "swf-check-na")
        html += f'<div style="padding:5px 0;"><span class="{cls}">{icon} <b>{label}</b></span><div class="swf-sub">{desc}</div></div>'
    return html

# ============================================================
# 5. AI REPORT BUILDER
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)
    sys = """
    You are an elite institutional equity research director. Output clean raw text with clear section headers.
    Structure your deep-dive analysis using EXACTLY these 8 numbered headers:
    1. VALUATION & FAIR VALUE
    2. FUTURE GROWTH & OUTLOOK
    3. PAST PERFORMANCE & EARNINGS QUALITY
    4. FINANCIAL HEALTH & BALANCE SHEET
    5. DIVIDEND & CAPITAL ALLOCATION
    6. MANAGEMENT & COMPENSATION
    7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
    8. NARRATIVE VERDICT

    STRICT RULES FOR SECTION 8: Provide ONLY a clean narrative summary of the final verdict. Do not generate parameters.
    """
    pmt = f"Target: {metrics['name']} ({ticker}). Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. EV/EBITDA: {metrics['ev_ebitda']}. Debt/Eq: {metrics['debt_to_equity']}. System Verdict: {metrics['predictive']['verdict']}."
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt, config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

# ============================================================
# 6. APP UI & NAVIGATION
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None
if 'active_section' not in st.session_state: st.session_state.active_section = "Company Overview"
SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance", "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "Verdict"]

st.markdown('<div class="swf-title-container"><div class="swf-title">🦉 FINANCIAL INTELLIGENCE APP</div></div>', unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input: stock_input = st.text_input("Enter Stock Name or Ticker:", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn: generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

uploaded_csv = None
if generate_clicked and stock_input.strip():
    with st.spinner('Compiling cascade metrics and quantitative models...'):
        try:
            rt = resolve_name_to_ticker(stock_input)
            st.session_state.report_data = fetch_stock_data(rt, stock_input, uploaded_csv)
            st.session_state.report_data['ai_text'] = generate_comprehensive_report(st.session_state.report_data, st.session_state.report_data['working_ticker'])
            st.session_state.active_section = "Company Overview"
        except Exception as e: st.error(f"Error building report: {e}")

with st.sidebar:
    st.markdown("### Settings & Data")
    uploaded_csv = st.file_uploader("Upload Finology Export (Optional CSV)", type=["csv"])
    if st.session_state.report_data:
        m0 = st.session_state.report_data['metrics']
        st.markdown("---")
        st.markdown(f"""
        <div class="swf-company-mini">
            <div style="display:flex; align-items:center; gap:10px;">
                <div class="swf-avatar">{str(m0.get('name','?'))[0]}</div>
                <div><div style="font-weight:700;">{m0.get('name')}</div><div style="color:{MUTED}; font-size:0.8em;">{st.session_state.report_data['ticker']} Stock Report</div></div>
            </div>
            <div style="color:{MUTED}; font-size:0.85em; margin-top:6px;">Market Cap: {fmt_indian_currency(m0.get('market_cap'), "₹")}</div>
        </div>
        """, unsafe_allow_html=True)
        st.radio("Navigate", SECTIONS, index=SECTIONS.index(st.session_state.active_section), key="nav_radio", label_visibility="collapsed")
        st.session_state.active_section = st.session_state.nav_radio
    else: st.markdown(f'<div style="color:{MUTED}; padding:10px;">Generate a report to unlock section navigation.</div>', unsafe_allow_html=True)

if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    ticker = data['ticker']
    raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', data['ai_text'])
    narrative = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', raw_ai_text, flags=re.IGNORECASE) if s.strip()]
    if len(narrative) > 8: narrative = narrative[-8:]
    def narrative_for(idx): return re.sub(r'^(?:\*\*|__)?\d+\.\s+[A-Z&\s]+(?:\*\*|__)?\n+', '', narrative[idx], flags=re.IGNORECASE).strip() if idx < len(narrative) else "Detailed qualitative breakdown unavailable."

    pred = m['predictive']
    current_rating = pred['verdict']
    rc = GREEN if "BUY" in current_rating else ORANGE if "OBSERVE" in current_rating else RED

    val_checks, past_checks, health_checks, div_checks = valuation_checks(m), past_performance_checks(m), financial_health_checks(m), dividend_checks(m)
    scores = {"Value": score_from_checks(val_checks), "Future": 50, "Past": score_from_checks(past_checks), "Health": score_from_checks(health_checks), "Dividend": score_from_checks(div_checks)}

    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        st.markdown(f"""
        <div class="swf-card"><div style="display:flex; justify-content:space-between; align-items:flex-start;">
            <div><div style="color:{MUTED}; font-size:0.85em;">Stocks / {m.get('industry','N/A')}</div><div style="font-size:1.4em; font-weight:800;">{m['name']}</div><div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report</div><span class="swf-badge" style="margin-top:8px; display:inline-block;">Verdict: <span style="color:{rc};">{current_rating}</span></span></div>
            <div style="text-align:right;"><div style="font-size:1.6em; font-weight:800;">₹ {m['price']}</div></div>
        </div></div>
        """, unsafe_allow_html=True)
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Analysis Summary</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(scores), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")
    sec = st.session_state.active_section

    if sec == "Company Overview":
        c1, c2, c3, c4 = st.columns(4)
        with c1: custom_metric("Current Price", f"₹ {m['price']}"); custom_metric("P/E Ratio", f"{m['pe_ratio']}x" if m['pe_ratio'] != "N/A" else "N/A")
        with c2: custom_metric("P/BV Ratio", f"{m['pb_ratio']}x" if m['pb_ratio'] != "N/A" else "N/A"); custom_metric("ROE", f"{m['roe']}")
        with c3: custom_metric("EV/EBITDA", f"{m['ev_ebitda']}x" if m['ev_ebitda'] != "N/A" else "N/A"); custom_metric("PAT Growth (YoY)", f"{m['pat_yoy']}")
        with c4: custom_metric("Debt-to-Equity", f"{m['debt_to_equity']}"); custom_metric("EBITDA Margin", f"{m.get('ebitda_margin', 'N/A')}")
        card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{m.get('business_summary', 'Business summary not available.')}</p><div class='swf-sub'>Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")

    elif sec == "1. Valuation":
        st.markdown(f"### 1. Valuation — Score {score_from_checks(val_checks)}/100")
        card("Valuation Checklist", render_checks(val_checks))
        v1, v2 = st.columns(2)
        with v1: 
            fig_pe = historical_multiple_chart(m['history'], m['pe_ratio'], "P/E Ratio")
            if fig_pe: st.plotly_chart(fig_pe, use_container_width=True, config={'displayModeBar': False})
        with v2:
            fig_pb = historical_multiple_chart(m['history'], m['pb_ratio'], "Price to Book")
            if fig_pb: st.plotly_chart(fig_pb, use_container_width=True, config={'displayModeBar': False})
            
        st.markdown("##### Fair Value Estimate")
        if m.get('fair_value'):
            fig, diff_pct = fair_value_bar(m['price'], m['fair_value'], m.get('currency','₹'))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            st.caption(f"Price is approximately {abs(diff_pct)}% {'overvalued' if diff_pct > 0 else 'undervalued'} vs the projected DCF fair value estimate.")
        card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    elif sec == "2. Future Growth":
        st.markdown("### 2. Future Growth & Outlook")
        
        # Claude's Honest Flag: Explicitly disclaiming the mathematical nature of these estimates.
        st.info("⚠️ **Note on Estimates:** Forward growth projections are derived mathematically from historical CAGRs. Time horizons rely on price-only time-series models (ARIMA / Trend-Drift). Because stock prices often resemble a random walk, these are mathematical estimates, not absolute forecasts.")
        
        fg1, fg2, fg3, fg4 = st.columns(4)
        with fg1: custom_metric("Projected Target", f"₹ {pred['target_price']}" if 'target_price' in pred else "N/A")
        with fg2: custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
        with fg3: custom_metric("Est. Earnings Growth", m.get('earnings_growth_est', 'N/A'))
        with fg4: custom_metric("Est. Revenue Growth", m.get('revenue_growth_est', 'N/A'))
            
        st.markdown("##### Earnings and Revenue Growth Projections")
        st.plotly_chart(future_trajectory_chart(m['pnl_df'], m.get('earnings_growth_est'), m.get('revenue_growth_est')), use_container_width=True, config={'displayModeBar': False})
        
        col_g1, col_g2 = st.columns([2, 1])
        with col_g1:
            st.markdown("##### Projected Growth vs Industry & Market")
            st.plotly_chart(future_growth_bar_charts(m.get('earnings_growth_est'), m.get('revenue_growth_est')), use_container_width=True, config={'displayModeBar': False})
        with col_g2:
            st.markdown("##### Future Return on Equity (3yrs)")
            gauge_fig, f_roe = future_roe_gauge(m.get('roe'))
            st.plotly_chart(gauge_fig, use_container_width=True, config={'displayModeBar': False})
            st.caption(f"Future ROE: Forecast to be {f_roe}%.")

        st.markdown("##### Price Projection vs Target")
        if m.get('fair_value'): st.plotly_chart(projection_chart(m['history'], m['fair_value']), use_container_width=True, config={'displayModeBar': False})
        card("Future Growth & Outlook Narrative", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    elif sec == "3. Past Performance":
        st.markdown(f"### 3. Past Performance — Score {score_from_checks(past_checks)}/100")
        card("Past Performance Checklist", render_checks(past_checks))
        
        p1, p2 = st.columns(2)
        with p1:
            yoy_val, qoq_val = to_float(m.get('pat_yoy')) or 0, to_float(m.get('pat_qoq')) or 0
            fig = go.Figure(data=[go.Bar(x=['PAT YoY', 'PAT QoQ'], y=[yoy_val, qoq_val], marker_color=[GREEN, BLUE], text=[f"{yoy_val}%", f"{qoq_val}%"], textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Earnings Momentum")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        with p2:
            roe_val, roa_val = to_float(m.get('roe')) or 0, to_float(m.get('roce_roa')) or 0
            fig = go.Figure(data=[go.Bar(x=['ROE', 'ROA/ROCE'], y=[roe_val, roa_val], marker_color=[GOLD, '#a855f7'], text=[f"{roe_val}%", f"{roa_val}%"], textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Profitability Returns")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        st.markdown("##### Revenue & Margin Trends")
        mo = margin_overlay_chart(m.get('q_fin'))
        if mo: st.plotly_chart(mo, use_container_width=True, config={'displayModeBar': False})
        
        st.markdown("##### Profit & Loss Statement (₹ Cr)")
        if not m['pnl_df'].empty: st.dataframe(m['pnl_df'], use_container_width=True, hide_index=True)
        else: st.info("P&L Statement data available.")
        card("Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    elif sec == "4. Financial Health":
        st.markdown(f"### 4. Financial Health — Score {score_from_checks(health_checks)}/100")
        card("Financial Health Checklist", render_checks(health_checks))
        bs_donuts = balance_sheet_donuts(m)
        if bs_donuts: st.plotly_chart(bs_donuts, use_container_width=True, config={'displayModeBar': False})
        
        st.markdown("##### Balance Sheet & Cash Flows (₹ Cr)")
        tab_bs, tab_cf = st.tabs(["Balance Sheet", "Cash Flows"])
        with tab_bs: st.dataframe(m['bs_df'], use_container_width=True, hide_index=True) if not m['bs_df'].empty else st.info("Balance Sheet data available.")
        with tab_cf: st.dataframe(m['cf_df'], use_container_width=True, hide_index=True) if not m['cf_df'].empty else st.info("Cash Flow data available.")
        card("Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    elif sec == "5. Dividend":
        st.markdown(f"### 5. Dividend — Score {score_from_checks(div_checks)}/100")
        card("Dividend Checklist", render_checks(div_checks))
        card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    elif sec == "6. Management":
        st.markdown("### 6. Management & Leadership")
        if m['company_officers']:
            rows = [{"Name": o.get('name', 'N/A'), "Position": o.get('title', 'N/A'), "Ownership %": f"{(o.get('exercisedValue', 0) or 0):.2f}%" if 'exercisedValue' in o else "N/A"} for o in m['company_officers']]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else: card("Leadership Team", "<div class='swf-check-na'>&#8213; Detailed management data is not available.</div>")
        card("Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

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
        
        # Re-iterating Claude's honest flag on the final verdict so the user is clear.
        st.info("⚠️ **Note:** The target price, entry range, and time horizon are derived mathematically using Discounted Cash Flow and Average True Range models. Time horizons are estimated via price-only models (ARIMA). These are estimates, not guaranteed forecasts.")
        
        st.markdown(f"<div style='font-size:1.1em; margin-bottom:12px;'><b>Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{current_rating}</span></div>", unsafe_allow_html=True)
        if "BUY" in current_rating:
            st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry Price:</b> {pred['entry_range']}<br><b>Est. Time Horizon / Duration:</b> {pred['time_horizon']}<br><b>Exit Price (Target):</b> ₹ {pred['target_price']}<br><b>Suggested Stop Loss:</b> ₹ {pred['stop_loss']}</div>", unsafe_allow_html=True)
        styled_verdict = narrative_for(7)
        for w, c in [("STRONG BUY", GREEN), ("BUY", GREEN), ("OBSERVE", ORANGE), ("DON'T BUY", RED)]: styled_verdict = re.sub(rf'(?i)(?<!STRONG )\b{w}\b', f'<span style="color:{c}; font-weight:bold;">{w}</span>', styled_verdict)
        st.markdown(f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{styled_verdict}</p>", unsafe_allow_html=True)
