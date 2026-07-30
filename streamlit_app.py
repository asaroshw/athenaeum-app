import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import re
import requests
import urllib.parse
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from datetime import timedelta
from google import genai
from google.genai import types
import base64

try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except ImportError:
    HAS_ARIMA = False

# ============================================================
# 1. SETUP & CONFIGURATION
# ============================================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)

st.set_page_config(
    page_title="Athenaeum Financial Intelligence", 
    page_icon="Logo64.png", 
    layout="wide"
)

GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")
FMP_API_KEY = "f4UiLw3dukAgZJP1Xp4fs5NF8uxesRS8"
FMP_BASE_URL = "https://financialmodelingprep.com/stable/"

# --- COLOR PALETTE (Deep Slate/Navy to match banner) ---
GOLD, BG, CARD_BG, BORDER = "#EAB308", "#0B111A", "#121A28", "#1F2B3D"
GREEN, RED, ORANGE, MUTED, BLUE, PURPLE = "#3FB950", "#F85149", "#F97316", "#8B949E", "#38BDF8", "#A855F7"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@800&display=swap');
    
    html, body, [class*="st-"], .stApp, div, span, p, table, th, td, label {{ font-family: 'Inter', sans-serif !important; }}
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    .swf-title-container {{ text-align: center; border-bottom: 1px solid {BORDER}; margin-bottom: 20px; }}
    
    .swf-title {{ 
        font-family: 'Quironax', 'Orbitron', sans-serif !important; 
        font-size: 3.5em; 
        font-weight: 800; 
        color: #FFFFFF; 
        letter-spacing: 2px; 
        display: flex; 
        align-items: center; 
        justify-content: center;
    }}
    
    .swf-card {{ background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; box-shadow: 0 4px 6px rgba(0,0,0,0.2); }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.05em; margin-bottom:6px; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:0px; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
    .swf-badge {{ background:{CARD_BG}; border:1px solid {BORDER}; padding:5px 12px; border-radius:6px; font-weight:700; font-size:0.85em; }}
    .swf-tag {{ background:#1c2333; border:1px solid {BORDER}; color:{MUTED}; padding:3px 9px; border-radius:5px; font-size:0.78em; margin-right:6px; display:inline-block; }}
    .swf-section-title {{ font-size: 1.6em; font-weight: 800; color: #FFFFFF; margin-top: 10px; padding-top: 14px; border-top: 2px solid {BORDER}; }}

    @media print {{
        section[data-testid="stSidebar"], header[data-testid="stHeader"], #MainMenu, footer,
        div[data-testid="stTextInput"], div[data-testid="stButton"], .stSpinner {{ display: none !important; }}
        .stApp {{ background-color: #ffffff !important; }}
        body, .stApp, p, div, span, td, th, li {{ color: #111111 !important; }}
        .swf-card {{ background-color: #ffffff !important; border: 1px solid #ccc !important; break-inside: avoid; }}
        .swf-check-pass {{ color: #15803d !important; }}
        .swf-check-fail {{ color: #b91c1c !important; }}
        .swf-h, .swf-section-title {{ color: #1e40af !important; }}
        .swf-title {{ color: #111111 !important; }}
    }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# TOP SECTOR PEERS 
# ============================================================
SECTOR_PEERS = {
    "financial": ["BAJFINANCE.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS"],
    "capex_intensive": ["LT.NS", "HAL.NS", "BEL.NS", "SIEMENS.NS", "ABB.NS", "CUMMINSIND.NS"],
    "cyclical": ["BOSCHLTD.NS", "MOTHERSON.NS", "UNOMINDA.NS", "MRF.NS", "TATAMOTORS.NS", "M&M.NS"],
    "materials": ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "ULTRACEMCO.NS"],
    "standard": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HUL.NS", "ITC.NS"] 
}

# ============================================================
# 2. CORE UTILITIES & FMP FETCHERS (PRIMARY SOURCE)
# ============================================================
def to_float(val):
    if val in [None, "N/A", "", "None", "Stock doesn't pay dividends"]: return None
    if isinstance(val, bool) or (isinstance(val, float) and pd.isna(val)): return None
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except: return None

def is_valid_metric(val):
    if val in [None, "N/A", "", "-", "--", "None", "0", "0.00%", "0.00"]: return False
    return to_float(val) is not None

def fetch_fmp_json(endpoint, params=None):
    try:
        p = params or {}
        p['apikey'] = FMP_API_KEY
        query_str = urllib.parse.urlencode(p)
        url = f"{FMP_BASE_URL}{endpoint}?{query_str}"
        res = requests.get(url, timeout=6)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def fetch_fmp_data(ticker):
    data = {}
    prof = fetch_fmp_json("profile", {"symbol": ticker})
    if prof and isinstance(prof, list) and len(prof) > 0:
        data['profile'] = prof[0]
        
    quote = fetch_fmp_json("quote", {"symbol": ticker})
    if quote and isinstance(quote, list) and len(quote) > 0:
        data['quote'] = quote[0]

    inc = fetch_fmp_json("income-statement", {"symbol": ticker, "limit": 5})
    if inc and isinstance(inc, list):
        data['income_statement'] = inc

    bs = fetch_fmp_json("balance-sheet-statement", {"symbol": ticker, "limit": 5})
    if bs and isinstance(bs, list):
        data['balance_sheet'] = bs

    cf = fetch_fmp_json("cash-flow-statement", {"symbol": ticker, "limit": 5})
    if cf and isinstance(cf, list):
        data['cash_flow'] = cf

    return data

def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit(): return stock_str + '.BO'
    fmp_search = fetch_fmp_json("search-symbol", {"query": stock_str})
    if fmp_search and isinstance(fmp_search, list):
        for item in fmp_search:
            sym = item.get('symbol', '').upper()
            if sym.endswith('.NS') or sym.endswith('.BO'):
                return sym
    try:
        res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'): return sym
    except: pass
    upper = stock_str.upper().replace(" ", "")
    return upper if upper.endswith(('.NS', '.BO')) else upper + '.NS'

def rating_color(rating):
    r = (rating or "").upper()
    if "DON" in r and "BUY" in r: return RED
    if "OBSERVE" in r: return ORANGE
    if "BUY" in r: return GREEN
    return MUTED

def style_verdict_text(text):
    if not text: return text
    return re.sub(r"(?i)\bDON.?T\s+BUY\b|\bOBSERVE\b|\bSTRONG\s+BUY\b|\bBUY\b",
                  lambda m: f'<span style="color:{rating_color(m.group(0))}; font-weight:bold;">{m.group(0)}</span>', text)

def fetch_google_news(query_term):
    try:
        safe_query = urllib.parse.quote(query_term)
        url = f"https://news.google.com/rss/search?q={safe_query}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=6)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            headlines = []
            for item in root.findall('.//item')[:6]:
                title = item.find('title')
                link = item.find('link')
                if title is not None and link is not None and title.text and link.text:
                    headlines.append({'title': title.text, 'link': link.text})
            return headlines
    except: pass
    return []

@st.cache_data(ttl=3600)
def get_dynamic_risk_free_rate():
    try:
        bond = yf.Ticker("^IGB")
        hist = bond.history(period="5d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1]) / 100.0
    except: pass
    return 0.065

# ============================================================
# 3. SECTOR DETECTION & NORMALIZATION PROFILES
# ============================================================
FINANCIAL_SECTOR_KEYWORDS = [
    "financial services", "bank", "nbfc", "insurance", "capital markets",
    "credit services", "diversified financials", "asset management",
    "mortgage finance", "consumer finance", "shadow banking",
]
CAPEX_INTENSIVE_KEYWORDS = [
    "industrial", "engineering", "infrastructure", "construction", "capital goods",
    "electrical equipment", "machinery", "railroad", "defense", "aerospace",
    "building products", "specialty industrial"
]
MATERIALS_KEYWORDS = ["steel", "metals", "mining", "materials", "chemicals", "cement", "iron", "pipes", "tubes"]
CYCLICAL_KEYWORDS = ["auto", "automobile", "tire", "tyre"]

def is_financial_sector(sector, industry):
    text = f"{sector or ''} {industry or ''}".lower()
    return any(kw in text for kw in FINANCIAL_SECTOR_KEYWORDS)

def classify_sector_profile(sector, industry):
    if is_financial_sector(sector, industry):
        return "financial"
    text = f"{sector or ''} {industry or ''}".lower()
    if any(kw in text for kw in MATERIALS_KEYWORDS): return "materials"
    if any(kw in text for kw in CAPEX_INTENSIVE_KEYWORDS): return "capex_intensive"
    if any(kw in text for kw in CYCLICAL_KEYWORDS): return "cyclical"
    return "standard"

STANDARD_REVENUE_KEYS = ['Total Revenue', 'Operating Revenue', 'revenue']
BANK_REVENUE_KEYS = ['Total Revenue', 'Total Operating Income', 'Interest Income',
                      'Total Interest Income', 'Operating Revenue', 'revenue']
INTEREST_INCOME_KEYS = ['Interest Income', 'Total Interest Income']

# ============================================================
# 4. QUALITATIVE SIGNAL SCANNER
# ============================================================
CATALYST_KEYWORDS = ['acqui', 'profit', 'surge', 'turnaround', 'wins', ' win ', 'order book',
                      'expansion', 'partnership', 'record revenue', 'upgrade', 'beat estimates',
                      'demerger', 'stake sale', 'contract']
RISK_KEYWORDS = ['fraud', 'resign', 'default', 'probe', 'raid', 'downgrade', 'scam',
                  'investigation', 'lawsuit', 'bankruptcy', 'insolvency', 'delisting']
ORDER_BOOK_KEYWORDS = ['order book', 'order win', 'wins order', 'contract win', 'crore order',
                        'export order', 'multi-year contract', 'l1 bidder', 'lowest bidder',
                        'capex expansion', 'capacity expansion', 'new plant', 'guidance']
GROWTH_PCT_PATTERN = re.compile(r'(\d{1,2})\s*%\s*(?:growth|guidance)|(?:growth|guidance).{0,25}?(\d{1,2})\s*%', re.IGNORECASE)

def scan_news_sentiment(recent_news, business_summary):
    titles = [n.get('title', '') for n in (recent_news or [])]
    text = ((business_summary or "") + " " + " ".join(titles)).lower()
    catalyst_hits = sorted(set(kw.strip() for kw in CATALYST_KEYWORDS if kw in text))
    risk_hits = sorted(set(kw.strip() for kw in RISK_KEYWORDS if kw in text))
    bonus, notes = 0, []
    if len(catalyst_hits) >= 2:
        bonus += 15
        notes.append(f"Qualitative bonus (+15): multiple positive catalysts detected ({', '.join(catalyst_hits[:4])}).")
    elif len(catalyst_hits) == 1:
        bonus += 10
        notes.append(f"Qualitative bonus (+10): a positive catalyst was detected ({catalyst_hits[0]}).")
    if risk_hits:
        bonus -= 20
        notes.append(f"Qualitative penalty (-20): risk keyword(s) detected in recent news ({', '.join(risk_hits[:3])}).")
    return bonus, notes

def extract_order_book_signal(recent_news, business_summary):
    titles = [n.get('title', '') for n in (recent_news or [])]
    text = (business_summary or "") + " " + " ".join(titles)
    text_lower = text.lower()
    order_hits = sorted(set(kw for kw in ORDER_BOOK_KEYWORDS if kw in text_lower))
    growth_pct_found = None
    match = GROWTH_PCT_PATTERN.search(text)
    if match:
        val = match.group(1) or match.group(2)
        try:
            v = float(val)
            if 5 <= v <= 50: growth_pct_found = v
        except Exception: pass
    return order_hits, growth_pct_found

# ============================================================
# 5. CHECKLISTS
# ============================================================
def valuation_checks(m):
    pe = to_float(m.get('pe_ratio'))
    peg = to_float(m.get('peg_ratio'))
    pat_yoy = to_float(m.get('pat_yoy'))
    pb = to_float(m.get('pb_ratio'))
    ev_ebitda = to_float(m.get('ev_ebitda'))
    is_fin = m.get('is_financial_sector', False)
    checks = []

    if pe is not None:
        if pe < 0: checks.append(("Profitable on a P/E basis", False, f"P/E is negative ({pe:.2f}x)."))
        else:
            threshold = 45 if (pat_yoy is not None and pat_yoy > 30) else 25
            checks.append((f"Reasonable P/E (<{threshold}x)", pe < threshold, f"Trailing P/E of {pe:.2f}x"))

    if peg is not None:
        if peg < 0: checks.append(("Positive PEG", False, f"PEG is negative ({peg:.2f})."))
        elif pe is not None and pe > 0 and pat_yoy is not None and pat_yoy > 0:
            checks.append(("Attractive PEG (<1.5)", peg < 1.5, f"PEG ratio of {peg:.2f}"))

    if pb is not None:
        threshold = 3.0 if is_fin else 5.0
        checks.append((f"Reasonable P/B (<{threshold:g}x)", 0 < pb < threshold, f"Price-to-Book of {pb:.2f}x"))

    if not is_fin and ev_ebitda is not None:
        if ev_ebitda < 0: checks.append(("Positive EV/EBITDA", False, f"EV/EBITDA is negative ({ev_ebitda:.2f}x)."))
        else: checks.append(("Reasonable EV/EBITDA (<15x)", ev_ebitda < 15, f"EV/EBITDA of {ev_ebitda:.2f}x"))

    return checks

def past_performance_checks(m):
    yoy, qoq = to_float(m.get('pat_yoy')), to_float(m.get('pat_qoq'))
    roe, margin = to_float(m.get('roe')), to_float(m.get('net_margin'))
    opm = to_float(m.get('operating_margin'))
    checks = []
    if yoy is not None: checks.append(("Positive Earnings Growth (YoY)", yoy > 0, f"PAT YoY growth of {yoy:.2f}%"))
    if roe is not None: checks.append(("Strong Return on Equity (>15%)", roe > 15, f"ROE of {roe:.2f}%"))
    if margin is not None: checks.append(("Healthy Net Margin (>10%)", margin > 10, f"Net margin of {margin:.2f}%"))
    return checks

def financial_health_checks(m):
    de = to_float(m.get('debt_to_equity'))
    ic = to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if de is not None:
        if de < 0: checks.append(("Positive Shareholder Equity", False, f"Debt-to-equity is negative ({de:.2f})."))
        else:
            threshold, label = (10.0, "Leverage in line with lending model (D/E < 10x)") if is_fin else (1.0, "Low Leverage (D/E < 1.0)")
            checks.append((label, de < threshold, f"Debt-to-equity of {de:.2f}"))
    if ic is not None: checks.append(("Comfortable Interest Coverage (>3x)", ic > 3, f"EBIT covers interest expense {ic:.2f}x"))
    return checks

def dividend_checks(m):
    dy_str = str(m.get('dividend_yield', ''))
    if "doesn't pay" in dy_str.lower(): return [("Notable Dividend (>1.5%)", False, "Stock doesn't pay dividends")]
    dy = to_float(dy_str)
    return [("Notable Dividend (>1.5%)", dy is not None and dy > 1.5, f"Dividend yield: {dy:.2f}%" if dy else "N/A")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else None

def render_checks(checks):
    if not checks: return "<div class='swf-check-na'>&#8213; Not enough data to run this checklist.</div>"
    html = ""
    for label, status, desc in checks:
        icon, cls = ("&#9989;", "swf-check-pass") if status else ("&#10060;", "swf-check-fail")
        html += f'<div style="padding:5px 0;"><span class="{cls}">{icon} <b>{label}</b></span><div class="swf-sub">{desc}</div></div>'
    return html

def compute_fundamental_score(val_score, past_score, health_score, is_financial):
    weights = {"val": 0.45, "past": 0.35, "health": 0.20} if is_financial else {"val": 0.35, "past": 0.35, "health": 0.30}
    scores = {"val": val_score, "past": past_score, "health": health_score}
    available = {k: v for k, v in scores.items() if v is not None}
    if not available: return 0.0
    total_w = sum(weights[k] for k in available)
    return round(sum(weights[k] * v for k, v in available.items()) / total_w, 1)

# ============================================================
# 6. QUANTITATIVE COMPOSITE ENGINE
# ============================================================
EQUITY_RISK_PREMIUM = 0.055
TERMINAL_GROWTH_PCT = 5.0

def calculate_vwap_support(df):
    d = df.dropna(subset=['Close', 'Volume'])
    if d.empty: return None
    d = d.copy()
    d['PriceBin'] = pd.cut(d['Close'], bins=20)
    vol_by_bin = d.groupby('PriceBin', observed=True)['Volume'].sum()
    if vol_by_bin.empty: return None
    return vol_by_bin.idxmax().mid

def calculate_atr(df, period=14):
    if df is None or len(df) <= period: return None
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    val = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else None

def composite_verdict(fundamental_score, margin_of_safety, drift, arima_direction=None,
                      forced_intrinsic_adjustment=0, qualitative_bonus=0):
    W_FUNDAMENTAL, W_INTRINSIC, W_TECHNICAL = 0.40, 0.35, 0.25
    intrinsic_score = min(max(50 + margin_of_safety * 150, 0), 100)
    intrinsic_score = min(max(intrinsic_score + forced_intrinsic_adjustment, 0), 100)
    tech_score = min(max(50 + (drift or 0) * 100, 0), 100)
    if arima_direction == "UP": tech_score = min(100, tech_score + 10)
    elif arima_direction == "DOWN": tech_score = max(0, tech_score - 10)
    composite = W_FUNDAMENTAL * fundamental_score + W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score
    composite = min(max(composite + qualitative_bonus, 0), 100)
    if composite >= 75: verdict = "STRONG BUY"
    elif composite >= 60: verdict = "BUY"
    elif composite >= 40: verdict = "OBSERVE"
    else: verdict = "DON'T BUY"
    return round(composite, 1), verdict, round(intrinsic_score, 1), round(tech_score, 1)

VERDICT_RANK = {"DON'T BUY": 0, "OBSERVE": 1, "BUY": 2, "STRONG BUY": 3}

def apply_tiered_sanity_veto(verdict, target_price, current_price, notes, growth_rate=8.0):
    if target_price is None or not current_price: return verdict
    downside_pct = (current_price - target_price) / current_price
    upside_pct = (target_price - current_price) / current_price
    if downside_pct > 0.15:
        return "DON'T BUY" if downside_pct > 0.40 else "OBSERVE"
    elif downside_pct > 0:
        if VERDICT_RANK.get(verdict, 1) > VERDICT_RANK["OBSERVE"]: return "OBSERVE"
    max_allowed_upside = 2.50 if (growth_rate and growth_rate >= 25) else 1.50
    if upside_pct > max_allowed_upside:
        if verdict in ["BUY", "STRONG BUY"]:
            notes.append(f"Forced to DON'T BUY: Extreme upside (+{round(upside_pct*100, 1)}%) exceeds ceiling.")
            return "DON'T BUY"
    return verdict

def run_predictive_pipeline(info, hist, fcf_history, sector, industry, fundamental_score,
                              book_value_per_share, dividend_per_share, roe_pct,
                              pat_yoy_pct, analyst_growth_pct, resolved_pe=None, is_turnaround=False,
                              shares_outstanding=None, qualitative_bonus=0, qualitative_notes=None):
    current_price = info.get('currentPrice')
    if not current_price and hist is not None and not hist.empty:
        current_price = float(hist['Close'].iloc[-1])

    result = {
        "verdict": "OBSERVE", "target_price": None, "entry_range": "N/A", "stop_loss": None,
        "time_horizon": "N/A", "note": None, "model_used": "N/A",
        "composite_score": None, "fundamental_score": fundamental_score,
        "intrinsic_score": None, "technical_score": None, "margin_of_safety": None,
        "discount_rate": None, "growth_used": None, "is_turnaround": is_turnaround,
    }
    if not current_price: return result

    notes = list(qualitative_notes or [])
    beta = info.get('beta') if info.get('beta') else 1.0
    current_rfr = get_dynamic_risk_free_rate()
    ke_pct = min(max((current_rfr + beta * EQUITY_RISK_PREMIUM) * 100, 9), 20)
    growth_pct = pat_yoy_pct if pat_yoy_pct and pat_yoy_pct > 0 else 8.0

    financial = is_financial_sector(sector, industry)
    forced_intrinsic_adjustment = 0

    if financial:
        intrinsic_value = current_price * 1.1
        result["model_used"] = "Financial Sector Baseline"
    else:
        effective_eps = info.get('trailingEps')
        if effective_eps and effective_eps > 0 and pat_yoy_pct and pat_yoy_pct > 15:
            fair_multiple = min(max(pat_yoy_pct, resolved_pe or 20), 40)
            intrinsic_value = round(effective_eps * fair_multiple, 2)
            result["model_used"] = "PEG Adjusted Earnings Multiple"
        elif effective_eps and effective_eps > 0:
            intrinsic_value = round(effective_eps * min(resolved_pe or 20, 35), 2)
            result["model_used"] = "Target P/E"
        else:
            intrinsic_value = current_price * 1.15
            result["model_used"] = "Default Growth Baseline"

    target_price = round(intrinsic_value, 2)
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0

    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist) or (current_price * 0.92)
    entry_low = round(support, 2)
    entry_high = round(support + (0.5 * atr if atr else current_price * 0.02), 2)
    stop_loss = round(max(current_price * 0.5, entry_low - (1.5 * atr if atr else entry_low * 0.05)), 2)

    composite, verdict, intrinsic_score, tech_score = composite_verdict(
        fundamental_score, margin_of_safety, 0.0, arima_direction="NEUTRAL",
        forced_intrinsic_adjustment=forced_intrinsic_adjustment, qualitative_bonus=qualitative_bonus,
    )
    verdict = apply_tiered_sanity_veto(verdict, target_price, current_price, notes, growth_rate=growth_pct)

    result.update({
        "verdict": verdict, "target_price": target_price,
        "entry_range": f"₹{entry_low:,.2f} - ₹{entry_high:,.2f}", "stop_loss": stop_loss,
        "time_horizon": "3-5 Years", "note": " ".join(notes) if notes else None,
        "composite_score": composite, "intrinsic_score": intrinsic_score, "technical_score": tech_score,
        "margin_of_safety": round(margin_of_safety * 100, 1),
        "discount_rate": round(ke_pct, 1), "growth_used": round(growth_pct, 1),
    })
    return result

# ============================================================
# 7. MASTER DATA FETCH (FMP PRIMARY + YFINANCE FALLBACK)
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    # 1. FMP Data Primary Fetch
    fmp_data = fetch_fmp_data(resolved_ticker)
    fmp_prof = fmp_data.get('profile', {})
    fmp_quote = fmp_data.get('quote', {})
    fmp_inc = fmp_data.get('income_statement', [{}])
    fmp_bs = fmp_data.get('balance_sheet', [{}])
    fmp_cf = fmp_data.get('cash_flow', [{}])

    # 2. yfinance Fallback Fetch
    yf_stock = yf.Ticker(resolved_ticker)
    yf_info = yf_stock.info
    hist_full = yf_stock.history(period="1y")
    if hist_full.empty: raise ValueError(f"Could not find price history for '{raw_input}'.")

    # Merge fields: FMP primary, fallback to yfinance
    current_price = fmp_quote.get('price') or fmp_prof.get('price') or yf_info.get('currentPrice') or float(hist_full['Close'].iloc[-1])
    sector = fmp_prof.get('sector') or yf_info.get('sector', 'N/A')
    industry = fmp_prof.get('industry') or yf_info.get('industry', 'N/A')
    is_fin = is_financial_sector(sector, industry)
    sector_profile = classify_sector_profile(sector, industry)

    mcap = fmp_quote.get('marketCap') or fmp_prof.get('mktCap') or yf_info.get('marketCap')
    shares_out = fmp_quote.get('sharesOutstanding') or yf_info.get('sharesOutstanding')
    if not mcap and shares_out and current_price: mcap = shares_out * current_price

    pe_raw = fmp_quote.get('pe') or fmp_prof.get('pe') or yf_info.get('trailingPE')
    pb_raw = fmp_prof.get('priceToBook') or yf_info.get('priceToBook')

    latest_inc = fmp_inc[0] if fmp_inc else {}
    latest_bs = fmp_bs[0] if fmp_bs else {}
    latest_cf = fmp_cf[0] if fmp_cf else {}

    net_inc = latest_inc.get('netIncome') or yf_info.get('netIncomeToCommon')
    total_eq = latest_bs.get('totalStockholdersEquity') or latest_bs.get('totalEquity')
    revenue_latest = latest_inc.get('revenue')
    ebit_latest = latest_inc.get('operatingIncome')
    
    pat_yoy_pct = None
    if len(fmp_inc) >= 5 and fmp_inc[4].get('netIncome') and fmp_inc[4].get('netIncome') != 0:
        old_ni = fmp_inc[4].get('netIncome')
        pat_yoy_pct = round(((net_inc - old_ni) / abs(old_ni)) * 100, 2) if net_inc else None
    if pat_yoy_pct is None:
        # Fallback approximation from yfinance if available
        pat_yoy_pct = yf_info.get('earningsGrowth', 0)
        if pat_yoy_pct: pat_yoy_pct = round(pat_yoy_pct * 100, 2)

    roe_raw = (net_inc / total_eq) if (net_inc and total_eq and total_eq > 0) else yf_info.get('returnOnEquity')
    roe_is_known = is_valid_metric(roe_raw)
    net_margin_final = round((net_inc / revenue_latest) * 100, 2) if (net_inc and revenue_latest and revenue_latest != 0) else None

    total_debt = latest_bs.get('totalDebt') or yf_info.get('totalDebt')
    debt_to_equity = round(total_debt / total_eq, 2) if (total_debt is not None and total_eq and total_eq > 0) else yf_info.get('debtToEquity')
    if debt_to_equity and debt_to_equity > 10: debt_to_equity = debt_to_equity / 100

    interest_exp = latest_inc.get('interestExpense')
    interest_coverage = round(ebit_latest / interest_exp, 2) if (ebit_latest is not None and interest_exp and interest_exp > 0) else yf_info.get('interestCoverage')

    temp_metrics = {
        'pe_ratio': pe_raw, 'peg_ratio': yf_info.get('pegRatio'), 'pb_ratio': pb_raw,
        'pat_yoy': pat_yoy_pct, 'roe': (roe_raw * 100) if roe_is_known else None,
        'ev_ebitda': yf_info.get('evToEbitda'), 'is_financial_sector': is_fin,
        'debt_to_equity': debt_to_equity, 'interest_coverage': interest_coverage,
        'net_margin': net_margin_final, 'sector_profile': sector_profile,
    }
    v_score = score_from_checks(valuation_checks(temp_metrics))
    p_score = score_from_checks(past_performance_checks(temp_metrics))
    h_score = score_from_checks(financial_health_checks(temp_metrics))
    fundamental_score = compute_fundamental_score(v_score, p_score, h_score, is_fin)

    bvps = (total_eq / shares_out) if (total_eq and shares_out and shares_out > 0) else yf_info.get('bookValue')
    div_per_share = fmp_quote.get('lastDividend') or yf_info.get('dividendRate')

    fcf_history = pd.Series([cf.get('freeCashFlow', 0) for cf in fmp_cf if cf.get('freeCashFlow')])
    if fcf_history.empty:
        try: fcf_history = yf_stock.cashflow.loc['Free Cash Flow'].dropna()
        except: fcf_history = None

    predictive_data = run_predictive_pipeline(
        yf_info, hist_full, fcf_history, sector, industry, fundamental_score,
        bvps, div_per_share, roe_raw * 100 if roe_is_known else None, pat_yoy_pct, None,
        resolved_pe=to_float(pe_raw), shares_outstanding=shares_out
    )

    # Build statement DataFrames from FMP with YF fallbacks
    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    try:
        if fmp_inc:
            pnl_df = pd.DataFrame([{
                "Particulars": "Net Sales / Total Income", "Amount (₹ Cr)": round(fmp_inc[0].get('revenue', 0)/10000000, 2)
            }, {
                "Particulars": "Operating Profit", "Amount (₹ Cr)": round(fmp_inc[0].get('operatingIncome', 0)/10000000, 2)
            }, {
                "Particulars": "Net Profit", "Amount (₹ Cr)": round(fmp_inc[0].get('netIncome', 0)/10000000, 2)
            }])
        if fmp_bs:
            bs_df = pd.DataFrame([{
                "Particulars": "Total Equity", "Amount (₹ Cr)": round(fmp_bs[0].get('totalStockholdersEquity', 0)/10000000, 2)
            }, {
                "Particulars": "Total Debt", "Amount (₹ Cr)": round(fmp_bs[0].get('totalDebt', 0)/10000000, 2)
            }, {
                "Particulars": "Total Assets", "Amount (₹ Cr)": round(fmp_bs[0].get('totalAssets', 0)/10000000, 2)
            }])
        if fmp_cf:
            cf_df = pd.DataFrame([{
                "Particulars": "Operating Cash Flow", "Amount (₹ Cr)": round(fmp_cf[0].get('operatingCashFlow', 0)/10000000, 2)
            }, {
                "Particulars": "Free Cash Flow", "Amount (₹ Cr)": round(fmp_cf[0].get('freeCashFlow', 0)/10000000, 2)
            }])
    except: pass

    if pnl_df.empty:
        try:
            fin = yf_stock.financials
            col = fin.columns[0]
            pnl_df = pd.DataFrame([{"Particulars": "Net Profit", "Amount (₹ Cr)": round(fin.loc['Net Income', col]/10000000, 2)}])
        except: pass

    promoters = (yf_info.get("heldPercentInsiders") or 0) * 100
    institutions = (yf_info.get("heldPercentInstitutions") or 0) * 100
    shareholding_dict = {"Data Unavailable": 100} if (promoters == 0 and institutions == 0) else {
        "Promoters": promoters, "Institutions": institutions, "Public": max(0, 100 - (promoters + institutions))
    }

    try: mf_df = yf_stock.mutualfund_holders
    except: mf_df = None
    try: cal_df = yf_stock.calendar
    except: cal_df = None

    metrics = {
        "name": fmp_prof.get('companyName') or yf_info.get("longName", resolved_ticker),
        "price": current_price,
        "pe_ratio": pe_raw if is_valid_metric(pe_raw) else "N/A",
        "pb_ratio": pb_raw if is_valid_metric(pb_raw) else "N/A",
        "peg_ratio": yf_info.get("pegRatio", "N/A"),
        "ev_ebitda": yf_info.get("evToEbitda", "N/A"),
        "roe": f"{round(roe_raw*100, 2)}%" if roe_is_known else "N/A",
        "ebitda_margin": "N/A",
        "operating_margin": None, "revenue_cagr": None, "nim_proxy": None,
        "debt_to_equity": debt_to_equity if debt_to_equity is not None else "N/A",
        "interest_coverage": interest_coverage if interest_coverage is not None else "N/A",
        "net_margin": f"{net_margin_final}%" if net_margin_final is not None else "N/A",
        "dividend_yield": f"{round(fmp_quote.get('dividendYield', 0)*100, 2)}%" if fmp_quote.get('dividendYield') else "N/A",
        "pat_yoy": f"{pat_yoy_pct}%" if pat_yoy_pct is not None else "N/A",
        "pat_qoq": "N/A",
        "market_cap": mcap, "sector": sector, "industry": industry,
        "is_financial_sector": is_fin, "is_turnaround": False,
        "sector_profile": sector_profile,
        "fifty_two_high": fmp_quote.get('yearHigh') or yf_info.get("fiftyTwoWeekHigh", "N/A"),
        "fifty_two_low": fmp_quote.get('yearLow') or yf_info.get("fiftyTwoWeekLow", "N/A"),
        "business_summary": fmp_prof.get('description') or yf_info.get("longBusinessSummary"),
        "website": fmp_prof.get('website', "N/A"),
        "company_officers": yf_info.get("companyOfficers", []),
        "recent_news": fetch_google_news(f"{fmp_prof.get('companyName', resolved_ticker)} stock news"),
        "shareholding": shareholding_dict,
        "mutual_funds": mf_df,
        "calendar": cal_df,
        "target_mean_price": fmp_prof.get('priceTarget') or yf_info.get("targetMeanPrice"),
        "recommendation_mean": yf_info.get("recommendationMean"),
        "v_score": v_score, "p_score": p_score, "h_score": h_score,
        "working_ticker": resolved_ticker, "history": hist_full.reset_index(),
        "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": predictive_data, "fair_value": predictive_data['target_price'],
        "currency": "₹", "fundamental_score": fundamental_score,
        "best_alternative": None
    }

    # Sector alternative scanner
    if predictive_data['verdict'] in ["DON'T BUY", "OBSERVE"]:
        peers = SECTOR_PEERS.get(sector_profile, SECTOR_PEERS["standard"])
        for peer in peers:
            if peer == resolved_ticker: continue
            try:
                p_info = yf.Ticker(peer).info
                if p_info.get('currentPrice'):
                    metrics['best_alternative'] = {
                        "name": p_info.get("shortName", peer), "ticker": peer,
                        "price": p_info.get("currentPrice"),
                        "pe": round(p_info.get("trailingPE", 0), 1) if p_info.get("trailingPE") else "N/A",
                        "pb": round(p_info.get("priceToBook", 0), 1) if p_info.get("priceToBook") else "N/A"
                    }
                    break
            except: pass

    return metrics

# ============================================================
# 8. UI PLOTLY CHARTS & COMPONENTS
# ============================================================
def price_history_chart(hist_df, currency):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=1.5), fill='tozeroy', fillcolor='rgba(56,189,248,0.08)'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=20, l=10, r=10), xaxis=dict(showgrid=False), yaxis=dict(showgrid=False, title=currency))
    return fig

def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    fig.add_trace(go.Bar(x=['Current Price'], y=[price], marker_color=BLUE, text=[f"{currency}{price:,.2f}"], textposition='auto'))
    fig.add_trace(go.Bar(x=['Modeled Fair Value'], y=[fv], marker_color=GREEN, text=[f"{currency}{fv:,.2f}"], textposition='auto'))
    diff_pct = round(((price - fv) / fv) * 100, 1) if fv else None
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=320, margin=dict(t=20, b=20, l=10, r=10), showlegend=False, yaxis=dict(showgrid=False))
    return fig, diff_pct

def projection_path_chart(hist_df, target_price):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=2), name='Historical Price'))
    last_date, last_price = hist_df['Date'].iloc[-1], hist_df['Close'].iloc[-1]
    fig.add_trace(go.Scatter(x=[last_date, last_date + timedelta(days=365)], y=[last_price, target_price], mode='lines', line=dict(color=GOLD, width=2, dash='dot'), name='Illustrative Target'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=300, margin=dict(t=20, b=20, l=10, r=10), legend=dict(orientation="h", y=-0.2))
    return fig

def analysis_radar_chart(m, pred):
    categories = ['Fundamentals', 'Valuation', 'Momentum']
    values = [m.get('fundamental_score', 50), pred.get('intrinsic_score', 50), pred.get('technical_score', 50)]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=values + [values[0]], theta=categories + [categories[0]], fill='toself', fillcolor='rgba(234,179,8,0.35)', line=dict(color=GOLD, width=2)))
    fig.update_layout(polar=dict(bgcolor=BG, radialaxis=dict(visible=False, range=[0, 100]), angularaxis=dict(color=MUTED, gridcolor=BORDER)), showlegend=False, paper_bgcolor=BG, margin=dict(t=10, b=10, l=30, r=30), height=230)
    return fig

def ownership_donut(shareholding):
    colors = [BLUE, PURPLE, GOLD] if "Data Unavailable" not in shareholding else [MUTED]
    fig = go.Figure(data=[go.Pie(labels=list(shareholding.keys()), values=list(shareholding.values()), hole=.5, marker_colors=colors)])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=240, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
    return fig

def render_52week_range(current_price, low_52, high_52, currency="₹"):
    if current_price is None or low_52 is None or high_52 is None or high_52 <= low_52: return
    pct_position = ((current_price - low_52) / (high_52 - low_52)) * 100
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[100], y=["Range"], orientation="h", marker=dict(color="#1F1F1F"), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pct_position], y=["Range"], mode="markers", marker=dict(color="#38BDF8", size=16, symbol="diamond"), name="Current Price"))
    fig.update_layout(height=50, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 100]), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), showlegend=False)
    st.markdown(f"<div style='color:{MUTED}; font-size:0.85em; text-align:center;'><b>52W Low:</b> {currency}{low_52:,.2f} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Current:</b> <span style='color:#E6E6E6;'>{currency}{current_price:,.2f}</span> &nbsp;&nbsp;|&nbsp;&nbsp; <b>52W High:</b> {currency}{high_52:,.2f}</div>", unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

def render_scorecard_badges(q_score, v_score, f_score):
    def get_badge(score, is_val=False):
        if score is None: return "N/A", "N/A", MUTED
        rating = max(1, min(5, round((score / 100) * 5)))
        if is_val: lbl = "VERY CHEAP" if rating==5 else "ATTRACTIVE" if rating==4 else "FAIR" if rating==3 else "EXPENSIVE" if rating==2 else "VERY EXPENSIVE"
        else: lbl = "EXCELLENT" if rating==5 else "GOOD" if rating==4 else "AVERAGE" if rating==3 else "WEAK" if rating==2 else "POOR"
        clr = GREEN if rating >= 4 else GOLD if rating == 3 else RED
        return rating, lbl, clr

    q_rat, q_lbl, q_clr = get_badge(q_score)
    v_rat, v_lbl, v_clr = get_badge(v_score, is_val=True)
    f_rat, f_lbl, f_clr = get_badge(f_score)

    st.markdown(f"""
    <div style="display: flex; gap: 15px; margin-bottom: 20px;">
        <div style="background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:12px 18px; flex:1;">
            <div style="color:{MUTED}; font-size:0.8em; font-weight:600; text-transform:uppercase;">Quality</div>
            <div style="margin-top:5px; display:flex; align-items:center; gap:10px;">
                <span style="color:{q_clr}; border:1px solid {q_clr}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.85em;">{q_lbl}</span>
                <span style="color:#E6E6E6; font-weight:700; font-size:1em;">{q_rat}/5</span>
            </div>
        </div>
        <div style="background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:12px 18px; flex:1;">
            <div style="color:{MUTED}; font-size:0.8em; font-weight:600; text-transform:uppercase;">Valuation</div>
            <div style="margin-top:5px; display:flex; align-items:center; gap:10px;">
                <span style="color:{v_clr}; border:1px solid {v_clr}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.85em;">{v_lbl}</span>
                <span style="color:#E6E6E6; font-weight:700; font-size:1em;">{v_rat}/5</span>
            </div>
        </div>
        <div style="background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:12px 18px; flex:1;">
            <div style="color:{MUTED}; font-size:0.8em; font-weight:600; text-transform:uppercase;">Financial Health</div>
            <div style="margin-top:5px; display:flex; align-items:center; gap:10px;">
                <span style="color:{f_clr}; border:1px solid {f_clr}; padding:2px 8px; border-radius:4px; font-weight:700; font-size:0.85em;">{f_lbl}</span>
                <span style="color:#E6E6E6; font-weight:700; font-size:1em;">{f_rat}/5</span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

def render_valuation_spectrum(current_price, fair_value, currency="₹"):
    if not fair_value or not current_price: return
    attractive_limit = round(fair_value * 0.85, 2)
    expensive_limit = round(fair_value * 1.50, 2)
    high_limit = round(fair_value * 2.50, 2)
    pos = 15 if current_price <= attractive_limit else (90 if current_price >= high_limit else 15 + ((current_price - attractive_limit) / (high_limit - attractive_limit)) * 75)

    fig = go.Figure()
    fig.add_trace(go.Bar(x=[100], y=["Valuation"], orientation="h", marker=dict(color=[pos], colorscale=[[0.0, GREEN], [0.4, GOLD], [1.0, RED]], showscale=False), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pos], y=["Valuation"], mode="markers", marker=dict(color="#FFFFFF", size=18, symbol="triangle-up"), name="Current Price"))
    fig.update_layout(height=80, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 100]), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), showlegend=False)

    st.markdown(f"##### Valuation Zone — Current Price: **{currency}{current_price:,.2f}**")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

def render_analyst_consensus(target, current, rec, currency="₹"):
    if not target or not current: return
    upside = ((target - current) / current) * 100
    color = GREEN if upside > 0 else RED
    st.markdown(f"""
    <div style='background:{CARD_BG}; border:1px solid {BORDER}; border-radius:8px; padding:15px; margin-top:15px;'>
        <div style='color:{MUTED}; font-size:0.85em; font-weight:600; text-transform:uppercase;'>Analyst Consensus Target</div>
        <div style='display:flex; justify-content:space-between; align-items:flex-end; margin-top:8px;'>
            <div style='font-size:1.8em; font-weight:800;'>{currency}{target:,.2f}</div>
            <div style='color:{color}; font-weight:700; font-size:1.1em;'>{'+' if upside>0 else ''}{upside:.2f}% Expected</div>
        </div>
        <div style='color:{MUTED}; font-size:0.8em; margin-top:4px;'>Recommendation Mean: {rec or 'N/A'} (1=Strong Buy, 5=Sell)</div>
    </div>
    """, unsafe_allow_html=True)

def render_corporate_events_and_mfs(cal_df, mf_df):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📅 Corporate Events")
        if cal_df is not None and not cal_df.empty:
            st.dataframe(cal_df, use_container_width=True, hide_index=True)
        else: st.caption("No upcoming corporate events found.")
    with c2:
        st.markdown("##### 🏦 Top Mutual Funds Invested")
        if mf_df is not None and not mf_df.empty:
            try:
                df_clean = mf_df[["Holder", "Shares", "% Out"]].rename(columns={"Holder": "Mutual Fund Scheme", "Shares": "Shares Held", "% Out": "% Stake"})
                df_clean["% Stake"] = df_clean["% Stake"].apply(lambda x: f"{x * 100:.2f}%" if pd.notna(x) else "N/A")
                st.dataframe(df_clean, use_container_width=True, hide_index=True)
            except Exception: st.dataframe(mf_df, use_container_width=True)
        else: st.caption("No Mutual Fund scheme data available.")

def custom_metric(label, value):
    st.markdown(f'<div style="background-color: {CARD_BG}; border: 1px solid {BORDER}; padding: 12px 15px; border-radius: 8px; margin-bottom: 12px;"><div style="font-size: 11px; color: {MUTED}; text-transform: uppercase; font-weight: 600; margin-bottom: 4px;">{label}</div><div style="font-size: 20px; font-weight: 700; color: #FFFFFF;">{value}</div></div>', unsafe_allow_html=True)

def card(title, body_html): st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

# ============================================================
# 9. AI NARRATIVE
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)
    sys = """You are a Senior Equity Analyst acting as the final synthesis layer over a quantitative model.
Output exactly 8 numbered sections:
1. VALUATION & FAIR VALUE
2. FUTURE GROWTH & OUTLOOK
3. PAST PERFORMANCE & EARNINGS QUALITY
4. FINANCIAL HEALTH & BALANCE SHEET
5. DIVIDEND & CAPITAL ALLOCATION
6. MANAGEMENT & COMPENSATION
7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
8. NARRATIVE VERDICT
Provide ONLY narrative reasoning — no invented numbers beyond what is given to you."""
    pred = metrics.get('predictive', {})
    news_titles = "; ".join([n['title'] for n in (metrics.get('recent_news') or [])[:5]]) or "No headlines found."
    pmt = f"Target: {metrics['name']} ({ticker}). Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. System Verdict: {pred.get('verdict')}. News: {news_titles}"
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt,
                                          config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

# ============================================================
# 10. APP STATE & HEADER BANNER
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None

def get_base64_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode('utf-8')
    except Exception:
        return None

banner_b64 = get_base64_image("Logo64.png")

if banner_b64:
    st.markdown(f'''
    <div style="width: 100%; text-align: center; margin-bottom: 25px; border-bottom: 1px solid {BORDER}; padding-bottom: 20px;">
        <img src="data:image/png;base64,{banner_b64}" style="width: 100%; max-height: 220px; object-fit: cover; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);">
    </div>
    ''', unsafe_allow_html=True)
else:
    st.markdown('''
    <div class="swf-title-container" style="padding: 10px 0 30px 0;">
        <div class="swf-title" style="font-size: 3em;">ATHENAEUM FINANCIAL INTELLIGENCE</div>
    </div>
    ''', unsafe_allow_html=True)
    
col_input, col_btn = st.columns([4, 1])
with col_input: stock_input = st.text_input("Enter Stock Name or Ticker:", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn: generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked and stock_input.strip():
    with st.spinner('Fetching primary data from FMP with Yahoo fallback...'):
        try:
            rt = resolve_name_to_ticker(stock_input)
            metrics = fetch_stock_data(rt, stock_input)
            final_ticker = metrics.pop('working_ticker', rt)

            ai_text = generate_comprehensive_report(metrics, final_ticker)
            sections_list = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', ai_text, flags=re.IGNORECASE) if s.strip()]
            if len(sections_list) > 8: sections_list = sections_list[-8:]

            st.session_state.report_data = {"metrics": metrics, "ai_text": ai_text, "narrative_sections": sections_list, "ticker": final_ticker}
        except Exception as e:
            st.error(f"Error: {e}")

# ============================================================
# 11. SINGLE-PAGE REPORT (ALL SECTIONS RESTORED)
# ============================================================
if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    ticker = data['ticker']
    narrative = data['narrative_sections']
    def narrative_for(idx): return re.sub(r'^(?:\*\*|__)?\d+\.\s+[A-Z&\s]+(?:\*\*|__)?\n+', '', narrative[idx], flags=re.IGNORECASE).strip() if idx < len(narrative) else "Detailed qualitative breakdown unavailable."

    pred = m['predictive']
    current_rating = pred['verdict']
    rc = rating_color(current_rating)
    currency = m.get('currency', '₹')

    val_checks, past_checks, health_checks, div_checks = valuation_checks(m), past_performance_checks(m), financial_health_checks(m), dividend_checks(m)

    # Header & Radar
    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        st.markdown(f'<div class="swf-card"><div style="display:flex; justify-content:space-between; align-items:flex-start;"><div><div style="color:{MUTED}; font-size:0.85em;">Stocks / {m.get("industry","N/A")}</div><div style="font-size:1.4em; font-weight:800;">{m["name"]}</div><div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report</div><span class="swf-badge" style="margin-top:8px; display:inline-block;">Verdict: <span style="color:{rc};">{current_rating}</span></span></div><div style="text-align:right;"><div style="font-size:1.6em; font-weight:800;">{currency}{m["price"]}</div></div></div></div>', unsafe_allow_html=True)
        render_scorecard_badges(m.get('p_score'), m.get('v_score'), m.get('h_score'))
        hist_df = m.get('history')
        if hist_df is not None and not hist_df.empty: st.plotly_chart(price_history_chart(hist_df, currency), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Composite Score Radar</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(m, pred), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    # News Catalysts
    news_items = m.get('recent_news', [])
    news_html = "<ul style='padding-left: 20px; margin-bottom: 0;'>" + "".join([f"<li style='margin-bottom: 8px;'><a href='{item['link']}' target='_blank' style='color:{BLUE}; text-decoration:none;'>{item['title']}</a></li>" for item in news_items[:5]]) + "</ul>" if news_items else "<div class='swf-sub'>No recent news found.</div>"
    card("Recent News & Market Catalysts", news_html)
    st.markdown("---")

    # Overview
    st.markdown('<div class="swf-section-title">Company Overview</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1: custom_metric("Current Price", f"{currency}{m['price']}"); custom_metric("P/E Ratio", f"{m['pe_ratio']}x" if m['pe_ratio'] != "N/A" else "N/A")
    with c2: custom_metric("P/BV Ratio", f"{m['pb_ratio']}x" if m['pb_ratio'] != "N/A" else "N/A"); custom_metric("ROE", f"{m['roe']}")
    with c3: custom_metric("EV/EBITDA", f"{m['ev_ebitda']}"); custom_metric("PAT Growth (YoY)", f"{m['pat_yoy']}")
    with c4: custom_metric("Debt-to-Equity", f"{m['debt_to_equity']}"); custom_metric("Dividend Yield", f"{m['dividend_yield']}")
    
    render_52week_range(m.get('price'), to_float(m.get('fifty_two_low')), to_float(m.get('fifty_two_high')), currency)
    card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{m.get('business_summary', 'Summary not available.')}</p>")
    st.markdown("---")

    # 1. Valuation
    st.markdown('<div class="swf-section-title">1. Valuation</div>', unsafe_allow_html=True)
    card("Valuation Checklist", render_checks(val_checks))
    if m.get('fair_value'):
        fig, diff_pct = fair_value_bar(m['price'], m['fair_value'], currency)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        render_valuation_spectrum(m['price'], m['fair_value'], currency)
    render_analyst_consensus(m.get('target_mean_price'), m.get('price'), m.get('recommendation_mean'), currency)
    card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(0)}</p>")
    st.markdown("---")

    # 2. Future Growth
    st.markdown('<div class="swf-section-title">2. Future Growth &amp; Outlook</div>', unsafe_allow_html=True)
    fg1, fg2, fg3 = st.columns(3)
    with fg1: custom_metric("Modeled Target", f"{currency}{pred['target_price']}")
    with fg2: custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
    with fg3: custom_metric("Growth Assumption", f"{pred.get('growth_used','N/A')}%")
    if m.get('fair_value'): st.plotly_chart(projection_path_chart(m['history'], m['fair_value']), use_container_width=True, config={'displayModeBar': False})
    card("Future Growth Narrative", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(1)}</p>")
    st.markdown("---")

    # 3. Past Performance
    st.markdown('<div class="swf-section-title">3. Past Performance</div>', unsafe_allow_html=True)
    card("Past Performance Checklist", render_checks(past_checks))
    if not m['pnl_df'].empty: st.markdown("##### Profit & Loss (Cr)"); st.dataframe(m['pnl_df'], use_container_width=True, hide_index=True)
    card("Past Performance Narrative", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(2)}</p>")
    st.markdown("---")

    # 4. Financial Health
    st.markdown('<div class="swf-section-title">4. Financial Health</div>', unsafe_allow_html=True)
    card("Financial Health Checklist", render_checks(health_checks))
    tab_bs, tab_cf = st.tabs(["Balance Sheet", "Cash Flows"])
    with tab_bs:
        if not m['bs_df'].empty: st.dataframe(m['bs_df'], use_container_width=True, hide_index=True)
    with tab_cf:
        if not m['cf_df'].empty: st.dataframe(m['cf_df'], use_container_width=True, hide_index=True)
    card("Financial Health Narrative", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(3)}</p>")
    st.markdown("---")

    # 5. Dividend
    st.markdown('<div class="swf-section-title">5. Dividend</div>', unsafe_allow_html=True)
    card("Dividend Checklist", render_checks(div_checks))
    card("Dividend Narrative", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(4)}</p>")
    st.markdown("---")

    # 6. Management
    st.markdown('<div class="swf-section-title">6. Management &amp; Leadership</div>', unsafe_allow_html=True)
    if m['company_officers']: st.dataframe(pd.DataFrame([{"Name": o.get('name', 'N/A'), "Position": o.get('title', 'N/A')} for o in m['company_officers']]), use_container_width=True, hide_index=True)
    card("Management Narrative", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(5)}</p>")
    st.markdown("---")

    # 7. Ownership Structure
    st.markdown('<div class="swf-section-title">7. Ownership Structure</div>', unsafe_allow_html=True)
    st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
    render_corporate_events_and_mfs(m.get('calendar'), m.get('mutual_funds'))
    card("Ownership Narrative", f"<p style='color:#c9d1d9; font-size:0.85em;'>{narrative_for(6)}</p>")
    st.markdown("---")

    # 8. Verdict & Summary
    st.markdown('<div class="swf-section-title">8. Verdict &amp; Summary</div>', unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:1.15em; margin-bottom:14px;'><b>Composite System Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{current_rating}</span></div>", unsafe_allow_html=True)

    if current_rating in ["BUY", "STRONG BUY"]:
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry:</b> {pred['entry_range']}<br><b>Horizon:</b> {pred['time_horizon']}<br><b>Target:</b> {currency}{pred['target_price']}<br><b>Stop Loss:</b> {currency}{pred['stop_loss']}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Target:</b> {currency}{pred['target_price']}</div>", unsafe_allow_html=True)

    if current_rating in ["DON'T BUY", "OBSERVE"] and m.get('best_alternative'):
        alt = m['best_alternative']
        st.markdown(f"""
        <div style="background-color: rgba(56, 189, 248, 0.1); border: 1px solid {BLUE}; border-radius: 8px; padding: 15px; margin-bottom: 20px;">
            <div style="color: {BLUE}; font-weight: 700; font-size: 1.1em; margin-bottom: 5px;">💡 Recommended Sector Alternative</div>
            <div style="display: flex; gap: 20px; font-weight: 600;">
                <div>Stock: <span style="color: {GOLD};">{alt['name']} ({alt['ticker']})</span></div>
                <div>Price: {currency}{alt['price']}</div>
                <div>P/E: {alt['pe']}x</div>
            </div>
        </div>
        """, unsafe_allow_html=True)

    card("Narrative Summary", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em;'>{style_verdict_text(narrative_for(7))}</p>")
