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
# 1. SETUP & CONFIGURATION
# ============================================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
st.set_page_config(page_title="Financial Intelligence Terminal", layout="wide")

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
</style>
""", unsafe_allow_html=True)

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

# ============================================================
# 3. QUANTITATIVE COMPOSITE ENGINE (Overhauled)
# ============================================================
def annualized_drift(hist_df, lookback=180):
    if hist_df is None or len(hist_df) < 30: return None
    d = hist_df.tail(lookback)
    try:
        y, x = np.log(d['Close'].values.astype(float)), np.arange(len(d))
        slope, _ = np.polyfit(x, y, 1)
        return float(np.exp(slope * 252) - 1)
    except: return None

def calculate_atr(df, period=14):
    if df is None or len(df) <= period: return None
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean().iloc[-1]

def run_predictive_pipeline(info, hist, fcf_history, pat_yoy_pct, net_income, total_equity, shares_out):
    """COMPOSITE SCORING ARCHITECTURE (Sector-Aware)"""
    current_price = info.get('currentPrice') or (float(hist['Close'].iloc[-1]) if not hist.empty else None)
    sector = info.get('sector', 'N/A')
    is_fin = sector in ['Financial Services', 'Banks', 'Credit Services']
    
    result = {"verdict": "OBSERVE", "target_price": None, "entry_range": "N/A", "stop_loss": None, "time_horizon": "N/A", "note": None}
    if not current_price: return result

    # --- 1. FUNDAMENTAL SCORE (0-100) ---
    fund_score = 50
    pe = info.get('trailingPE') or (current_price / (net_income/shares_out) if net_income and shares_out else 20)
    if pe < 15: fund_score += 25
    elif pe < 25: fund_score += 10
    elif pe > 40: fund_score -= 20

    roe = info.get('returnOnEquity', (net_income/total_equity if net_income and total_equity else 0))
    if roe > 0.15: fund_score += 20
    elif roe < 0.05: fund_score -= 15

    if not is_fin:
        de = info.get('debtToEquity', 0) / 100
        if de < 0.5: fund_score += 15
        elif de > 2.0: fund_score -= 20
    fund_score = max(0, min(100, fund_score))

    # --- 2. VALUATION SCORE (Sector Aware DCF/DDM) ---
    ke = 0.07 + (info.get('beta', 1.0) * 0.06)
    target_price = current_price
    
    if is_fin:
        # Excess ROE / Justified P/B Model for Financials
        g = 0.05
        bvps = info.get('bookValue') or (total_equity/shares_out if total_equity and shares_out else current_price)
        safe_ke = max(ke, g + 0.01)
        justified_pb = (roe - g) / (safe_ke - g) if roe > g else 1.0
        target_price = justified_pb * bvps
        result['note'] = "Financial Sector detected: Valuation utilized Justified P/B (Excess ROE) Model rather than FCF-based DCF."
    else:
        # Standard 2-Stage FCF DCF
        avg_fcf = float(fcf_history.mean()) if fcf_history is not None and len(fcf_history) > 0 else net_income or 0
        fcf_per_share = (avg_fcf / shares_out) if shares_out else 0
        g = min(max((pat_yoy_pct or 10)/100, 0.05), 0.20)
        tg = 0.04
        if fcf_per_share > 0:
            pv_fcf = sum(fcf_per_share * (1 + g)**t / (1 + ke)**t for t in range(1, 6))
            tv = (fcf_per_share * (1 + g)**5 * (1 + tg)) / (ke - tg)
            target_price = pv_fcf + (tv / (1 + ke)**5)

    target_price = round(target_price, 2)
    mos = (target_price - current_price) / current_price
    
    val_score = 50
    if mos > 0.20: val_score = 100
    elif mos > 0: val_score = 75
    elif mos > -0.15: val_score = 40
    else: val_score = 10

    # --- 3. MOMENTUM SCORE (0-100) ---
    drift = annualized_drift(hist) or 0
    if drift > 0.20: mom_score = 100
    elif drift > 0: mom_score = 75
    elif drift > -0.10: mom_score = 40
    else: mom_score = 10

    # --- COMPOSITE VERDICT ---
    comp = (0.3 * fund_score) + (0.4 * val_score) + (0.3 * mom_score)
    if comp >= 78: final_verdict = "STRONG BUY"
    elif comp >= 62: final_verdict = "BUY"
    elif comp >= 40: final_verdict = "OBSERVE"
    else: final_verdict = "DON'T BUY"

    atr = calculate_atr(hist)
    entry_low = round(current_price * 0.96, 2)
    stop_loss = round(entry_low - (1.5 * atr if atr else entry_low * 0.05), 2)

    result.update({
        "verdict": final_verdict, "target_price": target_price,
        "entry_range": f"₹{entry_low:,.2f} - ₹{current_price:,.2f}",
        "stop_loss": stop_loss, "time_horizon": "12-18 Months" if drift > 0.05 else "3-5 Years"
    })
    return result

# ============================================================
# 4. MASTER DATA PIPELINE (N/A Bug Fixed)
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist_full = stock.history(period="1y")
    if hist_full.empty: raise ValueError(f"Could not find '{raw_input}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist_full['Close'].iloc[-1]), 2))
    
    # Financial Statements
    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    net_inc, total_eq, ebitda_val = None, None, info.get('ebitda')
    fcf_history = None

    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            net_inc = q_fin.loc['Net Income'].dropna().sum() # TTM
        
        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            for k in ['Stockholders Equity', 'Total Stockholder Equity', 'Common Stock Equity']:
                if k in bs.index:
                    total_eq = float(bs.loc[k].dropna().iloc[0])
                    break
        
        cf = stock.cashflow
        if cf is not None and 'Free Cash Flow' in cf.index:
            fcf_history = cf.loc['Free Cash Flow'].dropna()
    except: pass

    mcap = info.get("marketCap")
    shares_out = info.get("sharesOutstanding")
    sector = info.get("sector", "N/A")
    is_fin = sector in ['Financial Services', 'Banks', 'Credit Services']

    # RATIO FALLBACKS
    pe_raw = info.get("trailingPE")
    if not is_valid_metric(pe_raw) and net_inc and mcap and net_inc > 0:
        pe_raw = round(mcap / net_inc, 2)
        
    pb_raw = info.get("priceToBook")
    if not is_valid_metric(pb_raw) and total_eq and mcap and total_eq > 0:
        pb_raw = round(mcap / total_eq, 2)

    roe_raw = info.get("returnOnEquity")
    if not is_valid_metric(roe_raw) and net_inc and total_eq and total_eq > 0:
        roe_raw = (net_inc / total_eq)
        
    ev_ebitda = "N/A"
    if is_fin:
        ev_ebitda = "N/A (Fin Sector)"
    else:
        ev_val = info.get("enterpriseValue") or (mcap + info.get('totalDebt',0) - info.get('totalCash',0) if mcap else None)
        if ebitda_val and ev_val and ebitda_val > 0:
            ev_ebitda = round(ev_val / ebitda_val, 2)

    pat_yoy = info.get("earningsQuarterlyGrowth")
    pat_yoy_pct = round(pat_yoy * 100, 2) if is_valid_metric(pat_yoy) else None

    predictive_data = run_predictive_pipeline(
        info, hist_full, fcf_history, pat_yoy_pct, net_inc, total_eq, shares_out
    )

    metrics = {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw if is_valid_metric(pe_raw) else "N/A",
        "pb_ratio": pb_raw if is_valid_metric(pb_raw) else "N/A",
        "roe": f"{round(roe_raw*100, 2)}%" if is_valid_metric(roe_raw) else "N/A",
        "ev_ebitda": ev_ebitda,
        "debt_to_equity": round(info.get("debtToEquity", 0) / 100, 2) if info.get("debtToEquity") else "N/A",
        "dividend_yield": f"{round(info.get('dividendYield',0)*100,2)}%" if info.get('dividendYield') else "N/A",
        "pat_yoy": f"{pat_yoy_pct}%" if pat_yoy_pct else "N/A",
        "market_cap": mcap, "sector": sector, "industry": info.get("industry", "N/A"),
        "working_ticker": resolved_ticker, "history": hist_full.reset_index(),
        "predictive": predictive_data, "fair_value": predictive_data['target_price']
    }
    return metrics

# ============================================================
# 5. CHECKLISTS (Wired for Dynamic Scoring)
# ============================================================
def valuation_checks(m):
    pe, pb = to_float(m.get('pe_ratio')), to_float(m.get('pb_ratio'))
    return [
        ("Reasonable P/E (<25x)", pe < 25 if pe else None, f"Trailing P/E of {pe}x"),
        ("Price to Book (<3x)", pb < 3 if pb else None, f"P/B Ratio of {pb}x"),
        ("Trading Below Modeled Fair Value", m['price'] < m['fair_value'] if m.get('fair_value') else None, "DCF / DDM Margin of Safety check")
    ]

def past_performance_checks(m):
    roe, yoy = to_float(m.get('roe')), to_float(m.get('pat_yoy'))
    return [
        ("Positive Earnings Growth (YoY)", yoy > 0 if yoy else None, f"PAT YoY growth of {m.get('pat_yoy')}"),
        ("Strong Return on Equity (>15%)", roe > 15 if roe else None, f"ROE of {m.get('roe')}")
    ]

def financial_health_checks(m):
    if m.get('sector') in ['Financial Services', 'Banks', 'Credit Services']:
        return [("Sector Exemption", True, "Standard Debt/Equity checks bypassed for NBFC/Banking structural leverage.")]
    de = to_float(m.get('debt_to_equity'))
    return [("Low Leverage (D/E < 1.0)", de < 1.0 if de else None, f"Debt-to-equity of {m.get('debt_to_equity')}")]

def dividend_checks(m):
    dy = to_float(m.get('dividend_yield'))
    return [("Notable Dividend (>1.0%)", dy > 1.0 if dy else False, f"Dividend yield: {m.get('dividend_yield')}")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else 50

# ============================================================
# 6. PDF INTERLACING ENGINE
# ============================================================
def build_pdf_report(pdf_buffer, m, ai_text, ticker, rating_val, pred):
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, textColor=colors.HexColor('#1A365D'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=12, spaceBefore=14, spaceAfter=6, textColor=colors.HexColor('#2B6CB0'))
    body_style = ParagraphStyle('BodyText', fontName='Helvetica', fontSize=9, leading=13, textColor=colors.HexColor('#2D3748'))
    
    story = []
    currency = "INR "
    
    # Header Banner
    story.append(Paragraph("Financial Intelligence Terminal", title_style))
    story.append(Paragraph(f"Dossier: {m['name']} ({ticker}) | VERDICT: {rating_val}", h1_style))
    story.append(Spacer(1, 10))

    # Parse AI Text and Interlace
    sections = re.split(r'(?=\d+\.\s+[A-Z&\s]+)', ai_text)
    
    for section in sections:
        if not section.strip() or section.strip().startswith("DYNAMIC_"): continue
        lines = section.strip().split('\n')
        header = lines[0].replace('**', '')
        
        story.append(Paragraph(header, h1_style))
        
        # --- INTERLACE: 1. VALUATION ---
        if "1. VALUATION" in header:
            # Summary Table
            sum_data = [
                ["Market Cap", f"{currency}{fmt_indian_currency(m.get('market_cap'),'')}", "Target Price", f"{currency}{pred.get('target_price')}"],
                ["P/E Ratio", f"{m.get('pe_ratio')}x", "P/B Ratio", f"{m.get('pb_ratio')}x"],
                ["ROE", f"{m.get('roe')}", "EV/EBITDA", f"{m.get('ev_ebitda')}"]
            ]
            t = Table(sum_data, colWidths=[1.5*inch, 1.8*inch, 1.5*inch, 1.8*inch])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (0,-1), colors.HexColor('#F7FAFC')),
                ('BACKGROUND', (2,0), (2,-1), colors.HexColor('#F7FAFC')),
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
                ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
                ('FONTNAME', (2,0), (2,-1), 'Helvetica-Bold'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
                ('PADDING', (0,0), (-1,-1), 6),
            ]))
            story.append(t)
            story.append(Spacer(1, 10))
            
            # Fair Value Chart
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

        # --- INTERLACE: 2. FUTURE GROWTH ---
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

        # Append Paragraph Text
        for line in lines[1:]:
            fmt_line = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', line)
            story.append(Paragraph(fmt_line, body_style))
            story.append(Spacer(1, 4))
            
    doc.build(story)

# ============================================================
# 7. UI RENDER
# ============================================================
if 'report_data' not in st.session_state: st.session_state.report_data = None

st.markdown('<div class="swf-title-container"><div class="swf-title">🦉 FINANCIAL INTELLIGENCE TERMINAL</div></div>', unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input: stock_input = st.text_input("Ticker:", label_visibility="collapsed", placeholder="Enter Ticker (e.g., LTF.NS, RELIANCE.NS)")
with col_btn: generate_clicked = st.button("Analyse", type="primary", use_container_width=True)

if generate_clicked and stock_input.strip():
    with st.spinner('Running multi-model composite valuation...'):
        rt = resolve_name_to_ticker(stock_input)
        metrics = fetch_stock_data(rt, stock_input)
        final_ticker = metrics.pop('working_ticker')
        
        # AI Generation
        client = genai.Client(api_key=GEMINI_KEY)
        sys_instr = "You are an elite equity research director. Output exactly 8 numbered sections: 1. VALUATION & FAIR VALUE 2. FUTURE GROWTH & OUTLOOK 3. PAST PERFORMANCE & EARNINGS QUALITY 4. FINANCIAL HEALTH & BALANCE SHEET 5. DIVIDEND & CAPITAL ALLOCATION 6. MANAGEMENT & COMPENSATION 7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT 8. NARRATIVE VERDICT. Provide ONLY narrative reasoning."
        pmt = f"Target: {metrics['name']} ({final_ticker}). Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. EV/EBITDA: {metrics['ev_ebitda']}. Debt/Eq: {metrics['debt_to_equity']}. System Verdict: {metrics['predictive']['verdict']}."
        ai_text = client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt, config=types.GenerateContentConfig(system_instruction=sys_instr, temperature=0.2)).text
        
        st.session_state.report_data = {"metrics": metrics, "ai_text": ai_text, "ticker": final_ticker}

if st.session_state.report_data:
    d = st.session_state.report_data
    m, ticker, ai_text = d['metrics'], d['ticker'], d['ai_text']
    pred = m['predictive']
    rc = rating_color(pred['verdict'])
    
    # Top Banner
    st.markdown(f"""
    <div class="swf-card">
        <div style="display:flex; justify-content:space-between;">
            <div>
                <div style="font-size:1.6em; font-weight:800;">{m['name']} ({ticker})</div>
                <div style="color:{MUTED};">{m['sector']} | Market Cap: {fmt_indian_currency(m['market_cap'])}</div>
                <div class="swf-badge" style="margin-top:10px;">Composite Verdict: <span style="color:{rc};">{pred['verdict']}</span></div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:2em; font-weight:800;">₹{m['price']}</div>
                <div style="color:{MUTED};">Current Price</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # Metrics Grid
    c1, c2, c3, c4 = st.columns(4)
    with c1: 
        st.metric("P/E Ratio", f"{m['pe_ratio']}x" if m['pe_ratio']!="N/A" else "N/A")
        st.metric("Target Price (DCF/DDM)", f"₹{pred['target_price']}")
    with c2: 
        st.metric("P/B Ratio", f"{m['pb_ratio']}x" if m['pb_ratio']!="N/A" else "N/A")
        st.metric("Entry Range", pred['entry_range'])
    with c3: 
        st.metric("ROE", f"{m['roe']}")
        st.metric("Stop Loss", f"₹{pred['stop_loss']}")
    with c4: 
        st.metric("EV/EBITDA", f"{m['ev_ebitda']}x" if "N/A" not in str(m['ev_ebitda']) else m['ev_ebitda'])
        st.metric("Time Horizon", pred['time_horizon'])

    if pred.get('note'): st.info(pred['note'])

    # Dynamic Pros & Cons (Fixed)
    pros, cons = [], []
    for checks in [valuation_checks(m), past_performance_checks(m), financial_health_checks(m), dividend_checks(m)]:
        for lbl, stat, desc in checks:
            (pros if stat else cons).append(lbl)
            
    pc1, pc2 = st.columns(2)
    with pc1: 
        html = "".join([f"<div style='color:{GREEN}; padding:4px 0;'>✅ {p}</div>" for p in pros])
        st.markdown(f"<div class='swf-card'><div class='swf-h'>Strengths</div>{html}</div>", unsafe_allow_html=True)
    with pc2:
        html = "".join([f"<div style='color:{RED}; padding:4px 0;'>❌ {c}</div>" for c in cons])
        st.markdown(f"<div class='swf-card'><div class='swf-h'>Risks</div>{html}</div>", unsafe_allow_html=True)

    # Narrative Block
    styled_ai = style_verdict_text(ai_text)
    st.markdown(f"<div class='swf-card'><div class='swf-h'>Institutional Narrative</div><p style='white-space:pre-wrap; color:#E6E6E6; font-size:0.95em;'>{styled_ai}</p></div>", unsafe_allow_html=True)

    # Download PDF
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, ai_text, ticker, pred['verdict'], pred)
    pdf_buffer.seek(0)
    st.download_button("📥 Download Institutional PDF Dossier", data=pdf_buffer, file_name=f"{ticker}_Dossier.pdf", mime="application/pdf", type="primary")
