import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import math
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import re
import io
import requests
import urllib.parse
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from datetime import timedelta
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.barcharts import VerticalBarChart
from google import genai
from google.genai import types

try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except ImportError:
    HAS_ARIMA = False

# ============================================================
# 1. SETUP & CONFIGURATION  (unchanged)
# ============================================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
st.set_page_config(page_title="Financial Intelligence App", layout="wide")

GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")

GOLD, BG, CARD_BG, BORDER = "#EAB308", "#0D1117", "#161B22", "#262B36"
GREEN, RED, ORANGE, MUTED, BLUE, PURPLE = "#3FB950", "#F85149", "#F97316", "#8B949E", "#38BDF8", "#A855F7"

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
    .swf-badge {{ background:{CARD_BG}; border:1px solid {BORDER}; padding:5px 12px; border-radius:6px; font-weight:700; font-size:0.85em; }}
    .swf-factrow {{ display:flex; justify-content:space-between; padding:5px 0; border-bottom:1px solid {BORDER}; font-size:0.9em; }}
    .swf-newsrow {{ padding:7px 0; border-bottom:1px solid {BORDER}; font-size:0.9em; }}
    .swf-newsrow a {{ color:{BLUE}; text-decoration:none; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 2. CORE UTILITIES  (unchanged from your file — these were fine)
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

# ============================================================
# 3. SECTOR DETECTION
# ------------------------------------------------------------
# FIX: your file checked `sector in ['Financial Services','Banks','Credit Services']`
# — an exact match. yfinance almost never puts "Banks" or "Credit Services" in the
# *sector* field (those are *industry*-level labels; sector is usually just
# "Financial Services" for all of banking/NBFC/insurance/broking). A bank whose
# sector string has different casing, or whose real signal lives in `industry`
# instead, would silently fall through to the wrong (FCF-DCF) valuation branch.
# Checks both fields with substring matching instead.
# ============================================================
FINANCIAL_SECTOR_KEYWORDS = [
    "financial services", "bank", "nbfc", "insurance", "capital markets",
    "credit services", "diversified financials", "asset management",
    "mortgage finance", "consumer finance", "shadow banking",
]

def is_financial_sector(sector, industry):
    text = f"{sector or ''} {industry or ''}".lower()
    return any(kw in text for kw in FINANCIAL_SECTOR_KEYWORDS)

# ============================================================
# 4. CHECKLISTS
# ------------------------------------------------------------
# Fixes applied here:
#  - render_checks was CALLED four times in your file but never DEFINED anywhere —
#    that's an immediate NameError the moment any tab other than Overview/Future
#    Growth/Management/Ownership/Verdict is opened. Restored below.
#  - Negative P/E, negative PEG, and negative EV/EBITDA now hard-fail instead of
#    silently satisfying "< threshold" (Suggestion 2).
#  - Negative D/E (negative shareholders' equity) hard-fails instead of passing
#    "< 1.0" (same bug pattern, one level over).
#  - Removed the "Trading Below Modeled Fair Value" check that had crept into
#    valuation_checks: that signal is exactly what `intrinsic_score` already
#    measures from the DCF/DDM/Justified-P/B margin of safety. Leaving it in
#    valuation_checks meant it got counted TWICE in the composite (once via
#    Fundamental score, again via Intrinsic score) — silently over-weighting
#    valuation relative to the intended 40/35/25 split. Fundamental score now
#    stays limited to independent accounting ratios, as a genuine ensemble
#    requires.
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
                            f"P/E is negative ({pe}x) — the company is currently loss-making."))
        else:
            checks.append(("Reasonable P/E (<25x)", pe < 25, f"Trailing P/E of {pe}x"))

    if peg is not None:
        if peg < 0:
            checks.append(("Positive PEG", False,
                            f"PEG is negative ({peg}) — implies shrinking earnings or a loss-making company, not a value signal."))
        elif pe is not None and pe > 0 and pat_yoy is not None and pat_yoy > 0:
            # PEG is only meaningful as "cheap growth" when both P/E and growth are
            # genuinely positive; otherwise it doesn't measure what it looks like it does.
            checks.append(("Attractive PEG (<1.5)", peg < 1.5, f"PEG ratio of {peg}"))

    if pb is not None:
        threshold = 3.0 if is_fin else 5.0
        checks.append((f"Reasonable P/B (<{threshold:g}x)", 0 < pb < threshold, f"Price-to-Book of {pb}x"))

    if is_fin and pb is not None and m.get('justified_pb'):
        jpb = m['justified_pb']
        checks.append(("P/B vs Excess-ROE Justified P/B", pb < jpb,
                        f"Actual P/B {pb}x vs a model-justified P/B of {jpb}x (from ROE, cost of equity, and growth)"))

    if not is_fin and ev_ebitda is not None:
        if ev_ebitda < 0:
            checks.append(("Positive EV/EBITDA", False,
                            f"EV/EBITDA is negative ({ev_ebitda}x) — implies negative EBITDA (operating losses)."))
        else:
            checks.append(("Reasonable EV/EBITDA (<15x)", ev_ebitda < 15, f"EV/EBITDA of {ev_ebitda}x"))

    return checks

def past_performance_checks(m):
    yoy, qoq = to_float(m.get('pat_yoy')), to_float(m.get('pat_qoq'))
    roe, margin = to_float(m.get('roe')), to_float(m.get('net_margin'))
    checks = []
    if yoy is not None:
        checks.append(("Positive Earnings Growth (YoY)", yoy > 0, f"PAT YoY growth of {m.get('pat_yoy')}"))
    if yoy is not None and qoq is not None:
        checks.append(("Accelerating Growth", qoq > yoy, "Comparing most recent quarter growth to the yearly figure"))
    if roe is not None:
        checks.append(("Strong Return on Equity (>15%)", roe > 15, f"ROE of {m.get('roe')}"))
    if margin is not None:
        checks.append(("Healthy Net Margin (>10%)", margin > 10, f"Net margin of {m.get('net_margin')}"))
    return checks

def financial_health_checks(m):
    de = to_float(m.get('debt_to_equity'))
    ic = to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if de is not None:
        if de < 0:
            checks.append(("Positive Shareholder Equity", False,
                            f"Debt-to-equity is negative ({de}) — implies negative shareholders' equity."))
        else:
            threshold, label = (10.0, "Leverage in line with a lending-book business model (D/E < 10x)") if is_fin \
                else (1.0, "Low Leverage (D/E < 1.0)")
            checks.append((label, de < threshold, f"Debt-to-equity of {de}"))
    if ic is not None:
        checks.append(("Comfortable Interest Coverage (>3x)", ic > 3, f"EBIT covers interest expense {ic}x"))
    return checks

def dividend_checks(m):
    dy_str = str(m.get('dividend_yield', ''))
    if "doesn't pay" in dy_str.lower():
        return [("Notable Dividend (>1.5%)", False, "Stock doesn't pay dividends")]
    dy = to_float(dy_str)
    return [("Notable Dividend (>1.5%)", dy is not None and dy > 1.5, f"Dividend yield: {m.get('dividend_yield')}")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else None

def render_checks(checks):
    """RESTORED — this was called 4+ times in your UI but had no definition anywhere
    in the file, which is a guaranteed NameError on the Valuation / Past Performance /
    Financial Health / Dividend tabs."""
    if not checks:
        return "<div class='swf-check-na'>&#8213; Not enough data to run this checklist.</div>"
    html = ""
    for label, status, desc in checks:
        icon, cls = ("&#9989;", "swf-check-pass") if status else ("&#10060;", "swf-check-fail")
        html += f'<div style="padding:5px 0;"><span class="{cls}">{icon} <b>{label}</b></span><div class="swf-sub">{desc}</div></div>'
    return html

def compute_fundamental_score(val_score, past_score, health_score, is_financial):
    """Suggestion 5 — missing categories are excluded and the remaining weights are
    renormalized, instead of defaulting a missing category to a comfortable neutral
    50 (a softer version of the same 'missing data quietly gets a good score' bug).
    Returns 0 only if literally nothing at all is available."""
    weights = {"val": 0.45, "past": 0.35, "health": 0.20} if is_financial else {"val": 0.35, "past": 0.35, "health": 0.30}
    scores = {"val": val_score, "past": past_score, "health": health_score}
    available = {k: v for k, v in scores.items() if v is not None}
    if not available:
        return 0.0
    total_w = sum(weights[k] for k in available)
    return round(sum(weights[k] * v for k, v in available.items()) / total_w, 1)

# ============================================================
# 5. QUANTITATIVE COMPOSITE ENGINE
# ============================================================
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
    """Suggestion 4 — floored at 0.4x, not 1.0x: a bank earning less than its cost of
    equity should be allowed to trade meaningfully below book value."""
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

def composite_verdict(fundamental_score, margin_of_safety, drift, arima_direction=None, forced_intrinsic_adjustment=0):
    W_FUNDAMENTAL, W_INTRINSIC, W_TECHNICAL = 0.40, 0.35, 0.25
    intrinsic_score = min(max(50 + margin_of_safety * 150, 0), 100)
    intrinsic_score = min(max(intrinsic_score + forced_intrinsic_adjustment, 0), 100)
    tech_score = min(max(50 + (drift or 0) * 100, 0), 100)
    if arima_direction == "UP":
        tech_score = min(100, tech_score + 10)
    elif arima_direction == "DOWN":
        tech_score = max(0, tech_score - 10)
    composite = W_FUNDAMENTAL * fundamental_score + W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score
    if composite >= 75: verdict = "STRONG BUY"
    elif composite >= 60: verdict = "BUY"
    elif composite >= 40: verdict = "OBSERVE"
    else: verdict = "DON'T BUY"
    return round(composite, 1), verdict, round(intrinsic_score, 1), round(tech_score, 1)

VERDICT_RANK = {"DON'T BUY": 0, "OBSERVE": 1, "BUY": 2, "STRONG BUY": 3}

def apply_tiered_sanity_veto(verdict, target_price, current_price, notes):
    """Suggestion 1 — tiered veto (replaces the flat 'cap at OBSERVE' veto):
       > 15% downside (target_price < current_price * 0.85): forced DON'T BUY,
         an ABSOLUTE override — this can pull an OBSERVE down too, not just BUY.
         (Your ₹392-vs-₹137 example: a 65% downside must never read as a neutral HOLD.)
       0-15% downside: ceiling at OBSERVE only — caps BUY/STRONG BUY, but does not
         lift an already-DON'T-BUY verdict up.
       Undervalued (target >= price): untouched.
    """
    if target_price is None or not current_price:
        return verdict
    downside_pct = (current_price - target_price) / current_price
    if downside_pct > 0.15:
        if verdict != "DON'T BUY":
            notes.append(f"Forced to DON'T BUY: the modeled target price is {round(downside_pct*100,1)}% below "
                          f"the current price — a downside this large overrides the composite score entirely, "
                          f"regardless of fundamentals or momentum.")
        return "DON'T BUY"
    elif downside_pct > 0:
        if VERDICT_RANK.get(verdict, 1) > VERDICT_RANK["OBSERVE"]:
            notes.append(f"Downgraded to OBSERVE: the modeled target price is {round(downside_pct*100,1)}% below "
                          f"the current price, so the model will not issue a BUY/STRONG BUY.")
            return "OBSERVE"
    return verdict

def run_predictive_pipeline(info, hist, fcf_history, sector, industry, fundamental_score,
                              book_value_per_share, dividend_per_share, roe_pct,
                              pat_yoy_pct, analyst_growth_pct,
                              precomputed_jpb=None, precomputed_ddm=None):
    current_price = info.get('currentPrice')
    if not current_price and hist is not None and not hist.empty:
        current_price = float(hist['Close'].iloc[-1])

    result = {
        "verdict": "OBSERVE", "target_price": None, "entry_range": "N/A", "stop_loss": None,
        "time_horizon": "N/A", "note": None, "model_used": "N/A",
        "composite_score": None, "fundamental_score": fundamental_score,
        "intrinsic_score": None, "technical_score": None, "margin_of_safety": None,
        "discount_rate": None, "growth_used": None,
    }
    if not current_price:
        return result

    notes = []
    beta = info.get('beta')
    if beta is None or pd.isna(beta) or beta <= 0:
        beta = 1.0
    ke_pct = min(max((0.07 + beta * 0.07) * 100, 9), 20)

    growth_pct = 8.0
    if analyst_growth_pct and analyst_growth_pct > 0:
        growth_pct = min(max(analyst_growth_pct, 5), 25)
    elif pat_yoy_pct and pat_yoy_pct > 0:
        growth_pct = min(max(pat_yoy_pct, 5), 20)

    financial = is_financial_sector(sector, industry)
    forced_intrinsic_adjustment = 0

    # ---------------- Model 1: intrinsic valuation (sector-aware) ----------------
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
            result["model_used"] = "No valid financial-sector inputs — valuation score penalized, not defaulted to neutral"
    else:
        if fcf_history is not None and len(fcf_history) > 0:
            avg_fcf = float(fcf_history.mean())
        else:
            avg_fcf = info.get('netIncomeToCommon') or 0
        shares = info.get('sharesOutstanding')
        fcf_per_share = (avg_fcf / shares) if (avg_fcf and shares and shares > 0) else 0

        if fcf_per_share > 0:
            terminal_growth_pct = 4.0
            g = growth_pct if growth_pct > terminal_growth_pct else terminal_growth_pct + 2
            discount_rate, g_frac, tg_frac = ke_pct / 100, g / 100, terminal_growth_pct / 100
            pv_fcf = sum(fcf_per_share * (1 + g_frac) ** t / (1 + discount_rate) ** t for t in range(1, 6))
            fcf5 = fcf_per_share * (1 + g_frac) ** 5
            terminal_value = (fcf5 * (1 + tg_frac)) / (discount_rate - tg_frac)
            intrinsic_value = pv_fcf + terminal_value / (1 + discount_rate) ** 5
            result["model_used"] = "2-Stage DCF (Free Cash Flow)"
        else:
            # Suggestion 3 — FCF is negative/unusable. Try the Graham Number first
            # (needs positive EPS AND positive book value — valid for a company that's
            # accrual-profitable but cash-flow-negative, e.g. investing heavily in
            # working capital). Fall back to a book-value haircut, then to a hard
            # penalty rather than ever defaulting target_price = current_price (which
            # would silently imply a misleading ~0% margin of safety).
            eps = info.get('trailingEps')
            if eps and eps > 0 and book_value_per_share and book_value_per_share > 0:
                intrinsic_value = round(math.sqrt(22.5 * eps * book_value_per_share), 2)
                result["model_used"] = "Graham Number (defensive — FCF unusable)"
                notes.append("Free cash flow is negative or unavailable, so the standard DCF could not be run; "
                              "the target price instead uses the Graham Number (a conservative value-investing "
                              "ceiling based on EPS and book value).")
            elif book_value_per_share and book_value_per_share > 0:
                intrinsic_value = round(book_value_per_share * 0.8, 2)
                result["model_used"] = "Book Value Haircut (defensive — FCF and EPS both unusable)"
                notes.append("Neither free cash flow nor a positive EPS was usable for a Graham Number, so the "
                              "target price instead uses a 20%-discounted book value as a conservative proxy.")
            else:
                intrinsic_value = current_price
                forced_intrinsic_adjustment = -35
                result["model_used"] = "Insufficient data for DCF, Graham Number, or book value — valuation score penalized"
                notes.append("No free cash flow, EPS, or book value was usable, so no defensible target price "
                              "could be modeled; the valuation score was penalized rather than left neutral.")

    target_price = round(intrinsic_value, 2)
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0

    # ---------------- Model 2: ATR + volume-weighted support ----------------
    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist) or (current_price * 0.92)
    entry_low = round(support, 2)
    entry_high = round(support + (0.5 * atr if atr else current_price * 0.02), 2)
    if entry_low > current_price:
        entry_low, entry_high = round(current_price * 0.95, 2), round(current_price, 2)
    stop_loss = round(entry_low - (1.5 * atr if atr else entry_low * 0.05), 2)

    # ---------------- Model 3: momentum + optional ARIMA ----------------
    momentum, horizon, drift = "NEUTRAL", "3-5 Years", None
    if len(hist) > 30:
        normalized_prices = hist['Close'].values / current_price
        slope, _ = np.polyfit(np.arange(len(hist)), normalized_prices, 1)
        drift = slope * 252
        if slope > 0.0005:
            momentum, horizon = "UP", "12-18 Months (Accelerated)"
        elif slope < -0.0005:
            momentum = "DOWN"
        if HAS_ARIMA and len(hist) > 100:
            try:
                fitted = ARIMA(hist['Close'].values, order=(5, 1, 0)).fit()
                forecast = fitted.forecast(steps=30)
                momentum = "UP" if forecast[-1] > forecast[0] else "DOWN"
                horizon = "12-18 Months (Accelerated)" if momentum == "UP" else "3-5 Years"
            except Exception:
                pass

    # ---------------- Weighted composite verdict ----------------
    composite, verdict, intrinsic_score, tech_score = composite_verdict(
        fundamental_score, margin_of_safety, drift, arima_direction=momentum,
        forced_intrinsic_adjustment=forced_intrinsic_adjustment,
    )

    if verdict in ("BUY", "STRONG BUY") and momentum == "DOWN" and margin_of_safety > 0.15:
        notes.append("Intrinsic valuation and fundamentals support the rating, but price momentum is currently "
                      "negative — a possible value trap.")
    elif verdict == "DON'T BUY" and fundamental_score >= 65:
        notes.append("Fundamentals score well here — the DON'T BUY comes from rich valuation and/or weak "
                      "momentum, not weak underlying business quality.")

    # ---------------- Suggestion 1: tiered sanity veto, applied LAST ----------------
    verdict = apply_tiered_sanity_veto(verdict, target_price, current_price, notes)

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
# 6. MASTER DATA FETCH
# ------------------------------------------------------------
# Fixes applied here (Objective 1 + supporting fixes):
#  - `debt_to_equity` no longer uses `info.get("debtToEquity", 0)` (a missing D/E
#    silently became "0 debt", the best possible leverage score). Missing D/E is
#    now genuinely None / "N/A", and the checklist correctly omits it rather than
#    rewarding it.
#  - `roe` no longer uses `if roe_raw else None` (a bare truthy check nulls out a
#    real, valid 0% ROE the same way it nulls out a missing one — a company that
#    broke exactly even is a real, different data point from "we don't know").
#    Replaced with an explicit `is_valid_metric` / `is not None` check everywhere
#    ROE is used, including the value fed into the Justified P/B model.
#  - Restored net_margin, PAT QoQ, PEG-from-P/E-and-growth, and interest coverage,
#    which existed in an earlier iteration but were dropped from this version —
#    dropping them silently thins out the Past Performance and Financial Health
#    checklists (fewer checks run = a less discriminating fundamental score).
#  - Quarterly net income is now capped at the 4 most recent quarters before
#    summing (`.iloc[:4]`), instead of summing however many happen to be
#    non-null — summing 2 or 3 quarters instead of 4 understates a TTM figure
#    and throws off the P/E / ROE fallbacks that depend on it.
#  - Sector detection now goes through `is_financial_sector()` (substring match
#    on sector AND industry) instead of an exact match on sector alone.
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist_full = stock.history(period="1y")
    if hist_full.empty: raise ValueError(f"Could not find '{raw_input}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist_full['Close'].iloc[-1]), 2))
    currency_symbol = "₹"

    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    net_inc, total_eq, ebitda_val = None, None, info.get('ebitda')
    revenue_latest, ebit_latest, interest_exp_latest = None, None, None
    fcf_history = None
    pat_qoq, pat_yoy_pct, net_margin_final = None, None, None

    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            ni_series = q_fin.loc['Net Income'].dropna()
            if len(ni_series) > 0:
                # Cap at the 4 most-recent quarters for a proper TTM figure — summing
                # every non-null column (which could be 2, 3, or 5+ depending on what
                # yfinance happens to return) silently mis-states the annual total.
                net_inc = float(ni_series.iloc[:4].sum())
            if len(ni_series) >= 2 and ni_series.iloc[1] != 0:
                pat_qoq = round(((ni_series.iloc[0] - ni_series.iloc[1]) / abs(ni_series.iloc[1])) * 100, 2)
            if len(ni_series) >= 5 and ni_series.iloc[4] != 0:
                pat_yoy_pct = round(((ni_series.iloc[0] - ni_series.iloc[4]) / abs(ni_series.iloc[4])) * 100, 2)
            if 'Total Revenue' in q_fin.index and len(ni_series) > 0:
                rev_series = q_fin.loc['Total Revenue'].dropna()
                if len(rev_series) > 0 and rev_series.iloc[0] != 0:
                    net_margin_final = round((ni_series.iloc[0] / rev_series.iloc[0]) * 100, 2)

        fin = stock.financials
        if fin is not None and not fin.empty and 'Total Revenue' in fin.index:
            revenue_latest = float(fin.loc['Total Revenue'].iloc[0])
        if fin is not None and not fin.empty:
            for k in ['EBIT', 'Operating Income']:
                if k in fin.index and pd.notna(fin.loc[k].iloc[0]):
                    ebit_latest = float(fin.loc[k].iloc[0]); break
            if 'Interest Expense' in fin.index:
                ie_series = fin.loc['Interest Expense'].dropna()
                if len(ie_series) > 0:
                    interest_exp_latest = float(ie_series.iloc[0])

        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            for k in ['Stockholders Equity', 'Total Stockholder Equity', 'Common Stock Equity']:
                if k in bs.index:
                    eq_series = bs.loc[k].dropna()
                    if len(eq_series) > 0:
                        total_eq = float(eq_series.iloc[0])
                        break

        cf = stock.cashflow
        if cf is not None and not cf.empty and 'Free Cash Flow' in cf.index:
            fcf_history = cf.loc['Free Cash Flow'].dropna()

        if fin is not None and not fin.empty:
            col = fin.columns[0]
            pnl_df = pd.DataFrame([
                {"Particulars": "Net Sales", "Amount (₹ Cr)": round(fin.loc['Total Revenue', col] / 10000000, 2) if 'Total Revenue' in fin.index else "—"},
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

    mcap = info.get("marketCap")
    shares_out = info.get("sharesOutstanding")
    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")
    is_fin = is_financial_sector(sector, industry)

    # -------- RATIOS (Objective 1: never default to N/A if calculable) --------
    pe_raw = info.get("trailingPE")
    if not is_valid_metric(pe_raw) and net_inc and mcap and net_inc > 0:
        pe_raw = round(mcap / net_inc, 2)

    pb_raw = info.get("priceToBook")
    if not is_valid_metric(pb_raw) and total_eq and mcap and total_eq > 0:
        pb_raw = round(mcap / total_eq, 2)

    roe_raw = info.get("returnOnEquity")   # a fraction, e.g. 0.145
    if not is_valid_metric(roe_raw) and net_inc and total_eq and total_eq > 0:
        roe_raw = net_inc / total_eq
    roe_is_known = is_valid_metric(roe_raw)   # single source of truth — never a bare `if roe_raw`

    peg_raw = info.get("pegRatio")
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and pat_yoy_pct and pat_yoy_pct > 0:
        peg_raw = round(to_float(pe_raw) / pat_yoy_pct, 2)

    ev_ebitda = "N/A"
    if is_fin:
        ev_ebitda = "N/A (Financial Sector)"
    else:
        ev_val = info.get("enterpriseValue")
        if not is_valid_metric(ev_val) and mcap:
            ev_val = mcap + (info.get('totalDebt') or 0) - (info.get('totalCash') or 0)
        if is_valid_metric(ebitda_val) and is_valid_metric(ev_val) and ebitda_val != 0:
            ev_ebitda = round(ev_val / ebitda_val, 2)

    ebitda_margin = round((ebitda_val / revenue_latest) * 100, 2) if (is_valid_metric(ebitda_val) and revenue_latest) else "N/A"
    interest_coverage = round(ebit_latest / interest_exp_latest, 2) if (ebit_latest is not None and interest_exp_latest) else "N/A"

    dte_raw = info.get("debtToEquity")   # NO unsafe default here
    debt_to_equity = round(dte_raw / 100, 2) if is_valid_metric(dte_raw) else "N/A"

    # -------- Pre-compute fundamental score so the predictive pipeline can use it --------
    temp_metrics = {
        'pe_ratio': pe_raw, 'peg_ratio': peg_raw, 'pb_ratio': pb_raw,
        'pat_yoy': pat_yoy_pct, 'roe': (roe_raw * 100) if roe_is_known else None,
        'ev_ebitda': ev_ebitda, 'is_financial_sector': is_fin, 'debt_to_equity': debt_to_equity,
        'interest_coverage': interest_coverage, 'net_margin': net_margin_final, 'pat_qoq': pat_qoq,
    }

    v_score = score_from_checks(valuation_checks(temp_metrics))
    p_score = score_from_checks(past_performance_checks(temp_metrics))
    h_score = score_from_checks(financial_health_checks(temp_metrics))
    fundamental_score = compute_fundamental_score(v_score, p_score, h_score, is_fin)

    bvps = info.get('bookValue')
    if not is_valid_metric(bvps) and total_eq and shares_out:
        bvps = total_eq / shares_out
    bvps = bvps if is_valid_metric(bvps) else None

    div_per_share = info.get("dividendRate")

    # Precompute the sector-specific intrinsic models once, so the checklist (P/B vs
    # Justified P/B, above) and the predictive pipeline never disagree.
    jpb_ratio = jpb_value = ddm_val = None
    if is_fin:
        beta_preview = info.get('beta') if info.get('beta') and pd.notna(info.get('beta')) and info.get('beta') > 0 else 1.0
        ke_preview = min(max((0.07 + beta_preview * 0.07) * 100, 9), 20)
        growth_preview = pat_yoy_pct if (pat_yoy_pct and pat_yoy_pct > 0) else 8.0
        jpb_ratio, jpb_value = justified_pb_fair_value(roe_raw * 100 if roe_is_known else None, ke_preview, growth_preview, bvps)
        ddm_val = ddm_fair_value(div_per_share, ke_preview, growth_preview)
    temp_metrics["justified_pb"] = jpb_ratio

    predictive_data = run_predictive_pipeline(
        info, hist_full, fcf_history, sector, industry, fundamental_score,
        bvps, div_per_share, roe_raw * 100 if roe_is_known else None, pat_yoy_pct, None,
        precomputed_jpb=(jpb_ratio, jpb_value), precomputed_ddm=ddm_val,
    )

    metrics = {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw if is_valid_metric(pe_raw) else "N/A",
        "pb_ratio": pb_raw if is_valid_metric(pb_raw) else "N/A",
        "peg_ratio": peg_raw if is_valid_metric(peg_raw) else "N/A",
        "ev_ebitda": ev_ebitda,
        "roe": f"{round(roe_raw*100, 2)}%" if roe_is_known else "N/A",
        "ebitda_margin": f"{ebitda_margin}%" if ebitda_margin != "N/A" else "N/A",
        "debt_to_equity": debt_to_equity,
        "interest_coverage": interest_coverage,
        "net_margin": f"{net_margin_final}%" if net_margin_final is not None else "N/A",
        "dividend_yield": f"{round(info.get('dividendYield',0)*100,2)}%" if is_valid_metric(info.get('dividendYield')) else "N/A",
        "pat_yoy": f"{pat_yoy_pct}%" if pat_yoy_pct is not None else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq is not None else "N/A",
        "market_cap": mcap, "sector": sector, "industry": industry,
        "is_financial_sector": is_fin, "justified_pb": jpb_ratio,
        "fifty_two_high": info.get("fiftyTwoWeekHigh", "N/A"),
        "fifty_two_low": info.get("fiftyTwoWeekLow", "N/A"),
        "business_summary": info.get("longBusinessSummary"),
        "website": info.get("website", "N/A"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": fetch_google_news(f"{info.get('longName', resolved_ticker)} stock news"),
        "shareholding": {"Promoters": (info.get("heldPercentInsiders") or 0) * 100,
                          "Institutions": (info.get("heldPercentInstitutions") or 0) * 100,
                          "Public": max(0, 100 - ((info.get("heldPercentInsiders") or 0) * 100 + (info.get("heldPercentInstitutions") or 0) * 100))},
        "working_ticker": resolved_ticker, "history": hist_full.reset_index(),
        "q_fin": None, "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": predictive_data, "fair_value": predictive_data['target_price'],
        "currency": currency_symbol,
        "fundamental_score": fundamental_score,
    }
    return metrics

# ============================================================
# 7. UI PLOTLY CHARTS  (unchanged)
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
    fig = go.Figure(data=[go.Pie(labels=list(shareholding.keys()), values=list(shareholding.values()), hole=.5, marker_colors=[BLUE, PURPLE, GOLD])])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=240, margin=dict(t=10, b=10, l=10, r=10), legend=dict(orientation="h", y=-0.1))
    return fig

def custom_metric(label, value):
    st.markdown(f'<div style="background-color: {CARD_BG}; border: 1px solid {BORDER}; padding: 12px 15px; border-radius: 8px; margin-bottom: 12px;"><div style="font-size: 11px; color: {MUTED}; text-transform: uppercase; font-weight: 600; margin-bottom: 4px;">{label}</div><div style="font-size: 20px; font-weight: 700; color: #FFFFFF;">{value}</div></div>', unsafe_allow_html=True)

def card(title, body_html): st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

# ============================================================
# 8. AI & INTERLACED PDF  (unchanged)
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)
    sys = "You are an elite equity research director. Output exactly 8 numbered sections: 1. VALUATION & FAIR VALUE 2. FUTURE GROWTH & OUTLOOK 3. PAST PERFORMANCE & EARNINGS QUALITY 4. FINANCIAL HEALTH & BALANCE SHEET 5. DIVIDEND & CAPITAL ALLOCATION 6. MANAGEMENT & COMPENSATION 7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT 8. NARRATIVE VERDICT. Provide ONLY narrative reasoning."
    pmt = f"Target: {metrics['name']} ({ticker}). Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. EV/EBITDA: {metrics['ev_ebitda']}. Debt/Eq: {metrics['debt_to_equity']}. System Verdict: {metrics['predictive']['verdict']}."
    return client.models.generate_content(model='gemini-1.5-pro', contents=pmt, config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

def build_pdf_report(pdf_buffer, m, ai_text, ticker, rating_val, pred):
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#1A365D'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=12, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor('#2B6CB0'))
    body_style = ParagraphStyle('BodyText', fontName='Helvetica', fontSize=9, leading=13, textColor=colors.HexColor('#2D3748'))

    story = [Paragraph("Financial Intelligence Terminal", title_style), Paragraph(f"Dossier: {m['name']} ({ticker}) | VERDICT: {rating_val}", h1_style), Spacer(1, 10)]
    currency = m.get('currency', '₹')

    sections = re.split(r'(?=\d+\.\s+[A-Z&\s]+)', ai_text)
    for section in sections:
        if not section.strip() or section.strip().startswith("DYNAMIC_"): continue
        lines = section.strip().split('\n')
        header = lines[0].replace('**', '')
        story.append(Paragraph(header, h1_style))

        if "1. VALUATION" in header:
            sum_data = [
                ["Market Cap", f"{currency}{fmt_indian_currency(m.get('market_cap'),'')}", "Target Price", f"{currency}{pred.get('target_price')}"],
                ["P/E Ratio", f"{m.get('pe_ratio')}x", "P/B Ratio", f"{m.get('pb_ratio')}x"],
                ["ROE", f"{m.get('roe')}", "EV/EBITDA", f"{m.get('ev_ebitda')}"]
            ]
            t = Table(sum_data, colWidths=[1.5*inch, 1.8*inch, 1.5*inch, 1.8*inch])
            t.setStyle(TableStyle([('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F7FAFC')), ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#F7FAFC')), ('FONTNAME', (0,0), (-1,-1), 'Helvetica'), ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'), ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'), ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')), ('PADDING', (0,0), (-1,-1), 6)]))
            story.append(t)
            story.append(Spacer(1, 10))

            d2 = Drawing(4*inch, 1.5*inch)
            d2.add(Rect(0, 0, 3.8*inch, 1.4*inch, fillColor=colors.HexColor('#F8FAFC'), strokeColor=colors.HexColor('#E2E8F0')))
            bc = VerticalBarChart()
            bc.x, bc.y, bc.height, bc.width = 40, 20, 1.0*inch, 3*inch
            bc.data = [[m['price'], pred['target_price']]]
            bc.categoryAxis.categoryNames = ['Current', 'Fair Value']
            bc.bars[0].fillColor = colors.HexColor('#3B82F6')
            bc.valueAxis.valueMin = 0
            d2.add(bc)
            story.append(d2)
            story.append(Spacer(1, 10))

        elif "2. FUTURE GROWTH" in header:
            hist_df = m.get('history')
            if hist_df is not None and not hist_df.empty:
                d1 = Drawing(5*inch, 2*inch)
                d1.add(Rect(0, 0, 4.8*inch, 1.8*inch, fillColor=colors.HexColor('#F8FAFC'), strokeColor=colors.HexColor('#E2E8F0')))
                lp = LinePlot()
                lp.x, lp.y, lp.height, lp.width = 40, 20, 1.4*inch, 4*inch
                prices = hist_df['Close'].tolist()
                lp.data = [tuple((i, p) for i, p in enumerate(prices))]
                lp.joinedLines = 1
                lp.lines[0].strokeColor = colors.HexColor('#2B6CB0')
                lp.xValueAxis.visible = False
                lp.yValueAxis.valueMin, lp.yValueAxis.valueMax = min(prices)*0.95, max(prices)*1.05
                d1.add(lp)
                story.append(d1)
                story.append(Spacer(1, 10))

        for line in lines[1:]:
            fmt_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
            story.append(Paragraph(fmt_line, body_style))
            story.append(Spacer(1, 4))
    doc.build(story)

# ============================================================
# 9. APP UI & NAVIGATION (EXPANSIVE DASHBOARD)  (unchanged)
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None
if 'active_section' not in st.session_state: st.session_state.active_section = "Company Overview"
SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance", "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "8. Verdict"]

st.markdown('<div class="swf-title-container"><div class="swf-title">🦉 FINANCIAL INTELLIGENCE APP</div></div>', unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input: stock_input = st.text_input("Enter Stock Name or Ticker:", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn: generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked and stock_input.strip():
    with st.spinner('Compiling metrics and running the composite models...'):
        try:
            rt = resolve_name_to_ticker(stock_input)
            metrics = fetch_stock_data(rt, stock_input)
            final_ticker = metrics.pop('working_ticker')

            ai_text = generate_comprehensive_report(metrics, final_ticker)
            raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
            sections_list = [s.strip() for s in re.split(r'\n+(?=\d+\.\s+(?:VALUATION|FUTURE GROWTH|PAST PERFORMANCE|FINANCIAL HEALTH|DIVIDEND|MANAGEMENT|OWNERSHIP STRUCTURE|NARRATIVE VERDICT))', raw_ai_text, flags=re.IGNORECASE) if s.strip()]
            if len(sections_list) > 8: sections_list = sections_list[-8:]

            st.session_state.report_data = {"metrics": metrics, "ai_text": ai_text, "narrative_sections": sections_list, "ticker": final_ticker}
            st.session_state.active_section = "Company Overview"
        except Exception as e: st.error(f"Error: {e}")

with st.sidebar:
    if st.session_state.report_data:
        m0 = st.session_state.report_data['metrics']
        t0 = st.session_state.report_data['ticker']
        st.markdown(f'<div class="swf-company-mini"><div style="display:flex; align-items:center; gap:10px;"><div class="swf-avatar">{str(m0.get("name","?"))[0]}</div><div><div style="font-weight:700;">{m0.get("name")}</div><div style="color:{MUTED}; font-size:0.8em;">{t0} Report</div></div></div><div style="color:{MUTED}; font-size:0.85em; margin-top:6px;">Market Cap: {fmt_indian_currency(m0.get("market_cap"), m0.get("currency","₹"))}</div></div>', unsafe_allow_html=True)
        st.radio("Navigate", SECTIONS, index=SECTIONS.index(st.session_state.active_section), key="nav_radio", label_visibility="collapsed")
        st.session_state.active_section = st.session_state.nav_radio

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

    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        st.markdown(f'<div class="swf-card"><div style="display:flex; justify-content:space-between; align-items:flex-start;"><div><div style="color:{MUTED}; font-size:0.85em;">Stocks / {m.get("industry","N/A")}</div><div style="font-size:1.4em; font-weight:800;">{m["name"]}</div><div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report</div><span class="swf-badge" style="margin-top:8px; display:inline-block;">Verdict: <span style="color:{rc};">{current_rating}</span></span></div><div style="text-align:right;"><div style="font-size:1.6em; font-weight:800;">{currency}{m["price"]}</div></div></div></div>', unsafe_allow_html=True)
        hist_df = m.get('history')
        if hist_df is not None and not hist_df.empty: st.plotly_chart(price_history_chart(hist_df, currency), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Composite Score Radar</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(m, pred), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")
    sec = st.session_state.active_section

    if sec == "Company Overview":
        c1, c2, c3, c4 = st.columns(4)
        with c1: custom_metric("Current Price", f"{currency}{m['price']}"); custom_metric("P/E Ratio", f"{m['pe_ratio']}x" if m['pe_ratio'] != "N/A" else "N/A")
        with c2: custom_metric("P/BV Ratio", f"{m['pb_ratio']}x" if m['pb_ratio'] != "N/A" else "N/A"); custom_metric("ROE", f"{m['roe']}")
        with c3: custom_metric("EV/EBITDA", f"{m['ev_ebitda']}x" if "N/A" not in str(m['ev_ebitda']) else m['ev_ebitda']); custom_metric("PAT Growth (YoY)", f"{m['pat_yoy']}")
        with c4: custom_metric("Debt-to-Equity", f"{m['debt_to_equity']}"); custom_metric("EBITDA Margin", f"{m.get('ebitda_margin', 'N/A')}")
        card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{m.get('business_summary', 'Business summary not available.')}</p><div class='swf-sub'>Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")

    elif sec == "1. Valuation":
        st.markdown(f"### 1. Valuation")
        card("Valuation Checklist", render_checks(val_checks))
        st.markdown("##### Fair Value Estimate")
        if m.get('fair_value'):
            fig, diff_pct = fair_value_bar(m['price'], m['fair_value'], currency)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            st.caption(f"Price is approx {abs(diff_pct)}% {'overvalued' if diff_pct > 0 else 'undervalued'} vs the modeled {pred.get('model_used','valuation')} fair value.")
        card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    elif sec == "2. Future Growth":
        st.markdown("### 2. Future Growth & Outlook")
        fg1, fg2 = st.columns(2)
        with fg1: custom_metric(f"Modeled Target ({pred.get('model_used','DCF')})", f"{currency}{pred['target_price']}")
        with fg2: custom_metric("Est. Time Horizon", pred.get('time_horizon', 'N/A'))
        if m.get('fair_value'): st.plotly_chart(projection_path_chart(m['history'], m['fair_value']), use_container_width=True, config={'displayModeBar': False})
        card("Future Growth & Outlook Narrative", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    elif sec == "3. Past Performance":
        st.markdown(f"### 3. Past Performance")
        card("Past Performance Checklist", render_checks(past_checks))
        if not m['pnl_df'].empty: st.markdown("##### Profit & Loss (Cr)"); st.dataframe(m['pnl_df'], use_container_width=True, hide_index=True)
        card("Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    elif sec == "4. Financial Health":
        st.markdown(f"### 4. Financial Health")
        card("Financial Health Checklist", render_checks(health_checks))
        tab_bs, tab_cf = st.tabs(["Balance Sheet", "Cash Flows"])
        with tab_bs:
            if not m['bs_df'].empty: st.dataframe(m['bs_df'], use_container_width=True, hide_index=True)
        with tab_cf:
            if not m['cf_df'].empty: st.dataframe(m['cf_df'], use_container_width=True, hide_index=True)
        card("Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    elif sec == "5. Dividend":
        st.markdown(f"### 5. Dividend")
        card("Dividend Checklist", render_checks(div_checks))
        card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    elif sec == "6. Management":
        st.markdown("### 6. Management & Leadership")
        if m['company_officers']: st.dataframe(pd.DataFrame([{"Name": o.get('name', 'N/A'), "Position": o.get('title', 'N/A')} for o in m['company_officers']]), use_container_width=True, hide_index=True)
        card("Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

    elif sec == "7. Ownership":
        st.markdown("### 7. Ownership Structure")
        st.plotly_chart(ownership_donut(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
        card("Ownership Analysis", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    elif sec == "8. Verdict":
        st.markdown("### 8. Verdict & Summary")
        st.markdown(f"<div style='font-size:1.15em; margin-bottom:14px;'><b>Composite System Verdict:</b> <span style='color:{rc}; font-weight:bold;'>{current_rating}</span></div>", unsafe_allow_html=True)

        if pred.get('note'): st.info(pred['note'])

        if current_rating in ["BUY", "STRONG BUY"]:
            st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Recommended Entry:</b> {pred['entry_range']}<br><b>Horizon:</b> {pred['time_horizon']}<br><b>Target:</b> {currency}{pred['target_price']}<br><b>Stop Loss:</b> {currency}{pred['stop_loss']}</div>", unsafe_allow_html=True)
        else:
            st.markdown(f"<div style='font-size:0.95em; line-height:1.8em; margin-bottom:15px;'><b>Target ({pred.get('model_used','DCF')}):</b> {currency}{pred['target_price']}</div>", unsafe_allow_html=True)

        styled = style_verdict_text(narrative_for(7))
        card("AI Narrative Summary", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.6em; white-space:pre-wrap;'>{styled}</p>")

    st.markdown("---")
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, data['ai_text'], ticker, current_rating, pred)
    pdf_buffer.seek(0)
    st.download_button("📥 Download Official PDF Dossier", data=pdf_buffer, file_name=f"{ticker}_Dossier.pdf", mime="application/pdf", type="primary")
