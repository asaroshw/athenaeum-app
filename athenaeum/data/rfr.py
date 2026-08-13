"""Risk-free rate sourcing with provenance."""
from __future__ import annotations
import logging
import requests
import streamlit as st
import yfinance as yf

logger = logging.getLogger("athenaeum")

def get_dynamic_risk_free_rate():
    """Indian 10Y G-Sec yield as decimal.
    FMP primary → limited yfinance symbols that can plausibly represent Indian rates → static 6.5%.
    Never substitutes equity indices (e.g. GSPC) for a bond yield.
    """
    try:
        FMP_KEY = st.secrets.get("FMP_API_KEY", "")
    except Exception:
        FMP_KEY = ""
    if FMP_KEY:
        for symbol in ["IN10Y", "GSIN10YR"]:
            try:
                r = requests.get(
                    f"https://financialmodelingprep.com/api/v3/historical-price-full"
                    f"/{symbol}?timeseries=5&apikey={FMP_KEY}",
                    headers={"Accept": "application/json"}, timeout=6)
                if r.status_code == 200:
                    hist = r.json().get("historical", [])
                    if hist:
                        yield_val = float(hist[0].get("close", 0))
                        if 3.0 <= yield_val <= 15.0:
                            return yield_val / 100.0, f"FMP:{symbol}"
            except Exception as e:
                logger.warning("FMP RFR fetch failed for %s: %s", symbol, e)
    # Limited yfinance fallbacks — only symbols that can plausibly be bond yields
    for yf_sym in ["^IGB", "IN=F"]:
        try:
            bond = yf.Ticker(yf_sym)
            hist = bond.history(period="5d")
            if not hist.empty:
                val = float(hist["Close"].iloc[-1])
                if 3.0 <= val <= 15.0:
                    return val / 100.0, f"yfinance:{yf_sym}"
        except Exception as e:
            logger.warning("yfinance RFR fallback failed for %s: %s", yf_sym, e)
    # Static default approximating recent Indian 10Y G-Sec neighbourhood (not repo rate)
    return 0.065, "fallback_static_6.5pct"

# ============================================================
# 3. SECTOR DETECTION & NORMALIZATION PROFILES
# ============================================================
FINANCIAL_SECTOR_KEYWORDS = [
    "financial services", "bank", "nbfc", "insurance", "capital markets",
    "credit services", "diversified financials", "asset management",
    "mortgage finance", "consumer finance", "shadow banking",
]
CAPEX_INTENSIVE_KEYWORDS = [
    "industrial", "engineering", "infrastructure", "construction", "capital goods",
    "electrical equipment", "machinery", "railroad", "defense", "aerospace",
    "building products", "specialty industrial"
]
MATERIALS_KEYWORDS = ["steel", "metals", "mining", "materials", "chemicals", "cement", "iron", "pipes", "tubes"]
CYCLICAL_KEYWORDS = ["auto", "automobile", "tire", "tyre"]

