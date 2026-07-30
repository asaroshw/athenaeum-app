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
    page_icon="Logo.png", 
    layout="wide"
)

GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")

GOLD, BG, CARD_BG, BORDER = "#EAB308", "#000000", "#0D0D0D", "#1F1F1F"
GREEN, RED, ORANGE, MUTED, BLUE, PURPLE = "#3FB950", "#F85149", "#F97316", "#8B949E", "#38BDF8", "#A855F7"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    /* Import Orbitron as a standard fallback for the futuristic look if Quironax isn't locally installed */
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@800&display=swap');
    
    html, body, [class*="st-"], .stApp, div, span, p, table, th, td, label {{ font-family: 'Inter', sans-serif !important; }}
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    .swf-title-container {{ text-align: center; border-bottom: 1px solid {BORDER}; margin-bottom: 20px; }}
    
    /* Updated Title CSS with Quironax and massive font size */
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
    
    .swf-card {{ background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; }}
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
# 2. CORE UTILITIES
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

def fmt_indian_currency(val, currency="₹"):
    if not is_valid_metric(val): return "N/A"
    try:
        num = float(str(val).replace(',', '').replace('₹', '').replace('%', '').strip())
        if abs(num) >= 10000000: return f"{currency}{num/10000000:,.2f} Cr"
        elif abs(num) >= 100000: return f"{currency}{num/100000:,.2f} Lakh"
        return f"{currency}{num:,.2f}"
    except: return f"{currency} {val}"

def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit(): return stock_str + '.BO'
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
    """Fetches the 10-Year Indian Government Bond yield, falls back to 6.5%."""
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
    if any(kw in text for kw in MATERIALS_KEYWORDS):
        return "materials"
    if any(kw in text for kw in CAPEX_INTENSIVE_KEYWORDS):
        return "capex_intensive"
    if any(kw in text for kw in CYCLICAL_KEYWORDS):
        return "cyclical"
    return "standard"

STANDARD_REVENUE_KEYS = ['Total Revenue', 'Operating Revenue']
BANK_REVENUE_KEYS = ['Total Revenue', 'Total Operating Income', 'Interest Income',
                      'Total Interest Income', 'Operating Revenue']
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
        notes.append(f"Qualitative bonus (+15): multiple positive catalysts detected in recent "
                      f"news/business summary ({', '.join(catalyst_hits[:4])}).")
    elif len(catalyst_hits) == 1:
        bonus += 10
        notes.append(f"Qualitative bonus (+10): a positive catalyst was detected ({catalyst_hits[0]}).")
    if risk_hits:
        bonus -= 20
        notes.append(f"Qualitative penalty (-20): risk keyword(s) detected in recent news "
                      f"({', '.join(risk_hits[:3])}) — treat with caution.")
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
            if 5 <= v <= 50:
                growth_pct_found = v
        except Exception:
            pass
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
        if pe < 0:
            checks.append(("Profitable on a P/E basis", False,
                            f"P/E is negative ({pe:.2f}x) — the company is currently loss-making."))
        else:
            threshold = 45 if (pat_yoy is not None and pat_yoy > 30) else 25
            checks.append((f"Reasonable P/E (<{threshold}x{' — growth-adjusted' if threshold==45 else ''})",
                            pe < threshold, f"Trailing P/E of {pe:.2f}x"))

    if peg is not None:
        if peg < 0:
            checks.append(("Positive PEG", False,
                            f"PEG is negative ({peg:.2f}) — implies shrinking earnings or a loss-making company."))
        elif pe is not None and pe > 0 and pat_yoy is not None and pat_yoy > 0:
            checks.append(("Attractive PEG (<1.5)", peg < 1.5, f"PEG ratio of {peg:.2f}"))

    if pb is not None:
        threshold = 3.0 if is_fin else 5.0
        checks.append((f"Reasonable P/B (<{threshold:g}x)", 0 < pb < threshold, f"Price-to-Book of {pb:.2f}x"))

    if is_fin and pb is not None and m.get('justified_pb'):
        jpb = m['justified_pb']
        checks.append(("P/B vs Excess-ROE Justified P/B", pb < jpb,
                        f"Actual P/B {pb:.2f}x vs a model-justified P/B of {jpb:.2f}x"))

    if not is_fin and ev_ebitda is not None:
        if ev_ebitda < 0:
            checks.append(("Positive EV/EBITDA", False,
                            f"EV/EBITDA is negative ({ev_ebitda:.2f}x) — implies operating losses."))
        else:
            checks.append(("Reasonable EV/EBITDA (<15x)", ev_ebitda < 15, f"EV/EBITDA of {ev_ebitda:.2f}x"))

    return checks

def past_performance_checks(m):
    yoy, qoq = to_float(m.get('pat_yoy')), to_float(m.get('pat_qoq'))
    roe, margin = to_float(m.get('roe')), to_float(m.get('net_margin'))
    opm = to_float(m.get('operating_margin'))
    rev_cagr = to_float(m.get('revenue_cagr'))
    sector_profile = m.get('sector_profile', 'standard')
    checks = []
    if yoy is not None:
        checks.append(("Positive Earnings Growth (YoY)", yoy > 0, f"PAT YoY growth of {yoy:.2f}%"))
    if yoy is not None and qoq is not None:
        checks.append(("Accelerating Growth", qoq > yoy, "Comparing most recent quarter growth to the yearly figure"))
    if roe is not None:
        checks.append(("Strong Return on Equity (>15%)", roe > 15, f"ROE of {roe:.2f}%"))
    if margin is not None:
        if sector_profile in ["cyclical", "materials", "capex_intensive"] and opm is not None and opm > 15 and rev_cagr is not None and rev_cagr > 8:
            checks.append(("Healthy Net Margin (cyclical-adjusted, >6%)", margin > 6,
                            f"Net margin of {margin:.2f}% — bar relaxed given strong OPM/CAGR."))
        else:
            checks.append(("Healthy Net Margin (>10%)", margin > 10, f"Net margin of {margin:.2f}%"))
    return checks

def financial_health_checks(m):
    de = to_float(m.get('debt_to_equity'))
    ic = to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if de is not None:
        if de < 0:
            checks.append(("Positive Shareholder Equity", False,
                            f"Debt-to-equity is negative ({de:.2f}) — implies negative shareholders' equity."))
        else:
            threshold, label = (10.0, "Leverage in line with a lending-book business model (D/E < 10x)") if is_fin \
                else (1.0, "Low Leverage (D/E < 1.0)")
            checks.append((label, de < threshold, f"Debt-to-equity of {de:.2f}"))
    if ic is not None:
        checks.append(("Comfortable Interest Coverage (>3x)", ic > 3, f"EBIT covers interest expense {ic:.2f}x"))
    if is_fin and m.get('nim_proxy') is not None:
        nim = m['nim_proxy']
        checks.append(("Positive Net Interest Margin (approx.)", nim > 0,
                        f"Approximate NIM of {nim:.2f}%"))
    return checks

def dividend_checks(m):
    dy_str = str(m.get('dividend_yield', ''))
    if "doesn't pay" in dy_str.lower():
        return [("Notable Dividend (>1.5%)", False, "Stock doesn't pay dividends")]
    dy = to_float(dy_str)
    return [("Notable Dividend (>1.5%)", dy is not None and dy > 1.5, f"Dividend yield: {dy:.2f}%" if dy else "N/A")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else None

def render_checks(checks):
    if not checks:
        return "<div class='swf-check-na'>&#8213; Not enough data to run this checklist.</div>"
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

def justified_pb_fair_value(roe_pct, ke_pct, growth_pct, book_value_per_share, pb_floor=0.4, pb_cap=8.0):
    if not book_value_per_share or book_value_per_share <= 0 or roe_pct is None:
        return None, None
    roe, ke, g = roe_pct / 100, ke_pct / 100, growth_pct / 100
    if ke <= g:
        g = ke - 0.02
    jpb = 1 + (roe - ke) / (ke - g)
    jpb = min(max(jpb, pb_floor), pb_cap)
    return round(jpb, 2), round(jpb * book_value_per_share, 2)

def ddm_fair_value(dividend_per_share, ke_pct, growth_pct):
    if not dividend_per_share or dividend_per_share <= 0:
        return None
    ke, g = ke_pct / 100, growth_pct / 100
    if ke <= g:
        g = ke - 0.02
    return round((dividend_per_share * (1 + g)) / (ke - g), 2)

def composite_verdict(fundamental_score, margin_of_safety, drift, arima_direction=None,
                      forced_intrinsic_adjustment=0, qualitative_bonus=0):
    W_FUNDAMENTAL, W_INTRINSIC, W_TECHNICAL = 0.40, 0.35, 0.25
    intrinsic_score = min(max(50 + margin_of_safety * 150, 0), 100)
    intrinsic_score = min(max(intrinsic_score + forced_intrinsic_adjustment, 0), 100)
    tech_score = min(max(50 + (drift or 0) * 100, 0), 100)
    if arima_direction == "UP":
        tech_score = min(100, tech_score + 10)
    elif arima_direction == "DOWN":
        tech_score = max(0, tech_score - 10)
    composite = W_FUNDAMENTAL * fundamental_score + W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score
    composite = min(max(composite + qualitative_bonus, 0), 100)
    if composite >= 75: verdict = "STRONG BUY"
    elif composite >= 60: verdict = "BUY"
    elif composite >= 40: verdict = "OBSERVE"
    else: verdict = "DON'T BUY"
    return round(composite, 1), verdict, round(intrinsic_score, 1), round(tech_score, 1)

VERDICT_RANK = {"DON'T BUY": 0, "OBSERVE": 1, "BUY": 2, "STRONG BUY": 3}

def apply_tiered_sanity_veto(verdict, target_price, current_price, notes, growth_rate=8.0):
    if target_price is None or not current_price:
        return verdict
        
    downside_pct = (current_price - target_price) / current_price
    upside_pct = (target_price - current_price) / current_price
    
    if downside_pct > 0.15:
        if verdict != "DON'T BUY":
            notes.append(f"Model indicates {round(downside_pct*100,1)}% downside. Treated as OBSERVE or DON'T BUY based on fundamental strength.")
        return "DON'T BUY" if downside_pct > 0.40 else "OBSERVE"
        
    elif downside_pct > 0:
        if VERDICT_RANK.get(verdict, 1) > VERDICT_RANK["OBSERVE"]:
            notes.append(f"Downgraded to OBSERVE: the modeled target price is {round(downside_pct*100,1)}% below the current price.")
            return "OBSERVE"
            
    # --- DYNAMIC UPSIDE CEILING BASED ON GROWTH ---
    max_allowed_upside = 2.50 if (growth_rate and growth_rate >= 25) else 1.50
    
    if upside_pct > max_allowed_upside:
        if verdict in ["BUY", "STRONG BUY"]:
            notes.append(f"Forced to DON'T BUY: Extreme upside (+{round(upside_pct*100, 1)}%) exceeds growth-adjusted ceiling (+{int(max_allowed_upside*100)}%).")
            return "DON'T BUY"
            
    return verdict

def run_predictive_pipeline(info, hist, fcf_history, sector, industry, fundamental_score,
                              book_value_per_share, dividend_per_share, roe_pct,
                              pat_yoy_pct, analyst_growth_pct,
                              precomputed_jpb=None, precomputed_ddm=None,
                              resolved_pe=None, is_turnaround=False, latest_quarter_net_income=None,
                              shares_outstanding=None, qualitative_bonus=0, qualitative_notes=None,
                              sector_profile="standard", order_book_hits=None, growth_pct_from_news=None):
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
    if not current_price:
        return result

    notes = list(qualitative_notes or [])
    order_book_hits = order_book_hits or []
    beta = info.get('beta')
    if beta is None or pd.isna(beta) or beta <= 0:
        beta = 1.0
        
    current_rfr = get_dynamic_risk_free_rate()
    ke_pct = min(max((current_rfr + beta * EQUITY_RISK_PREMIUM) * 100, 9), 20)

    growth_pct = 8.0
    if analyst_growth_pct and analyst_growth_pct > 0:
        growth_pct = min(max(analyst_growth_pct, 5), 25)
    elif pat_yoy_pct and pat_yoy_pct > 0:
        growth_pct = min(max(pat_yoy_pct, 5), 25)
        
    if is_turnaround:
        growth_pct = max(growth_pct, 20.0)
        notes.append(f"Turnaround detected: forward growth assumption raised to {growth_pct:.0f}%.")

    if order_book_hits:
        if growth_pct_from_news:
            new_growth = min(max(growth_pct_from_news, growth_pct), 30)
            growth_pct = new_growth
        else:
            growth_pct = min(growth_pct + 5, 25)

    financial = is_financial_sector(sector, industry)
    forced_intrinsic_adjustment = 0

    if financial:
        jpb_ratio, jpb_value = (precomputed_jpb if precomputed_jpb is not None
                                  else justified_pb_fair_value(roe_pct, ke_pct, growth_pct, book_value_per_share))
        ddm_val = precomputed_ddm if precomputed_ddm is not None else ddm_fair_value(dividend_per_share, ke_pct, growth_pct)
        if jpb_value and ddm_val:
            intrinsic_value = (jpb_value + ddm_val) / 2
            result["model_used"] = "Blended: Excess-ROE (Justified P/B) + DDM"
        elif jpb_value:
            intrinsic_value = jpb_value
            result["model_used"] = "Excess Return on Equity (Justified P/B)"
        elif ddm_val:
            intrinsic_value = ddm_val
            result["model_used"] = "Dividend Discount Model (DDM)"
        else:
            intrinsic_value = current_price
            forced_intrinsic_adjustment = -30
            result["model_used"] = "No valid financial-sector inputs"
    else:
        shares = info.get('sharesOutstanding') or shares_outstanding
        trailing_eps = info.get('trailingEps')
        effective_eps = trailing_eps
        
        if is_turnaround and latest_quarter_net_income and latest_quarter_net_income > 0 and shares:
            effective_eps = (latest_quarter_net_income / shares) * 4
            
        # --- FIXED PRIORITY: PEG / Earnings Multiple Override for High Growth ---
        if effective_eps and effective_eps > 0 and pat_yoy_pct and pat_yoy_pct > 15:
            historical_pe = resolved_pe if (resolved_pe and resolved_pe > 0) else 20.0
            # Use growth rate to scale a justified forward multiple
            fair_multiple = min(max(pat_yoy_pct, historical_pe), 40)
            intrinsic_value = round(effective_eps * fair_multiple, 2)
            result["model_used"] = "PEG Adjusted Earnings Multiple"
            
        elif sector_profile in ["capex_intensive", "cyclical", "materials"] and effective_eps and effective_eps > 0:
            historical_pe = resolved_pe if (resolved_pe and resolved_pe > 0) else 20.0
            target_pe = min(max(historical_pe, 15), 35)
            intrinsic_value = round(effective_eps * target_pe, 2)
            result["model_used"] = "Target P/E (Capex/Cyclical Adjusted)"
            
        elif fcf_history is not None and len(fcf_history) > 0:
            weights = np.arange(1, len(fcf_history) + 1)
            avg_fcf = float(np.average(fcf_history, weights=weights))
            fcf_per_share = (avg_fcf / shares) if (avg_fcf and shares and shares > 0) else 0

            if fcf_per_share > 0:
                g = growth_pct if growth_pct > TERMINAL_GROWTH_PCT else TERMINAL_GROWTH_PCT + 2
                discount_rate, g_frac, tg_frac = ke_pct / 100, g / 100, TERMINAL_GROWTH_PCT / 100
                pv_fcf = sum(fcf_per_share * (1 + g_frac) ** t / (1 + discount_rate) ** t for t in range(1, 6))
                fcf5 = fcf_per_share * (1 + g_frac) ** 5
                terminal_value = (fcf5 * (1 + tg_frac)) / (discount_rate - tg_frac)
                intrinsic_value = pv_fcf + terminal_value / (1 + discount_rate) ** 5
                result["model_used"] = "2-Stage DCF (Free Cash Flow)"
            else:
                intrinsic_value = current_price * 1.25
                result["model_used"] = "Normalized Growth Proxy (FCF Neutralized)"
                
        elif effective_eps and effective_eps > 0:
            historical_pe = resolved_pe if (resolved_pe and resolved_pe > 0) else 20.0
            target_pe = min(historical_pe, 35)
            intrinsic_value = round(effective_eps * target_pe, 2)
            result["model_used"] = "Target P/E (defensive)"
            
        elif book_value_per_share and book_value_per_share > 0:
            intrinsic_value = round(book_value_per_share * 1.2, 2)
            result["model_used"] = "Book Value Asset Base"
            
        else:
            intrinsic_value = current_price * 1.15
            result["model_used"] = "Default Growth Baseline"

    if intrinsic_value == current_price or not intrinsic_value:
        intrinsic_value = round(current_price * 1.12, 2)

    target_price = round(intrinsic_value, 2)
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0

    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist) or (current_price * 0.92)
    entry_low = round(support, 2)
    entry_high = round(support + (0.5 * atr if atr else current_price * 0.02), 2)
    if entry_low > current_price:
        entry_low, entry_high = round(current_price * 0.95, 2), round(current_price, 2)
    raw_stop_loss = entry_low - (1.5 * atr if atr else entry_low * 0.05)
    stop_loss = round(max(current_price * 0.5, raw_stop_loss), 2)

    momentum, horizon, drift = "NEUTRAL", "3-5 Years", None
    try:
        closes_clean = hist['Close'].dropna()
        if len(closes_clean) > 30 and current_price:
            normalized_prices = closes_clean.values / current_price
            if np.all(np.isfinite(normalized_prices)):
                slope, _ = np.polyfit(np.arange(len(normalized_prices)), normalized_prices, 1)
                drift = slope * 252
                if slope > 0.0005:
                    momentum, horizon = "UP", "12-18 Months (Accelerated)"
                elif slope < -0.0005:
                    momentum = "DOWN"
            if HAS_ARIMA and len(closes_clean) > 100:
                try:
                    fitted = ARIMA(closes_clean.values, order=(5, 1, 0)).fit()
                    forecast = fitted.forecast(steps=30)
                    momentum = "UP" if forecast[-1] > forecast[0] else "DOWN"
                    horizon = "12-18 Months (Accelerated)" if momentum == "UP" else "3-5 Years"
                except Exception:
                    pass
    except Exception:
        momentum, horizon, drift = "NEUTRAL", "3-5 Years", None

    composite, verdict, intrinsic_score, tech_score = composite_verdict(
        fundamental_score, margin_of_safety, drift, arima_direction=momentum,
        forced_intrinsic_adjustment=forced_intrinsic_adjustment, qualitative_bonus=qualitative_bonus,
    )

    verdict = apply_tiered_sanity_veto(verdict, target_price, current_price, notes, growth_rate=growth_pct)

    result.update({
        "verdict": verdict, "target_price": target_price,
        "entry_range": f"₹{entry_low:,.2f} - ₹{entry_high:,.2f}", "stop_loss": stop_loss,
        "time_horizon": horizon, "note": " ".join(notes) if notes else None,
        "composite_score": composite, "intrinsic_score": intrinsic_score, "technical_score": tech_score,
        "margin_of_safety": round(margin_of_safety * 100, 1),
        "discount_rate": round(ke_pct, 1), "growth_used": round(growth_pct, 1),
    })
    return result

# ============================================================
# 7. MASTER DATA FETCH
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist_full = stock.history(period="1y")
    if hist_full.empty: raise ValueError(f"Could not find '{raw_input}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist_full['Close'].iloc[-1]), 2))
    currency_symbol = "₹"

    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")
    is_fin = is_financial_sector(sector, industry)
    sector_profile = classify_sector_profile(sector, industry)
    revenue_keys = BANK_REVENUE_KEYS if is_fin else STANDARD_REVENUE_KEYS

    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    net_inc, total_eq, total_assets_latest, ebitda_val = None, None, None, info.get('ebitda')
    revenue_latest, ebit_latest, interest_exp_latest, interest_income_latest = None, None, None, None
    fcf_history = None
    pat_qoq, pat_yoy_pct, net_margin_final = None, None, None
    latest_quarter_net_income = None
    revenue_cagr_pct = None

    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            ni_series = q_fin.loc['Net Income'].dropna()
            if len(ni_series) > 0:
                net_inc = float(ni_series.iloc[:4].sum())
                latest_quarter_net_income = float(ni_series.iloc[0])
            if len(ni_series) >= 2 and ni_series.iloc[1] != 0:
                pat_qoq = round(((ni_series.iloc[0] - ni_series.iloc[1]) / abs(ni_series.iloc[1])) * 100, 2)
            if len(ni_series) >= 5 and ni_series.iloc[4] != 0:
                pat_yoy_pct = round(((ni_series.iloc[0] - ni_series.iloc[4]) / abs(ni_series.iloc[4])) * 100, 2)
            rev_key_found = next((k for k in revenue_keys if k in q_fin.index), None)
            if rev_key_found and len(ni_series) > 0:
                rev_series = q_fin.loc[rev_key_found].dropna()
                if len(rev_series) > 0 and rev_series.iloc[0] != 0:
                    net_margin_final = round((ni_series.iloc[0] / rev_series.iloc[0]) * 100, 2)

        fin = stock.financials
        if fin is not None and not fin.empty:
            rev_key_found = next((k for k in revenue_keys if k in fin.index), None)
            if rev_key_found and pd.notna(fin.loc[rev_key_found].iloc[0]):
                revenue_latest = float(fin.loc[rev_key_found].iloc[0])
                rev_series_annual = fin.loc[rev_key_found].dropna()
                if len(rev_series_annual) >= 2 and rev_series_annual.iloc[-1] > 0:
                    years = len(rev_series_annual) - 1
                    revenue_cagr_pct = round((((rev_series_annual.iloc[0] / rev_series_annual.iloc[-1]) ** (1 / years)) - 1) * 100, 2)
            for k in ['EBIT', 'Operating Income']:
                if k in fin.index and pd.notna(fin.loc[k].iloc[0]):
                    ebit_latest = float(fin.loc[k].iloc[0]); break
            if 'Interest Expense' in fin.index:
                ie_series = fin.loc['Interest Expense'].dropna()
                if len(ie_series) > 0:
                    interest_exp_latest = float(ie_series.iloc[0])
            ii_key_found = next((k for k in INTEREST_INCOME_KEYS if k in fin.index), None)
            if ii_key_found:
                ii_series = fin.loc[ii_key_found].dropna()
                if len(ii_series) > 0:
                    interest_income_latest = float(ii_series.iloc[0])

        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            for k in ['Stockholders Equity', 'Total Stockholder Equity', 'Common Stock Equity']:
                if k in bs.index:
                    eq_series = bs.loc[k].dropna()
                    if len(eq_series) > 0:
                        total_eq = float(eq_series.iloc[0]); break
            if 'Total Assets' in bs.index:
                ta_series = bs.loc['Total Assets'].dropna()
                if len(ta_series) > 0:
                    total_assets_latest = float(ta_series.iloc[0])

        cf = stock.cashflow
        if cf is not None and not cf.empty and 'Free Cash Flow' in cf.index:
            fcf_history = cf.loc['Free Cash Flow'].dropna()

        if fin is not None and not fin.empty:
            col = fin.columns[0]
            rev_key_found = next((k for k in revenue_keys if k in fin.index), None)
            pnl_df = pd.DataFrame([
                {"Particulars": "Net Sales / Total Income", "Amount (₹ Cr)": round(fin.loc[rev_key_found, col] / 10000000, 2) if rev_key_found else "—"},
                {"Particulars": "Operating Profit", "Amount (₹ Cr)": round(fin.loc['Operating Income', col] / 10000000, 2) if 'Operating Income' in fin.index else "—"},
                {"Particulars": "Net Profit", "Amount (₹ Cr)": round(fin.loc['Net Income', col] / 10000000, 2) if 'Net Income' in fin.index else "—"}
            ])
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            bs_df = pd.DataFrame([
                {"Particulars": "Total Equity", "Amount (₹ Cr)": round(total_eq / 10000000, 2) if total_eq else "—"},
                {"Particulars": "Total Debt", "Amount (₹ Cr)": round(bs.loc['Total Debt', col] / 10000000, 2) if 'Total Debt' in bs.index else "—"},
                {"Particulars": "Total Assets", "Amount (₹ Cr)": round(bs.loc['Total Assets', col] / 10000000, 2) if 'Total Assets' in bs.index else "—"}
            ])
        if cf is not None and not cf.empty:
            col = cf.columns[0]
            cf_df = pd.DataFrame([
                {"Particulars": "Operating Cash Flow", "Amount (₹ Cr)": round(cf.loc['Operating Cash Flow', col] / 10000000, 2) if 'Operating Cash Flow' in cf.index else "—"},
                {"Particulars": "Free Cash Flow", "Amount (₹ Cr)": round(cf.loc['Free Cash Flow', col] / 10000000, 2) if 'Free Cash Flow' in cf.index else "—"}
            ])
    except Exception:
        pass

    shares_out = info.get("sharesOutstanding")
    mcap = info.get("marketCap")
    
    # --- FIX: Indian Market Cap / Shares Sanity Check ---
    if mcap and shares_out and current_price:
        calculated_mcap = shares_out * current_price
        if abs(calculated_mcap - mcap) / mcap > 0.15:
            mcap = calculated_mcap
    elif current_price and shares_out:
        mcap = current_price * shares_out

    operating_margin = round((ebit_latest / revenue_latest) * 100, 2) if (ebit_latest is not None and revenue_latest) else None

    nim_proxy = None
    if is_fin and interest_income_latest is not None and interest_exp_latest is not None and total_assets_latest:
        nim_proxy = round(((interest_income_latest - interest_exp_latest) / total_assets_latest) * 100, 2)

    trailing_earnings_negative = (net_inc is not None and net_inc < 0) or (info.get('trailingEps') and info.get('trailingEps') < 0)
    
    # --- FIX: Turnaround strictly needs positive operating profit ---
    is_turnaround = bool(trailing_earnings_negative and (
        (pat_qoq is not None and pat_qoq > 50) or 
        (latest_quarter_net_income is not None and latest_quarter_net_income > 0 and ebit_latest is not None and ebit_latest > 0)
    ))

    recent_news = fetch_google_news(f"{info.get('longName', resolved_ticker)} stock news")
    business_summary = info.get("longBusinessSummary")
    qualitative_bonus, qualitative_notes = scan_news_sentiment(recent_news, business_summary)
    order_book_hits, growth_pct_from_news = extract_order_book_signal(recent_news, business_summary)

    pe_raw = info.get("trailingPE")
    if not is_valid_metric(pe_raw) and net_inc and mcap:
        pe_raw = round(mcap / net_inc, 2)
    elif is_valid_metric(pe_raw):
        pe_raw = round(float(pe_raw), 2)

    pb_raw = info.get("priceToBook")
    if not is_valid_metric(pb_raw) and total_eq and mcap and total_eq > 0:
        pb_raw = round(mcap / total_eq, 2)
    elif is_valid_metric(pb_raw):
        pb_raw = round(float(pb_raw), 2)

    roe_raw = info.get("returnOnEquity")
    if not is_valid_metric(roe_raw) and net_inc and total_eq and total_eq > 0:
        roe_raw = net_inc / total_eq
    roe_is_known = is_valid_metric(roe_raw)

    peg_raw = info.get("pegRatio")
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and pat_yoy_pct and pat_yoy_pct > 0:
        peg_raw = round(to_float(pe_raw) / pat_yoy_pct, 2)
    elif is_valid_metric(peg_raw):
        peg_raw = round(float(peg_raw), 2)

    ev_ebitda = "N/A"
    if is_fin:
        ev_ebitda = "N/A (Financial Sector)"
    else:
        ev_val = info.get("enterpriseValue")
        if not is_valid_metric(ev_val) and mcap:
            ev_val = mcap + (info.get('totalDebt') or 0) - (info.get('totalCash') or 0)
        if is_valid_metric(ebitda_val) and is_valid_metric(ev_val) and ebitda_val != 0:
            ev_ebitda = round(ev_val / ebitda_val, 2)
        elif is_valid_metric(ev_ebitda):
            ev_ebitda = round(float(ev_ebitda), 2)

    ebitda_margin = round((ebitda_val / revenue_latest) * 100, 2) if (is_valid_metric(ebitda_val) and revenue_latest) else "N/A"
    interest_coverage = round(ebit_latest / interest_exp_latest, 2) if (ebit_latest is not None and interest_exp_latest) else "N/A"
    dte_raw = info.get("debtToEquity")
    debt_to_equity = round(dte_raw / 100, 2) if is_valid_metric(dte_raw) else "N/A"

    temp_metrics = {
        'pe_ratio': pe_raw, 'peg_ratio': peg_raw, 'pb_ratio': pb_raw,
        'pat_yoy': pat_yoy_pct, 'roe': (roe_raw * 100) if roe_is_known else None,
        'ev_ebitda': ev_ebitda, 'is_financial_sector': is_fin, 'debt_to_equity': debt_to_equity,
        'interest_coverage': interest_coverage, 'net_margin': net_margin_final, 'pat_qoq': pat_qoq,
        'operating_margin': operating_margin, 'revenue_cagr': revenue_cagr_pct,
        'sector_profile': sector_profile, 'nim_proxy': nim_proxy,
    }
    v_score = score_from_checks(valuation_checks(temp_metrics))
    p_score = score_from_checks(past_performance_checks(temp_metrics))
    h_score = score_from_checks(financial_health_checks(temp_metrics))
    fundamental_score = compute_fundamental_score(v_score, p_score, h_score, is_fin)
    if is_turnaround:
        fundamental_score = min(100, fundamental_score + 15)

    bvps = info.get('bookValue')
    if not is_valid_metric(bvps) and total_eq and shares_out:
        bvps = total_eq / shares_out
    bvps = bvps if is_valid_metric(bvps) else None
    div_per_share = info.get("dividendRate")

    jpb_ratio = jpb_value = ddm_val = None
    if is_fin:
        beta_preview = info.get('beta') if info.get('beta') and pd.notna(info.get('beta')) and info.get('beta') > 0 else 1.0
        current_rfr = get_dynamic_risk_free_rate()
        ke_preview = min(max((current_rfr + beta_preview * EQUITY_RISK_PREMIUM) * 100, 9), 20)
        growth_preview = pat_yoy_pct if (pat_yoy_pct and pat_yoy_pct > 0) else 8.0
        jpb_ratio, jpb_value = justified_pb_fair_value(roe_raw * 100 if roe_is_known else None, ke_preview, growth_preview, bvps)
        ddm_val = ddm_fair_value(div_per_share, ke_preview, growth_preview)
    temp_metrics["justified_pb"] = jpb_ratio

    predictive_data = run_predictive_pipeline(
        info, hist_full, fcf_history, sector, industry, fundamental_score,
        bvps, div_per_share, roe_raw * 100 if roe_is_known else None, pat_yoy_pct, None,
        precomputed_jpb=(jpb_ratio, jpb_value), precomputed_ddm=ddm_val,
        resolved_pe=to_float(pe_raw), is_turnaround=is_turnaround,
        latest_quarter_net_income=latest_quarter_net_income, shares_outstanding=shares_out,
        qualitative_bonus=qualitative_bonus, qualitative_notes=qualitative_notes,
        sector_profile=sector_profile, order_book_hits=order_book_hits, growth_pct_from_news=growth_pct_from_news,
    )

    promoters = (info.get("heldPercentInsiders") or 0) * 100
    institutions = (info.get("heldPercentInstitutions") or 0) * 100
    if promoters == 0 and institutions == 0:
        shareholding_dict = {"Data Unavailable": 100}
    else:
        shareholding_dict = {
            "Promoters": promoters,
            "Institutions": institutions,
            "Public": max(0, 100 - (promoters + institutions))
        }

    try:
        mf_df = stock.mutualfund_holders
    except Exception:
        mf_df = None
        
    try:
        cal = stock.calendar
        if isinstance(cal, dict):
            cal_df = pd.DataFrame(list(cal.items()), columns=['Event', 'Date'])
        else:
            cal_df = cal
    except Exception:
        cal_df = None

    metrics = {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw if is_valid_metric(pe_raw) else "N/A",
        "pb_ratio": pb_raw if is_valid_metric(pb_raw) else "N/A",
        "peg_ratio": peg_raw if is_valid_metric(peg_raw) else "N/A",
        "ev_ebitda": ev_ebitda if is_valid_metric(ev_ebitda) else ev_ebitda,
        "roe": f"{round(roe_raw*100, 2)}%" if roe_is_known else "N/A",
        "ebitda_margin": f"{ebitda_margin}%" if ebitda_margin != "N/A" else "N/A",
        "operating_margin": operating_margin, "revenue_cagr": revenue_cagr_pct, "nim_proxy": nim_proxy,
        "debt_to_equity": debt_to_equity,
        "interest_coverage": interest_coverage,
        "net_margin": f"{net_margin_final}%" if net_margin_final is not None else "N/A",
        "dividend_yield": f"{round(info.get('dividendYield',0)*100,2)}%" if is_valid_metric(info.get('dividendYield')) else "N/A",
        "pat_yoy": f"{pat_yoy_pct}%" if pat_yoy_pct is not None else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq is not None else "N/A",
        "market_cap": mcap, "sector": sector, "industry": industry,
        "is_financial_sector": is_fin, "justified_pb": jpb_ratio, "is_turnaround": is_turnaround,
        "sector_profile": sector_profile, "order_book_hits": order_book_hits,
        "growth_pct_from_news": growth_pct_from_news,
        "fifty_two_high": info.get("fiftyTwoWeekHigh", "N/A"),
        "fifty_two_low": info.get("fiftyTwoWeekLow", "N/A"),
        "business_summary": business_summary,
        "website": info.get("website", "N/A"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": recent_news,
        "shareholding": shareholding_dict,
        "mutual_funds": mf_df,
        "calendar": cal_df,
        "target_mean_price": info.get("targetMeanPrice"),
        "recommendation_mean": info.get("recommendationMean"),
        "v_score": v_score,
        "p_score": p_score,
        "h_score": h_score,
        "working_ticker": resolved_ticker, "history": hist_full.reset_index(),
        "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": predictive_data, "fair_value": predictive_data['target_price'],
        "currency": currency_symbol, "fundamental_score": fundamental_score,
    }

    # --- TRUE STRONG BUY SECTOR ALTERNATIVE SCANNER ---
    metrics['best_alternative'] = None
    if predictive_data['verdict'] in ["DON'T BUY", "OBSERVE"]:
        peers = SECTOR_PEERS.get(sector_profile, SECTOR_PEERS["standard"])
        best_peer = None
        
        for peer in peers:
            if peer == resolved_ticker: continue 
            try:
                peer_stock = yf.Ticker(peer)
                p_info = peer_stock.info
                p_hist = peer_stock.history(period="1y")
                
                if p_hist.empty: continue
                p_current_price = p_info.get("currentPrice", float(p_hist['Close'].iloc[-1]))
                
                p_sector = p_info.get("sector", "N/A")
                p_industry = p_info.get("industry", "N/A")
                p_is_fin = is_financial_sector(p_sector, p_industry)
                
                p_pe = p_info.get("trailingPE")
                p_pb = p_info.get("priceToBook")
                p_roe = p_info.get("returnOnEquity")
                p_dte = p_info.get("debtToEquity")
                
                pe_val = float(p_pe) if p_pe and p_pe > 0 else 999
                roe_val = float(p_roe) * 100 if p_roe and pd.notna(p_roe) else 0
                dte_val = float(p_dte) / 100 if p_dte and pd.notna(p_dte) else 999
                
                closes = p_hist['Close'].dropna()
                is_uptrend = closes.iloc[-1] > closes.rolling(50).mean().iloc[-1] if len(closes) > 50 else True
                
                if 0 < pe_val < 30 and roe_val > 15 and dte_val < (2.0 if p_is_fin else 0.8) and is_uptrend:
                    best_peer = {
                        "name": p_info.get("shortName", peer),
                        "ticker": peer,
                        "price": p_current_price,
                        "pe": round(pe_val, 1),
                        "pb": round(float(p_pb), 1) if p_pb and pd.notna(p_pb) else "N/A"
                    }
                    break 
            except Exception:
                pass
                
        metrics['best_alternative'] = best_peer

    return metrics

# ============================================================
# 8. UI PLOTLY CHARTS & NEW ANGEL ONE COMPONENTS
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

# --- ANGEL ONE COMPONENT: 52-WEEK RANGE BAR ---
def render_52week_range(current_price, low_52, high_52, currency="₹"):
    if current_price is None or low_52 is None or high_52 is None or high_52 <= low_52: return
    pct_position = ((current_price - low_52) / (high_52 - low_52)) * 100
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[100], y=["Range"], orientation="h", marker=dict(color="#1F1F1F"), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pct_position], y=["Range"], mode="markers", marker=dict(color="#38BDF8", size=16, symbol="diamond"), name="Current Price"))
    fig.update_layout(height=50, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 100]), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), showlegend=False)
    st.markdown(f"<div style='color:{MUTED}; font-size:0.85em; text-align:center;'><b>52W Low:</b> {currency}{low_52:,.2f} &nbsp;&nbsp;|&nbsp;&nbsp; <b>Current:</b> <span style='color:#E6E6E6;'>{currency}{current_price:,.2f}</span> &nbsp;&nbsp;|&nbsp;&nbsp; <b>52W High:</b> {currency}{high_52:,.2f}</div>", unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

# --- ANGEL ONE COMPONENT: SMART SUMMARY CARDS ---
def render_price_summary_cards(df, current_price, low_52, high_52):
    if df is None or df.empty or current_price is None: return
    sma_20 = df["Close"].rolling(20).mean().iloc[-1]
    sma_50 = df["Close"].rolling(50).mean().iloc[-1]
    sma_200 = df["Close"].rolling(200).mean().iloc[-1]

    c1, c2, c3 = st.columns(3)
    if low_52 and high_52:
        dist_from_low = ((current_price - low_52) / low_52) * 100
        with c1:
            if dist_from_low <= 5:
                st.info(f"📍 **Near 52W Low:** Just {dist_from_low:.1f}% above 52-week low.")
            else:
                st.info(f"📊 **52W Position:** {((current_price - low_52) / (high_52 - low_52)) * 100:.1f}% of 52-week range.")
    with c2:
        if pd.notna(sma_50) and pd.notna(sma_200):
            if current_price > sma_50 and current_price > sma_200:
                st.success("📈 **Bullish Trend:** Trading above 50-day & 200-day SMAs.")
            elif current_price < sma_50 and current_price < sma_200:
                st.error("📉 **Bearish Trend:** Trading below 50-day & 200-day SMAs.")
            else:
                st.warning("⚖️ **Mixed Trend:** Trading between 50-day & 200-day SMAs.")
    with c3:
        if "Volume" in df.columns and len(df) >= 6:
            vol_today = df["Volume"].iloc[-1]
            vol_avg_5d = df["Volume"].iloc[-6:-1].mean()
            vol_ratio = (vol_today / vol_avg_5d) if vol_avg_5d > 0 else 1.0
            if vol_ratio > 1.5:
                st.success(f"🔥 **High Volume:** Today's volume is {vol_ratio:.1f}x higher than 5-day avg.")
            else:
                st.info(f"💧 **Normal Volume:** Trading volume is steady ({vol_ratio:.1f}x 5-day avg).")

# --- ANGEL ONE COMPONENT: SCORECARD BADGES ---
def render_scorecard_badges(q_score, v_score, f_score):
    def get_badge(score, is_val=False):
        if score is None: return "N/A", "N/A", MUTED
        rating = max(1, min(5, round((score / 100) * 5)))
        if is_val:
            lbl = "VERY CHEAP" if rating==5 else "ATTRACTIVE" if rating==4 else "FAIR" if rating==3 else "EXPENSIVE" if rating==2 else "VERY EXPENSIVE"
        else:
            lbl = "EXCELLENT" if rating==5 else "GOOD" if rating==4 else "AVERAGE" if rating==3 else "WEAK" if rating==2 else "POOR"
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

# --- ANGEL ONE COMPONENT: VALUATION SPECTRUM ---
def render_valuation_spectrum(current_price, fair_value, currency="₹"):
    if not fair_value or not current_price: return
    attractive_limit = round(fair_value * 0.85, 2)
    expensive_limit = round(fair_value * 1.50, 2)
    high_limit = round(fair_value * 2.50, 2)

    if current_price <= attractive_limit: pos = 15
    elif current_price >= high_limit: pos = 90
    else: pos = 15 + ((current_price - attractive_limit) / (high_limit - attractive_limit)) * 75

    fig = go.Figure()
    fig.add_trace(go.Bar(x=[100], y=["Valuation"], orientation="h", marker=dict(color=[pos], colorscale=[[0.0, GREEN], [0.4, GOLD], [1.0, RED]], showscale=False), hoverinfo="none"))
    fig.add_trace(go.Scatter(x=[pos], y=["Valuation"], mode="markers", marker=dict(color="#FFFFFF", size=18, symbol="triangle-up"), name="Current Price"))
    fig.update_layout(height=80, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False, zeroline=False, showticklabels=False, range=[0, 100]), yaxis=dict(showgrid=False, zeroline=False, showticklabels=False), showlegend=False)

    st.markdown(f"##### Valuation Zone — Current Price: **{currency}{current_price:,.2f}**")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col1: st.markdown(f"<div style='color:{GREEN}; font-size:0.85em;'><b>Attractive:</b> Below {currency}{attractive_limit:,.2f}</div>", unsafe_allow_html=True)
    with col2: st.markdown(f"<div style='color:{GOLD}; font-size:0.85em; text-align:center;'><b>Fair/Exp:</b> {currency}{attractive_limit:,.2f} – {currency}{expensive_limit:,.2f}</div>", unsafe_allow_html=True)
    with col3: st.markdown(f"<div style='color:{RED}; font-size:0.85em; text-align:right;'><b>High:</b> Above {currency}{high_limit:,.2f}</div>", unsafe_allow_html=True)

# --- ANGEL ONE COMPONENT: ANALYST CONSENSUS ---
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

# --- ANGEL ONE COMPONENT: HIGHLIGHTS CARD ---
def extract_highlights(metrics, cf_df):
    working, not_working = [], []
    if cf_df is not None and not cf_df.empty and "Operating Cash Flow" in cf_df.index:
        ocf_series = cf_df.loc["Operating Cash Flow"].dropna()
        if len(ocf_series) > 0 and ocf_series.iloc[0] == ocf_series.max() and ocf_series.iloc[0] > 0:
            working.append(f"Operating Cash Flow (Yearly) — Highest at ₹{round(ocf_series.iloc[0] / 10000000, 2):,.2f} Cr")
    
    ic = metrics.get('interest_coverage')
    if is_valid_metric(ic):
        if float(ic) > 10: working.append(f"Operating Profit to Interest — Strong coverage at {float(ic):.2f}x")
        elif float(ic) < 2.5: not_working.append(f"Interest Coverage — Low buffer at {float(ic):.2f}x EBIT")
        
    dte = metrics.get('debt_to_equity')
    if is_valid_metric(dte):
        if float(dte) < 0.2: working.append(f"Balance Sheet Strength — Virtually debt-free (D/E: {float(dte):.2f})")
        elif float(dte) > 1.5: not_working.append(f"Leverage Risk — High Debt-to-Equity at {float(dte):.2f}x")
        
    yoy = to_float(metrics.get('pat_yoy'))
    if yoy and yoy > 20: working.append(f"Strong Earnings Growth — PAT up {yoy:.2f}% YoY")
    elif yoy and yoy < 0: not_working.append(f"Earnings Contraction — PAT down {yoy:.2f}% YoY")
    
    return working, not_working

def render_highlights_card(working, not_working):
    st.markdown("### Key Drivers & Operational Highlights")
    col_pos, col_neg = st.columns(2)
    with col_pos:
        st.markdown(f"##### 🟢 What's Working Well?")
        if working:
            for w in working: st.markdown(f"<div style='background:rgba(63,185,80,0.1); border-left:3px solid {GREEN}; padding:8px 12px; margin-bottom:8px; border-radius:4px; font-size:0.9em; color:#E6E6E6;'>• {w}</div>", unsafe_allow_html=True)
        else: st.caption("No significant positive extremes detected.")
    with col_neg:
        st.markdown(f"##### 🔴 What's Not Working Well?")
        if not_working:
            for nw in not_working: st.markdown(f"<div style='background:rgba(248,81,73,0.1); border-left:3px solid {RED}; padding:8px 12px; margin-bottom:8px; border-radius:4px; font-size:0.9em; color:#E6E6E6;'>• {nw}</div>", unsafe_allow_html=True)
        else: st.caption("No major balance sheet red flags detected.")

# --- ANGEL ONE COMPONENT: CORPORATE EVENTS & MF ---
def render_corporate_events_and_mfs(cal_df, mf_df):
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### 📅 Corporate Events")
        if cal_df is not None and not cal_df.empty:
            mask = cal_df['Event'].astype(str).str.contains('High|Low|Average|Revenue', case=False, na=False)
            cal_df_clean = cal_df[~mask]
            st.dataframe(cal_df_clean, use_container_width=True, hide_index=True)
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
Provide ONLY narrative reasoning — no invented numbers beyond what is given to you.

REALITY CHECKER MANDATE:
- If the implied upside/downside is an extreme outlier (beyond +200% or beyond -60%), or the
  price action looks driven by temporary macro/geopolitical noise rather than the company's
  own fundamentals, say so explicitly and explain why the number should be read with caution.
- If recent_news shows a genuine operating catalyst (acquisition, turnaround quarter, large
  order win) or a real risk event (fraud, resignation, default), weave it into the narrative.

NO BLIND AGREEMENT MANDATE:
- You are given both the quantitative baseline (composite score, intrinsic value, model used)
  AND separately-extracted forward catalysts (order book / guidance signals, sector profile,
  turnaround status). These are lagging-vs-forward-looking inputs and can disagree.
- Do not simply restate the quantitative verdict. Weigh both and explain your reasoning."""
    pred = metrics.get('predictive', {})
    news_titles = "; ".join([n['title'] for n in (metrics.get('recent_news') or [])[:5]]) or "No recent headlines found."
    turnaround_note = " TURNAROUND flagged." if metrics.get('is_turnaround') else ""
    order_book_note = (f" Forward catalyst signal(s) detected in recent news: {', '.join(metrics.get('order_book_hits', [])[:4])}."
                        if metrics.get('order_book_hits') else " No explicit order-book/guidance signal detected in recent news.")
    
    target_display = f"{metrics['currency']}{pred.get('target_price')}" if pred.get('verdict') != "DON'T BUY" else "N/A (Model Rejected due to strict veto)"
    
    pmt = (f"Target: {metrics['name']} ({ticker}). Sector: {metrics.get('sector')} "
           f"(profile: {metrics.get('sector_profile')}).{turnaround_note}{order_book_note} "
           f"Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. "
           f"EV/EBITDA: {metrics['ev_ebitda']}. Debt/Eq: {metrics['debt_to_equity']}. "
           f"Valuation model used: {pred.get('model_used')}. Forward growth assumption used in the model: "
           f"{pred.get('growth_used')}%. Quantitative Target Price: {target_display}. "
           f"System Verdict: {pred.get('verdict')} (composite score {pred.get('composite_score')}/100 — "
           f"fundamental {pred.get('fundamental_score')}, intrinsic {pred.get('intrinsic_score')}, "
           f"technical {pred.get('technical_score')}). Recent news headlines: {news_titles}")
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt,
                                          config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

# ============================================================
# 10. APP STATE & HEADER
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None

def get_base64_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode('utf-8')
    except Exception:
        return None

logo_b64 = get_base64_image("Logo.png")

# --- UI UPDATE: Drop-cap aligned logo ---
if logo_b64:
    st.markdown(f'''
    <div class="swf-title-container" style="padding: 10px 0 30px 0;">
        <div class="swf-title" style="display: flex; align-items: flex-end; justify-content: center;">
            <img src="data:image/png;base64,{logo_b64}" style="height: 1.8em; filter: invert(1); margin-right: 4px; transform: translateY(6px);">
            <span style="line-height: 0.85;">THENAEUM FINANCIAL INTELLIGENCE</span>
        </div>
    </div>
    ''', unsafe_allow_html=True)
else:
    st.markdown('''
    <div class="swf-title-container" style="padding: 10px 0 30px 0;">
        <div class="swf-title">ATHENAEUM FINANCIAL INTELLIGENCE</div>
    </div>
    ''', unsafe_allow_html=True)
    
col_input, col_btn = st.columns([4, 1])
with col_input: stock_input = st.text_input("Enter Stock Name or Ticker:", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn: generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked and stock_input.strip():
    with st.spinner('Compiling metrics, applying sector normalization, and running the composite models...'):
        try:
            rt = resolve_name_to_ticker(stock_input)
            metrics = fetch_stock_data(rt, stock_input)
            
            if metrics is not None and isinstance(metrics, dict):
                final_ticker = metrics.pop('working_ticker', rt)
            else:
                st.error("Error: Could not retrieve valid data for this stock ticker.")
                st.stop()

            ai_text = generate_comprehensive_report(metrics, final_ticker)
            raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
            sections_list = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', raw_ai_text, flags=re.IGNORECASE) if s.strip()]
            if len(sections_list) > 8: sections_list = sections_list[-8:]

            st.session_state.report_data = {"metrics": metrics, "ai_text": ai_text, "narrative_sections": sections_list, "ticker": final_ticker}
        except Exception as e:
            st.error(f"Error: {e}")

# ============================================================
# 11. SINGLE-PAGE REPORT
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

    # ---------------- Header ----------------
    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        turnaround_badge = ' <span class="swf-badge" style="margin-left:6px; color:#F97316;">TURNAROUND</span>' if m.get('is_turnaround') else ''
        st.markdown(f'<div class="swf-card"><div style="display:flex; justify-content:space-between; align-items:flex-start;"><div><div style="color:{MUTED}; font-size:0.85em;">Stocks / {m.get("industry","N/A")}</div><div style="font-size:1.4em; font-weight:800;">{m["name"]}</div><div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report</div><span class="swf-badge" style="margin-top:8px; display:inline-block;">Verdict: <span style="color:{rc};">{current_rating}</span></span>{turnaround_badge}</div><div style="text-align:right;"><div style="font-size:1.6em; font-weight:800;">{currency}{m["price"]}</div></div></div></div>', unsafe_allow_html=True)
        
        render_scorecard_badges(m.get('p_score'), m.get('v_score'), m.get('h_score'))
        
        render_price_summary_cards(m.get('history'), m.get('price'), to_float(m.get('fifty_two_low')), to_float(m.get('fifty_two_high')))
        
        hist_df = m.get('history')
        if hist_df is not None and not hist_df.empty: st.plotly_chart(price_history_chart(hist_df, currency), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Composite Score Radar</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(m, pred), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    # ---------------- Recent News & Catalysts ----------------
    news_items = m.get('recent_news', [])
    if news_items:
        news_html = "<ul style='padding-left: 20px; margin-bottom: 0;'>"
        for item in news_items[:5]:
            news_html += f"<li style='margin-bottom: 8px;'><a href='{item['link']}' target='_blank' style='color:{BLUE}; text-decoration:none;'>{item['title']}</a></li>"
        news_html += "</ul>"
    else:
        news_html = "<div class='swf-sub'>No recent news found for this stock.</div>"
        
    card("Recent News & Market Catalysts", news_html)

    st.markdown("---")

    # ---------------- Company Overview ----------------
    st.markdown('<div class="swf-section-title">Company Overview</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1: custom_metric("Current Price", f"{currency}{m['price']}"); custom_metric("P/E Ratio", f"{m['pe_ratio']}x" if m['pe_ratio'] != "N/A" else "N/A")
    with c2: custom_metric("P/BV Ratio", f"{m['pb_ratio']}x" if m['pb_ratio'] != "N/A" else "N/A"); custom_metric("ROE", f"{m['roe']}")
    with c3: custom_metric("EV/EBITDA", f"{m['ev_ebitda']}x" if "N/A" not in str(m['ev_ebitda']) else m['ev_ebitda']); custom_metric("PAT Growth (YoY)", f"{m['pat_yoy']}")
    with c4: custom_metric("Debt-to-Equity", f"{m['debt_to_equity']}"); custom_metric("EBITDA Margin", f"{m.get('ebitda_margin', 'N/A')}")
    
    render_52week_range(m.get('price'), to_float(m.get('fifty_two_low')), to_float(m.get('fifty_two_high')), currency)
    
    card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{m.get('business_summary', 'Business summary not available.')}</p><div class='swf-sub'>Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")

    st.markdown("---")
    # ---------------- 1. Valuation ----------------
    st.markdown('<div class="swf-section-title">1. Valuation</div>', unsafe_allow_html=True)
    card("Valuation Checklist", render_checks(val_checks))
    st.markdown("##### Fair Value Estimate")
    if m.get('fair_value'):
        fig, diff_pct = fair_value_bar(m['price'], m['fair_value'], currency)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
        render_valuation_spectrum(m['price'], m['fair_value'], currency)
        
        st.caption(f"Price is approx {abs(diff_pct)}% {'overvalued' if diff_pct > 0 else 'undervalued'} vs the modeled {pred.get('model_used','valuation')} fair value (growth assumption used: {pred.get('growth_used','N/A')}%).")
    
    render_analyst_consensus(m.get('target_mean_price'), m.get('price'), m.get('recommendation_mean'), currency)
    
    card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    st.markdown("---")
    # ---------------- 2. Future Growth ----------------
    st.markdown('<div class="swf-section-title">2. Future Growth &amp; Outlook</div>', unsafe_allow_html=True)
    fg1, fg2, fg3 = st.columns(3)
    
    target_display = f"{currency}{pred['target_price']}" if current_rating != "DON'T BUY" else f"N/A (Rejected Model)"
    with fg1: custom_metric(f"Modeled Target ({pred.get('model_used','DCF')})", target_display)
    
    with fg2: custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
    with fg3: custom_metric("Growth Assumption Used", f"{pred.get('growth_used','N/A')}%")
    if m.get('fair_value'): st.plotly_chart(projection_path_chart(m['history'], m['fair_value']), use_container_width=True, config={'displayModeBar': False})
    card("Future Growth & Outlook Narrative", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    st.markdown("---")
    # ---------------- 3. Past Performance ----------------
    st.markdown('<div class="swf-section-title">3. Past Performance</div>', unsafe_allow_html=True)
    card("Past Performance Checklist", render_checks(past_checks))
    pp1, pp2 = st.columns(2)
    with pp1: custom_metric("Operating Margin (OPM)", f"{m['operating_margin']}%" if m.get('operating_margin') is not None else "N/A")
    with pp2: custom_metric("Multi-Year Revenue CAGR", f"{m['revenue_cagr']}%" if m.get('revenue_cagr') is not None else "N/A")
    if not m['pnl_df'].empty: st.markdown("##### Profit & Loss (Cr)"); st.dataframe(m['pnl_df'], use_container_width=True, hide_index=True)
    
    working, not_working = extract_highlights(m, m.get('cf_df'))
    render_highlights_card(working, not_working)
    
    card("Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    st.markdown("---")
    # ---------------- 4. Financial Health ----------------
    st.markdown('<div class="swf-section-title">4. Financial Health</div>', unsafe_allow_html=True)
    card("Financial Health Checklist", render_checks(health_checks))
    if m.get('is_financial_sector'):
        st.caption("Note: Capital Adequacy Ratio and NPA (asset quality) figures are not available from this "
                   "data source and are not shown or estimated. The Net Interest Margin above is an approximation.")
    tab_bs, tab_cf = st.tabs(["Balance Sheet", "Cash Flows"])
    with tab_bs:
        if not m['bs_df'].empty: st.dataframe(m['bs_df'], use_container_width=True, hide_index=True)
    with tab_cf:
        if not m['cf_df'].empty: st.dataframe(m['cf_df'], use_container_width=True, hide_index=True)
    card("Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    st.markdown("---")
    # ---------------- 5. Dividend ----------------
    st.markdown('<div class="swf-section-title">5. Dividend</div>', unsafe_allow_html=True)
    card("Dividend Checklist", render_checks(div_checks))
    card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    st.markdown("---")
    # ---------------- 6. Management ----------------
    st.markdown('<div class="swf-section-title">6. Management &amp; Leadership</div>', unsafe_allow_html=True)
    if m['company_officers']: st.dataframe(pd.DataFrame([{"Name": o.get('name', 'N/A'), "Position": o.get('title', 'N/A')} for o in m['company_officers']]), use_container_width=True, hide_index=True)
    card("Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

    st.markdown("---")
    # ---------------- 7. Ownership ----------------
    st.markdown('<div class="swf-section-title">7. Ownership Structure</div>', unsafe_allow_html=True)
    st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
    
    render_corporate_events_and_mfs(m.get('calendar'), m.get('mutual_funds'))
    
    card("Ownership Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    st.markdown("---")
    # ---------------- 8. Verdict ----------------
    st.markdown('<div class="swf-section-title">8. Verdict &amp; Summary</div>', unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:1.15em; margin-bottom:14px;'><b>Composite System Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{current_rating}</span></div>", unsafe_allow_html=True)

    if current_rating in ["BUY", "STRONG BUY"]:
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry:</b> {pred['entry_range']}<br><b>Horizon:</b> {pred['time_horizon']}<br><b>Target:</b> {currency}{pred['target_price']}<br><b>Stop Loss:</b> {currency}{pred['stop_loss']}</div>", unsafe_allow_html=True)
    elif current_rating == "OBSERVE":
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Target ({pred.get('model_used','DCF')}):</b> {currency}{pred['target_price']}</div>", unsafe_allow_html=True)
    else:
        st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px; color:{RED};'><b>Rejected Valuation Baseline:</b> {currency}{pred['target_price']} (Do Not Trade)</div>", unsafe_allow_html=True)


    # --- RECOMMENDED SECTOR ALTERNATIVE ---
    if current_rating in ["DON'T BUY", "OBSERVE"] and m.get('best_alternative'):
        alt = m['best_alternative']
        st.markdown(
            f"""
            <div style="background-color: rgba(56, 189, 248, 0.1); border: 1px solid {BLUE}; border-radius: 8px; padding: 15px; margin-bottom: 20px;">
                <div style="color: {BLUE}; font-weight: 700; font-size: 1.1em; margin-bottom: 5px;">💡 Recommended Sector Alternative</div>
                <div style="font-size: 0.9em; color: #E6E6E6; margin-bottom: 8px;">
                    This stock scored poorly. Based on its sector profile, you might want to look at a fundamental leader in this space:
                </div>
                <div style="display: flex; gap: 20px; font-weight: 600;">
                    <div>Stock: <span style="color: {GOLD};">{alt['name']} ({alt['ticker']})</span></div>
                    <div>Price: {currency}{alt['price']}</div>
                    <div>P/E: {alt['pe']}x</div>
                    <div>P/B: {alt['pb']}x</div>
                </div>
            </div>
            """, unsafe_allow_html=True
        )

    # --- PROS AND CONS CARDS ---
    all_checks = val_checks + past_checks + health_checks + div_checks
    pros = [c for c in all_checks if c[1] is True]
    cons = [c for c in all_checks if c[1] is False]

    def render_pro_con_list(items, is_pro=True):
        if not items: return "<div class='swf-sub'>None identified based on current data.</div>"
        html = "<ul style='padding-left: 20px; margin-bottom: 0; font-size: 0.9em;'>"
        for label, _, desc in items:
            if is_pro:
                html += f"<li style='margin-bottom: 8px; color: #E6E6E6;'><b>{label}</b><br><span style='color: {MUTED}; font-size: 0.85em;'>{desc}</span></li>"
            else:
                html += f"<li style='margin-bottom: 8px; color: #E6E6E6;'><b style='color: {RED};'>Failed:</b> {label}<br><span style='color: {MUTED}; font-size: 0.85em;'>{desc}</span></li>"
        html += "</ul>"
        return html

    pc1, pc2 = st.columns(2)
    with pc1:
        card("✅ Quantitative Strengths", render_pro_con_list(pros, is_pro=True))
    with pc2:
        card("⚠️ Quantitative Weaknesses", render_pro_con_list(cons, is_pro=False))

    # --- NARRATIVE SUMMARY ---
    styled = style_verdict_text(narrative_for(7))
    card("Narrative Summary", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{styled}</p>")

    st.caption("This report combines sector-normalized checklists, a sector-aware intrinsic valuation model, an "
               "ATR/volume-profile risk model, a trend-based time estimate, and a lightweight news/catalyst scan ")
