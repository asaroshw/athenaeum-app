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

try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except ImportError:
    HAS_ARIMA = False

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

FINANCIAL_SECTOR_KEYWORDS = [
    "financial services", "bank", "nbfc", "insurance", "capital markets",
    "credit services", "diversified financials", "asset management", "mortgage finance"
]
CAPEX_INTENSIVE_KEYWORDS = [
    "industrial", "engineering", "infrastructure", "construction", "capital goods",
    "electrical equipment", "machinery", "railroad", "defense", "aerospace"
]
CYCLICAL_KEYWORDS = ["auto", "automobile", "tire", "tyre"]

STANDARD_REVENUE_KEYS = ['Total Revenue', 'Operating Revenue']
BANK_REVENUE_KEYS = ['Total Revenue', 'Total Operating Income', 'Interest Income', 'Total Interest Income', 'Operating Revenue']
INTEREST_INCOME_KEYS = ['Interest Income', 'Total Interest Income']

CATALYST_KEYWORDS = ['acqui', 'profit', 'surge', 'turnaround', 'wins', ' win ', 'order book',
                      'expansion', 'partnership', 'record revenue', 'upgrade', 'beat estimates',
                      'demerger', 'stake sale', 'contract']
RISK_KEYWORDS = ['fraud', 'resign', 'default', 'probe', 'raid', 'downgrade', 'scam',
                  'investigation', 'lawsuit', 'bankruptcy', 'insolvency', 'delisting']
ORDER_BOOK_KEYWORDS = ['order book', 'order win', 'wins order', 'contract win', 'crore order',
                        'export order', 'multi-year contract', 'l1 bidder', 'lowest bidder',
                        'capex expansion', 'capacity expansion', 'new plant', 'guidance']
GROWTH_PCT_PATTERN = re.compile(r'(\d{1,2})\s*%\s*(?:growth|guidance)|(?:growth|guidance).{0,25}?(\d{1,2})\s*%', re.IGNORECASE)

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
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=6)
        if res.status_code == 200:
            root = ET.fromstring(res.content)
            headlines = []
            for item in root.findall('.//item')[:6]:
                title = item.find('title')
                link = item.find('link')
                if title is not None and link is not None and title.text and link.text:
                    headlines.append({'title': title.text, 'link': link.text})
            return headlines
    except: pass
    return []

def scan_news_sentiment(recent_news, business_summary):
    titles = [n.get('title', '') for n in (recent_news or [])]
    text = ((business_summary or "") + " " + " ".join(titles)).lower()
    catalyst_hits = sorted(set(kw.strip() for kw in CATALYST_KEYWORDS if kw in text))
    risk_hits = sorted(set(kw.strip() for kw in RISK_KEYWORDS if kw in text))
    bonus, notes = 0, []
    if len(catalyst_hits) >= 2:
        bonus += 15
        notes.append(f"Qualitative bonus (+15): multiple positive catalysts detected ({', '.join(catalyst_hits[:4])}).")
    elif len(catalyst_hits) == 1:
        bonus += 10
        notes.append(f"Qualitative bonus (+10): positive catalyst detected ({catalyst_hits[0]}).")
    if risk_hits:
        bonus -= 20
        notes.append(f"Qualitative penalty (-20): risk keyword(s) detected ({', '.join(risk_hits[:3])}).")
    return bonus, notes

def extract_order_book_signal(recent_news, business_summary):
    titles = [n.get('title', '') for n in (recent_news or [])]
    text = (business_summary or "") + " " + " ".join(titles)
    order_hits = sorted(set(kw for kw in ORDER_BOOK_KEYWORDS if kw in text.lower()))
    growth_pct_found = None
    match = GROWTH_PCT_PATTERN.search(text)
    if match:
        val = match.group(1) or match.group(2)
        try:
            v = float(val)
            if 5 <= v <= 50: growth_pct_found = v
        except: pass
    return order_hits, growth_pct_found

def valuation_checks(m):
    pe, peg, pat_yoy, pb, ev_ebitda = to_float(m.get('pe_ratio')), to_float(m.get('peg_ratio')), to_float(m.get('pat_yoy')), to_float(m.get('pb_ratio')), to_float(m.get('ev_ebitda'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if pe is not None:
        if pe < 0: checks.append(("Profitable on a P/E basis", False, f"P/E is negative ({pe}x)."))
        else:
            thresh = 45 if (pat_yoy is not None and pat_yoy > 30) else 25
            checks.append((f"Reasonable P/E (<{thresh}x)", bool(pe < thresh), f"Trailing P/E of {pe}x"))
    if peg is not None and peg > 0:
        checks.append(("Attractive PEG (<1.5)", bool(peg < 1.5), f"PEG ratio of {peg}"))
    if pb is not None:
        thresh = 3.0 if is_fin else 5.0
        checks.append((f"Reasonable P/B (<{thresh:g}x)", bool(0 < pb < thresh), f"Price-to-Book of {pb}x"))
    if is_fin and pb is not None and m.get('justified_pb'):
        jpb = m['justified_pb']
        checks.append(("P/B vs Excess-ROE Justified P/B", bool(pb < jpb), f"Actual P/B {pb}x vs justified {jpb}x"))
    if not is_fin and ev_ebitda is not None:
        if ev_ebitda < 0: checks.append(("Positive EV/EBITDA", False, f"EV/EBITDA is negative ({ev_ebitda}x)."))
        else: checks.append(("Reasonable EV/EBITDA (<15x)", bool(ev_ebitda < 15), f"EV/EBITDA of {ev_ebitda}x"))
    return checks

def past_performance_checks(m):
    yoy, qoq, roe, margin = to_float(m.get('pat_yoy')), to_float(m.get('pat_qoq')), to_float(m.get('roe')), to_float(m.get('net_margin'))
    checks = []
    if yoy is not None: checks.append(("Positive Earnings Growth (YoY)", bool(yoy > 0), f"PAT YoY growth of {yoy}%"))
    if yoy is not None and qoq is not None: checks.append(("Accelerating Growth", bool(qoq > yoy), "Comparing recent quarter growth to yearly figure"))
    if roe is not None: checks.append(("Strong Return on Equity (>15%)", bool(roe > 15), f"ROE of {roe}%"))
    if margin is not None: checks.append(("Healthy Net Margin (>10%)", bool(margin > 10), f"Net margin of {margin}%"))
    return checks

def financial_health_checks(m):
    de, ic = to_float(m.get('debt_to_equity')), to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    checks = []
    if de is not None:
        if de < 0: checks.append(("Positive Shareholder Equity", False, f"Debt-to-equity is negative ({de})."))
        else:
            threshold = 10.0 if is_fin else 1.0
            checks.append((f"Leverage Control (D/E < {threshold:g}x)", bool(de < threshold), f"Debt-to-equity of {de}"))
    if ic is not None: checks.append(("Comfortable Interest Coverage (>3x)", bool(ic > 3), f"Interest coverage of {ic}x"))
    return checks

def dividend_checks(m):
    dy_str = str(m.get('dividend_yield', ''))
    if "doesn't pay" in dy_str.lower() or dy_str == "None":
        return [("Notable Dividend (>1.5%)", False, "Stock doesn't pay dividends")]
    dy = to_float(dy_str)
    return [("Notable Dividend (>1.5%)", bool(dy is not None and dy > 1.5), f"Dividend yield: {m.get('dividend_yield')}")]

def score_from_checks(checks):
    vals = [c[1] for c in checks if c[1] is not None]
    return round(100 * sum(vals) / len(vals)) if vals else 50

def compute_fundamental_score(val_score, past_score, health_score, is_financial):
    weights = {"val": 0.45, "past": 0.35, "health": 0.20} if is_financial else {"val": 0.35, "past": 0.35, "health": 0.30}
    scores = {"val": val_score, "past": past_score, "health": health_score}
    available = {k: v for k, v in scores.items() if v is not None}
    if not available: return 50.0
    total_w = sum(weights[k] for k in available)
    return round(sum(weights[k] * v for k, v in available.items()) / total_w, 1)

def calculate_vwap_support(df):
    d = df.dropna(subset=['Close', 'Volume'])
    if d.empty: return None
    d = d.copy()
    d['PriceBin'] = pd.cut(d['Close'], bins=20)
    vol_by_bin = d.groupby('PriceBin', observed=True)['Volume'].sum()
    if vol_by_bin.empty: return None
    return vol_by_bin.idxmax().mid

def calculate_atr(df, period=14):
    if df is None or len(df) <= period: return None
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    val = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else None

def justified_pb_fair_value(roe_pct, ke_pct, growth_pct, book_value_per_share, pb_floor=0.4, pb_cap=8.0):
    if not book_value_per_share or book_value_per_share <= 0 or roe_pct is None: return None, None
    roe, ke, g = roe_pct / 100, ke_pct / 100, growth_pct / 100
    if ke <= g: g = ke - 0.02
    jpb = 1 + (roe - ke) / (ke - g)
    jpb = min(max(jpb, pb_floor), pb_cap)
    return round(jpb, 2), round(jpb * book_value_per_share, 2)

def ddm_fair_value(dividend_per_share, ke_pct, growth_pct):
    if not dividend_per_share or dividend_per_share <= 0: return None
    ke, g = ke_pct / 100, growth_pct / 100
    if ke <= g: g = ke - 0.02
    return round((dividend_per_share * (1 + g)) / (ke - g), 2)

def composite_verdict(fundamental_score, margin_of_safety, drift, arima_direction=None, forced_intrinsic_adjustment=0, qualitative_bonus=0):
    W_FUNDAMENTAL, W_INTRINSIC, W_TECHNICAL = 0.40, 0.35, 0.25
    intrinsic_score = min(max(50 + margin_of_safety * 150, 0), 100)
    intrinsic_score = min(max(intrinsic_score + forced_intrinsic_adjustment, 0), 100)
    tech_score = min(max(50 + (drift or 0) * 100, 0), 100)
    if arima_direction == "UP": tech_score = min(100, tech_score + 10)
    elif arima_direction == "DOWN": tech_score = max(0, tech_score - 10)
    composite = W_FUNDAMENTAL * fundamental_score + W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score
    composite = min(max(composite + qualitative_bonus, 0), 100)
    if composite >= 75: verdict = "STRONG BUY"
    elif composite >= 60: verdict = "BUY"
    elif composite >= 40: verdict = "OBSERVE"
    else: verdict = "DON'T BUY"
    return round(composite, 1), verdict, round(intrinsic_score, 1), round(tech_score, 1)

VERDICT_RANK = {"DON'T BUY": 0, "OBSERVE": 1, "BUY": 2, "STRONG BUY": 3}

def apply_tiered_sanity_veto(verdict, target_price, current_price, notes):
    if target_price is None or not current_price: return verdict
    downside_pct = (current_price - target_price) / current_price
    upside_pct = (target_price - current_price) / current_price
    if downside_pct > 0.15:
        if verdict != "DON'T BUY": notes.append("Forced to DON'T BUY due to extreme modeled downside.")
        return "DON'T BUY"
    elif downside_pct > 0:
        if VERDICT_RANK.get(verdict, 1) > VERDICT_RANK["OBSERVE"]:
            notes.append("Downgraded to OBSERVE due to negative margin of safety.")
            return "OBSERVE"
    if upside_pct > 1.50:
        if verdict in ["BUY", "STRONG BUY"]:
            notes.append("Forced to DON'T BUY: Micro-cap upside hallucination override.")
            return "DON'T BUY"
    return verdict

def run_predictive_pipeline(info, hist, fcf_history, sector, industry, fundamental_score,
                              book_value_per_share, dividend_per_share, roe_pct, pat_yoy_pct,
                              precomputed_jpb=None, precomputed_ddm=None, resolved_pe=None,
                              is_turnaround=False, latest_quarter_net_income=None,
                              shares_outstanding=None, qualitative_bonus=0, qualitative_notes=None,
                              sector_profile="standard", order_book_hits=None, growth_pct_from_news=None):
    current_price = info.get('currentPrice')
    if not current_price and hist is not None and not hist.empty:
        current_price = float(hist['Close'].iloc[-1])

    result = {
        "verdict": "OBSERVE", "target_price": None, "entry_range": "N/A", "stop_loss": None,
        "time_horizon": "N/A", "note": None, "model_used": "N/A", "composite_score": None,
        "fundamental_score": fundamental_score, "intrinsic_score": None, "technical_score": None,
        "margin_of_safety": None, "discount_rate": None, "growth_used": None, "is_turnaround": is_turnaround,
    }
    if not current_price: return result

    notes = list(qualitative_notes or [])
    beta = info.get('beta') if (info.get('beta') and pd.notna(info.get('beta')) and info.get('beta') > 0) else 1.0
    ke_pct = min(max((RISK_FREE_RATE + beta * EQUITY_RISK_PREMIUM) * 100, 9), 20)

    growth_pct = 8.0
    if pat_yoy_pct and pat_yoy_pct > 0: growth_pct = min(max(pat_yoy_pct, 5), 20)
    if is_turnaround: growth_pct = max(growth_pct, 20.0)

    financial = is_financial_sector(sector, industry)
    forced_intrinsic_adjustment = 0

    if financial:
        jpb_ratio, jpb_value = (precomputed_jpb if precomputed_jpb is not None else justified_pb_fair_value(roe_pct, ke_pct, growth_pct, book_value_per_share))
        ddm_val = precomputed_ddm if precomputed_ddm is not None else ddm_fair_value(dividend_per_share, ke_pct, growth_pct)
        if jpb_value and ddm_val:
            intrinsic_value = (jpb_value + ddm_val) / 2
            result["model_used"] = "Blended: Excess-ROE + DDM"
        elif jpb_value:
            intrinsic_value = jpb_value
            result["model_used"] = "Excess Return on Equity"
        else:
            intrinsic_value = current_price
            forced_intrinsic_adjustment = -30
            result["model_used"] = "No valid inputs"
    else:
        avg_fcf = float(fcf_history.mean()) if (fcf_history is not None and len(fcf_history) > 0) else (info.get('netIncomeToCommon') or 0)
        shares = info.get('sharesOutstanding') or shares_outstanding
        fcf_per_share = (avg_fcf / shares) if (avg_fcf and shares and shares > 0) else 0

        if fcf_per_share > 0:
            g = growth_pct if growth_pct > TERMINAL_GROWTH_PCT else TERMINAL_GROWTH_PCT + 2
            discount_rate, g_frac, tg_frac = ke_pct / 100, g / 100, TERMINAL_GROWTH_PCT / 100
            pv_fcf = sum(fcf_per_share * (1 + g_frac) ** t / (1 + discount_rate) ** t for t in range(1, 6))
            fcf5 = fcf_per_share * (1 + g_frac) ** 5
            terminal_value = (fcf5 * (1 + tg_frac)) / (discount_rate - tg_frac)
            intrinsic_value = pv_fcf + terminal_value / (1 + discount_rate) ** 5
            result["model_used"] = "2-Stage DCF (Free Cash Flow)"
        else:
            trailing_eps = info.get('trailingEps')
            if trailing_eps and trailing_eps > 0:
                intrinsic_value = round(trailing_eps * min(resolved_pe or 20, 35), 2)
                result["model_used"] = "Target P/E (defensive)"
            elif book_value_per_share and book_value_per_share > 0:
                intrinsic_value = round(book_value_per_share * 0.8, 2)
                result["model_used"] = "Book Value Haircut"
            else:
                intrinsic_value = current_price
                forced_intrinsic_adjustment = -35
                result["model_used"] = "Insufficient data"

    target_price = round(intrinsic_value, 2)
    margin_of_safety = (intrinsic_value - current_price) / current_price if current_price else 0

    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist) or (current_price * 0.92)
    entry_low, entry_high = round(support, 2), round(support + (0.5 * atr if atr else current_price * 0.02), 2)
    stop_loss = round(max(current_price * 0.5, entry_low - (1.5 * atr if atr else entry_low * 0.05)), 2)

    momentum, horizon, drift = "NEUTRAL", "3-5 Years", None
    try:
        closes_clean = hist['Close'].dropna()
        if len(closes_clean) > 30 and current_price:
            slope, _ = np.polyfit(np.arange(len(closes_clean)), closes_clean.values / current_price, 1)
            drift = slope * 252
            if slope > 0.0005: momentum, horizon = "UP", "12-18 Months (Accelerated)"
            elif slope < -0.0005: momentum = "DOWN"
            if HAS_ARIMA and len(closes_clean) > 100:
                fitted = ARIMA(closes_clean.values, order=(5, 1, 0)).fit()
                forecast = fitted.forecast(steps=30)
                momentum = "UP" if forecast[-1] > forecast[0] else "DOWN"
    except: pass

    composite, verdict, intrinsic_score, tech_score = composite_verdict(
        fundamental_score, margin_of_safety, drift, arima_direction=momentum,
        forced_intrinsic_adjustment=forced_intrinsic_adjustment, qualitative_bonus=qualitative_bonus,
    )
    verdict = apply_tiered_sanity_veto(verdict, target_price, current_price, notes)

    result.update({
        "verdict": verdict, "target_price": target_price,
        "entry_range": f"₹{entry_low:,.2f} - ₹{entry_high:,.2f}", "stop_loss": stop_loss,
        "time_horizon": horizon, "note": " ".join(notes) if notes else None,
        "composite_score": composite, "intrinsic_score": intrinsic_score, "technical_score": tech_score,
        "margin_of_safety": round(margin_of_safety * 100, 1), "discount_rate": round(ke_pct, 1), "growth_used": round(growth_pct, 1),
    })
    return result

def fetch_stock_data(resolved_ticker, raw_input):
    stock = yf.Ticker(resolved_ticker)
    hist_full = stock.history(period="1y")
    if hist_full.empty: raise ValueError(f"Could not find '{raw_input}'.")

    info = stock.info
    current_price = info.get("currentPrice", round(float(hist_full['Close'].iloc[-1]), 2))
    sector, industry = info.get("sector", "N/A"), info.get("industry", "N/A")
    is_fin = is_financial_sector(sector, industry)
    sector_profile = classify_sector_profile(sector, industry)
    revenue_keys = BANK_REVENUE_KEYS if is_fin else STANDARD_REVENUE_KEYS

    pnl_df, bs_df = [], []
    net_inc, total_eq, total_assets_latest, ebitda_val = None, None, None, info.get('ebitda')
    revenue_latest, ebit_latest, interest_exp_latest = None, None, None
    fcf_history = None
    pat_qoq, pat_yoy_pct, net_margin_final = None, None, None
    latest_quarter_net_income = None
    revenue_cagr_pct = None

    try:
        q_fin = stock.quarterly_financials
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            ni_series = q_fin.loc['Net Income'].dropna()
            if len(ni_series) > 0:
                net_inc = float(ni_series.iloc[:4].sum())
                latest_quarter_net_income = float(ni_series.iloc[0])
            if len(ni_series) >= 2 and ni_series.iloc[1] != 0:
                pat_qoq = round(((ni_series.iloc[0] - ni_series.iloc[1]) / abs(ni_series.iloc[1])) * 100, 2)
            if len(ni_series) >= 5 and ni_series.iloc[4] != 0:
                pat_yoy_pct = round(((ni_series.iloc[0] - ni_series.iloc[4]) / abs(ni_series.iloc[4])) * 100, 2)
            rev_key_found = next((k for k in revenue_keys if k in q_fin.index), None)
            if rev_key_found and len(ni_series) > 0:
                rev_series = q_fin.loc[rev_key_found].dropna()
                if len(rev_series) > 0 and rev_series.iloc[0] != 0:
                    net_margin_final = round((ni_series.iloc[0] / rev_series.iloc[0]) * 100, 2)

        fin = stock.financials
        if fin is not None and not fin.empty:
            rev_key_found = next((k for k in revenue_keys if k in fin.index), None)
            if rev_key_found and pd.notna(fin.loc[rev_key_found].iloc[0]):
                revenue_latest = float(fin.loc[rev_key_found].iloc[0])
                rev_series_annual = fin.loc[rev_key_found].dropna()
                if len(rev_series_annual) >= 2 and rev_series_annual.iloc[-1] > 0:
                    revenue_cagr_pct = round((((rev_series_annual.iloc[0] / rev_series_annual.iloc[-1]) ** (1 / (len(rev_series_annual)-1))) - 1) * 100, 2)
            for k in ['EBIT', 'Operating Income']:
                if k in fin.index and pd.notna(fin.loc[k].iloc[0]): ebit_latest = float(fin.loc[k].iloc[0]); break
            if 'Interest Expense' in fin.index:
                ie_series = fin.loc['Interest Expense'].dropna()
                if len(ie_series) > 0: interest_exp_latest = float(ie_series.iloc[0])

        bs = stock.balance_sheet
        if bs is not None and not bs.empty:
            for k in ['Stockholders Equity', 'Total Stockholder Equity', 'Common Stock Equity']:
                if k in bs.index:
                    eq_series = bs.loc[k].dropna()
                    if len(eq_series) > 0: total_eq = float(eq_series.iloc[0]); break

        cf = stock.cashflow
        if cf is not None and not cf.empty and 'Free Cash Flow' in cf.index:
            fcf_history = cf.loc['Free Cash Flow'].dropna()

        if fin is not None and not fin.empty:
            col = fin.columns[0]
            rev_key_found = next((k for k in revenue_keys if k in fin.index), None)
            pnl_df = [
                {"Particulars": "Net Sales / Total Income", "Amount (₹ Cr)": round(fin.loc[rev_key_found, col] / 10000000, 2) if rev_key_found else "—"},
                {"Particulars": "Operating Profit", "Amount (₹ Cr)": round(fin.loc['Operating Income', col] / 10000000, 2) if 'Operating Income' in fin.index else "—"},
                {"Particulars": "Net Profit", "Amount (₹ Cr)": round(fin.loc['Net Income', col] / 10000000, 2) if 'Net Income' in fin.index else "—"}
            ]
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            bs_df = [
                {"Particulars": "Total Equity", "Amount (₹ Cr)": round(total_eq / 10000000, 2) if total_eq else "—"},
                {"Particulars": "Total Debt", "Amount (₹ Cr)": round(bs.loc['Total Debt', col] / 10000000, 2) if 'Total Debt' in bs.index else "—"},
                {"Particulars": "Total Assets", "Amount (₹ Cr)": round(bs.loc['Total Assets', col] / 10000000, 2) if 'Total Assets' in bs.index else "—"}
            ]
    except:
        pnl_df, bs_df = [], []

    mcap = info.get("marketCap")
    shares_out = info.get("sharesOutstanding")

    trailing_earnings_negative = (net_inc is not None and net_inc < 0) or (info.get('trailingEps') and info.get('trailingEps') < 0)
    is_turnaround = bool(trailing_earnings_negative and ((pat_qoq is not None and pat_qoq > 50) or (latest_quarter_net_income is not None and latest_quarter_net_income > 0)))

    recent_news = fetch_google_news(f"{info.get('longName', resolved_ticker)} stock news")
    business_summary = info.get("longBusinessSummary")
    qualitative_bonus, qualitative_notes = scan_news_sentiment(recent_news, business_summary)

    pe_raw = info.get("trailingPE")
    if not is_valid_metric(pe_raw) and net_inc and mcap: pe_raw = round(mcap / net_inc, 2)

    pb_raw = info.get("priceToBook")
    if not is_valid_metric(pb_raw) and total_eq and mcap and total_eq > 0: pb_raw = round(mcap / total_eq, 2)

    roe_raw = info.get("returnOnEquity")
    if not is_valid_metric(roe_raw) and net_inc and total_eq and total_eq > 0: roe_raw = net_inc / total_eq
    roe_is_known = is_valid_metric(roe_raw)

    ev_ebitda = "N/A"
    if not is_fin:
        ev_val = info.get("enterpriseValue") or (mcap + (info.get('totalDebt') or 0) - (info.get('totalCash') or 0) if mcap else None)
        if is_valid_metric(ebitda_val) and is_valid_metric(ev_val) and ebitda_val != 0:
            ev_ebitda = round(ev_val / ebitda_val, 2)

    interest_coverage = round(ebit_latest / interest_exp_latest, 2) if (ebit_latest is not None and interest_exp_latest) else "N/A"
    dte_raw = info.get("debtToEquity")
    debt_to_equity = round(dte_raw / 100, 2) if is_valid_metric(dte_raw) else "N/A"

    temp_metrics = {
        'pe_ratio': pe_raw, 'pb_ratio': pb_raw, 'pat_yoy': pat_yoy_pct, 'roe': (roe_raw * 100) if roe_is_known else None,
        'ev_ebitda': ev_ebitda, 'is_financial_sector': is_fin, 'debt_to_equity': debt_to_equity,
        'interest_coverage': interest_coverage, 'net_margin': net_margin_final, 'pat_qoq': pat_qoq,
        'sector_profile': sector_profile,
    }
    v_score = score_from_checks(valuation_checks(temp_metrics))
    p_score = score_from_checks(past_performance_checks(temp_metrics))
    h_score = score_from_checks(financial_health_checks(temp_metrics))
    fundamental_score = compute_fundamental_score(v_score, p_score, h_score, is_fin)
    if is_turnaround: fundamental_score = min(100, fundamental_score + 15)

    bvps = info.get('bookValue') or (total_eq / shares_out if (total_eq and shares_out) else None)
    div_per_share = info.get("dividendRate")

    jpb_ratio, jpb_value = None, None
    if is_fin:
        beta_p = info.get('beta') if (info.get('beta') and pd.notna(info.get('beta')) and info.get('beta') > 0) else 1.0
        ke_p = min(max((RISK_FREE_RATE + beta_p * EQUITY_RISK_PREMIUM) * 100, 9), 20)
        jpb_ratio, jpb_value = justified_pb_fair_value(roe_raw * 100 if roe_is_known else None, ke_p, pat_yoy_pct or 8.0, bvps)
    temp_metrics["justified_pb"] = jpb_ratio

    predictive_data = run_predictive_pipeline(
        info, hist_full, fcf_history, sector, industry, fundamental_score,
        bvps, div_per_share, roe_raw * 100 if roe_is_known else None, pat_yoy_pct,
        precomputed_jpb=(jpb_ratio, jpb_value), resolved_pe=to_float(pe_raw), is_turnaround=is_turnaround,
        latest_quarter_net_income=latest_quarter_net_income, shares_outstanding=shares_out,
        qualitative_bonus=qualitative_bonus, qualitative_notes=qualitative_notes, sector_profile=sector_profile
    )

    shareholding = {
        "Promoters": round((info.get("heldPercentInsiders") or 0) * 100, 1),
        "Institutions": round((info.get("heldPercentInstitutions") or 0) * 100, 1),
        "Public": round(max(0, 100 - (((info.get("heldPercentInsiders") or 0) + (info.get("heldPercentInstitutions") or 0)) * 100)), 1)
    }

    return {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw if is_valid_metric(pe_raw) else "N/A",
        "pb_ratio": pb_raw if is_valid_metric(pb_raw) else "N/A",
        "roe": f"{round(roe_raw*100, 2)}%" if roe_is_known else "N/A",
        "debt_to_equity": debt_to_equity, "interest_coverage": interest_coverage,
        "market_cap": mcap, "sector": sector, "industry": industry,
        "business_summary": business_summary, "recent_news": recent_news,
        "shareholding": shareholding, "history": [{"Date": str(d.date()), "Close": float(c)} for d, c in zip(hist_full.index, hist_full['Close'])],
        "pnl_df": pnl_df, "bs_df": bs_df,
        "val_checks": valuation_checks(temp_metrics),
        "past_checks": past_performance_checks(temp_metrics),
        "health_checks": financial_health_checks(temp_metrics),
        "div_checks": dividend_checks(temp_metrics),
        "predictive": predictive_data, "currency": "₹"
    }

def generate_comprehensive_report(metrics, ticker):
    client = genai.Client(api_key=GEMINI_KEY)
    sys = """You are a Senior Equity Analyst acting as the final synthesis layer over a quantitative model.
Output exactly 8 numbered sections starting with:
1. VALUATION & FAIR VALUE
2. FUTURE GROWTH & OUTLOOK
3. PAST PERFORMANCE & EARNINGS QUALITY
4. FINANCIAL HEALTH & BALANCE SHEET
5. DIVIDEND & CAPITAL ALLOCATION
6. MANAGEMENT & COMPENSATION
7. OWNERSHIP STRUCTURE & INSIDER SENTIMENT
8. NARRATIVE VERDICT
Provide thorough, professional analysis for each section."""
    pred = metrics.get('predictive', {})
    news_titles = "; ".join([n['title'] for n in (metrics.get('recent_news') or [])[:5]]) or "No recent headlines."
    pmt = (f"Target: {metrics['name']} ({ticker}). Sector: {metrics.get('sector')}. "
           f"Price: {metrics['price']}. P/E: {metrics['pe_ratio']}. P/B: {metrics['pb_ratio']}. "
           f"Debt/Eq: {metrics['debt_to_equity']}. Model: {pred.get('model_used')}. "
           f"Target Price: ₹{pred.get('target_price')}. Verdict: {pred.get('verdict')}. Headlines: {news_titles}")
    return client.models.generate_content(model='gemini-3.5-flash-lite', contents=pmt, config=types.GenerateContentConfig(system_instruction=sys, temperature=0.2)).text

@app.get("/")
def root(): return {"status": "Athenaeum API is fully live"}

@app.get("/api/analyze")
def analyze(ticker: str):
    try:
        resolved = resolve_name_to_ticker(ticker)
        metrics = fetch_stock_data(resolved, ticker)
        ai_text = generate_comprehensive_report(metrics, resolved)
        return {"status": "success", "metrics": metrics, "ai_narrative": ai_text}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
