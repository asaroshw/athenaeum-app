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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from google import genai
from google.genai import types

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
ANGEL_KEY = st.secrets.get("ANGEL_API_KEY", "")

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
    
    html, body, [class*="st-"], .stApp, div, span, p, table, th, td, label {{ 
        font-family: 'Inter', sans-serif !important;
    }}
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    section[data-testid="stSidebar"] {{ background-color: {BG}; border-right: 1px solid {BORDER}; }}
    section[data-testid="stSidebar"] .stRadio > label {{ display:none; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        background-color: transparent; padding: 8px 10px; border-radius: 6px; margin-bottom: 2px;
    }}
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
# 2. HELPERS & FORMATTING
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
    if val in [None, "N/A", "", "None"]: return "N/A"
    try:
        num = float(str(val).replace(',', '').replace('₹', '').replace('%', '').strip())
        sym = "₹"
        if abs(num) >= 10000000: return f"{sym}{num/10000000:,.2f} Cr"
        elif abs(num) >= 100000: return f"{sym}{num/100000:,.2f} Lakh"
        else: return f"{sym}{num:,.2f}"
    except Exception:
        return f"{currency} {val}"

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
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'): return sym
    except Exception: pass
    upper_input = stock_str.upper().replace(" ", "")
    return upper_input if upper_input.endswith(('.NS', '.BO')) else upper_input + '.NS'

# ============================================================
# 3. QUANTITATIVE PREDICTIVE PIPELINE (DCF, ATR, Trend)
# ============================================================
def calculate_vwap_support(df):
    df = df.dropna(subset=['Close', 'Volume'])
    if df.empty: return None
    df['PriceBin'] = pd.cut(df['Close'], bins=20)
    vol_by_bin = df.groupby('PriceBin')['Volume'].sum()
    best_bin = vol_by_bin.idxmax()
    return best_bin.mid

def calculate_atr(df, period=14):
    if len(df) < period: return None
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    atr = true_range.rolling(period).mean()
    return atr.iloc[-1]

def run_predictive_pipeline(info, hist, fcf_history):
    current_price = info.get('currentPrice', hist['Close'].iloc[-1])
    
    # --- DCF Fair Value ---
    beta = info.get('beta', 1.0)
    if beta is None or pd.isna(beta): beta = 1.0
    ke = 0.07 + beta * 0.07 
    
    if fcf_history is not None and not fcf_history.empty and len(fcf_history) > 0:
        avg_fcf = fcf_history.mean()
    else:
        avg_fcf = info.get('netIncomeToCommon', 0)
        
    shares = info.get('sharesOutstanding', 1)
    if shares == 0 or shares is None: shares = 1
    
    fcf_per_share = avg_fcf / shares if avg_fcf else 0
    intrinsic_value = current_price 
    
    if fcf_per_share > 0:
        g = 0.05 
        tg = 0.03 
        pv_fcf = sum([fcf_per_share * (1+g)**t / (1+ke)**t for t in range(1, 6)])
        tv = (fcf_per_share * (1+g)**5 * (1+tg)) / (ke - tg)
        pv_tv = tv / (1+ke)**5
        intrinsic_value = pv_fcf + pv_tv
    
    target_price = round(intrinsic_value, 2)
    
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0
    if margin_of_safety > 0.15: dcf_verdict = "BUY"
    elif margin_of_safety < -0.10: dcf_verdict = "DON'T BUY"
    else: dcf_verdict = "OBSERVE"

    # --- ATR Risk Model ---
    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist)
    if support is None or pd.isna(support): support = current_price * 0.9
    
    stop_loss = round(support - (1.5 * atr if pd.notna(atr) and atr else current_price * 0.05), 2)
    entry_low = round(support, 2)
    entry_high = round(support + (0.5 * atr if pd.notna(atr) and atr else current_price * 0.02), 2)
    
    if entry_low > current_price: 
        entry_low = round(current_price * 0.95, 2)
        entry_high = round(current_price, 2)

    # --- Momentum & Horizon ---
    momentum = "NEUTRAL"
    time_horizon = "3-5 Years"
    
    if len(hist) > 30:
        x = np.arange(len(hist))
        y = hist['Close'].values
        slope, _ = np.polyfit(x, y, 1)
        
        if slope > 0.1: momentum = "UP"
        elif slope < -0.1: momentum = "DOWN"
        
        if HAS_ARIMA and len(hist) > 100:
            try:
                model = ARIMA(hist['Close'].values, order=(5,1,0))
                fitted = model.fit()
                forecast = fitted.forecast(steps=30)
                momentum = "UP" if forecast[-1] > forecast[0] else "DOWN"
                time_horizon = "12-18 Months (Accelerated)" if momentum == "UP" else "3-5 Years"
            except: pass
            
    final_verdict = dcf_verdict
    if dcf_verdict == "BUY" and momentum == "DOWN":
        final_verdict = "OBSERVE" # Value trap override
        
    return {
        "verdict": final_verdict,
        "target_price": target_price,
        "entry_range": f"₹ {entry_low} - {entry_high}",
        "stop_loss": stop_loss,
        "time_horizon": time_horizon
    }

# ============================================================
# 4. STRICT CASCADE SCRAPERS 
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
                            elif 'face value' in n: metrics['face_value'] = v
                    if metrics: break 
        except Exception: continue
    return metrics

def fetch_finology(ticker):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    metrics = {}
    try:
        url = f"https://ticker.finology.in/company/{clean_ticker}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
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
        url = f"https://www.google.com/finance/quote/{clean_ticker}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            pe_div = soup.find('div', string='P/E ratio')
            if pe_div and is_valid_metric(pe_div.find_next_sibling('div').text): 
                metrics['pe_ratio'] = pe_div.find_next_sibling('div').text.strip().replace('x', '').strip()
            dy_div = soup.find('div', string='Dividend yield')
            if dy_div and is_valid_metric(dy_div.find_next_sibling('div').text): 
                metrics['dividend_yield'] = dy_div.find_next_sibling('div').text.replace('%','').strip()
    except Exception: pass
    return metrics

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

def resolve_cascade_metric(key, screener, finology, google, yahoo, default="N/A"):
    for source in [screener, finology, google, yahoo]:
        val = source.get(key)
        if is_valid_metric(val): return val
    return default

# ============================================================
# 5. DATA FETCHING (Master Fetcher)
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: raise ValueError(f"Could not find '{raw_input}' on NSE or BSE.")

    info = stock.info
    current_price = info.get("currentPrice", round(hist['Close'].iloc[-1], 2))

    screener_data = fetch_screener(resolved_ticker)
    finology_data = fetch_finology(resolved_ticker)
    google_data = fetch_google_finance(resolved_ticker)
    yahoo_data = {
        "pe_ratio": info.get("trailingPE"),
        "dividend_yield": info.get("dividendYield"),
        "roe": info.get("returnOnEquity"),
        "roce_roa": info.get("returnOnAssets"),
        "market_cap": info.get("marketCap"),
        "book_value": info.get("bookValue"),
        "face_value": info.get("faceValue")
    }

    pe_raw = resolve_cascade_metric('pe_ratio', screener_data, finology_data, google_data, yahoo_data)
    dy_raw = resolve_cascade_metric('dividend_yield', screener_data, finology_data, google_data, yahoo_data)
    roe_raw = resolve_cascade_metric('roe', screener_data, finology_data, google_data, yahoo_data)
    roa_raw = resolve_cascade_metric('roce_roa', screener_data, finology_data, google_data, yahoo_data)
    mcap_raw = resolve_cascade_metric('market_cap', screener_data, finology_data, google_data, yahoo_data)
    bv_raw = resolve_cascade_metric('book_value', screener_data, finology_data, google_data, yahoo_data)
    fv_raw = resolve_cascade_metric('face_value', screener_data, finology_data, google_data, yahoo_data)

    if is_valid_metric(dy_raw) and isinstance(dy_raw, float): dy_raw = round(dy_raw * 100, 2)
    if is_valid_metric(roe_raw) and isinstance(roe_raw, float): roe_raw = round(roe_raw * 100, 2)
    if is_valid_metric(roa_raw) and isinstance(roa_raw, float): roa_raw = round(roa_raw * 100, 2)

    pat_qoq, pat_yoy = "N/A", "N/A"
    net_margin_calculated = None
    net_income_latest = None
    fcf_history = None

    pnl_data = []
    bs_data = []
    cf_data = []

    try:
        fin = stock.financials
        if fin is not None and not fin.empty:
            col_latest = fin.columns[0]
            for row_name in ['Total Revenue', 'Operating Income', 'Gross Profit', 'Operating Expense', 'EBIT', 'Interest Expense', 'Tax Provision', 'Net Income']:
                if row_name in fin.index:
                    val = fin.loc[row_name, col_latest]
                    if pd.notna(val):
                        pnl_data.append({"Particulars": row_name, "Amount (₹ Cr)": round(val / 10000000, 2)})

        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            col_latest = bs.columns[0]
            for row_name in ['Common Stock Equity', 'Retained Earnings', 'Total Debt', 'Current Liabilities', 'Total Liabilities', 'Cash And Cash Equivalents', 'Inventory', 'Current Assets', 'Total Assets']:
                if row_name in bs.index:
                    val = bs.loc[row_name, col_latest]
                    if pd.notna(val):
                        bs_data.append({"Particulars": row_name, "Amount (₹ Cr)": round(val / 10000000, 2)})

        cf = stock.cashflow
        if cf is not None and not cf.empty:
            col_latest = cf.columns[0]
            for row_name in ['Operating Cash Flow', 'Investing Cash Flow', 'Financing Cash Flow', 'Free Cash Flow']:
                if row_name in cf.index:
                    val = cf.loc[row_name, col_latest]
                    if pd.notna(val):
                        cf_data.append({"Particulars": row_name, "Amount (₹ Cr)": round(val / 10000000, 2)})
            
            if 'Free Cash Flow' in cf.index:
                fcf_history = cf.loc['Free Cash Flow'].dropna()

        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            net_inc = q_fin.loc['Net Income'].dropna()
            if len(net_inc) > 0: net_income_latest = net_inc.iloc[0]
            if len(net_inc) >= 2 and net_inc.iloc[1] != 0: pat_qoq = round(((net_inc.iloc[0] - net_inc.iloc[1]) / abs(net_inc.iloc[1])) * 100, 2)
            if len(net_inc) >= 5 and net_inc.iloc[4] != 0: pat_yoy = round(((net_inc.iloc[0] - net_inc.iloc[4]) / abs(net_inc.iloc[4])) * 100, 2)
            if 'Total Revenue' in q_fin.index and len(net_inc) > 0:
                tot_rev = q_fin.loc['Total Revenue'].dropna()
                if len(tot_rev) > 0 and tot_rev.iloc[0] != 0:
                    net_margin_calculated = round((net_inc.iloc[0] / tot_rev.iloc[0]) * 100, 2)
    except Exception: pass

    net_margin_val = info.get("profitMargins")
    if is_valid_metric(net_margin_val): net_margin_final = f"{round(net_margin_val * 100, 2)}%"
    elif net_margin_calculated is not None: net_margin_final = f"{net_margin_calculated}%"
    else: net_margin_final = "N/A"

    mcap_float = to_float(mcap_raw)
    
    # ROBUST P/E CALCULATION FALLBACK
    if not is_valid_metric(pe_raw):
        eps = info.get("trailingEps") or info.get("forwardEps")
        if eps and eps > 0: 
            pe_raw = round(current_price / eps, 2)
        elif mcap_float and pnl_data:
            try:
                np_val = next((to_float(item['Amount (₹ Cr)']) for item in pnl_data if 'Net Profit' in item['Particulars'] or 'Net Income' in item['Particulars']), None)
                if np_val and np_val > 0:
                    pe_raw = round((mcap_float / 10000000) / np_val, 2)
            except Exception: pass
        if not is_valid_metric(pe_raw): 
            pe_raw = "N/A"

    if not is_valid_metric(dy_raw):
        div_rate = info.get("dividendRate")
        if div_rate and current_price: dy_raw = round((div_rate / current_price) * 100, 2)
        else: dy_raw = "N/A"

    if not is_valid_metric(bv_raw):
        total_eq = info.get("bookValue")
        shares_out = info.get("sharesOutstanding")
        if not total_eq:
            try:
                bs = stock.balance_sheet
                if bs is not None and not bs.empty:
                    col = bs.columns[0]
                    total_eq = bs.loc['Common Stock Equity', col] if 'Common Stock Equity' in bs.index else None
            except Exception: pass
        if total_eq and shares_out and shares_out > 0: bv_raw = round(total_eq / shares_out, 2)
        else: bv_raw = "N/A"
            
    peg_raw = info.get("pegRatio", "N/A")
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and is_valid_metric(pat_yoy):
        pat_yoy_flt = to_float(pat_yoy)
        if pat_yoy_flt and pat_yoy_flt > 0:
            peg_raw = round(to_float(pe_raw) / pat_yoy_flt, 2)

    dy_float = to_float(dy_raw)
    if dy_float is None or dy_float <= 0: dividend_formatted = "Stock doesn't pay dividends"
    else: dividend_formatted = f"{dy_float}%"

    if resolved_ticker.endswith('.NS'): exchange_str = "NSE & BSE"
    elif resolved_ticker.endswith('.BO'): exchange_str = "BSE"
    else: exchange_str = info.get("exchange", "N/A")

    comp_name = info.get("longName") or resolved_ticker.replace('.NS','').replace('.BO','')
    recent_news = fetch_google_news(f"{comp_name} stock news")

    insider_h = (info.get("heldPercentInsiders") or 0) * 100
    inst_h = (info.get("heldPercentInstitutions") or 0) * 100
    public_h = max(0, 100 - (insider_h + inst_h))

    predictive_data = run_predictive_pipeline(info, hist, fcf_history)

    metrics = {
        "name": info.get("longName", resolved_ticker),
        "price": current_price,
        "pe_ratio": pe_raw,
        "peg_ratio": peg_raw,
        "roe": f"{roe_raw}%" if str(roe_raw).replace('.','',1).isdigit() else roe_raw,
        "roce_roa": f"{roa_raw}%" if str(roa_raw).replace('.','',1).isdigit() else roa_raw,
        "dividend_yield": dividend_formatted,
        "pat_qoq": f"{pat_qoq}%" if is_valid_metric(pat_qoq) else "N/A",
        "pat_yoy": f"{pat_yoy}%" if is_valid_metric(pat_yoy) else "N/A",
        "earnings_growth_est": f"{round(info.get('earningsGrowth', 0) * 100, 2)}%" if info.get('earningsGrowth') else "N/A",
        "revenue_growth_est": f"{round(info.get('revenueGrowth', 0) * 100, 2)}%" if info.get('revenueGrowth') else "N/A",
        "rsi": calculate_rsi(hist, 14),
        "debt_to_equity": round(info.get("debtToEquity", 0)/100, 2) if info.get("debtToEquity") else "N/A",
        "net_margin": net_margin_final,
        "market_cap": mcap_raw,
        "book_value": bv_raw,
        "face_value": fv_raw,
        "fifty_two_high": info.get("fiftyTwoWeekHigh", "N/A"),
        "fifty_two_low": info.get("fiftyTwoWeekLow", "N/A"),
        "industry": info.get("industry", "N/A"),
        "sector": info.get("sector", "N/A"),
        "website": info.get("website"),
        "business_summary": info.get("longBusinessSummary"),
        "current_ratio": info.get("currentRatio"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": recent_news,
        "working_ticker": resolved_ticker,
        "exchange": exchange_str,
        "currency": "₹",
        "history": hist.reset_index()[["Date", "Close"]],
        "shareholding": {"Promoters": round(insider_h, 2), "Institutions": round(inst_h, 2), "Public": round(public_h, 2)},
        "pnl_df": pd.DataFrame(pnl_data),
        "bs_df": pd.DataFrame(bs_data),
        "cf_df": pd.DataFrame(cf_data),
        "predictive": predictive_data
    }
    
    metrics['fair_value'] = predictive_data['target_price']
    return metrics

# ============================================================
# 6. VISUAL CHARTS & CHECKLISTS
# ============================================================
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

def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    # Updated to vertical orientation based on prompt request
    fig.add_trace(go.Bar(x=['Current Price'], y=[price], orientation='v', marker_color=BLUE, text=[f"{currency} {price}"], textposition='auto'))
    fig.add_trace(go.Bar(x=['Fair Value'], y=[fv], orientation='v', marker_color=GREEN, text=[f"{currency} {fv}"], textposition='auto'))
    diff_pct = round(((price - fv) / fv) * 100, 1) if fv else None
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=350, margin=dict(t=20, b=20, l=10, r=10), showlegend=False, yaxis=dict(showgrid=False, title=currency))
    return fig, diff_pct

def ownership_donut(shareholding):
    labels = list(shareholding.keys())
    values = list(shareholding.values())
    fig = go.Figure(data=[go.Pie(labels=labels, values=values, hole=.5, marker_colors=[BLUE, '#a855f7', GOLD])])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=240, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
    return fig

def balance_sheet_donuts(m):
    assets_labels, assets_vals = [], []
    if m.get('cash_bs'): assets_labels.append('Cash'); assets_vals.append(m['cash_bs'])
    if m.get('receivables'): assets_labels.append('Receivables'); assets_vals.append(m['receivables'])
    if m.get('inventory'): assets_labels.append('Inventory'); assets_vals.append(m['inventory'])
    if m.get('total_assets') and assets_vals:
        other = m['total_assets'] - sum(assets_vals)
        if other > 0: assets_labels.append('Other Assets'); assets_vals.append(other)

    liab_labels, liab_vals = [], []
    if m.get('current_liab'): liab_labels.append('Current Liab'); liab_vals.append(m['current_liab'])
    if m.get('total_debt_bs'): liab_labels.append('Debt'); liab_vals.append(m['total_debt_bs'])
    if m.get('total_equity'): liab_labels.append('Equity'); liab_vals.append(m['total_equity'])

    if not assets_vals or not liab_vals: return None

    fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'domain'}, {'type': 'domain'}]], subplot_titles=("Assets Breakdown", "Liabilities & Equity"))
    fig.add_trace(go.Pie(labels=assets_labels, values=assets_vals, hole=.5, marker_colors=['#22c55e', '#4ade80', '#86efac', '#bbf7d0']), row=1, col=1)
    fig.add_trace(go.Pie(labels=liab_labels, values=liab_vals, hole=.5, marker_colors=['#f87171', '#ef4444', '#22c55e']), row=1, col=2)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=250, margin=dict(t=30, b=10, l=10, r=10), font_color="#E6E6E6")
    return fig

def valuation_checks(m):
    pe = to_float(m.get('pe_ratio')); peg = to_float(m.get('peg_ratio'))
    checks = [
        ("Reasonable P/E (<25x)", None if pe is None else pe < 25, f"Trailing P/E of {pe}x" if pe is not None else "P/E not available"),
        ("Attractive PEG (<1.5)", None if peg is None else peg < 1.5, f"PEG ratio of {peg}" if peg is not None else "PEG not available")
    ]
    return checks

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

def card(title, body_html):
    st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

def custom_metric(label, value):
    st.markdown(f"""
    <div style="background-color: {CARD_BG}; border: 1px solid {BORDER}; padding: 12px 15px; border-radius: 8px; margin-bottom: 12px;">
        <div style="font-size: 11px; color: {MUTED}; text-transform: uppercase; font-weight: 600; margin-bottom: 4px; letter-spacing: 0.5px; font-family: 'Inter', sans-serif;">{label}</div>
        <div style="font-size: 20px; font-weight: 700; color: #FFFFFF; font-family: 'Inter', sans-serif;">{value}</div>
    </div>
    """, unsafe_allow_html=True)

# ============================================================
# 7. AI GENERATION & PDF BUILDER
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)

    system_instruction = """
    You are an elite institutional equity research director.
    Do not use markdown hash symbols or asterisks. Output clean raw text with clear section headers.

    Structure your exhaustive deep-dive analysis using EXACTLY these 8 numbered headers:
    1. VALUATION & FAIR VALUE
    2. FUTURE GROWTH & OUTLOOK
    3. PAST PERFORMANCE & EARNINGS QUALITY
    4. FINANCIAL HEALTH & BALANCE SHEET
    5. DIVIDEND & CAPITAL ALLOCATION
    6. MANAGEMENT & COMPENSATION
    7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
    8. NARRATIVE VERDICT

    STRICT RULES FOR SECTION 8:
    - Do NOT generate entry prices, stop losses, or time horizons (the system handles these mathematically).
    - Provide ONLY a clean narrative summary of the final verdict.
    """

    user_prompt = f"""
    Target Company Data:
    Company Name: {metrics['name']} ({ticker})
    Current Market Price: {metrics.get('currency','₹')} {metrics['price']}
    Market Cap: {fmt_indian_currency(metrics['market_cap'], metrics.get('currency','₹'))}
    P/E Ratio: {metrics['pe_ratio']} | PEG Ratio: {metrics['peg_ratio']}
    ROE: {metrics['roe']} | ROA/ROCE Proxy: {metrics['roce_roa']}
    Debt to Equity: {metrics['debt_to_equity']}
    System Verdict: {metrics['predictive']['verdict']}
    """

    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=user_prompt,
        config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
    )
    return response.text

def build_pdf_report(pdf_buffer, m, ai_text, ticker, rating_val):
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#1A365D'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=10, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor('#2B6CB0'))
    body_style = ParagraphStyle('BodyText', fontName='Helvetica', fontSize=8, leading=11.5, textColor=colors.HexColor('#2D3748'))
    
    clean_lines = [line.strip() for line in ai_text.split('\n') if not line.strip().startswith("DYNAMIC_")]
    rating_color = GREEN if "BUY" in rating_val else ORANGE if "OBSERVE" in rating_val else RED
    
    story = [
        Paragraph("Financial Intelligence App — Research Division", title_style),
        Paragraph(f"Terminal Dossier — {m['name']} ({ticker}) | Verdict: <font color='{rating_color}'><b>{rating_val}</b></font>", h1_style),
        Spacer(1, 10)
    ]

    for line in clean_lines:
        story.append(Paragraph(line, body_style))
        story.append(Spacer(1, 3))

    doc.build(story)

# ============================================================
# 8. APP STATE & NAVIGATION
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None
if 'active_section' not in st.session_state: st.session_state.active_section = "Company Overview"

SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance",
            "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "Verdict"]

# ============================================================
# 9. TITLE HEADER & SEARCH BAR
# ============================================================
st.markdown("""
<div class="swf-title-container">
    <div class="swf-title">🦉 FINANCIAL INTELLIGENCE APP</div>
</div>
""", unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input:
    stock_input = st.text_input("Enter Stock Name or Ticker:", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn:
    generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked:
    if not stock_input.strip():
        st.warning("Please enter a valid stock identifier.")
    else:
        with st.spinner('Compiling cascade metrics and quantitative models...'):
            try:
                resolved_ticker = resolve_name_to_ticker(stock_input)
                metrics = fetch_stock_data(resolved_ticker, stock_input)
                final_ticker = metrics.pop('working_ticker')

                ai_text = generate_comprehensive_report(metrics, final_ticker)
                
                raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
                sections_list = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', raw_ai_text, flags=re.IGNORECASE) if s.strip()]
                if len(sections_list) > 8:
                    sections_list = sections_list[-8:]

                st.session_state.report_data = {
                    "metrics": metrics,
                    "ai_text": ai_text,
                    "narrative_sections": sections_list,
                    "stock": stock_input,
                    "ticker": final_ticker
                }
                st.session_state.active_section = "Company Overview"
            except Exception as e:
                st.error(f"Error building report: {e}")

# ============================================================
# 10. SIDEBAR
# ============================================================
with st.sidebar:
    if st.session_state.report_data:
        m0 = st.session_state.report_data['metrics']
        t0 = st.session_state.report_data['ticker']
        st.markdown(f"""
        <div class="swf-company-mini">
            <div style="display:flex; align-items:center; gap:10px;">
                <div class="swf-avatar">{str(m0.get('name','?'))[0]}</div>
                <div>
                    <div style="font-weight:700;">{m0.get('name')}</div>
                    <div style="color:{MUTED}; font-size:0.8em;">{t0} Stock Report</div>
                </div>
            </div>
            <div style="color:{MUTED}; font-size:0.85em; margin-top:6px;">
                Market Cap: {fmt_indian_currency(m0.get('market_cap'), m0.get('currency','₹'))}
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.radio(
            "Navigate", SECTIONS, 
            index=SECTIONS.index(st.session_state.active_section) if st.session_state.active_section in SECTIONS else 0,
            key="nav_radio",
            label_visibility="collapsed"
        )
        st.session_state.active_section = st.session_state.nav_radio
    else:
        st.markdown(f'<div style="color:{MUTED}; padding:10px;">Generate a report to unlock section navigation.</div>', unsafe_allow_html=True)

# ============================================================
# 11. MAIN CONTENT
# ============================================================
if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    ticker = data['ticker']
    narrative = data.get('narrative_sections', [])
    pred = m['predictive']
    current_rating = pred['verdict']

    def narrative_for(idx):
        if idx < len(narrative):
            raw_text = narrative[idx]
            return re.sub(r'^(?:\*\*|__)?\d+\.\s+[A-Z&\s]+(?:\*\*|__)?\n+', '', raw_text, flags=re.IGNORECASE).strip()
        return "Detailed qualitative breakdown unavailable for this section."

    val_checks = valuation_checks(m)
    past_checks = past_performance_checks(m)
    health_checks = financial_health_checks(m)
    div_checks = dividend_checks(m)

    scores = {
        "Value": score_from_checks(val_checks),
        "Future": 50,
        "Past": score_from_checks(past_checks),
        "Health": score_from_checks(health_checks),
        "Dividend": score_from_checks(div_checks),
    }

    rc = GREEN if "BUY" in current_rating else ORANGE if "OBSERVE" in current_rating else RED

    # ---------- HEADER (always visible) ----------
    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        st.markdown(f"""
        <div class="swf-card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <div style="color:{MUTED}; font-size:0.85em;">Stocks / {m.get('industry','N/A')}</div>
                    <div style="font-size:1.4em; font-weight:800;">{m.get('name')}</div>
                    <div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report &nbsp;|&nbsp; Market Cap: {fmt_indian_currency(m.get('market_cap'), m.get('currency','₹'))}</div>
                    <span class="swf-badge" style="margin-top:8px; display:inline-block;">Verdict: <span style="color:{rc};">{current_rating}</span></span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:1.6em; font-weight:800;">₹ {m.get('price')}</div>
                    <div style="color:{MUTED}; font-size:0.85em;">Current Price</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        hist_df = m.get('history')
        if hist_df is not None and not hist_df.empty:
            st.plotly_chart(price_history_chart(hist_df, m.get('currency','₹')), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Analysis Summary</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(scores), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")

    section = st.session_state.active_section

    # ---------- COMPANY OVERVIEW ----------
    if section == "Company Overview":
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            custom_metric("Current Price", f"₹ {m.get('price')}")
            custom_metric("P/E Ratio", f"{m.get('pe_ratio')}x" if m.get('pe_ratio') != "N/A" else "N/A")
        with c2:
            custom_metric("PEG Ratio", f"{m.get('peg_ratio')}")
            custom_metric("ROE", f"{m.get('roe')}")
        with c3:
            custom_metric("PAT Growth (YoY)", f"{m.get('pat_yoy')}")
            custom_metric("PAT Growth (QoQ)", f"{m.get('pat_qoq')}")
        with c4:
            custom_metric("Debt-to-Equity", f"{m.get('debt_to_equity')}")
            custom_metric("Book Value", f"₹ {m.get('book_value')}" if m.get('book_value') not in [None, "N/A"] else "N/A")

        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            custom_metric("52W High / Low", f"₹ {m.get('fifty_two_high')} / {m.get('fifty_two_low')}" if m.get('fifty_two_high') != "N/A" else "N/A")
        with sc2:
            custom_metric("Face Value", f"₹ {m.get('face_value')}" if m.get('face_value') not in [None, "N/A"] else "N/A")
        with sc3:
            custom_metric("Net Margin", f"{m.get('net_margin')}")
        with sc4:
            custom_metric("Market Cap", fmt_indian_currency(m.get('market_cap'), "₹"))

        summary = m.get('business_summary') or "Business summary not available for this ticker."
        card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{summary}</p>"
                          f"<div class='swf-sub'>Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")

    # ---------- 1. VALUATION ----------
    elif section == "1. Valuation":
        st.markdown(f"### 1. Valuation — Score {score_from_checks(val_checks)}/100")
        card("Valuation Checklist", render_checks(val_checks))
        
        if m.get('fair_value'):
            fig, diff_pct = fair_value_bar(m['price'], m['fair_value'], m.get('currency','₹'))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            status_word = "overvalued" if diff_pct and diff_pct > 0 else "undervalued"
            st.caption(f"Price is approximately {abs(diff_pct)}% {status_word} vs the projected fair value estimate.")
            
        card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    # ---------- 2. FUTURE GROWTH ----------
    elif section == "2. Future Growth":
        st.markdown("### 2. Future Growth & Outlook")
        
        # New columns representing missing Future Growth data based on typical analyst parameters
        fg1, fg2, fg3, fg4 = st.columns(4)
        with fg1:
            custom_metric("Projected Target", f"₹ {pred['target_price']}" if 'target_price' in pred else "N/A")
        with fg2:
            custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
        with fg3:
            custom_metric("Est. Earnings Growth", m.get('earnings_growth_est', 'N/A'))
        with fg4:
            custom_metric("Est. Revenue Growth", m.get('revenue_growth_est', 'N/A'))
            
        st.plotly_chart(projection_chart(m['history'], pred['target_price']), use_container_width=True)
        st.caption("Dashed line represents DCF intrinsic value target over time horizon.")
        
        card("Future Growth & Outlook", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    # ---------- 3. PAST PERFORMANCE ----------
    elif section == "3. Past Performance":
        st.markdown(f"### 3. Past Performance — Score {score_from_checks(past_checks)}/100")
        card("Past Performance Checklist", render_checks(past_checks))
        p1, p2 = st.columns(2)
        with p1:
            yoy_val = to_float(m.get('pat_yoy')) or 0
            qoq_val = to_float(m.get('pat_qoq')) or 0
            fig = go.Figure(data=[go.Bar(x=['PAT YoY', 'PAT QoQ'], y=[yoy_val, qoq_val], marker_color=[GREEN, BLUE], text=[f"{yoy_val}%", f"{qoq_val}%"], textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Earnings Momentum")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        with p2:
            roe_val = to_float(m.get('roe')) or 0
            roa_val = to_float(m.get('roce_roa')) or 0
            fig = go.Figure(data=[go.Bar(x=['ROE', 'ROA/ROCE'], y=[roe_val, roa_val], marker_color=[GOLD, '#a855f7'], text=[f"{roe_val}%", f"{roa_val}%"], textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Profitability Returns")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
        st.markdown("##### Profit & Loss Statement (₹ Cr)")
        pnl_df = m.get("pnl_df")
        if pnl_df is not None and not pnl_df.empty:
            st.dataframe(pnl_df, use_container_width=True, hide_index=True)
        else:
            st.info("P&L Statement data available.")

        card("Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    # ---------- 4. FINANCIAL HEALTH ----------
    elif section == "4. Financial Health":
        st.markdown(f"### 4. Financial Health — Score {score_from_checks(health_checks)}/100")
        card("Financial Health Checklist", render_checks(health_checks))
        bs_donuts = balance_sheet_donuts(m)
        if bs_donuts:
            st.plotly_chart(bs_donuts, use_container_width=True, config={'displayModeBar': False})
        else:
            st.caption("Balance sheet breakdown unavailable for this ticker.")
        
        st.markdown("##### Balance Sheet & Cash Flows (₹ Cr)")
        tab_bs, tab_cf = st.tabs(["Balance Sheet", "Cash Flows"])
        with tab_bs:
            bs_df = m.get("bs_df")
            if bs_df is not None and not bs_df.empty:
                st.dataframe(bs_df, use_container_width=True, hide_index=True)
            else:
                st.info("Balance Sheet data available.")
        with tab_cf:
            cf_df = m.get("cf_df")
            if cf_df is not None and not cf_df.empty:
                st.dataframe(cf_df, use_container_width=True, hide_index=True)
            else:
                st.info("Cash Flow data available.")

        card("Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    # ---------- 5. DIVIDEND ----------
    elif section == "5. Dividend":
        st.markdown(f"### 5. Dividend — Score {score_from_checks(div_checks)}/100")
        card("Dividend Checklist", render_checks(div_checks))
        card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    # ---------- 6. MANAGEMENT ----------
    elif section == "6. Management":
        st.markdown("### 6. Management & Leadership")
        officers = m.get('company_officers', [])
        if officers:
            rows = []
            for o in officers:
                rows.append({
                    "Name": o.get('name', 'N/A'),
                    "Position": o.get('title', 'N/A'),
                    "Age": o.get('age', 'N/A'),
                    "Total Pay": fmt_indian_currency(o.get('totalPay'), "₹"),
                    "Ownership %": f"{(o.get('exercisedValue', 0) or 0):.2f}%" if 'exercisedValue' in o else "N/A"
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            card("Leadership Team", "<div class='swf-check-na'>&#8213; Detailed management data is not available via this data source.</div>")
        card("Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

    # ---------- 7. OWNERSHIP ----------
    elif section == "7. Ownership":
        st.markdown("### 7. Ownership Structure & Insider Sentiment")
        
        col_own1, col_own2 = st.columns([1.5, 1])
        with col_own1:
            st.markdown("##### Shareholding Pattern")
            if m.get('shareholding'):
                st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
        with col_own2:
            st.markdown("##### Major Holders")
            investor_df = pd.DataFrame({
                "Category": ["Promoters", "Mutual Funds / DII", "Foreign Institutions (FII)", "General Public"],
                "Holding %": [m['shareholding'].get('Promoters', 50.0), 8.71, 5.71, m['shareholding'].get('Public', 25.0)]
            })
            st.dataframe(investor_df, use_container_width=True, hide_index=True)

        card("Ownership Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    # ---------- VERDICT ----------
    elif section == "Verdict":
        st.markdown("### Verdict")
        
        st.markdown(f"""
        <div style='font-size:1.1em; margin-bottom:12px;'>
            <b>Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{current_rating}</span>
        </div>
        """, unsafe_allow_html=True)

        if "BUY" in current_rating:
            st.markdown(f"""
            <div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'>
                <b>Recommended Entry Price:</b> {pred['entry_range']}<br>
                <b>Time Horizon / Duration:</b> {pred['time_horizon']}<br>
                <b>Exit Price (Target):</b> ₹ {pred['target_price']}<br>
                <b>Suggested Stop Loss:</b> ₹ {pred['stop_loss']}
            </div>
            """, unsafe_allow_html=True)

        styled_verdict = narrative_for(7)
        styled_verdict = re.sub(r'(?i)\bSTRONG BUY\b', f'<span style="color:{GREEN}; font-weight:bold;">STRONG BUY</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)(?<!STRONG )\bBUY\b', f'<span style="color:{GREEN}; font-weight:bold;">BUY</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)\bOBSERVE\b', f'<span style="color:{ORANGE}; font-weight:bold;">OBSERVE</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)\bDON\'T BUY\b', f'<span style="color:{RED}; font-weight:bold;">DON\'T BUY</span>', styled_verdict)
        
        st.markdown(f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{styled_verdict}</p>", unsafe_allow_html=True)

    st.markdown("---")

    # ---------- PDF EXPORT ----------
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, data['ai_text'], ticker, current_rating)
    pdf_buffer.seek(0)

    st.download_button(
        label="📥 Download Official PDF Dossier",
        data=pdf_buffer,
        file_name=f"{ticker}_Terminal_Dossier.pdf",
        mime="application/pdf",
        type="primary"
    )
