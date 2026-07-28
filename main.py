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

# ============================================================
# 1. SETUP & CONFIGURATION
# ============================================================
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

SECTOR_PEERS = {
    "financial": ["BAJFINANCE.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "HDFCBANK.NS"],
    "capex_intensive": ["LT.NS", "HAL.NS", "BEL.NS", "SIEMENS.NS"],
    "cyclical": ["BOSCHLTD.NS", "MOTHERSON.NS", "UNOMINDA.NS", "MRF.NS"],
    "standard": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HUL.NS"] 
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

def is_financial_sector(sector, industry):
    text = f"{sector or ''} {industry or ''}".lower()
    return any(kw in text for kw in ["financial", "bank", "nbfc", "insurance", "capital markets", "credit services"])

def classify_sector_profile(sector, industry):
    if is_financial_sector(sector, industry): return "financial"
    text = f"{sector or ''} {industry or ''}".lower()
    if any(kw in text for kw in ["industrial", "engineering", "infrastructure", "construction", "defense"]): return "capex_intensive"
    if any(kw in text for kw in ["auto", "automobile", "tire", "tyre"]): return "cyclical"
    return "standard"

# ============================================================
# 3. QUANTITATIVE & DCF ENGINE
# ============================================================
def calculate_atr(df, period=14):
    if df is None or len(df) <= period: return None
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    val = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else None

def run_predictive_pipeline(info, hist, fcf_history, sector, industry, fundamental_score, current_price, pat_yoy_pct):
    beta = info.get('beta') or 1.0
    ke_pct = min(max((RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM) * 100, 9), 20)
    growth_pct = min(max(pat_yoy_pct or 8.0, 5), 25)

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
        eps = info.get('trailingEps') or 10
        intrinsic_value = eps * 20
        model_used = "Target P/E"

    target_price = round(intrinsic_value, 2)
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0

    composite = round(0.50 * fundamental_score + 0.50 * min(max(50 + margin_of_safety * 100, 0), 100), 1)

    if composite >= 70 and margin_of_safety > 0: verdict = "BUY"
    elif composite >= 45: verdict = "OBSERVE"
    else: verdict = "DON'T BUY"

    return {
        "verdict": verdict,
        "target_price": target_price,
        "composite_score": composite,
        "margin_of_safety": round(margin_of_safety * 100, 1),
        "model_used": model_used
    }

# ============================================================
# 4. MASTER FETCH FUNCTION
# ============================================================
def fetch_stock_data(resolved_ticker):
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty: raise ValueError(f"No stock data found for ticker '{resolved_ticker}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist['Close'].iloc[-1]), 2))
    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")

    pe_raw = info.get("trailingPE", "N/A")
    pb_raw = info.get("priceToBook", "N/A")
    roe_raw = info.get("returnOnEquity")
    roe_fmt = f"{round(roe_raw * 100, 2)}%" if roe_raw else "N/A"

    cf = stock.cashflow
    fcf_history = cf.loc['Free Cash Flow'].dropna() if (cf is not None and not cf.empty and 'Free Cash Flow' in cf.index) else None

    predictive = run_predictive_pipeline(info, hist, fcf_history, sector, industry, 65, current_price, 12.0)

    return {
        "name": info.get("longName", resolved_ticker),
        "ticker": resolved_ticker,
        "price": current_price,
        "pe_ratio": pe_raw,
        "pb_ratio": pb_raw,
        "roe": roe_fmt,
        "sector": sector,
        "industry": industry,
        "predictive": predictive
    }

def generate_ai_narrative(metrics):
    if not GEMINI_KEY:
        return "Gemini API key not configured. Add GEMINI_API_KEY to Render environment variables."
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"Analyze stock {metrics['name']} ({metrics['ticker']}). Price: ₹{metrics['price']}, P/E: {metrics['pe_ratio']}, Verdict: {metrics['predictive']['verdict']}. Provide a 2-paragraph summary."
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=prompt).text

# ============================================================
# 5. API ENDPOINTS
# ============================================================
@app.get("/")
def root():
    return {"status": "Athenaeum API is live and healthy"}

@app.get("/api/analyze")
def analyze(ticker: str):
    try:
        resolved = resolve_name_to_ticker(ticker)
        data = fetch_stock_data(resolved)
        narrative = generate_ai_narrative(data)
        return {
            "status": "success",
            "metrics": data,
            "ai_narrative": narrative
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
