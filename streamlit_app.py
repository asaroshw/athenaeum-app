import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import re
import io
import requests
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
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
MUTED = "#8B949E"
BLUE = "#38BDF8"

# ============================================================
# 2. THEME (Dark UI)
# ============================================================
st.markdown(f"""
<style>
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
    .swf-topbar b {{ color:{GOLD}; font-size:1.1em; }}
    .swf-card {{
        background-color: {CARD_BG}; border: 1px solid {BORDER}; border-radius: 10px;
        padding: 18px 20px; margin-bottom: 16px;
    }}
    .swf-badge {{
        background:{GOLD}; color:#111; padding:4px 12px; border-radius:6px; font-weight:700; font-size:0.85em;
    }}
    .swf-check-pass {{ color: {GREEN}; }}
    .swf-check-fail {{ color: {RED}; }}
    .swf-check-na {{ color: {MUTED}; }}
    .swf-sub {{ color:{MUTED}; font-size:0.85em; margin-left:22px; }}
    .swf-h {{ color:{BLUE}; font-weight:700; font-size:1.05em; margin-bottom:6px; }}
    .swf-company-mini {{ padding: 6px 4px 14px 4px; border-bottom: 1px solid {BORDER}; margin-bottom: 8px; }}
    .swf-avatar {{
        width:40px; height:40px; border-radius:8px; background:#fff; color:#111; font-weight:800;
        display:flex; align-items:center; justify-content:center; font-size:1.2em;
    }}
</style>
""", unsafe_allow_html=True)

# ============================================================
# 3. HELPERS
# ============================================================
def to_float(val):
    if val is None or val == "N/A":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except Exception:
        return None

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

def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit():
        return stock_str + '.BO'
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}"
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Accept': 'application/json'}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if 'quotes' in data:
                for q in data['quotes']:
                    sym = q.get('symbol', '').upper()
                    if sym.endswith('.NS') or sym.endswith('.BO'):
                        return sym
    except Exception:
        pass
    upper_input = stock_str.upper().replace(" ", "")
    if not upper_input.endswith(('.NS', '.BO')):
        return upper_input + '.NS'
    return upper_input

def g(d, key, default="N/A"):
    if not isinstance(d, dict):
        return default
    val = d.get(key, default)
    return default if val is None else val

def fmt_num(val, prefix=""):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return f"{prefix}{val:,.0f}" if abs(val) >= 1 else f"{prefix}{val}"
        except Exception:
            return f"{prefix}{val}"
    return f"{prefix}{val}" if val not in (None, "N/A") else "N/A"

def compute_fair_value(price, pe, growth_pct):
    if price is None or pe is None or pe <= 0:
        return None
    eps = price / pe
    if growth_pct and growth_pct > 0:
        fair_pe = min(max(growth_pct, 8), 40)
    else:
        fair_pe = 15
    return round(eps * fair_pe, 2)

def fetch_google_news(query_term):
    """Fallback news puller using Google News RSS feed."""
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

# ============================================================
# 4. DATA FETCH
# ============================================================
@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty:
        raise ValueError(f"Could not find '{raw_input}' on NSE or BSE.")

    info = stock.info
    price = info.get("currentPrice", round(hist['Close'].iloc[-1], 2))

    rsi_val = calculate_rsi(hist, 14)
    peg_ratio = info.get("pegRatio", "N/A")
    roe = info.get("returnOnEquity", "N/A")
    if roe != "N/A" and roe is not None:
        roe = round(roe * 100, 2)
    roa = info.get("returnOnAssets", "N/A")
    if roa != "N/A" and roa is not None:
        roa = round(roa * 100, 2)

    dividend_yield = info.get("dividendYield", "N/A")
    if dividend_yield != "N/A" and dividend_yield is not None:
        dividend_yield = round(dividend_yield * 100, 2)

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

    insider_h = (info.get("heldPercentInsiders") or 0) * 100
    inst_h = (info.get("heldPercentInstitutions") or 0) * 100
    public_h = max(0, 100 - (insider_h + inst_h))

    shareholding = {
        "Promoters / Insiders": round(insider_h, 2),
        "Institutions (FII/DII)": round(inst_h, 2),
        "General Public": round(public_h, 2)
    }

    metrics = {
        "name": info.get("longName", resolved_ticker),
        "price": price,
        "pe_ratio": info.get("trailingPE", "N/A"),
        "peg_ratio": peg_ratio,
        "roe": f"{roe}%" if roe != "N/A" else "N/A",
        "roce_roa": f"{roa}%" if roa != "N/A" else "N/A",
        "dividend_yield": f"{dividend_yield}%" if dividend_yield != "N/A" else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq != "N/A" else "N/A",
        "pat_yoy": f"{pat_yoy}%" if pat_yoy != "N/A" else "N/A",
        "rsi": rsi_val,
        "debt_to_equity": info.get("debtToEquity", "N/A"),
        "net_margin": info.get("profitMargins", "N/A"),
        "market_cap": info.get("marketCap", "N/A"),
        "industry": info.get("industry", "N/A"),
        "sector": info.get("sector", "N/A"),
        "shares_outstanding": info.get("sharesOutstanding", "N/A"),
        "shareholding": shareholding,
        "recent_news": "",
        "working_ticker": resolved_ticker,
        "exchange": info.get("exchange", "N/A"),
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
    }

    # News fetching with Google News RSS fallback
    try:
        news_items = stock.news
        if news_items:
            headlines = [n.get('title', '') for n in news_items[:4]]
            metrics["recent_news"] = " | ".join(headlines)
    except Exception:
        pass

    if not metrics["recent_news"]:
        google_news = fetch_google_news(raw_input)
        metrics["recent_news"] = google_news if google_news else "No recent headlines available."

    if metrics["debt_to_equity"] != "N/A":
        try:
            metrics["debt_to_equity"] = round(metrics["debt_to_equity"] / 100, 2)
        except Exception:
            pass
    if metrics["net_margin"] != "N/A" and metrics["net_margin"] is not None:
        try:
            metrics["net_margin"] = f"{round(metrics['net_margin'] * 100, 2)}%"
        except Exception:
            pass

    # Balance sheet breakdown
    try:
        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            def g_bs(row):
                try:
                    return float(bs.loc[row, col])
                except Exception:
                    return None
            metrics["total_assets"] = g_bs('Total Assets')
            metrics["cash_bs"] = g_bs('Cash And Cash Equivalents')
            metrics["receivables"] = g_bs('Receivables')
            metrics["inventory"] = g_bs('Inventory')
            metrics["current_liab"] = g_bs('Current Liabilities')
            metrics["total_debt_bs"] = g_bs('Total Debt')
            metrics["total_equity"] = g_bs('Common Stock Equity') or g_bs('Stockholders Equity')
    except Exception:
        pass

    hist_df = hist.reset_index()[["Date", "Close"]]
    metrics["history"] = hist_df

    return metrics

# ============================================================
# 5. CRITERIA CHECKS
# ============================================================
def valuation_checks(m):
    price = g(m, 'price', None); fv = m.get('fair_value')
    currency = g(m, 'currency', '')
    pe = to_float(g(m, 'pe_ratio', None)); peg = to_float(g(m, 'peg_ratio', None))
    tgt = m.get('target_mean_price')
    checks = []
    if fv and price is not None:
        checks.append(("Below Fair Value", price < fv,
                        f"Price {currency} {price} vs an estimated fair value of {currency} {fv}"))
        checks.append(("Significantly Undervalued (20%+ below)", price < fv * 0.8,
                        "Price is more than 20% below the fair value estimate"))
    else:
        checks.append(("Below Fair Value", None, "Insufficient data to estimate fair value"))
        checks.append(("Significantly Undervalued", None, "Insufficient data"))
    checks.append(("Reasonable P/E (<25x)", None if pe is None else pe < 25,
                    f"Trailing P/E of {pe}x" if pe is not None else "P/E not available"))
    checks.append(("Attractive PEG (<1.5)", None if peg is None else peg < 1.5,
                    f"PEG ratio of {peg}" if peg is not None else "PEG not available"))
    if tgt and price is not None:
        checks.append(("Trading Below Analyst Target", price < tgt, f"Average analyst target {currency} {tgt}"))
    else:
        checks.append(("Analyst Target Coverage", None, "Insufficient analyst coverage"))
    return checks

def past_performance_checks(m):
    yoy = to_float(g(m, 'pat_yoy', None)); qoq = to_float(g(m, 'pat_qoq', None))
    roe = to_float(g(m, 'roe', None)); margin = to_float(g(m, 'net_margin', None))
    return [
        ("Positive Earnings Growth (YoY)", None if yoy is None else yoy > 0, f"PAT YoY growth of {g(m,'pat_yoy')}"),
        ("Accelerating Growth (recent quarter vs YoY)", None if (yoy is None or qoq is None) else qoq > yoy,
         "Comparing most recent quarter growth to the yearly figure"),
        ("Strong Return on Equity (>15%)", None if roe is None else roe > 15, f"ROE of {g(m,'roe')}"),
        ("Healthy Net Margin (>10%)", None if margin is None else margin > 10, f"Net margin of {g(m,'net_margin')}"),
    ]

def financial_health_checks(m):
    de = to_float(g(m, 'debt_to_equity', None))
    cash = m.get('total_cash'); debt = m.get('total_debt')
    cr = m.get('current_ratio')
    currency = g(m, 'currency', '')
    checks = [("Low Leverage (D/E < 1.0)", None if de is None else de < 1.0,
               f"Debt-to-equity of {de}" if de is not None else "Not available")]
    if cash is not None and debt is not None:
        checks.append(("Cash Exceeds Total Debt", cash > debt,
                        f"Cash {currency} {cash:,.0f} vs Debt {currency} {debt:,.0f}"))
    else:
        checks.append(("Cash Exceeds Total Debt", None, "Insufficient data"))
    if cr is not None:
        checks.append(("Short-Term Liquidity (Current Ratio > 1)", cr > 1, f"Current ratio of {round(cr,2)}"))
    else:
        checks.append(("Short-Term Liquidity", None, "Insufficient data"))
    return checks

def dividend_checks(m):
    dy = to_float(g(m, 'dividend_yield', None))
    payout = m.get('payout_ratio')
    checks = [("Pays a Notable Dividend (>1.5%)", None if dy is None else dy > 1.5,
               f"Dividend yield of {g(m,'dividend_yield')}")]
    if payout is not None:
        checks.append(("Sustainable Payout (<75%)", payout < 0.75, f"Payout ratio of {round(payout*100,1)}%"))
    else:
        checks.append(("Sustainable Payout", None, "Insufficient data"))
    return checks

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    if not vals:
        return 0
    return round(100 * sum(vals) / len(vals))

def render_checks(checks):
    html = ""
    for label, status, desc in checks:
        if status is True:
            icon, cls = "&#9989;", "swf-check-pass"
        elif status is False:
            icon, cls = "&#10060;", "swf-check-fail"
        else:
            icon, cls = "&#8213;", "swf-check-na"
        html += (f'<div style="padding:5px 0;"><span class="{cls}">{icon} <b>{label}</b></span>'
                 f'<div class="swf-sub">{desc}</div></div>')
    return html

def card(title, body_html):
    st.markdown(f'<div class="swf-card"><div class="swf-h">{title}</div>{body_html}</div>', unsafe_allow_html=True)

# ============================================================
# 6. CHART BUILDERS
# ============================================================
def analysis_radar_chart(scores):
    categories = list(scores.keys())
    values = list(scores.values())
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]], theta=categories + [categories[0]],
        fill='toself', fillcolor='rgba(234,179,8,0.35)', line=dict(color=GOLD, width=2)
    ))
    fig.update_layout(
        polar=dict(bgcolor=BG, radialaxis=dict(visible=False, range=[0, 100]),
                   angularaxis=dict(color=MUTED, gridcolor=BORDER)),
        showlegend=False, paper_bgcolor=BG, margin=dict(t=10, b=10, l=30, r=30), height=230
    )
    return fig

def price_history_chart(hist_df, fair_value, currency):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_df['Date'], y=hist_df['Close'], mode='lines',
                              line=dict(color=BLUE, width=1.5), fill='tozeroy',
                              fillcolor='rgba(56,189,248,0.08)', name='Price'))
    if fair_value:
        fig.add_hline(y=fair_value, line_dash='dot', line_color=GOLD,
                       annotation_text=f'Fair Value {currency} {fair_value}', annotation_font_color=GOLD)
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260,
                       margin=dict(t=20, b=20, l=10, r=10),
                       xaxis=dict(showgrid=False, title=None), yaxis=dict(showgrid=False, title=currency))
    return fig

def fair_value_bar(price, fv, currency):
    fig = go.Figure()
    fig.add_trace(go.Bar(y=['Current Price'], x=[price], orientation='h', marker_color=BLUE, text=f"{currency} {price}", textposition='inside'))
    fig.add_trace(go.Bar(y=['Fair Value'], x=[fv], orientation='h', marker_color=GREEN, text=f"{currency} {fv}", textposition='inside'))
    diff_pct = round(((price - fv) / fv) * 100, 1) if fv else None
    fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=170,
                       margin=dict(t=10, b=10, l=10, r=10), showlegend=False,
                       xaxis=dict(showgrid=False, title=currency))
    return fig, diff_pct

def ownership_bar(shareholding):
    fig = go.Figure()
    colors_list = [BLUE, '#a855f7', GOLD]
    for (k, v), c in zip(shareholding.items(), colors_list):
        fig.add_trace(go.Bar(y=['Ownership'], x=[v], name=f"{k} ({v}%)", orientation='h',
                              marker_color=c, text=f"{v}%", textposition='inside'))
    fig.update_layout(barmode='stack', template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=150,
                       margin=dict(t=10, b=40, l=10, r=10), xaxis=dict(visible=False), yaxis=dict(visible=False),
                       legend=dict(orientation='h', y=-0.3))
    return fig

def balance_sheet_treemap(m):
    assets_labels, assets_vals, assets_colors = [], [], []
    if m.get('cash_bs'):
        assets_labels.append('Cash & Equivalents'); assets_vals.append(m['cash_bs']); assets_colors.append('#22c55e')
    if m.get('receivables'):
        assets_labels.append('Receivables'); assets_vals.append(m['receivables']); assets_colors.append('#4ade80')
    if m.get('inventory'):
        assets_labels.append('Inventory'); assets_vals.append(m['inventory']); assets_colors.append('#86efac')
    if m.get('total_assets') and assets_vals:
        other = m['total_assets'] - sum(assets_vals)
        if other > 0:
            assets_labels.append('Other Assets'); assets_vals.append(other); assets_colors.append('#bbf7d0')

    liab_labels, liab_vals, liab_colors = [], [], []
    if m.get('current_liab'):
        liab_labels.append('Current Liabilities'); liab_vals.append(m['current_liab']); liab_colors.append('#4ade80')
    if m.get('total_debt_bs'):
        liab_labels.append('Debt'); liab_vals.append(m['total_debt_bs']); liab_colors.append('#f87171')
    if m.get('total_equity'):
        liab_labels.append('Equity'); liab_vals.append(m['total_equity']); liab_colors.append('#22c55e')

    if not assets_vals or not liab_vals:
        return None

    fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'domain'}, {'type': 'domain'}]],
                         subplot_titles=("Assets", "Liabilities + Equity"))
    fig.add_trace(go.Treemap(labels=assets_labels, parents=[""] * len(assets_labels), values=assets_vals,
                              marker_colors=assets_colors, textinfo="label+value"), row=1, col=1)
    fig.add_trace(go.Treemap(labels=liab_labels, parents=[""] * len(liab_labels), values=liab_vals,
                              marker_colors=liab_colors, textinfo="label+value"), row=1, col=2)
    fig.update_layout(paper_bgcolor=BG, margin=dict(t=40, b=10, l=10, r=10), height=320,
                       font_color="#E6E6E6")
    return fig

# ============================================================
# 7. AI NARRATIVE & RATING LOGIC
# ============================================================
def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)

    system_instruction = """
    You are an elite institutional equity research director building a comprehensive, exhaustive, multi-page stock intelligence dossier.
    Do not summarize; provide deep, granular breakdowns for every module.
    Do not use markdown hash symbols or asterisks in pre-ambles.

    MANDATORY PRE-AMBLE VARIABLES (Exact format on first 3 lines):
    DYNAMIC_SECTOR: [Insert Industry]
    DYNAMIC_RATING: [Choose strictly ONE: STRONG BUY, BUY, OBSERVE, DON'T BUY, SELL]
    DYNAMIC_DURATION: [1-3 Months, 3-5 Years, or N/A]

    Structure your deep-dive analysis using EXACTLY these 8 numbered headers:
    1. VALUATION & FAIR VALUE
    2. FUTURE GROWTH & OUTLOOK
    3. PAST PERFORMANCE & EARNINGS QUALITY
    4. FINANCIAL HEALTH & BALANCE SHEET
    5. DIVIDEND & CAPITAL ALLOCATION
    6. MANAGEMENT & COMPENSATION
    7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
    8. SUMMARY VERDICT & KEY RISKS

    RATING & VERDICT GUIDELINES:
    - Distinguish between BUY and STRONG BUY:
      * STRONG BUY: Conviction growth with high safety margin, stellar ROCE/ROE, low debt.
      * BUY: Strong fundamentals but moderate valuation margin.
      * OBSERVE: Use this instead of Hold. We do not know if the user already holds shares. Focus on key watch triggers/entry catalysts.
      * DON'T BUY / SELL: Clear warning signs, overvaluation, or high balance sheet risks.
    - Under section '8. SUMMARY VERDICT & KEY RISKS', you MUST explicitly begin with:
      Line 1: Rating: [STRONG BUY / BUY / OBSERVE / DON'T BUY / SELL]
      Line 2: Recommended Entry Level: [Specific price or range, e.g., ₹X - ₹Y]
      Line 3: Target Price & Horizon: [Price Target & time horizon, e.g., ₹Z (12-18 Months)]
      Line 4: Suggested Stop Loss: [Risk boundary, e.g., ₹SL]
      Followed by the detailed qualitative rationale and key risk factors.
    """

    user_prompt = f"""
    Target Company Data:
    Company Name: {metrics['name']} ({ticker})
    Current Market Price: {metrics['currency']} {metrics['price']}
    Market Cap: {metrics['currency']} {metrics['market_cap']}
    P/E Ratio: {metrics['pe_ratio']} | PEG Ratio: {metrics['peg_ratio']}
    ROE: {metrics['roe']} | ROA/ROCE Proxy: {metrics['roce_roa']}
    Dividend Yield: {metrics['dividend_yield']}
    14-Day RSI: {metrics['rsi']}
    PAT Growth YoY: {metrics['pat_yoy']} | QoQ: {metrics['pat_qoq']}
    Debt to Equity: {metrics['debt_to_equity']}
    Net Margin: {metrics['net_margin']}
    Industry: {metrics['industry']} | Sector: {metrics['sector']}
    Recent Headlines / Catalysts: {metrics['recent_news']}
    """

    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=user_prompt,
        config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.2)
    )
    return response.text

def build_pdf_report(pdf_buffer, metrics, ai_text, ticker):
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)

    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=colors.HexColor('#1A365D'))
    subtitle_style = ParagraphStyle('DocSub', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#718096'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=colors.HexColor('#2B6CB0'), spaceBefore=10, spaceAfter=4)
    body_style = ParagraphStyle('BodyTextCustom', fontName='Helvetica', fontSize=8, leading=11.5, textColor=colors.HexColor('#2D3748'))
    table_text = ParagraphStyle('TableText', fontName='Helvetica', fontSize=7, leading=9, textColor=colors.white)
    table_val = ParagraphStyle('TableVal', fontName='Helvetica-Bold', fontSize=7, leading=9, textColor=colors.white)

    rating_colors = {
        "STRONG BUY": "#15803D",
        "BUY": "#172554",
        "OBSERVE": "#D97706",
        "DON'T BUY": "#DC2626",
        "SELL": "#991B1B"
    }

    sector_val, duration_val, rating_val = "Growth / Cyclical", "N/A", "EVALUATED"
    clean_lines = []

    for line in ai_text.split('\n'):
        line_str = line.strip()
        if line_str.startswith("DYNAMIC_SECTOR:"):
            sector_val = line_str.replace("DYNAMIC_SECTOR:", "").strip()
        elif line_str.startswith("DYNAMIC_DURATION:"):
            duration_val = line_str.replace("DYNAMIC_DURATION:", "").strip()
        elif line_str.startswith("DYNAMIC_RATING:"):
            rating_val = line_str.replace("DYNAMIC_RATING:", "").strip()
        elif line_str:
            clean_lines.append(line_str)

    story = [
        Paragraph("ASW Stock Ideas — Research Division", title_style),
        Paragraph(f"Comprehensive Terminal Dossier — {metrics['name']} ({ticker})", subtitle_style),
        Spacer(1, 6)
    ]

    current_rating = rating_val.upper().strip()
    target_hex = rating_colors.get(current_rating, "#FFFFFF")
    grid_rating_display = f"<font color='{target_hex}'><b>{current_rating}</b></font>"

    table_data = [
        [Paragraph("<b>Company:</b>", table_text), Paragraph(str(metrics['name']), table_val), Paragraph("<b>Sector:</b>", table_text), Paragraph(sector_val, table_val)],
        [Paragraph("<b>Price / Cap:</b>", table_text), Paragraph(f"{metrics['currency']} {metrics['price']}", table_val), Paragraph("<b>Horizon:</b>", table_text), Paragraph(duration_val, table_val)],
        [Paragraph("<b>P/E | PEG:</b>", table_text), Paragraph(f"{metrics['pe_ratio']}x | {metrics['peg_ratio']}", table_val), Paragraph("<b>ROE | Div:</b>", table_text), Paragraph(f"{metrics['roe']} | {metrics['dividend_yield']}", table_val)],
        [Paragraph("<b>PAT YoY Growth:</b>", table_text), Paragraph(str(metrics['pat_yoy']), table_val), Paragraph("<b>Terminal Rating:</b>", table_text), Paragraph(grid_rating_display, table_val)]
    ]

    t = Table(table_data, colWidths=[105, 165, 100, 170])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#2B6CB0')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#4299E1')),
    ]))
    story.append(t)
    story.append(Spacer(1, 8))

    for line in clean_lines:
        if any(h in line for h in ["1. VALUATION", "2. FUTURE GROWTH", "3. PAST PERFORMANCE", "4. FINANCIAL HEALTH", "5. DIVIDEND", "6. MANAGEMENT", "7. OWNERSHIP", "8. SUMMARY VERDICT"]):
            story.append(Paragraph(line, h1_style))
        else:
            processed_line = line
            for r_text in sorted(rating_colors.keys(), key=len, reverse=True):
                if r_text in processed_line.upper():
                    pattern = r'(?i)(?<![a-zA-Z])' + re.escape(r_text) + r'(?![a-zA-Z])'
                    processed_line = re.sub(pattern, f'<font color="{rating_colors[r_text]}"><b>{r_text}</b></font>', processed_line)
            story.append(Paragraph(processed_line, body_style))
            story.append(Spacer(1, 3))

    doc.build(story)

# ============================================================
# 8. APP STATE & NAVIGATION
# ============================================================
if 'report_data' not in st.session_state:
    st.session_state.report_data = None
if 'active_section' not in st.session_state:
    st.session_state.active_section = "Company Overview"

SECTIONS = ["Company Overview", "1. Valuation", "2. Future Growth", "3. Past Performance",
            "4. Financial Health", "5. Dividend", "6. Management", "7. Ownership", "8. Other Information"]

# ============================================================
# 9. SIDEBAR
# ============================================================
with st.sidebar:
    if st.session_state.report_data:
        m0 = st.session_state.report_data['metrics']
        t0 = st.session_state.report_data['ticker']
        name0 = g(m0, 'name', t0)
        mcap_val = g(m0, 'market_cap')
        mcap_str = fmt_num(mcap_val, prefix=g(m0, 'currency', '') + ' ') if mcap_val != "N/A" else ""

        st.markdown(f"""
        <div class="swf-company-mini">
            <div style="display:flex; align-items:center; gap:10px;">
                <div class="swf-avatar">{name0[0] if name0 else '?'}</div>
                <div>
                    <div style="font-weight:700;">{name0}</div>
                    <div style="color:{MUTED}; font-size:0.8em;">{t0} Stock Report</div>
                </div>
            </div>
            {"<div style='color:" + MUTED + "; font-size:0.85em; margin-top:6px;'>Market Cap: " + mcap_str + "</div>" if mcap_str else ""}
        </div>
        """, unsafe_allow_html=True)
        st.session_state.active_section = st.radio(
            "Navigate", SECTIONS, index=SECTIONS.index(st.session_state.active_section),
            label_visibility="collapsed"
        )
    else:
        st.markdown(f'<div style="color:{MUTED}; padding:10px;">Generate a report to unlock section navigation.</div>', unsafe_allow_html=True)

# ============================================================
# 10. TOP BAR (CLEANED)
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

                pe_num = to_float(metrics['pe_ratio'])
                growth_num = to_float(metrics['pat_yoy'])
                metrics['fair_value'] = compute_fair_value(metrics['price'], pe_num, growth_num)

                ai_text = generate_comprehensive_report(metrics, final_ticker)
                raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', ai_text)
                sections_list = [s.strip() for s in re.split(r'\n(?=[0-9]\.\s[A-Z&]+)', raw_ai_text) if s.strip()]

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
# 11. MAIN CONTENT
# ============================================================
if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    ticker = data['ticker']
    narrative = data.get('narrative_sections', [])

    def narrative_for(idx):
        return narrative[idx] if idx < len(narrative) else "Detailed qualitative breakdown unavailable for this section."

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

    # ---------- HEADER ----------
    currency = g(m, 'currency', '')
    mcap_val = g(m, 'market_cap')
    mcap_display = fmt_num(mcap_val, prefix=currency + ' ') if mcap_val != "N/A" else ""

    hcol1, hcol2 = st.columns([2.2, 1])
    with hcol1:
        sector_str = f"Stocks / {g(m, 'industry')}" if g(m, 'industry') != "N/A" else "Stock Overview"
        st.markdown(f"""
        <div class="swf-card">
            <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                <div>
                    <div style="color:{MUTED}; font-size:0.85em;">{sector_str}</div>
                    <div style="font-size:1.4em; font-weight:800;">{g(m,'name',ticker)}</div>
                    <div style="color:{MUTED}; font-size:0.9em;">{ticker} Stock Report {"| Market Cap: " + mcap_display if mcap_display else ""}</div>
                    <span class="swf-badge" style="margin-top:8px; display:inline-block;">📊 Live Analysis</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:1.6em; font-weight:800;">{currency} {g(m,'price')}</div>
                    <div style="color:{MUTED}; font-size:0.85em;">Current Price</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)
        hist_df = m.get('history')
        if hist_df is not None and len(hist_df) > 0:
            st.plotly_chart(price_history_chart(hist_df, m.get('fair_value'), currency), use_container_width=True, config={'displayModeBar': False})
    with hcol2:
        st.markdown('<div class="swf-card"><div class="swf-h">Analysis</div>', unsafe_allow_html=True)
        st.plotly_chart(analysis_radar_chart(scores), use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")

    section = st.session_state.active_section

    # ---------- COMPANY OVERVIEW ----------
    if section == "Company Overview":
        pe_disp = g(m, 'pe_ratio')
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Price", f"{currency} {g(m,'price')}")
        c1.metric("P/E Ratio", f"{pe_disp}x" if pe_disp != "N/A" else "N/A")
        c2.metric("PEG Ratio", f"{g(m,'peg_ratio')}")
        c2.metric("14-Day RSI", f"{g(m,'rsi')}")
        c3.metric("ROE", f"{g(m,'roe')}")
        c3.metric("Dividend Yield", f"{g(m,'dividend_yield')}")
        c4.metric("PAT Growth (YoY)", f"{g(m,'pat_yoy')}")
        c4.metric("PAT Growth (QoQ)", f"{g(m,'pat_qoq')}")

        summary = m.get('business_summary')
        meta_items = []
        if m.get('employees'):
            meta_items.append(f"Employees: {m['employees']:,}")
        if g(m, 'sector') != "N/A":
            meta_items.append(f"Sector: {g(m, 'sector')}")
        if g(m, 'industry') != "N/A":
            meta_items.append(f"Industry: {g(m, 'industry')}")

        meta_html = f"<div class='swf-sub' style='margin-left:0; margin-top:8px;'>{' | '.join(meta_items)}</div>" if meta_items else ""

        if summary:
            st.markdown("### About the Company")
            card("Overview", f"<p style='color:#c9d1d9; font-size:0.9em; line-height:1.5em;'>{summary}</p>{meta_html}")
        elif meta_html:
            st.markdown("### About the Company")
            card("Overview", meta_html)

    # ---------- 1. VALUATION ----------
    elif section == "1. Valuation":
        st.markdown(f"### 1. Valuation — Score {score_from_checks(val_checks)}/100")
        card("Valuation Checklist", render_checks(val_checks))
        if m.get('fair_value') and g(m, 'price', None) is not None:
            fig, diff_pct = fair_value_bar(g(m, 'price'), m['fair_value'], currency)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            status_word = "overvalued" if diff_pct and diff_pct > 0 else "undervalued"
            st.caption(f"Price is approximately {abs(diff_pct)}% {status_word} vs the simplified fair value estimate.")
        card("Narrative — Valuation & Fair Value", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(0)}</p>")

    # ---------- 2. FUTURE GROWTH ----------
    elif section == "2. Future Growth":
        st.markdown("### 2. Future Growth & Outlook")
        if m.get('target_mean_price') and m.get('num_analysts'):
            card("Analyst Coverage",
                 f"<div class='swf-sub' style='margin-left:0;'>Average 12-month analyst target: "
                 f"<b>{currency} {m['target_mean_price']}</b> based on {m['num_analysts']} analyst(s).</div>")
        card("Narrative — Future Growth & Outlook", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(1)}</p>")

    # ---------- 3. PAST PERFORMANCE ----------
    elif section == "3. Past Performance":
        st.markdown(f"### 3. Past Performance — Score {score_from_checks(past_checks)}/100")
        card("Past Performance Checklist", render_checks(past_checks))

        yoy_val = to_float(g(m, 'pat_yoy', None)) or 0
        qoq_val = to_float(g(m, 'pat_qoq', None)) or 0
        roe_val = to_float(g(m, 'roe', None)) or 0
        roa_val = to_float(g(m, 'roce_roa', None)) or 0

        p1, p2 = st.columns(2)
        with p1:
            fig = go.Figure(data=[go.Bar(
                x=['PAT YoY', 'PAT QoQ'],
                y=[yoy_val, qoq_val],
                marker_color=[GREEN, BLUE],
                text=[f"{yoy_val}%", f"{qoq_val}%"],
                textposition='auto'
            )])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Earnings Momentum")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        with p2:
            fig = go.Figure(data=[go.Bar(
                x=['ROE', 'ROA/ROCE'],
                y=[roe_val, roa_val],
                marker_color=[GOLD, '#a855f7'],
                text=[f"{roe_val}%", f"{roa_val}%"],
                textposition='auto'
            )])
            fig.update_layout(template='plotly_dark', paper_bgcolor=BG, plot_bgcolor=BG, height=260, margin=dict(t=20, b=10, l=10, r=10), title="Profitability Returns")
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

        card("Narrative — Past Performance & Earnings Quality", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(2)}</p>")

    # ---------- 4. FINANCIAL HEALTH ----------
    elif section == "4. Financial Health":
        st.markdown(f"### 4. Financial Health — Score {score_from_checks(health_checks)}/100")
        card("Financial Health Checklist", render_checks(health_checks))
        tm = balance_sheet_treemap(m)
        if tm:
            st.plotly_chart(tm, use_container_width=True, config={'displayModeBar': False})
        card("Narrative — Financial Health & Balance Sheet", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(3)}</p>")

    # ---------- 5. DIVIDEND ----------
    elif section == "5. Dividend":
        st.markdown(f"### 5. Dividend — Score {score_from_checks(div_checks)}/100")
        card("Dividend Checklist", render_checks(div_checks))
        card("Narrative — Dividend & Capital Allocation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(4)}</p>")

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
                    "Total Pay": fmt_num(o.get('totalPay'), prefix=currency + ' ') if o.get('totalPay') else "N/A"
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        card("Narrative — Management & Compensation", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(5)}</p>")

    # ---------- 7. OWNERSHIP ----------
    elif section == "7. Ownership":
        st.markdown("### 7. Ownership")
        shareholding = m.get('shareholding') or {}
        if shareholding:
            st.plotly_chart(ownership_bar(shareholding), use_container_width=True, config={'displayModeBar': False})
        card("Narrative — Ownership Structure & Insider Sentiment", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(6)}</p>")

    # ---------- 8. OTHER INFORMATION ----------
    elif section == "8. Other Information":
        st.markdown("### 8. Other Information")
        oc1, oc2 = st.columns(2)
        with oc1:
            info_lines = []
            if g(m, 'exchange') != "N/A": info_lines.append(f"Exchange: {g(m,'exchange')}")
            info_lines.append(f"Ticker: {ticker}")
            if m.get('employees'): info_lines.append(f"Employees: {m['employees']:,}")
            if m.get('website'): info_lines.append(f"Website: <a href='{m['website']}' style='color:{BLUE};'>{m['website']}</a>")

            card("Key Information", f"<div class='swf-sub' style='margin-left:0;'>{'<br>'.join(info_lines)}</div>")
        with oc2:
            news_val = g(m, 'recent_news', '')
            headlines = news_val.split(" | ") if news_val and news_val != "N/A" else []
            news_html = "".join([f"<div class='swf-sub' style='margin-left:0; padding:4px 0; border-bottom:1px solid {BORDER};'>{h}</div>" for h in headlines]) if headlines else "<div class='swf-check-na'>No recent news available.</div>"
            card("Recent News & Updates", news_html)
        card("Narrative — Summary Verdict & Key Risks", f"<p style='color:#c9d1d9; font-size:0.85em; white-space:pre-wrap;'>{narrative_for(7)}</p>")

    st.markdown("---")

    # ---------- PDF EXPORT ----------
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, data['ai_text'], ticker)
    pdf_buffer.seek(0)

    st.download_button(
        label="📥 Download Official PDF Dossier",
        data=pdf_buffer,
        file_name=f"{ticker}_ASW_Stock_Ideas_Dossier.pdf",
        mime="application/pdf",
        type="primary"
    )
