import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import logging
import re
import io
import requests
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from google import genai
from google.genai import types

# 1. Setup & Configuration
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
st.set_page_config(page_title="Gatekeeper - Stock Ideas & Screener", layout="wide")

# API Keys from Secrets
GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")
ANGEL_KEY = st.secrets.get("ANGEL_API_KEY", "WjBiiHX1")

# 2. HELPER CALCULATIONS

def calculate_rsi(df, window=14):
    """Calculates 14-day Relative Strength Index (RSI)."""
    if len(df) < window:
        return "N/A"
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
    
    # Avoid division by zero
    loss = loss.replace(0, 1e-10)
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi.iloc[-1], 2)

# 3. CORE LOGIC FUNCTIONS

def resolve_name_to_ticker(stock_input):
    """Uses Yahoo's native search API, strictly filtering for Indian Stocks."""
    stock_str = str(stock_input).strip()
    
    if stock_str.isdigit():
        return stock_str + '.BO'
        
    try:
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
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

@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    """Fetches full stock profile, price history, ratios, and news."""
    stock = yf.Ticker(resolved_ticker)
    
    # Fetch 1 year historical data for RSI and price checks
    hist = stock.history(period="1y")
    if hist.empty:
        raise ValueError(f"Could not find '{raw_input}' on the NSE or BSE. Please check the spelling.")
        
    info = stock.info
    price = info.get("currentPrice")
    if price is None:
        price = round(hist['Close'].iloc[-1], 2)
        
    # Valuation & Technical Indicators
    rsi_val = calculate_rsi(hist, 14)
    peg_ratio = info.get("pegRatio", "N/A")
    roe = info.get("returnOnEquity", "N/A")
    if roe != "N/A": roe = f"{round(roe * 100, 2)}%"
    
    roa = info.get("returnOnAssets", "N/A")
    if roa != "N/A": roa = f"{round(roa * 100, 2)}%"
    
    # Quarterly PAT (Net Income) Growth: QoQ & YoY
    pat_qoq, pat_yoy = "N/A", "N/A"
    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            net_inc = q_fin.loc['Net Income'].dropna()
            if len(net_inc) >= 2 and net_inc.iloc[1] != 0:
                pat_qoq = f"{round(((net_inc.iloc[0] - net_inc.iloc[1]) / abs(net_inc.iloc[1])) * 100, 2)}%"
            if len(net_inc) >= 4 and net_inc.iloc[3] != 0:
                pat_yoy = f"{round(((net_inc.iloc[0] - net_inc.iloc[3]) / abs(net_inc.iloc[3])) * 100, 2)}%"
    except Exception:
        pass

    # Shareholding Breakdown
    insider_h = (info.get("heldPercentInsiders") or 0) * 100
    inst_h = (info.get("heldPercentInstitutions") or 0) * 100
    public_h = max(0, 100 - (insider_h + inst_h))
    
    shareholding = {
        "Promoters/Insiders": round(insider_h, 2),
        "Institutions (FII/DII)": round(inst_h, 2),
        "Public": round(public_h, 2)
    }

    # Core Metrics Dictionary
    metrics = {
        "name": info.get("longName", resolved_ticker),
        "price": price,
        "pe_ratio": info.get("trailingPE", "N/A"),
        "peg_ratio": peg_ratio,
        "roe": roe,
        "roce_roa": roa,
        "pat_qoq": pat_qoq,
        "pat_yoy": pat_yoy,
        "rsi": rsi_val,
        "debt_to_equity": info.get("debtToEquity", "N/A"),
        "net_margin": info.get("profitMargins", "N/A"),
        "market_cap": info.get("marketCap", "N/A"),
        "industry": info.get("industry", "N/A"),
        "shareholding": shareholding,
        "recent_news": "",
        "working_ticker": resolved_ticker
    }
    
    # News Headlines
    try:
        news_items = stock.news
        if news_items:
            headlines = [n.get('title', '') for n in news_items[:4]]
            metrics["recent_news"] = " | ".join(headlines)
        else:
            metrics["recent_news"] = "No recent major headlines."
    except Exception:
        metrics["recent_news"] = "News fetching unavailable."
    
    # Format Debt and Margin
    if metrics["debt_to_equity"] != "N/A": 
        try: metrics["debt_to_equity"] = round(metrics["debt_to_equity"] / 100, 2)
        except: metrics["debt_to_equity"] = "N/A"
        
    if metrics["net_margin"] != "N/A": 
        try: metrics["net_margin"] = f"{round(metrics['net_margin'] * 100, 2)}%"
        except: metrics["net_margin"] = "N/A"
        
    # Net Income and Debt Trends
    try:
        fin = stock.financials
        if fin is not None and not fin.empty and 'Net Income' in fin.index:
            ni_data = fin.loc['Net Income'].dropna()
            if len(ni_data) >= 2: metrics['net_income_trend'] = f"Net income moved from INR {ni_data.iloc[-1]:,.0f} to INR {ni_data.iloc[0]:,.0f}."
            else: metrics['net_income_trend'] = "Insufficient historical net income data."
        else: metrics['net_income_trend'] = "No historical net income available."
        
        bs = stock.balance_sheet
        if bs is not None and not bs.empty and 'Total Debt' in bs.index:
            td_data = bs.loc['Total Debt'].dropna()
            if len(td_data) >= 2: metrics['debt_trend'] = f"Total Debt moved from INR {td_data.iloc[-1]:,.0f} to INR {td_data.iloc[0]:,.0f}."
            else: metrics['debt_trend'] = "Insufficient historical debt data."
        else: metrics['debt_trend'] = "No historical debt available."
    except Exception: 
        metrics['net_income_trend'] = "Could not fetch history."
        metrics['debt_trend'] = "Could not fetch history."
        
    return metrics

def generate_report_content(stock_name, metrics, ticker):
    """Generates institutional qualitative AI analysis using Gemini."""
    client = genai.Client(api_key=GEMINI_KEY)
    
    system_instruction = """
    Act as an automated, professional-grade equity research assistant built in the style of an institutional advisory report. You are a ruthless analyst evaluating 360-degree risk.
    Do not use any markdown tags, asterisks, or hash symbols in your response. Output raw text separated by clean line breaks.
    
    CRITICAL STRUCTURE INSTRUCTION:
    Your response MUST begin exactly with these three variables for the engine parser, substituting the brackets with values:
    DYNAMIC_SECTOR: [Insert brief industry category]
    DYNAMIC_RATING: [Insert exactly one of these: STRONG BUY, BUY, HOLD, DON'T BUY, SELL]
    DYNAMIC_DURATION: [STRICT RULE: If Rating is DON'T BUY or SELL, this MUST be "N/A". If Swing Trade/Momentum, use "1-3 Months". If Long-Term Compounder, use "3-5 Years".]
    
    Following those lines, proceed immediately to the standard report using these exact headers:
    COMPANY OVERVIEW
    FUNDAMENTAL & MOMENTUM ANALYSIS
    MACRO AND SECTOR CATALYSTS
    KEY RISKS
    ACTIONABLE VERDICT
    
    ANALYST RULES (MACRO & RISKS):
    1. Read the provided parameters including PEG ratio, RSI, and Recent Headlines. Evaluate technical entry points and financial valuations against macro headwinds.
    2. OVERRIDE RULE: If severe external threats exist in headlines or margins are razor-thin, downgrade the rating (e.g., to HOLD, DON'T BUY, or SELL), even if historical momentum looks good.
    
    VERDICT FORMATTING RULE:
    Under the ACTIONABLE VERDICT header, output exactly two things:
    Line 1: The DYNAMIC_RATING itself in all caps (e.g., HOLD).
    Line 2: The detailed explanation of the verdict, explicitly weighing quantitative numbers against recent news.
    """
    
    user_prompt = f"""
    Analyze this stock:
    Company Name: {metrics['name']}
    Ticker: {ticker}
    Current Price: INR {metrics['price']}
    TTM P/E Ratio: {metrics['pe_ratio']}
    PEG Ratio: {metrics['peg_ratio']}
    ROE: {metrics['roe']} | ROA/ROCE Proxy: {metrics['roce_roa']}
    14-Day RSI: {metrics['rsi']}
    PAT Growth YoY: {metrics['pat_yoy']} | QoQ: {metrics['pat_qoq']}
    Debt-to-Equity Ratio: {metrics['debt_to_equity']}
    Net Profit Margin: {metrics['net_margin']}
    Market Cap: INR {metrics['market_cap']}
    
    HISTORICAL TRENDS:
    Net Income Trend: {metrics['net_income_trend']}
    Debt Trend: {metrics['debt_trend']}
    
    RECENT HEADLINES (For Macro Risk Assessment):
    {metrics['recent_news']}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash', 
        contents=user_prompt, 
        config=types.GenerateContentConfig(
            system_instruction=system_instruction, 
            temperature=0.15
        )
    )
    return response.text

def build_pdf_report(pdf_buffer, stock_name, metrics, ai_text, ticker):
    """Builds formal institutional PDF using ReportLab."""
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=45, leftMargin=45, topMargin=45, bottomMargin=45)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('DocTitle', fontName='Helvetica-Bold', fontSize=24, leading=28, textColor=colors.HexColor('#1A365D'))
    subtitle_style = ParagraphStyle('DocSub', fontName='Helvetica-Bold', fontSize=12, leading=16, textColor=colors.HexColor('#718096'))
    h1_style = ParagraphStyle('SectionH1', fontName='Helvetica-Bold', fontSize=14, leading=18, textColor=colors.HexColor('#2B6CB0'), spaceBefore=15, spaceAfter=8)
    body_style = ParagraphStyle('BodyTextCustom', fontName='Helvetica', fontSize=10, leading=15, textColor=colors.HexColor('#2D3748'))
    table_text = ParagraphStyle('TableText', fontName='Helvetica', fontSize=9, leading=12, textColor=colors.white)
    table_val = ParagraphStyle('TableVal', fontName='Helvetica-Bold', fontSize=9, leading=12, textColor=colors.white)
    
    rating_colors = {
        "STRONG BUY": "#15803D", "DON'T BUY": "#DC2626", "BUY": "#172554", "HOLD": "#D97706", "SELL": "#1E3A8A"
    }
    
    sector_val, duration_val, rating_val = "Growth / Cyclical", "N/A", "EVALUATED"
    clean_lines = []
    
    for line in ai_text.split('\n'):
        line_str = line.strip()
        if line_str.startswith("DYNAMIC_SECTOR:"): sector_val = line_str.replace("DYNAMIC_SECTOR:", "").strip()
        elif line_str.startswith("DYNAMIC_DURATION:"): duration_val = line_str.replace("DYNAMIC_DURATION:", "").strip()
        elif line_str.startswith("DYNAMIC_RATING:"): rating_val = line_str.replace("DYNAMIC_RATING:", "").strip()
        elif line_str: clean_lines.append(line_str)
                
    story = [
        Paragraph("Gatekeeper Research", title_style), 
        Paragraph("Automated Equity & Quantitative Report — Institutional Series", subtitle_style),
        Spacer(1, 15)
    ]
    
    current_rating = rating_val.upper().strip()
    target_hex = rating_colors.get(current_rating, "#FFFFFF")
    grid_rating_display = f"<font color='{target_hex}'><b>{current_rating}</b></font>"
    
    data = [
        [Paragraph("<b>Company:</b>", table_text), Paragraph(str(metrics['name']), table_val), Paragraph("<b>Category:</b>", table_text), Paragraph(sector_val, table_val)],
        [Paragraph("<b>Price:</b>", table_text), Paragraph(f"INR {metrics['price']}", table_val), Paragraph("<b>Time Horizon:</b>", table_text), Paragraph(duration_val, table_val)],
        [Paragraph("<b>P/E | PEG:</b>", table_text), Paragraph(f"{metrics['pe_ratio']}x | {metrics['peg_ratio']}", table_val), Paragraph("<b>ROE | ROA:</b>", table_text), Paragraph(f"{metrics['roe']} | {metrics['roce_roa']}", table_val)],
        [Paragraph("<b>PAT YoY Growth:</b>", table_text), Paragraph(str(metrics['pat_yoy']), table_val), Paragraph("<b>Verdict Rating:</b>", table_text), Paragraph(grid_rating_display, table_val)]
    ]
    
    t = Table(data, colWidths=[100, 160, 100, 160])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#2B6CB0')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#4299E1')),
    ]))
    story.append(t)
    story.append(Spacer(1, 15))
    
    for line in clean_lines:
        if any(h in line for h in ["COMPANY OVERVIEW", "FUNDAMENTAL & MOMENTUM ANALYSIS", "MACRO AND SECTOR CATALYSTS", "KEY RISKS", "ACTIONABLE VERDICT"]):
            story.append(Paragraph(line, h1_style))
        else:
            processed_line = line
            for r_text in sorted(rating_colors.keys(), key=len, reverse=True):
                if r_text in processed_line.upper():
                    pattern = r'(?i)(?<![a-zA-Z])' + re.escape(r_text) + r'(?![a-zA-Z])'
                    processed_line = re.sub(pattern, f'<font color="{rating_colors[r_text]}"><b>{r_text}</b></font>', processed_line)
            story.append(Paragraph(processed_line, body_style))
            story.append(Spacer(1, 4))
            
    doc.build(story)

# 4. STREAMLIT INTERFACE

if 'report_data' not in st.session_state:
    st.session_state.report_data = None

st.title("🛡️ Gatekeeper - Stock Screener & Research")
st.caption("Quantitative Dashboard & Institutional AI Intelligence for NSE/BSE Stocks")

stock_input = st.text_input("Enter Stock Name or Ticker (e.g., Reliance, Tata Motors, 505685):")

if st.button("Analyze Stock", type="primary"):
    if not stock_input.strip():
        st.warning("Please enter a valid stock name.")
    else:
        with st.spinner('Fetching quantitative metrics & analyzing live headlines...'):
            try:
                resolved_ticker = resolve_name_to_ticker(stock_input)
                metrics = fetch_stock_data(resolved_ticker, stock_input)
                final_ticker = metrics.pop('working_ticker')
                
                ai_text = generate_report_content(stock_input, metrics, final_ticker)
                st.session_state.report_data = {
                    "metrics": metrics, 
                    "ai_text": ai_text, 
                    "stock": stock_input, 
                    "ticker": final_ticker
                }
            except Exception as e:
                st.error(f"Error analyzing stock: {e}")

# 5. DASHBOARD & DISPLAY

if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    
    st.success(f"Analysis Complete for: **{m['name']} ({data['ticker']})**")
    
    # --- QUANTITATIVE METRICS CARDS ---
    st.subheader("📊 Quantitative Financial Dashboard")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current Price", f"₹{m['price']}")
    c1.metric("P/E Ratio", f"{m['pe_ratio']}x")
    
    c2.metric("PEG Ratio", f"{m['peg_ratio']}")
    c2.metric("14-Day RSI", f"{m['rsi']}")
    
    c3.metric("ROE", f"{m['roe']}")
    c3.metric("ROA / ROCE Proxy", f"{m['roce_roa']}")
    
    c4.metric("PAT Growth (YoY)", f"{m['pat_yoy']}")
    c4.metric("PAT Growth (QoQ)", f"{m['pat_qoq']}")

    st.markdown("---")
    
    # --- CHARTS SECTION (SimplyWallSt / Finology Style) ---
    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        st.markdown("### 🍰 Shareholding Pattern Split")
        sh_data = m['shareholding']
        if sum(sh_data.values()) > 0:
            fig_pie = px.pie(
                names=list(sh_data.keys()), 
                values=list(sh_data.values()), 
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Bold
            )
            fig_pie.update_layout(margin=dict(t=20, b=20, l=20, r=20))
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("Detailed shareholding breakdown unavailable for this stock.")

    with col_chart2:
        st.markdown(f"### 📈 Sector Valuation Benchmark ({m['industry']})")
        pe_num = float(m['pe_ratio']) if m['pe_ratio'] != "N/A" else 0
        peg_num = float(m['peg_ratio']) if m['peg_ratio'] != "N/A" else 0
        
        fig_bar = go.Figure(data=[
            go.Bar(name='P/E Ratio', x=['Valuation Metrics'], y=[pe_num], marker_color='#2B6CB0'),
            go.Bar(name='PEG Ratio (x10)', x=['Valuation Metrics'], y=[peg_num * 10], marker_color='#4299E1')
        ])
        fig_bar.update_layout(barmode='group', margin=dict(t=20, b=20, l=20, r=20))
        st.plotly_chart(fig_bar, use_container_width=True)

    st.markdown("---")
    
    # --- QUALITATIVE AI REPORT ---
    st.subheader("📑 Institutional Advisory Analysis")
    
    display_text = re.sub(r'DYNAMIC_.*?\n', '', data['ai_text'])
    for h in ["COMPANY OVERVIEW", "FUNDAMENTAL & MOMENTUM ANALYSIS", "MACRO AND SECTOR CATALYSTS", "KEY RISKS", "ACTIONABLE VERDICT"]:
        display_text = display_text.replace(h, f"\n### {h}")
        
    st.markdown(display_text)
    st.markdown("---")
    
    # --- PDF GENERATION & DOWNLOAD ---
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, data['stock'], m, data['ai_text'], data['ticker'])
    pdf_buffer.seek(0)
    
    st.download_button(
        label="📥 Download Official Institutional PDF Report", 
        data=pdf_buffer, 
        file_name=f"{data['ticker']}_Gatekeeper_Report.pdf", 
        mime="application/pdf",
        type="primary"
    )
