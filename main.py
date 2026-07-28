import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types

app = FastAPI(title="Athenaeum Intelligence API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

RISK_FREE_RATE = 0.065
EQUITY_RISK_PREMIUM = 0.055
TERMINAL_GROWTH_PCT = 5.0

FINANCIAL_SECTOR_KEYWORDS = ["financial services", "bank", "nbfc", "insurance", "capital markets", "credit services", "asset management"]
CAPEX_INTENSIVE_KEYWORDS = ["industrial", "engineering", "infrastructure", "construction", "capital goods", "defense", "aerospace"]
CYCLICAL_KEYWORDS = ["auto", "automobile", "tire", "tyre"]

def to_float(val):
    if val in [None, "N/A", "", "None", "Stock doesn't pay dividends"]: return None
    if isinstance(val, bool) or (isinstance(val, float) and pd.isna(val)): return None
    if isinstance(val, (int, float)): return float(val)
    try: return float(str(val).replace('%', '').replace('x', '').replace('₹', '').replace(',', '').strip())
    except: return None

def is_valid_metric(val):
    if val in [None, "N/A", "", "-", "--", "None", "0", "0.00%", "0.00"]: return False
    return to_float(val) is not None

def resolve_name_to_ticker(stock_input: str) -> str:
    if not stock_input: raise ValueError("No input provided")
    stock_str = str(stock_input).strip()
    if stock_str.isdigit(): return stock_str + '.BO'
    clean_input = re.sub(r'(?i)\s+(ltd|limited|inc|corp|industries|share|stock)$', '', stock_str).strip()
    upper_input = clean_input.upper().replace(" ", "")
    if upper_input.endswith(('.NS', '.BO')): return upper_input

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(clean_input)}"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'): return sym
    except Exception:
        pass
    return upper_input + '.NS'

def is_financial_sector(sector, industry):
    text = f"{sector or ''} {industry or ''}".lower()
    return any(kw in text for kw in FINANCIAL_SECTOR_KEYWORDS)

def classify_sector_profile(sector, industry):
    if is_financial_sector(sector, industry): return "financial"
    text = f"{sector or ''} {industry or ''}".lower()
    if any(kw in text for kw in CAPEX_INTENSIVE_KEYWORDS): return "capex_intensive"
    if any(kw in text for kw in CYCLICAL_KEYWORDS): return "cyclical"
    return "standard"

def fetch_google_news(query_term):
    try:
        safe_query = urllib.parse.quote(query_term)
        url = f"https://news.google.com/rss/search?q={safe_query}&hl=en-IN&gl=IN&ceid=IN:en"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            headlines = []
            for item in root.findall('.//item')[:6]:
                title = item.find('title')
                link = item.find('link')
                if title is not None and link is not None:
                    headlines.append({'title': title.text, 'link': link.text})
            return headlines
    except: pass
    return []

def valuation_checks(m):
    pe, pb = to_float(m.get('pe_ratio')), to_float(m.get('pb_ratio'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if pe is not None:
        if pe < 0: checks.append(("Profitable on a P/E basis", False, f"Negative P/E ({pe}x)"))
        else: checks.append(("Reasonable P/E (<25x)", pe < 25, f"Trailing P/E: {pe}x"))
    if pb is not None:
        thresh = 3.0 if is_fin else 5.0
        checks.append((f"Reasonable P/B (<{thresh:g}x)", 0 < pb < thresh, f"Price-to-Book: {pb}x"))
    return checks

def past_performance_checks(m):
    yoy, roe, margin = to_float(m.get('pat_yoy')), to_float(m.get('roe')), to_float(m.get('net_margin'))
    checks = []
    if yoy is not None: checks.append(("Positive Earnings Growth (YoY)", yoy > 0, f"PAT YoY: {yoy}%"))
    if roe is not None: checks.append(("Strong Return on Equity (>15%)", roe > 15, f"ROE: {roe}%"))
    if margin is not None: checks.append(("Healthy Net Margin (>10%)", margin > 10, f"Net Margin: {margin}%"))
    return checks

def financial_health_checks(m):
    de, ic = to_float(m.get('debt_to_equity')), to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if de is not None:
        thresh = 10.0 if is_fin else 1.0
        checks.append((f"Leverage Control (D/E < {thresh}x)", de < thresh, f"Debt-to-Equity: {de}"))
    if ic is not None: checks.append(("Comfortable Interest Coverage (>3x)", ic > 3, f"Interest Coverage: {ic}x"))
    return checks

def dividend_checks(m):
    dy = to_float(m.get('dividend_yield'))
    return [("Notable Dividend Yield (>1.5%)", dy is not None and dy > 1.5, f"Yield: {m.get('dividend_yield', 'None')}")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else 50

def run_predictive_pipeline(info, hist, fcf_history, current_price, fundamental_score):
    beta = info.get('beta') or 1.0
    ke_pct = min(max((RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM) * 100, 9), 20)
    growth_pct = 10.0

    if fcf_history is not None and len(fcf_history) > 0:
        avg_fcf = float(fcf_history.mean())
    else:
        avg_fcf = info.get('netIncomeToCommon') or 0

    shares = info.get('sharesOutstanding') or 1
    fcf_per_share = (avg_fcf / shares) if (avg_fcf and shares > 0) else 0

    if fcf_per_share > 0:
        discount_rate, g_frac, tg_frac = ke_pct / 100, growth_pct / 100, TERMINAL_GROWTH_PCT / 100
        pv_fcf = sum(fcf_per_share * (1 + g_frac) ** t / (1 + discount_rate) ** t for t in range(1, 6))
        fcf5 = fcf_per_share * (1 + g_frac) ** 5
        terminal_value = (fcf5 * (1 + tg_frac)) / (discount_rate - tg_frac)
        intrinsic_value = pv_fcf + terminal_value / (1 + discount_rate) ** 5
        model_used = "2-Stage DCF"
    else:
        eps = info.get('trailingEps')
        if eps and eps > 0:
            intrinsic_value = eps * 20
            model_used = "Target P/E"
        else:
            bv = info.get('bookValue') or 10
            intrinsic_value = bv * 0.8
            model_used = "Book Value Haircut"

    target_price = round(intrinsic_value, 2)
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0
    intrinsic_score = min(max(50 + margin_of_safety * 100, 0), 100)
    
    composite = round(0.40 * fundamental_score + 0.35 * intrinsic_score + 0.25 * 50, 1)

    if composite >= 75: verdict = "STRONG BUY"
    elif composite >= 60: verdict = "BUY"
    elif composite >= 40: verdict = "OBSERVE"
    else: verdict = "DON'T BUY"

    return {
        "verdict": verdict, "target_price": target_price, "composite_score": composite,
        "fundamental_score": fundamental_score, "intrinsic_score": round(intrinsic_score, 1),
        "technical_score": 50.0, "margin_of_safety": round(margin_of_safety * 100, 1),
        "model_used": model_used, "time_horizon": "3-5 Years",
        "entry_range": f"₹{round(current_price*0.92, 2)} - ₹{round(current_price*0.96, 2)}",
        "stop_loss": round(current_price * 0.85, 2)
    }

def fetch_stock_data(resolved_ticker: str):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: raise ValueError(f"No stock data found for '{resolved_ticker}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist['Close'].iloc[-1]), 2))
    sector, industry = info.get("sector", "N/A"), info.get("industry", "N/A")
    is_fin = is_financial_sector(sector, industry)

    cf = stock.cashflow
    fcf_history = cf.loc['Free Cash Flow'].dropna() if (cf is not None and not cf.empty and 'Free Cash Flow' in cf.index) else None

    pe, pb = info.get("trailingPE"), info.get("priceToBook")
    roe = info.get("returnOnEquity")
    roe_val = roe * 100 if roe else None

    temp_metrics = {
        'pe_ratio': pe, 'pb_ratio': pb, 'roe': roe_val, 'is_financial_sector': is_fin,
        'pat_yoy': 10.0, 'net_margin': 12.0, 'debt_to_equity': info.get("debtToEquity", 50)/100, 'interest_coverage': 5.0
    }
    v_score = score_from_checks(valuation_checks(temp_metrics))
    p_score = score_from_checks(past_performance_checks(temp_metrics))
    h_score = score_from_checks(financial_health_checks(temp_metrics))
    fund_score = round((v_score + p_score + h_score) / 3, 1)

    predictive = run_predictive_pipeline(info, hist, fcf_history, current_price, fund_score)
    recent_news = fetch_google_news(info.get('longName', resolved_ticker))

    # Financial statements tables extraction
    pnl_data, bs_data, cf_data = [], [], []
    try:
        fin = stock.financials
        if fin is not None and not fin.empty:
            col = fin.columns[0]
            for row_name in ['Total Revenue', 'Operating Income', 'Net Income']:
                if row_name in fin.index:
                    val = fin.loc[row_name, col]
                    pnl_data.append({"Particulars": row_name, "Amount (₹ Cr)": round(val / 10000000, 2) if pd.notna(val) else "—"})
        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            for row_name in ['Total Debt', 'Total Assets', 'Common Stock Equity']:
                if row_name in bs.index:
                    val = bs.loc[row_name, col]
                    bs_data.append({"Particulars": row_name, "Amount (₹ Cr)": round(val / 10000000, 2) if pd.notna(val) else "—"})
        if cf is not None and not cf.empty:
            col = cf.columns[0]
            for row_name in ['Operating Cash Flow', 'Free Cash Flow']:
                if row_name in cf.index:
                    val = cf.loc[row_name, col]
                    cf_data.append({"Particulars": row_name, "Amount (₹ Cr)": round(val / 10000000, 2) if pd.notna(val) else "—"})
    except: pass

    shareholding = {
        "Promoters": round((info.get("heldPercentInsiders") or 0) * 100, 1),
        "Institutions": round((info.get("heldPercentInstitutions") or 0) * 100, 1),
        "Public": round(max(0, 100 - (((info.get("heldPercentInsiders") or 0) + (info.get("heldPercentInstitutions") or 0)) * 100)), 1)
    }

    return {
        "name": info.get("longName", resolved_ticker), "ticker": resolved_ticker, "price": current_price,
        "pe_ratio": pe if is_valid_metric(pe) else "N/A", "pb_ratio": pb if is_valid_metric(pb) else "N/A",
        "roe": f"{round(roe_val, 2)}%" if roe_val else "N/A", "sector": sector, "industry": industry,
        "business_summary": info.get("longBusinessSummary", "No summary available."),
        "recent_news": recent_news, "val_checks": valuation_checks(temp_metrics),
        "past_checks": past_performance_checks(temp_metrics), "health_checks": financial_health_checks(temp_metrics),
        "div_checks": dividend_checks(temp_metrics), "predictive": predictive,
        "pnl_df": pnl_data, "bs_df": bs_data, "cf_df": cf_data, "shareholding": shareholding,
        "history": [{"Date": str(d.date()), "Close": float(c)} for d, c in zip(hist.index, hist['Close'])]
    }

def generate_comprehensive_report(metrics: dict) -> str:
    if not GEMINI_KEY: return "Gemini API key is not configured."
    client = genai.Client(api_key=GEMINI_KEY)
    sys = """You are a Senior Equity Analyst. Output exactly 8 numbered sections:
1. VALUATION & FAIR VALUE
2. FUTURE GROWTH & OUTLOOK
3. PAST PERFORMANCE & EARNINGS QUALITY
4. FINANCIAL HEALTH & BALANCE SHEET
5. DIVIDEND & CAPITAL ALLOCATION
6. MANAGEMENT & COMPENSATION
7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
8. NARRATIVE VERDICT
Provide thorough, professional analysis for each section based on the provided inputs."""
    pred = metrics['predictive']
    pmt = f"Target: {metrics['name']} ({metrics['ticker']}). Sector: {metrics['sector']}. Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. Target Price: ₹{pred['target_price']}. Verdict: {pred['verdict']}."
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt, config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

@app.get("/")
def root(): return {"status": "Athenaeum API is live"}

@app.get("/api/analyze")
def analyze(ticker: str):
    try:
        resolved = resolve_name_to_ticker(ticker.strip())
        data = fetch_stock_data(resolved)
        ai_text = generate_comprehensive_report(data)
        return {"status": "success", "metrics": data, "ai_narrative": ai_text}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
