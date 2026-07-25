import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import re
import io
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from google import genai
from google.genai import types

# ============================================================
# 1. SETUP & CONFIGURATION
# ============================================================
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
st.set_page_config(page_title="ASW Stock Ideas - Financial Intelligence Dashboard", layout="wide")

GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")
ANGEL_KEY = st.secrets.get("ANGEL_API_KEY", "WjBiiHX1")

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
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    section[data-testid="stSidebar"] {{ background-color: {BG}; border-right: 1px solid {BORDER}; }}
    .swf-topbar {{ background: linear-gradient(90deg, #12151c, #171b24); border-bottom: 1px solid {BORDER}; padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; display:flex; align-items:center; justify-content:space-between; color:{MUTED}; font-size:0.9em; }}
    .swf-card {{ background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.05em; margin-bottom:6px; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:22px; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 2. HELPERS & DATA CASCADE
# ============================================================
def to_float(val):
    if val in [None, "N/A", ""]: 
        return None
    try: 
        return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except Exception: 
        return None

def fmt_num(val, prefix=""):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try: 
            return f"{prefix}{val:,.0f}" if abs(val) >= 1 else f"{prefix}{val}"
        except Exception: 
            return f"{prefix}{val}"
    return f"{prefix}{val}" if val not in (None, "N/A") else "N/A"

def g(d, key, default="N/A"):
    if not isinstance(d, dict):
        return default
    val = d.get(key, default)
    return default if val is None else val

def calculate_rsi(df, window=14):
    if len(df) < window:
        return "N/A"
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    loss = loss.replace(0, 1e-10)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

def fetch_google_finance_fallback(ticker, metric_type):
    try:
        url = f"https://www.google.com/finance/quote/{ticker}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            if metric_type == "pe_ratio":
                pe_div = soup.find('div', string='P/E ratio')
                if pe_div: return pe_div.find_next_sibling('div').text.strip()
            if metric_type == "dividend_yield":
                dy_div = soup.find('div', string='Dividend yield')
                if dy_div: return dy_div.find_next_sibling('div').text.replace('%','').strip()
    except Exception:
        pass
    return "N/A"

def fetch_angel_one_fallback(ticker, metric_type):
    try:
        headers = {"Authorization": f"Bearer {ANGEL_KEY}", "Accept": "application/json"}
        pass
    except Exception:
        pass
    return "N/A"

def fetch_metric_cascade(y_val, ticker, metric_type):
    if y_val not in [None, "N/A", "", 0, 0.0]:
        return y_val
    g_val = fetch_google_finance_fallback(ticker, metric_type)
    if g_val not in [None, "N/A", ""]:
        return g_val
    a_val = fetch_angel_one_fallback(ticker, metric_type)
    if a_val not in [None, "N/A", ""]:
        return a_val
    return "N/A"

def fetch_google_news(query_term):
    try:
        url = f"https://news.google.com/rss/search?q={query_term}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = root.findall('.//item')
            headlines = [item.find('title').text for item in items[:4] if item.find('title') is not None]
            if headlines:
                return " | ".join(headlines)
    except Exception:
        pass
    return None

def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit(): 
        return stock_str + '.BO'
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'): 
                    return sym
    except Exception: 
        pass
    upper_input = stock_str.upper().replace(" ", "")
    return upper_input if upper_input.endswith(('.NS', '.BO')) else upper_input + '.NS'

# ============================================================
# 3. DATA FETCHING
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: 
        raise ValueError(f"Could not find '{raw_input}'.")
    info = stock.info

    pe_raw = fetch_metric_cascade(info.get("trailingPE", "N/A"), resolved_ticker, "pe_ratio")
    dy_raw = fetch_metric_cascade(info.get("dividendYield", "N/A"), resolved_ticker, "dividend_yield")
    
    if dy_raw != "N/A" and isinstance(dy_raw, (int, float)): 
        dy_raw = round(float(dy_raw) * 100, 2)

    pat_qoq, pat_yoy = "N/A", "N/A"
    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            net_inc = q_fin.loc['Net Income'].dropna()
            if len(net_inc) >= 2 and net_inc.iloc[1] != 0: 
                pat_qoq = round(((net_inc.iloc[0] - net_inc.iloc[1]) / abs(net_inc.iloc[1])) * 100, 2)
            if len(net_inc) >= 5 and net_inc.iloc[4] != 0: 
                pat_yoy = round(((net_inc.iloc[0] - net_inc.iloc[4]) / abs(net_inc.iloc[4])) * 100, 2)
    except Exception: 
        pass

    roe_val = info.get("returnOnEquity")
    roa_val = info.get("returnOnAssets")
    de_val = info.get("debtToEquity")

    recent_news = ""
    try:
        news_items = stock.news
        if news_items:
            headlines = [n.get('title', '') for n in news_items[:4]]
            recent_news = " | ".join(headlines)
    except Exception:
        pass

    if not recent_news:
        google_news = fetch_google_news(raw_input)
        recent_news = google_news if google_news else "No recent headlines available."

    metrics = {
        "name": info.get("longName", resolved_ticker),
        "price": info.get("currentPrice", round(hist['Close'].iloc[-1], 2)),
        "pe_ratio": pe_raw,
        "peg_ratio": info.get("pegRatio", "N/A"),
        "roe": f"{round(roe_val * 100, 2)}%" if isinstance(roe_val, (int, float)) else "N/A",
        "roce_roa": f"{round(roa_val * 100, 2)}%" if isinstance(roa_val, (int, float)) else "N/A",
        "dividend_yield": f"{dy_raw}%" if dy_raw != "N/A" else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq != "N/A" else "N/A",
        "pat_yoy": f"{pat_yoy}%" if pat_yoy != "N/A" else "N/A",
        "rsi": calculate_rsi(hist, 14),
        "debt_to_equity": round(de_val / 100, 2) if isinstance(de_val, (int, float)) else "N/A",
        "market_cap": info.get("marketCap", "N/A"),
        "industry": info.get("industry", "N/A"),
        "sector": info.get("sector", "N/A"),
        "currency": info.get("currency", "INR"),
        "recent_news": recent_news,
        "working_ticker": resolved_ticker,
        "history": hist.reset_index()[["Date", "Close"]]
    }
    
    pe_num = to_float(metrics['pe_ratio'])
    growth_num = to_float(pat_yoy)
    if metrics['price'] and pe_num and pe_num > 0:
        fair_pe = min(max(growth_num, 8), 40) if growth_num else 15
        metrics['fair_value'] = round((metrics['price'] / pe_num) * fair_pe, 2)
    else:
        metrics['fair_value'] = None

    return metrics

# ============================================================
# 4. CHART BUILDERS
# ============================================================
def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    fig.add_trace(go.Bar(y=['Current Price'], x=[price], orientation='h', marker_color=BLUE, text=[f"{currency} {price}"], textposition='auto'))
    fig.add_trace(go.Bar(y=['Fair Value'], x=[fv], orientation='h', marker_color=GREEN, text=[f"{currency} {fv}"], textposition='auto'))
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=170, margin=dict(t=10, b=10, l=10, r=10), showlegend=False)
    return fig

def mom_returns_chart(m):
    yoy = to_float(m['pat_yoy']) or 0
    qoq = to_float(m['pat_qoq']) or 0
    fig = go.Figure(data=[go.Bar(x=['YoY Growth', 'QoQ Growth'], y=[yoy, qoq], marker_color=[GREEN, BLUE], text=[f"{yoy}%", f"{qoq}%"], textposition='auto')])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10))
    return fig

# ============================================================
# 5. AI GENERATION & PDF BUILDER
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)

    system_instruction = """
    You are an elite institutional equity research director building a comprehensive stock intelligence dossier.
    
    MANDATORY PRE-AMBLE VARIABLES (Exact format on first 2 lines):
    DYNAMIC_SECTOR: [Insert Industry]
    DYNAMIC_RATING: [Choose strictly ONE: STRONG BUY, BUY, OBSERVE, SELL]
    
    Structure your deep-dive analysis using EXACTLY these headers:
    1. VALUATION & FAIR VALUE
    2. FUTURE GROWTH & OUTLOOK
    3. PAST PERFORMANCE & EARNINGS QUALITY
    4. FINANCIAL HEALTH & BALANCE SHEET
    5. DIVIDEND & CAPITAL ALLOCATION
    6. MANAGEMENT & COMPENSATION
    7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
    8. SUMMARY VERDICT & KEY RISKS

    STRICT VERDICT RULES FOR SECTION 8:
    - If the rating is STRONG BUY or BUY, you MUST explicitly include:
        Recommended Entry Level: [Price or Range]
        Target Price & Horizon: [Price Target & Duration]
        Suggested Stop Loss: [Risk boundary level]
    - If the rating is OBSERVE or SELL, you MUST NOT include Entry Level, Target Price, Horizon, or Stop Loss. Provide rationale only.
    """

    user_prompt = f"""
    Target Company: {metrics['name']} ({ticker})
    Current Price: {metrics['currency']} {metrics['price']}
    P/E Ratio: {metrics['pe_ratio']} | PEG Ratio: {metrics['peg_ratio']}
    ROE: {metrics['roe']} | ROA: {metrics['roce_roa']}
    Dividend Yield: {metrics['dividend_yield']}
    PAT Growth YoY: {metrics['pat_yoy']} | QoQ: {metrics['pat_qoq']}
    Debt to Equity: {metrics['debt_to_equity']}
    Headlines: {metrics['recent_news']}
    """

    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=user_prompt,
        config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
    )
    return response.text

def get_image_from_fig(fig):
    try:
        img_bytes = fig.to_image(format="png", width=600, height=250)
        return Image(io.BytesIO(img_bytes), width=450, height=187)
    except Exception:
        return Paragraph("<i>[Chart graphic unavailable: 'kaleido' dependency required for PDF chart export]</i>", getSampleStyleSheet()['Normal'])

def build_pdf_report(pdf_buffer, m, ai_text, ticker):
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#1A365D'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=10, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor('#2B6CB0'))
    body_style = ParagraphStyle('BodyText', fontName='Helvetica', fontSize=8, leading=11.5, textColor=colors.HexColor('#2D3748'))
    
    rating_val = "EVALUATED"
    clean_lines = []
    
    for line in ai_text.split('\n'):
        line_str = line.strip()
        if line_str.startswith("DYNAMIC_RATING:"):
            rating_val = line_str.replace("DYNAMIC_RATING:", "").strip().upper()
        elif not line_str.startswith("DYNAMIC_"):
            clean_lines.append(line_str)
            
    rating_color = GREEN if "BUY" in rating_val else ORANGE if "OBSERVE" in rating_val else RED
    
    story = [
        Paragraph("ASW Stock Ideas — Research Division", title_style),
        Paragraph(f"Terminal Dossier — {m['name']} ({ticker}) | Rating: <font color='{rating_color}'><b>{rating_val}</b></font>", h1_style),
        Spacer(1, 10)
    ]

    for line in clean_lines:
        if any(h in line for h in ["1. VALUATION", "2. FUTURE GROWTH", "3. PAST PERFORMANCE", "4. FINANCIAL", "5. DIVIDEND", "6. MANAGEMENT", "7. OWNERSHIP", "8. SUMMARY"]):
            story.append(Paragraph(line, h1_style))
            if "1. VALUATION" in line and m.get('fair_value'):
                story.append(get_image_from_fig(fair_value_bar(m['price'], m['fair_value'], m['currency'])))
            elif "3. PAST PERFORMANCE" in line:
                story.append(get_image_from_fig(mom_returns_chart(m)))
        else:
            processed_line = line
            processed_line = re.sub(r'(?i)\bSTRONG BUY\b', f'<font color="{GREEN}"><b>STRONG BUY</b></font>', processed_line)
            processed_line = re.sub(r'(?i)(?<!STRONG )\bBUY\b', f'<font color="{GREEN}"><b>BUY</b></font>', processed_line)
            processed_line = re.sub(r'(?i)\bOBSERVE\b', f'<font color="{ORANGE}"><b>OBSERVE</b></font>', processed_line)
            processed_line = re.sub(r'(?i)\bSELL\b', f'<font color="{RED}"><b>SELL</b></font>', processed_line)
            
            story.append(Paragraph(processed_line, body_style))
            story.append(Spacer(1, 3))

    doc.build(story)

# ============================================================
# 6. UI & MAIN EXECUTION
# ============================================================
st.markdown("<div class='swf-topbar'><div>🐂 <b>ASW STOCK IDEAS</b> &nbsp;|&nbsp; Financial Intelligence Dashboard</div></div>", unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input: 
    stock_input = st.text_input("Search Ticker:", label_visibility="collapsed", placeholder="Enter a company or ticker (e.g. Reliance, HBL, Tata Motors)...")
with col_btn: 
    generate_clicked = st.button("Generate Terminal Dossier", type="primary", use_container_width=True)

if 'report_data' not in st.session_state: 
    st.session_state.report_data = None

if generate_clicked:
    if stock_input.strip():
        with st.spinner('Compiling data cascade and institutional narrative...'):
            try:
                resolved_ticker = resolve_name_to_ticker(stock_input)
                m = fetch_stock_data(resolved_ticker, stock_input)
                final_ticker = m.pop('working_ticker', resolved_ticker)
                ai_text = generate_comprehensive_report(m, final_ticker)
                
                rating_match = re.search(r'DYNAMIC_RATING:\s*(.*)', ai_text)
                rating = rating_match.group(1).strip().upper() if rating_match else "EVALUATED"
                
                st.session_state.report_data = {"metrics": m, "ai_text": ai_text, "ticker": final_ticker, "rating": rating}
            except Exception as e:
                st.error(f"Error compiling dossier: {e}")

if st.session_state.report_data:
    d = st.session_state.report_data
    m = d['metrics']
    
    rc = GREEN if "BUY" in d['rating'] else ORANGE if "OBSERVE" in d['rating'] else RED
    st.markdown(f"## {m['name']} ({d['ticker']})")
    st.markdown(f"#### AI Rating: <span style='color:{rc}; font-weight:bold;'>{d['rating']}</span>", unsafe_allow_html=True)
    st.markdown("---")
    
    c1, c2 = st.columns([2, 1])
    with c1:
        clean_narrative = re.sub(r'DYNAMIC_.*?\n', '', d['ai_text'])
        clean_narrative = re.sub(r'(?i)\bSTRONG BUY\b', f'<span style="color:{GREEN}; font-weight:bold;">STRONG BUY</span>', clean_narrative)
        clean_narrative = re.sub(r'(?i)(?<!STRONG )\bBUY\b', f'<span style="color:{GREEN}; font-weight:bold;">BUY</span>', clean_narrative)
        clean_narrative = re.sub(r'(?i)\bOBSERVE\b', f'<span style="color:{ORANGE}; font-weight:bold;">OBSERVE</span>', clean_narrative)
        clean_narrative = re.sub(r'(?i)\bSELL\b', f'<span style="color:{RED}; font-weight:bold;">SELL</span>', clean_narrative)
        
        st.markdown(clean_narrative, unsafe_allow_html=True)
        
    with c2:
        st.markdown("### Interactive Charts")
        if m.get('fair_value'):
            st.plotly_chart(fair_value_bar(m['price'], m['fair_value'], m['currency']), use_container_width=True)
        st.plotly_chart(mom_returns_chart(m), use_container_width=True)

    st.markdown("---")
    
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, d['ai_text'], d['ticker'])
    pdf_buffer.seek(0)
    
    st.download_button("📥 Download Official PDF Dossier (Includes Charts)", data=pdf_buffer, file_name=f"{d['ticker']}_ASW_Dossier.pdf", mime="application/pdf", type="primary")
