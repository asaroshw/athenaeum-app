import streamlit as st
import yfinance as yf
import pandas as pd
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
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    
    html, body, [class*="st-"], .stApp {{ 
        font-family: 'Inter', sans-serif !important;
    }}
    .stApp {{ background-color: {BG}; color: #E6E6E6; }}
    section[data-testid="stSidebar"] {{ background-color: {BG}; border-right: 1px solid {BORDER}; }}
    section[data-testid="stSidebar"] .stRadio > label {{ display:none; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        background-color: transparent; padding: 8px 10px; border-radius: 6px; margin-bottom: 2px;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{ background-color: #1c2128; }}
    .swf-topbar {{
        background: linear-gradient(90deg, #12151c, #171b24); border-bottom: 1px solid {BORDER};
        padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; display:flex; align-items:center;
        justify-content:space-between; color:{MUTED}; font-size:0.9em;
    }}
    .swf-card {{ background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px; padding: 18px 20px; margin-bottom: 16px; }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.05em; margin-bottom:6px; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:22px; }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
    .swf-company-mini {{ padding: 6px 4px 14px 4px; border-bottom: 1px solid {BORDER}; margin-bottom: 8px; }}
    .swf-avatar {{ width:40px; height:40px; border-radius:8px; background:#fff; color:#111; font-weight:800; display:flex; align-items:center; justify-content:center; font-size:1.2em; }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 2. DATA SCRAPERS & HELPERS
# ============================================================
def to_float(val):
    if val in [None, "N/A", ""]: return None
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except Exception: return None

def fmt_num(val, prefix=""):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try: return f"{prefix}{val:,.0f}" if abs(val) >= 1 else f"{prefix}{val}"
        except Exception: return f"{prefix}{val}"
    return f"{prefix}{val}" if val not in (None, "N/A") else "N/A"

def calculate_rsi(df, window=14):
    if len(df) < window: return "N/A"
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    loss = loss.replace(0, 1e-10)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

def fetch_screener_fundamentals(ticker):
    clean_ticker = ticker.replace('.NS', '').replace('.BO', '')
    urls = [f"https://www.screener.in/company/{clean_ticker}/consolidated/", f"https://www.screener.in/company/{clean_ticker}/"]
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    html_content = None
    for url in urls:
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                html_content = res.text
                break
        except Exception: continue
            
    if not html_content: return {}
        
    soup = BeautifulSoup(html_content, 'html.parser')
    metrics = {}
    ratios_ul = soup.find('ul', id='top-ratios')
    if ratios_ul:
        for li in ratios_ul.find_all('li'):
            name_span = li.find('span', class_='name')
            num_span = li.find('span', class_='number')
            if name_span and num_span:
                name = name_span.text.strip().lower()
                val = num_span.text.strip().replace(',', '')
                if 'market cap' in name: metrics['market_cap'] = val
                elif 'stock p/e' in name: metrics['pe_ratio'] = val
                elif 'roce' in name: metrics['roce_roa'] = val
                elif 'roe' in name: metrics['roe'] = val
                elif 'dividend yield' in name: metrics['dividend_yield'] = val
    return metrics

def fetch_google_news(query_term):
    try:
        url = f"https://news.google.com/rss/search?q={query_term}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            items = root.findall('.//item')
            headlines = [item.find('title').text for item in items[:4] if item.find('title') is not None]
            if headlines: return " | ".join(headlines)
    except Exception: pass
    return None

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
# 3. DATA FETCHING (with Mathematical Fallbacks)
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: raise ValueError(f"Could not find historical data for '{raw_input}'.")
    info = stock.info
    current_price = info.get("currentPrice", round(hist['Close'].iloc[-1], 2))

    # 1. Fetch Fundamentals
    scr_data = fetch_screener_fundamentals(resolved_ticker)
    
    # 2. Map Base Data 
    pe_raw = scr_data.get('pe_ratio') or info.get("trailingPE", "N/A")
    dy_raw = scr_data.get('dividend_yield') or info.get("dividendYield", "N/A")
    if dy_raw != "N/A" and isinstance(dy_raw, (int, float)): 
        dy_raw = round(float(dy_raw) * 100, 2)

    roe_val = scr_data.get('roe') or info.get("returnOnEquity")
    if isinstance(roe_val, (int, float)): roe_val = f"{round(roe_val * 100, 2)}%"
    elif roe_val and str(roe_val) != "N/A" and "%" not in str(roe_val): roe_val = f"{roe_val}%"

    roa_val = scr_data.get('roce_roa') or info.get("returnOnAssets")
    if isinstance(roa_val, (int, float)): roa_val = f"{round(roa_val * 100, 2)}%"
    elif roa_val and str(roa_val) != "N/A" and "%" not in str(roa_val): roa_val = f"{roa_val}%"

    mcap_val = scr_data.get('market_cap') or info.get("marketCap", "N/A")
    de_val = info.get("debtToEquity")

    # 3. Quarterly Growth
    pat_qoq, pat_yoy = "N/A", "N/A"
    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            net_inc = q_fin.loc['Net Income'].dropna()
            if len(net_inc) >= 2 and net_inc.iloc[1] != 0: pat_qoq = round(((net_inc.iloc[0] - net_inc.iloc[1]) / abs(net_inc.iloc[1])) * 100, 2)
            if len(net_inc) >= 5 and net_inc.iloc[4] != 0: pat_yoy = round(((net_inc.iloc[0] - net_inc.iloc[4]) / abs(net_inc.iloc[4])) * 100, 2)
    except Exception: pass

    # 4. MATHEMATICAL FALLBACKS (If external data is missing)
    if pe_raw in ["N/A", None, ""]:
        eps = info.get("trailingEps") or info.get("forwardEps")
        if eps and eps > 0 and current_price:
            pe_raw = round(current_price / eps, 2)

    if dy_raw in ["N/A", None, ""]:
        div_rate = info.get("dividendRate")
        if div_rate and current_price:
            dy_raw = round((div_rate / current_price) * 100, 2)
            
    peg_raw = info.get("pegRatio", "N/A")
    if peg_raw in ["N/A", None, ""] and pe_raw not in ["N/A", None] and pat_yoy != "N/A":
        pat_yoy_flt = to_float(pat_yoy)
        if pat_yoy_flt and pat_yoy_flt > 0:
            peg_raw = round(to_float(pe_raw) / pat_yoy_flt, 2)

    # 5. Smart Google News 
    recent_news = ""
    try:
        news_items = stock.news
        if news_items:
            recent_news = " | ".join([n.get('title', '') for n in news_items[:4]])
    except Exception: pass
    
    if not recent_news:
        comp_name = info.get("longName") or resolved_ticker.replace('.NS','').replace('.BO','')
        safe_query = urllib.parse.quote(f"{comp_name} stock news")
        google_news = fetch_google_news(safe_query)
        recent_news = google_news if google_news else "No recent headlines available."
        
    insider_h = (info.get("heldPercentInsiders") or 0) * 100
    inst_h = (info.get("heldPercentInstitutions") or 0) * 100
    public_h = max(0, 100 - (insider_h + inst_h))

    metrics = {
        "name": info.get("longName", resolved_ticker),
        "price": current_price,
        "pe_ratio": pe_raw,
        "peg_ratio": peg_raw,
        "roe": roe_val if roe_val else "N/A",
        "roce_roa": roa_val if roa_val else "N/A",
        "dividend_yield": f"{dy_raw}%" if dy_raw != "N/A" else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq != "N/A" else "N/A",
        "pat_yoy": f"{pat_yoy}%" if pat_yoy != "N/A" else "N/A",
        "rsi": calculate_rsi(hist, 14),
        "debt_to_equity": round(de_val / 100, 2) if isinstance(de_val, (int, float)) else "N/A",
        "market_cap": mcap_val,
        "industry": info.get("industry", "N/A"),
        "sector": info.get("sector", "N/A"),
        "currency": info.get("currency", "INR"),
        "employees": info.get("fullTimeEmployees"),
        "website": info.get("website"),
        "business_summary": info.get("longBusinessSummary"),
        "current_ratio": info.get("currentRatio"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "target_mean_price": info.get("targetMeanPrice"),
        "num_analysts": info.get("numberOfAnalystOpinions"),
        "payout_ratio": info.get("payoutRatio"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": recent_news,
        "working_ticker": resolved_ticker,
        "history": hist.reset_index()[["Date", "Close"]],
        "shareholding": {"Promoters / Insiders": round(insider_h, 2), "Institutions (FII/DII)": round(inst_h, 2), "General Public": round(public_h, 2)}
    }

    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            def g_bs(row):
                try: return float(bs.loc[row, col])
                except Exception: return None
            metrics["total_assets"] = g_bs('Total Assets')
            metrics["cash_bs"] = g_bs('Cash And Cash Equivalents')
            metrics["receivables"] = g_bs('Receivables')
            metrics["inventory"] = g_bs('Inventory')
            metrics["current_liab"] = g_bs('Current Liabilities')
            metrics["total_debt_bs"] = g_bs('Total Debt')
            metrics["total_equity"] = g_bs('Common Stock Equity') or g_bs('Stockholders Equity')
    except Exception: pass
    
    # Fair Value Calculation
    pe_num = to_float(metrics['pe_ratio'])
    growth_num = to_float(pat_yoy)
    if metrics['price'] and pe_num and pe_num > 0:
        used_growth = growth_num if (growth_num and growth_num > 0) else 10.0
        fair_pe = min(max(used_growth, 8), 40)
        metrics['fair_value'] = round((metrics['price'] / pe_num) * fair_pe, 2)
    else:
        metrics['fair_value'] = None

    return metrics

# ============================================================
# 4. CHART BUILDERS
# ============================================================
def analysis_radar_chart(scores):
    categories = list(scores.keys())
    values = list(scores.values())
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]], theta=categories + [categories[0]],
        fill='toself', fillcolor='rgba(234,179,8,0.35)', line=dict(color=GOLD, width=2)
    ))
    fig.update_layout(polar=dict(bgcolor=BG, radialaxis=dict(visible=False, range=[0, 100]), angularaxis=dict(color=MUTED, gridcolor=BORDER)), showlegend=False, paper_bgcolor=BG, margin=dict(t=10, b=10, l=30, r=30), height=230)
    return fig

def price_history_chart(hist_df, fair_value, currency):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines', line=dict(color=BLUE, width=1.5), fill='tozeroy', fillcolor='rgba(56,189,248,0.08)', name='Price'))
    if fair_value: fig.add_hline(y=fair_value, line_dash='dot', line_color=GOLD, annotation_text=f'Fair Value {currency} {fair_value}', annotation_font_color=GOLD)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=20, l=10, r=10), xaxis=dict(showgrid=False, title=None), yaxis=dict(showgrid=False, title=currency))
    return fig

def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    fig.add_trace(go.Bar(y=['Current Price'], x=[price], orientation='h', marker_color=BLUE, text=[f"{currency} {price}"], textposition='auto'))
    fig.add_trace(go.Bar(y=['Fair Value'], x=[fv], orientation='h', marker_color=GREEN, text=[f"{currency} {fv}"], textposition='auto'))
    diff_pct = round(((price - fv) / fv) * 100, 1) if fv else None
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=170, margin=dict(t=10, b=10, l=10, r=10), showlegend=False, xaxis=dict(showgrid=False, title=currency))
    return fig, diff_pct

def mom_returns_chart(m):
    yoy = to_float(m.get('pat_yoy')) or 0
    qoq = to_float(m.get('pat_qoq')) or 0
    fig = go.Figure(data=[go.Bar(x=['YoY Growth', 'QoQ Growth'], y=[yoy, qoq], marker_color=[GREEN, BLUE], text=[f"{yoy}%", f"{qoq}%"], textposition='auto')])
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10))
    return fig

def ownership_bar(shareholding):
    fig = go.Figure()
    colors_list = [BLUE, '#a855f7', GOLD]
    for (k, v), c in zip(shareholding.items(), colors_list):
        fig.add_trace(go.Bar(y=['Ownership'], x=[v], name=f"{k} ({v}%)", orientation='h', marker_color=c, text=[f"{v}%"], textposition='auto'))
    fig.update_layout(barmode='stack', template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=150, margin=dict(t=10, b=40, l=10, r=10), xaxis=dict(visible=False), yaxis=dict(visible=False), legend=dict(orientation='h', y=-0.3))
    return fig

def balance_sheet_treemap(m):
    assets_labels, assets_vals, assets_colors = [], [], []
    if m.get('cash_bs'): assets_labels.append('Cash & Equivalents'); assets_vals.append(m['cash_bs']); assets_colors.append('#22c55e')
    if m.get('receivables'): assets_labels.append('Receivables'); assets_vals.append(m['receivables']); assets_colors.append('#4ade80')
    if m.get('inventory'): assets_labels.append('Inventory'); assets_vals.append(m['inventory']); assets_colors.append('#86efac')
    if m.get('total_assets') and assets_vals:
        other = m['total_assets'] - sum(assets_vals)
        if other > 0: assets_labels.append('Other Assets'); assets_vals.append(other); assets_colors.append('#bbf7d0')

    liab_labels, liab_vals, liab_colors = [], [], []
    if m.get('current_liab'): liab_labels.append('Current Liab'); liab_vals.append(m['current_liab']); liab_colors.append('#4ade80')
    if m.get('total_debt_bs'): liab_labels.append('Debt'); liab_vals.append(m['total_debt_bs']); liab_colors.append('#f87171')
    if m.get('total_equity'): liab_labels.append('Equity'); liab_vals.append(m['total_equity']); liab_colors.append('#22c55e')

    if not assets_vals or not liab_vals: return None

    fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'domain'}, {'type': 'domain'}]], subplot_titles=("Assets", "Liabilities + Equity"))
    fig.add_trace(go.Treemap(labels=assets_labels, parents=[""] * len(assets_labels), values=assets_vals, marker_colors=assets_colors, textinfo="label+value"), row=1, col=1)
    fig.add_trace(go.Treemap(labels=liab_labels, parents=[""] * len(liab_labels), values=liab_vals, marker_colors=liab_colors, textinfo="label+value"), row=1, col=2)
    fig.update_layout(paper_bgcolor=BG, margin=dict(t=40, b=10, l=10, r=10), height=320, font_color="#E6E6E6")
    return fig

# ============================================================
# 5. CHECKLIST BUILDERS
# ============================================================
def valuation_checks(m):
    price = m.get('price'); fv = m.get('fair_value')
    currency = m.get('currency', '')
    pe = to_float(m.get('pe_ratio')); peg = to_float(m.get('peg_ratio'))
    tgt = m.get('target_mean_price') or fv
    
    checks = []
    if fv and price is not None:
        checks.append(("Fair Value Estimate", price < fv, f"Price {currency} {price} vs an estimated fair value of {currency} {fv}"))
        checks.append(("Significantly Undervalued", price < fv * 0.8, "Price is more than 20% below the fair value estimate"))
    else:
        checks.append(("Fair Value Estimate", None, "Insufficient data to estimate fair value"))
        checks.append(("Significantly Undervalued", None, "Insufficient data"))
        
    checks.append(("Reasonable P/E (<25x)", None if pe is None else pe < 25, f"Trailing P/E of {pe}x" if pe is not None else "P/E not available"))
    checks.append(("Attractive PEG (<1.5)", None if peg is None else peg < 1.5, f"PEG ratio of {peg}" if peg is not None else "PEG not available"))
    
    if tgt and price is not None:
        checks.append(("Projected Upside Target", price < tgt, f"Projected fair target {currency} {tgt} vs Price {currency} {price}"))
    else:
        checks.append(("Projected Upside Target", None, "Insufficient data for upside target"))
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
    cash = m.get('total_cash'); debt = m.get('total_debt')
    cr = m.get('current_ratio'); currency = m.get('currency', '')
    checks = [("Low Leverage (D/E < 1.0)", None if de is None else de < 1.0, f"Debt-to-equity of {de}" if de is not None else "Not available")]
    if cash is not None and debt is not None:
        checks.append(("Cash Exceeds Total Debt", cash > debt, f"Cash {currency} {cash:,.0f} vs Debt {currency} {debt:,.0f}"))
    else:
        checks.append(("Cash Exceeds Total Debt", None, "Insufficient data"))
    if cr is not None:
        checks.append(("Short-Term Liquidity (Current Ratio > 1)", cr > 1, f"Current ratio of {round(cr,2)}"))
    else:
        checks.append(("Short-Term Liquidity", None, "Insufficient data"))
    return checks

def dividend_checks(m):
    dy = to_float(m.get('dividend_yield')); payout = m.get('payout_ratio')
    checks = [("Pays a Notable Dividend (>1.5%)", None if dy is None else dy > 1.5, f"Dividend yield of {m.get('dividend_yield')}")]
    if payout is not None:
        checks.append(("Sustainable Payout (<75%)", payout < 0.75, f"Payout ratio of {round(payout*100,1)}%"))
    else:
        checks.append(("Sustainable Payout", None, "Insufficient data"))
    return checks

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
        html += f'<div style="padding:5px 0; font-family: Inter, sans-serif;"><span class="{cls}">{icon} <b>{label}</b></span><div class="swf-sub">{desc}</div></div>'
    return html

def card(title, body_html):
    st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

# ============================================================
# 6. AI GENERATION & PDF BUILDER
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)

    system_instruction = """
    You are an elite institutional equity research director building a comprehensive stock intelligence dossier.
    Do not use markdown hash symbols or asterisks. Output clean raw text with clear section headers.

    MANDATORY PRE-AMBLE VARIABLES (Exact format on first 3 lines):
    DYNAMIC_SECTOR: [Insert Industry]
    DYNAMIC_RATING: [STRONG BUY, BUY, OBSERVE, or SELL]
    DYNAMIC_DURATION: [1-3 Months, 3-5 Years, or N/A]

    Structure your exhaustive deep-dive analysis using EXACTLY these 8 numbered headers:
    1. VALUATION & FAIR VALUE
    2. FUTURE GROWTH & OUTLOOK
    3. PAST PERFORMANCE & EARNINGS QUALITY
    4. FINANCIAL HEALTH & BALANCE SHEET
    5. DIVIDEND & CAPITAL ALLOCATION
    6. MANAGEMENT & COMPENSATION
    7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
    8. SUMMARY VERDICT & KEY RISKS

    STRICT VERDICT RULES FOR SECTION 8:
    - If the rating is STRONG BUY or BUY, you MUST explicitly generate and state:
        Recommended Entry Level: [Price or Zone]
        Target Price & Horizon: [Calculated Target Price]
        Suggested Stop Loss: [Risk boundary level]
    - If the rating is OBSERVE or SELL, you MUST NOT include Entry Level, Target Price, or Stop Loss. Provide rationale only.
    """

    user_prompt = f"""
    Target Company Data:
    Company Name: {metrics['name']} ({ticker})
    Current Market Price: {metrics.get('currency','INR')} {metrics['price']}
    Market Cap: {metrics.get('currency','INR')} {metrics['market_cap']}
    P/E Ratio: {metrics['pe_ratio']} | PEG Ratio: {metrics['peg_ratio']}
    ROE: {metrics['roe']} | ROA/ROCE Proxy: {metrics['roce_roa']}
    Dividend Yield: {metrics['dividend_yield']}
    14-Day RSI: {metrics['rsi']}
    PAT Growth YoY: {metrics['pat_yoy']} | QoQ: {metrics['pat_qoq']}
    Debt to Equity: {metrics['debt_to_equity']}
    Net Margin: {metrics.get('net_margin','N/A')}
    Calculated Fair Value Proxy: {metrics.get('fair_value','N/A')}
    Recent Headlines: {metrics['recent_news']}
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

def build_pdf_report(pdf_buffer, m, ai_text, ticker, rating_val):
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#1A365D'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=10, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor('#2B6CB0'))
    body_style = ParagraphStyle('BodyText', fontName='Helvetica', fontSize=8, leading=11.5, textColor=colors.HexColor('#2D3748'))
    
    clean_lines = [line.strip() for line in ai_text.split('\n') if not line.strip().startswith("DYNAMIC_")]
            
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
            elif "4. FINANCIAL" in line:
                tm = balance_sheet_treemap(m)
                if tm: story.append(get_image_from_fig(tm))
            elif "7. OWNERSHIP" in line and m.get('shareholding'):
                story.append(get_image_from_fig(ownership_bar(m['shareholding'])))
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
# 7. APP STATE 
# ============================================================
if 'report_data' not in st.session_state:
    st.session_state.report_data = None
if 'active_section' not in st.session_state:
    st.session_state.active_section = "Company Overview"

SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance",
            "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "8. Other Information"]

# ============================================================
# 8. TOP BAR & SEARCH
# ============================================================
st.markdown(f"""
<div class="swf-topbar">
    <div>🐂 <b>ASW STOCK IDEAS</b> &nbsp;|&nbsp; Financial Intelligence Dashboard</div>
</div>
""", unsafe_allow_html=True)

col_input, col_btn = st.columns([4, 1])
with col_input:
    stock_input = st.text_input("Enter Stock Name or Ticker (e.g., Reliance, Tata Motors, HBL):", label_visibility="collapsed", placeholder="Search a company or ticker...")
with col_btn:
    generate_clicked = st.button("Generate Terminal Dossier", type="primary", use_container_width=True)

if generate_clicked:
    if not stock_input.strip():
        st.warning("Please enter a valid stock identifier.")
    else:
        with st.spinner('Compiling exhaustive institutional intelligence and qualitative modules...'):
            try:
                resolved_ticker = resolve_name_to_ticker(stock_input)
                metrics = fetch_stock_data(resolved_ticker, stock_input)
                final_ticker = metrics.pop('working_ticker')

                ai_text = generate_comprehensive_report(metrics, final_ticker)
                
                rating_match = re.search(r'DYNAMIC_RATING:\s*(.*)', ai_text)
                rating = rating_match.group(1).strip().upper() if rating_match else "EVALUATED"

                raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
                sections_list = [s.strip() for s in re.split(r'\n+(?=(?:\*\*|__)?\d+\.\s)', raw_ai_text) if s.strip()]
                if len(sections_list) > 8:
                    sections_list = sections_list[-8:]

                st.session_state.report_data = {
                    "metrics": metrics,
                    "ai_text": ai_text,
                    "narrative_sections": sections_list,
                    "stock": stock_input,
                    "ticker": final_ticker,
                    "rating": rating
                }
                st.session_state.active_section = "Company Overview"
            except Exception as e:
                st.error(f"Error building report: {e}")

# ============================================================
# 9. SIDEBAR
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
                    <div style="font-weight:700; font-family: 'Inter', sans-serif;">{m0.get('name')}</div>
                    <div style="color:{MUTED}; font-size:0.8em; font-family: 'Inter', sans-serif;">{t0} Stock Report</div>
                </div>
            </div>
            <div style="color:{MUTED}; font-size:0.85em; margin-top:6px; font-family: 'Inter', sans-serif;">
                Market Cap: {m0.get('currency','INR')} {m0.get('market_cap','N/A')}
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
        st.markdown(f'<div style="color:{MUTED}; padding:10px; font-family: Inter, sans-serif;">Generate a report to unlock section navigation.</div>', unsafe_allow_html=True)

# ============================================================
# 10. MAIN CONTENT
# ============================================================
if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    ticker = data['ticker']
    narrative = data.get('narrative_sections', [])
    current_rating = data.get('rating', 'EVALUATED')

    def narrative_for(idx):
        """Fetches narrative and strips out the redundant AI header (e.g., '1. VALUATION & FAIR VALUE')"""
        if idx < len(narrative):
            raw_text = narrative[idx]
            cleaned = re.sub(r'^(?:\*\*|__)?\d+\.\s+[A-Z&\s]+(?:\*\*|__)?\n+', '', raw_text, flags=re.IGNORECASE).strip()
            return cleaned
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
                    <div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report &nbsp;|&nbsp; Market Cap: {m.get('currency','INR')} {m.get('market_cap','N/A')}</div>
                    <span class="swf-badge" style="margin-top:8px; display:inline-block;">Rating: <span style="color:{rc};">{current_rating}</span></span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:1.6em; font-weight:800;">{m.get('currency','INR')} {m.get('price')}</div>
                    <div style="color:{MUTED}; font-size:0.85em;">Current Price</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        hist_df = m.get('history')
        if hist_df is not None and not hist_df.empty:
            st.plotly_chart(price_history_chart(hist_df, m.get('fair_value'), m.get('currency','INR')), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Analysis Summary</div>', unsafe_allow_html=True)
        st.plotly_chart(snowflake_chart(scores), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")

    section = st.session_state.active_section

    # ---------- COMPANY OVERVIEW ----------
    if section == "Company Overview":
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Price", f"{m.get('currency','INR')} {m.get('price')}")
        c1.metric("P/E Ratio", f"{m.get('pe_ratio')}x" if m.get('pe_ratio') != "N/A" else "N/A")
        c2.metric("PEG Ratio", f"{m.get('peg_ratio')}")
        c2.metric("14-Day RSI", f"{m.get('rsi')}")
        c3.metric("ROE", f"{m.get('roe')}")
        c3.metric("Dividend Yield", f"{m.get('dividend_yield')}")
        c4.metric("PAT Growth (YoY)", f"{m.get('pat_yoy')}")
        c4.metric("PAT Growth (QoQ)", f"{m.get('pat_qoq')}")

        st.markdown("### About the Company")
        summary = m.get('business_summary') or "Business summary not available for this ticker."
        card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{summary}</p>"
                          f"<div class='swf-sub'>Employees: {m.get('employees', 'N/A')} | Sector: {m.get('sector', 'N/A')} | Industry: {m.get('industry', 'N/A')}</div>")

    # ---------- 1. VALUATION ----------
    elif section == "1. Valuation":
        st.markdown(f"### 1. Valuation — Score {score_from_checks(val_checks)}/100")
        card("Valuation Checklist", render_checks(val_checks))
        if m.get('fair_value'):
            fig, diff_pct = fair_value_bar(m['price'], m['fair_value'], m.get('currency','INR'))
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            status_word = "overvalued" if diff_pct and diff_pct > 0 else "undervalued"
            st.caption(f"Price is approximately {abs(diff_pct)}% {status_word} vs the projected fair value estimate.")
        card("Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    # ---------- 2. FUTURE GROWTH ----------
    elif section == "2. Future Growth":
        st.markdown("### 2. Future Growth & Outlook")
        if m.get('target_mean_price') and m.get('num_analysts'):
            card("Analyst / Projected Consensus",
                 f"<div class='swf-sub' style='margin-left:0;'>Average 12-month analyst target: "
                 f"<b>{m.get('currency','INR')} {m['target_mean_price']}</b> based on {m['num_analysts']} analyst(s).</div>")
        elif m.get('fair_value'):
            card("Analyst / Projected Consensus", f"<div class='swf-sub' style='margin-left:0;'>No institutional analysts track this stock. Using internal AI & mathematical projection target: <b>{m.get('currency','INR')} {m['fair_value']}</b>.</div>")
        else:
            card("Analyst / Projected Consensus", "<div class='swf-check-na'>&#8213; Insufficient data to forecast growth for this stock.</div>")
        card("Future Growth & Outlook", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    # ---------- 3. PAST PERFORMANCE ----------
    elif section == "3. Past Performance":
        st.markdown(f"### 3. Past Performance — Score {score_from_checks(past_checks)}/100")
        card("Past Performance Checklist", render_checks(past_checks))
        p1, p2 = st.columns(2)
        with p1:
            yoy_val = to_float(m.get('pat_yoy')) or 0
            qoq_val = to_float(m.get('pat_qoq')) or 0
            fig = go.Figure(data=[go.Bar(x=['PAT YoY', 'PAT QoQ'],
                                          y=[yoy_val, qoq_val],
                                          marker_color=[GREEN, BLUE], 
                                          text=[f"{yoy_val}%", f"{qoq_val}%"], 
                                          textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Earnings Momentum")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        with p2:
            roe_val = to_float(m.get('roe')) or 0
            roa_val = to_float(m.get('roce_roa')) or 0
            fig = go.Figure(data=[go.Bar(x=['ROE', 'ROA/ROCE'],
                                          y=[roe_val, roa_val],
                                          marker_color=[GOLD, '#a855f7'], 
                                          text=[f"{roe_val}%", f"{roa_val}%"], 
                                          textposition='auto')])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Profitability Returns")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        card("Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    # ---------- 4. FINANCIAL HEALTH ----------
    elif section == "4. Financial Health":
        st.markdown(f"### 4. Financial Health — Score {score_from_checks(health_checks)}/100")
        card("Financial Health Checklist", render_checks(health_checks))
        tm = balance_sheet_treemap(m)
        if tm:
            st.plotly_chart(tm, use_container_width=True, config={'displayModeBar': False})
        else:
            st.caption("Balance sheet breakdown unavailable for this ticker.")
        card("Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    # ---------- 5. DIVIDEND ----------
    elif section == "5. Dividend":
        st.markdown(f"### 5. Dividend — Score {score_from_checks(div_checks)}/100")
        card("Dividend Checklist", render_checks(div_checks))
        card("Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

    # ---------- 6. MANAGEMENT ----------
    elif section == "6. Management":
        st.markdown("### 6. Management")
        officers = m.get('company_officers') or []
        if officers:
            rows = []
            for o in officers[:8]:
                rows.append({
                    "Name": o.get('name', 'N/A'),
                    "Title": o.get('title', 'N/A'),
                    "Age": o.get('age', 'N/A'),
                    "Total Pay": f"{m.get('currency','INR')} {o.get('totalPay'):,}" if o.get('totalPay') else "N/A"
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            card("Leadership Team", "<div class='swf-check-na'>&#8213; Detailed management/board data is not available via this data source.</div>")
        card("Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

    # ---------- 7. OWNERSHIP ----------
    elif section == "7. Ownership":
        st.markdown("### 7. Ownership")
        if m.get('shareholding'):
            st.plotly_chart(ownership_bar(m['shareholding']), use_container_width=True, config={'displayModeBar': False})
        card("Ownership Structure & Insider Sentiment", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    # ---------- 8. OTHER INFORMATION ----------
    elif section == "8. Other Information":
        st.markdown("### 8. Other Information")
        oc1, oc2 = st.columns(2)
        with oc1:
            card("Key Information",
                 f"<div class='swf-sub' style='margin-left:0;'>Exchange: {m.get('exchange', 'N/A')}<br>"
                 f"Ticker: {ticker}<br>Employees: {m.get('employees') or 'N/A'}<br>"
                 f"Website: <a href='{m.get('website')}' style='color:{BLUE};'>{m.get('website') or 'N/A'}</a></div>")
        with oc2:
            headlines = str(m.get('recent_news', '')).split(" | ") if m.get('recent_news') else []
            news_html = "".join([f"<div class='swf-sub' style='margin-left:0; padding:4px 0; border-bottom:1px solid {BORDER};'>{h}</div>" for h in headlines]) or "<div class='swf-check-na'>No recent headlines.</div>"
            card("Recent News & Updates", news_html)
            
        styled_verdict = narrative_for(7)
        styled_verdict = re.sub(r'(?i)\bSTRONG BUY\b', f'<span style="color:{GREEN}; font-weight:bold;">STRONG BUY</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)(?<!STRONG )\bBUY\b', f'<span style="color:{GREEN}; font-weight:bold;">BUY</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)\bOBSERVE\b', f'<span style="color:{ORANGE}; font-weight:bold;">OBSERVE</span>', styled_verdict)
        styled_verdict = re.sub(r'(?i)\bSELL\b', f'<span style="color:{RED}; font-weight:bold;">SELL</span>', styled_verdict)
        card("Summary Verdict & Key Risks", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{styled_verdict}</p>")

    st.markdown("---")

    # ---------- PDF EXPORT ----------
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, data['ai_text'], ticker, current_rating)
    pdf_buffer.seek(0)

    st.download_button(
        label="📥 Download Official PDF Dossier",
        data=pdf_buffer,
        file_name=f"{ticker}_ASW_Stock_Ideas_Dossier.pdf",
        mime="application/pdf",
        type="primary"
    )
