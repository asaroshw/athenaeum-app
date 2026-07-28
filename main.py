import os
import re
import requests
import pandas as pd
import numpy as np
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai

# ============================================================
# 1. SETUP & APP CONFIG
# ============================================================
app = FastAPI(title="Athenaeum Intelligence API")

# Enable CORS so your frontend can call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# ============================================================
# 2. HELPER FUNCTIONS
# ============================================================
def resolve_name_to_ticker(stock_input: str) -> str:
    """Converts company names or ticker inputs to Yahoo Finance tickers (.NS / .BO)."""
    stock_str = str(stock_input).strip()
    if stock_str.isdigit():
        return stock_str + '.BO'
    try:
        res = requests.get(
            f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5
        )
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'):
                    return sym
    except Exception:
        pass
    
    upper = stock_str.upper().replace(" ", "")
    return upper if upper.endswith(('.NS', '.BO')) else upper + '.NS'


def run_predictive_pipeline(info, hist, fcf_history, current_price):
    """Calculates intrinsic value and stock verdict."""
    beta = info.get('beta') or 1.0
    ke_pct = min(max((0.065 + beta * 0.055) * 100, 9), 20)
    growth_pct = 10.0

    if fcf_history is not None and len(fcf_history) > 0:
        avg_fcf = float(fcf_history.mean())
    else:
        avg_fcf = info.get('netIncomeToCommon') or 0

    shares = info.get('sharesOutstanding') or 1
    fcf_per_share = (avg_fcf / shares) if (avg_fcf and shares > 0) else 0

    if fcf_per_share > 0:
        discount_rate, g_frac, tg_frac = ke_pct / 100, growth_pct / 100, 0.05
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
    composite = round(min(max(50 + margin_of_safety * 100, 0), 100), 1)

    if composite >= 70 and margin_of_safety > 0:
        verdict = "BUY"
    elif composite >= 45:
        verdict = "OBSERVE"
    else:
        verdict = "DON'T BUY"

    return {
        "verdict": verdict,
        "target_price": target_price,
        "composite_score": composite,
        "margin_of_safety": round(margin_of_safety * 100, 1),
        "model_used": model_used
    }


def fetch_stock_data(resolved_ticker: str):
    """Fetches stock info from yfinance and computes valuation metrics."""
    stock = yf.Ticker(resolved_ticker)
    hist = stock.history(period="1y")
    if hist.empty:
        raise ValueError(f"No stock data found for '{resolved_ticker}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist['Close'].iloc[-1]), 2))
    
    cf = stock.cashflow
    fcf_history = cf.loc['Free Cash Flow'].dropna() if (cf is not None and not cf.empty and 'Free Cash Flow' in cf.index) else None

    predictive = run_predictive_pipeline(info, hist, fcf_history, current_price)

    return {
        "name": info.get("longName", resolved_ticker),
        "ticker": resolved_ticker,
        "price": current_price,
        "pe_ratio": info.get("trailingPE", "N/A"),
        "pb_ratio": info.get("priceToBook", "N/A"),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "predictive": predictive
    }


def generate_ai_narrative(metrics: dict) -> str:
    """Generates stock summary narrative via Gemini API."""
    if not GEMINI_KEY:
        return "Gemini API key is not set in Render environment variables."
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        prompt = (
            f"Analyze stock {metrics['name']} ({metrics['ticker']}). "
            f"Current Price: ₹{metrics['price']}, P/E Ratio: {metrics['pe_ratio']}, "
            f"Model Verdict: {metrics['predictive']['verdict']}. "
            f"Provide a brief 2-paragraph fundamental analysis summary."
        )
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"AI summary generation unavailable: {str(e)}"

# ============================================================
# 3. API ENDPOINTS
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
