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
st.set_page_config(page_title="ASW Stock Ideas - Financial Intelligence Dashboard", layout="wide")

GEMINI_KEY = st.secrets.get("GEMINI_API_KEY", "")
ANGEL_KEY = st.secrets.get("ANGEL_API_KEY", "WjBiiHX1")

# 2. TECHNICAL CALCULATIONS
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

# 3. CORE LOGIC FUNCTIONS
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
    if roe != "N/A": roe = round(roe * 100, 2)
    roa = info.get("returnOnAssets", "N/A")
    if roa != "N/A": roa = round(roa * 100, 2)
    
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
        "working_ticker": resolved_ticker
    }
    
    try:
        news_items = stock.news
        if news_items:
            headlines = [n.get('title', '') for n in news_items[:4]]
            metrics["recent_news"] = " | ".join(headlines)
        else:
            metrics["recent_news"] = "No recent major headlines."
    except Exception:
        metrics["recent_news"] = "News unavailable."
    
    if metrics["debt_to_equity"] != "N/A": 
        try: metrics["debt_to_equity"] = round(metrics["debt_to_equity"] / 100, 2)
        except: pass
    if metrics["net_margin"] != "N/A": 
        try: metrics["net_margin"] = f"{round(metrics['net_margin'] * 100, 2)}%"
        except: pass
        
    return metrics

def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)
    
    system_instruction = """
    You are an elite institutional equity research director building a comprehensive, exhaustive, multi-page stock intelligence dossier styled after professional SimplyWallSt terminal reports. Do not summarize; provide deep, granular, multi-paragraph qualitative and quantitative breakdowns for every module.
    Do not use markdown hash symbols or asterisks. Output clean raw text with clear section headers.
    
    MANDATORY PRE-AMBLE VARIABLES (Exact format on first 3 lines):
    DYNAMIC_SECTOR: [Insert Industry]
    DYNAMIC_RATING: [STRONG BUY, BUY, HOLD, DON'T BUY, or SELL]
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
    
    Ensure each section contains thorough financial context, comparative industry positioning, cash flow dynamics, and risk evaluations.
    """
    
    user_prompt = f"""
    Target Company Data:
    Company Name: {metrics['name']} ({ticker})
    Current Market Price: INR {metrics['price']}
    Market Cap: INR {metrics['market_cap']}
    P/E Ratio: {metrics['pe_ratio']} | PEG Ratio: {metrics['peg_ratio']}
    ROE: {metrics['roe']} | ROA/ROCE Proxy: {metrics['roce_roa']}
    Dividend Yield: {metrics['dividend_yield']}
    14-Day RSI: {metrics['rsi']}
    PAT Growth YoY: {metrics['pat_yoy']} | QoQ: {metrics['pat_qoq']}
    Debt to Equity: {metrics['debt_to_equity']}
    Net Margin: {metrics['net_margin']}
    Industry: {metrics['industry']} | Sector: {metrics['sector']}
    Recent News Headwinds/Catalysts: {metrics['recent_news']}
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
    
    rating_colors = {"STRONG BUY": "#15803D", "DON'T BUY": "#DC2626", "BUY": "#172554", "HOLD": "#D97706", "SELL": "#1E3A8A"}
    
    sector_val, duration_val, rating_val = "Growth / Cyclical", "N/A", "EVALUATED"
    clean_lines = []
    
    for line in ai_text.split('\n'):
        line_str = line.strip()
        if line_str.startswith("DYNAMIC_SECTOR:"): sector_val = line_str.replace("DYNAMIC_SECTOR:", "").strip()
        elif line_str.startswith("DYNAMIC_DURATION:"): duration_val = line_str.replace("DYNAMIC_DURATION:", "").strip()
        elif line_str.startswith("DYNAMIC_RATING:"): rating_val = line_str.replace("DYNAMIC_RATING:", "").strip()
        elif line_str: clean_lines.append(line_str)
                
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
        [Paragraph("<b>Price / Cap:</b>", table_text), Paragraph(f"INR {metrics['price']}", table_val), Paragraph("<b>Horizon:</b>", table_text), Paragraph(duration_val, table_val)],
        [Paragraph("<b>P/E | PEG:</b>", table_text), Paragraph(f"{metrics['pe_ratio']}x | {metrics['peg_ratio']}", table_val), Paragraph("<b>ROE | Div:</b>", table_text), Paragraph(f"{metrics['roe']} | {metrics['dividend_yield']}", table_val)],
        [Paragraph("<b>PAT YoY Growth:</b>", table_text), Paragraph(str(metrics['pat_yoy']), table_val), Paragraph("<b>Terminal Rating:</b>", table_text), Paragraph(grid_rating_display, table_val)]
    ]
    
    t = Table(table_data, colWidths=[105, 165, 100, 170])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#2B6CB0')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#4299E1')),
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

# 4. STREAMLIT USER INTERFACE
if 'report_data' not in st.session_state:
    st.session_state.report_data = None

st.title("ASW Stock Ideas")
st.caption("Financial Intelligence Dashboard")

stock_input = st.text_input("Enter Stock Name or Ticker (e.g., Reliance, Tata Motors, HBL):")

if st.button("Generate Terminal Dossier", type="primary"):
    if not stock_input.strip():
        st.warning("Please enter a valid stock identifier.")
    else:
        with st.spinner('Compiling exhaustive institutional intelligence and modular subsections...'):
            try:
                resolved_ticker = resolve_name_to_ticker(stock_input)
                metrics = fetch_stock_data(resolved_ticker, stock_input)
                final_ticker = metrics.pop('working_ticker')
                
                ai_text = generate_comprehensive_report(metrics, final_ticker)
                st.session_state.report_data = {
                    "metrics": metrics, 
                    "ai_text": ai_text, 
                    "stock": stock_input, 
                    "ticker": final_ticker
                }
            except Exception as e:
                st.error(f"Error building report: {e}")

if st.session_state.report_data:
    data = st.session_state.report_data
    m = data['metrics']
    
    st.success(f"Terminal Report Ready: **{m['name']} ({data['ticker']})**")
    
    raw_ai_text = re.sub(r'DYNAMIC_.*?\n', '', data['ai_text'])
    sections_list = [s.strip() for s in re.split(r'\n(?=[0-9]\.\s[A-Z&]+)', raw_ai_text) if s.strip()]
    
    # -------------------------------------------------------------------------
    # SECTION 1: VALUATION & FAIR VALUE (Includes Valuation Multiples Chart & Table)
    # -------------------------------------------------------------------------
    st.markdown("## 1. VALUATION & FAIR VALUE")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[0] if len(sections_list) > 0 else "Detailed valuation breakdown compiling...")
    with c2:
        st.markdown("##### Key Valuation Metrics")
        val_df = pd.DataFrame({
            "Metric": ["Current Price", "Trailing P/E", "PEG Ratio", "Market Cap"],
            "Value": [f"₹{m['price']}", f"{m['pe_ratio']}x", str(m['peg_ratio']), str(m['market_cap'])]
        })
        st.dataframe(val_df, use_container_width=True, hide_index=True)
        
        pe_num = float(m['pe_ratio']) if m['pe_ratio'] != "N/A" else 0
        peg_num = float(m['peg_ratio']) if m['peg_ratio'] != "N/A" else 0
        fig_val = px.bar(x=['P/E Ratio', 'PEG Ratio (x10)'], y=[pe_num, peg_num * 10], color=['P/E', 'PEG'], text_auto=True)
        fig_val.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False, height=220)
        st.plotly_chart(fig_val, use_container_width=True)
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 2: FUTURE GROWTH & OUTLOOK (Includes Earnings Momentum Chart)
    # -------------------------------------------------------------------------
    st.markdown("## 2. FUTURE GROWTH & OUTLOOK")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[1] if len(sections_list) > 1 else "Future growth outlook compiling...")
    with c2:
        st.markdown("##### Growth Metrics")
        growth_df = pd.DataFrame({
            "Indicator": ["YoY PAT Growth", "QoQ PAT Growth", "14-Day RSI"],
            "Rate": [str(m['pat_yoy']), str(m['pat_qoq']), str(m['rsi'])]
        })
        st.dataframe(growth_df, use_container_width=True, hide_index=True)
        
        yoy_val = float(m['pat_yoy'].replace('%','')) if m['pat_yoy'] != "N/A" else 0
        qoq_val = float(m['pat_qoq'].replace('%','')) if m['pat_qoq'] != "N/A" else 0
        fig_growth = px.bar(x=['YoY Growth', 'QoQ Growth'], y=[yoy_val, qoq_val], color=['YoY', 'QoQ'], text_auto=True)
        fig_growth.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False, height=220)
        st.plotly_chart(fig_growth, use_container_width=True)
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 3: PAST PERFORMANCE & EARNINGS QUALITY (Includes Returns Chart)
    # -------------------------------------------------------------------------
    st.markdown("## 3. PAST PERFORMANCE & EARNINGS QUALITY")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[2] if len(sections_list) > 2 else "Past performance analysis compiling...")
    with c2:
        st.markdown("##### Quality & Returns")
        perf_df = pd.DataFrame({
            "Ratio": ["Return on Equity (ROE)", "ROA / ROCE Proxy", "Net Profit Margin"],
            "Percentage": [str(m['roe']), str(m['roce_roa']), str(m['net_margin'])]
        })
        st.dataframe(perf_df, use_container_width=True, hide_index=True)
        
        roe_val = float(m['roe'].replace('%','')) if m['roe'] != "N/A" else 0
        roa_val = float(m['roce_roa'].replace('%','')) if m['roce_roa'] != "N/A" else 0
        fig_perf = px.bar(x=['ROE', 'ROA'], y=[roe_val, roa_val], color=['ROE', 'ROA'], text_auto=True)
        fig_perf.update_layout(margin=dict(t=10, b=10, l=10, r=10), showlegend=False, height=220)
        st.plotly_chart(fig_perf, use_container_width=True)
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 4: FINANCIAL HEALTH & BALANCE SHEET
    # -------------------------------------------------------------------------
    st.markdown("## 4. FINANCIAL HEALTH & BALANCE SHEET")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[3] if len(sections_list) > 3 else "Financial health evaluation compiling...")
    with c2:
        st.markdown("##### Balance Sheet Profile")
        health_df = pd.DataFrame({
            "Parameter": ["Debt to Equity Ratio", "Industry Classification"],
            "Status": [str(m['debt_to_equity']), str(m['industry'])]
        })
        st.dataframe(health_df, use_container_width=True, hide_index=True)
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 5: DIVIDEND & CAPITAL ALLOCATION
    # -------------------------------------------------------------------------
    st.markdown("## 5. DIVIDEND & CAPITAL ALLOCATION")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[4] if len(sections_list) > 4 else "Dividend commentary compiling...")
    with c2:
        st.markdown("##### Distribution Metrics")
        div_df = pd.DataFrame({
            "Metric": ["Dividend Yield", "Capital Allocation Focus"],
            "Value": [str(m['dividend_yield']), "Reinvestment & Payouts"]
        })
        st.dataframe(div_df, use_container_width=True, hide_index=True)
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 6: MANAGEMENT & COMPENSATION
    # -------------------------------------------------------------------------
    st.markdown("## 6. MANAGEMENT & COMPENSATION")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[5] if len(sections_list) > 5 else "Management breakdown compiling...")
    with c2:
        st.markdown("##### Leadership Overview")
        mgmt_df = pd.DataFrame({
            "Attribute": ["Sector Alignment", "Execution Track Record"],
            "Assessment": ["Professional", "Verified Operational History"]
        })
        st.dataframe(mgmt_df, use_container_width=True, hide_index=True)
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 7: OWNERSHIP STRUCTURE & INSIDER SENTIMENT (Includes Ownership Donut Chart)
    # -------------------------------------------------------------------------
    st.markdown("## 7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT")
    c1, c2 = st.columns([1.5, 1])
    with c1:
        st.write(sections_list[6] if len(sections_list) > 6 else "Ownership structure overview compiling...")
    with c2:
        st.markdown("##### Shareholding Split")
        sh_data = m['shareholding']
        if sum(sh_data.values()) > 0:
            fig_pie = px.pie(names=list(sh_data.keys()), values=list(sh_data.values()), hole=0.45, color_discrete_sequence=px.colors.qualitative.Bold)
            fig_pie.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=240)
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("Shareholding breakdown unavailable.")
    st.markdown("---")

    # -------------------------------------------------------------------------
    # SECTION 8: SUMMARY VERDICT & KEY RISKS
    # -------------------------------------------------------------------------
    st.markdown("## 8. SUMMARY VERDICT & KEY RISKS")
    st.write(sections_list[7] if len(sections_list) > 7 else "Summary verdict compiling...")
    st.markdown("---")

    # --- PDF EXPORT ---
    pdf_buffer = io.BytesIO()
    build_pdf_report(pdf_buffer, m, data['ai_text'], data['ticker'])
    pdf_buffer.seek(0)
    
    st.download_button(
        label="📥 Download Official PDF Dossier (Full Layout Match)", 
        data=pdf_buffer, 
        file_name=f"{data['ticker']}_ASW_Stock_Ideas_Dossier.pdf", 
        mime="application/pdf",
        type="primary"
    )
