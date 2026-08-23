"""Equity market data: FMP primary, yfinance fallback, news, RFR."""
from __future__ import annotations
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from athenaeum.utils.helpers import (
    to_float, is_valid_metric, make_metric, _rfr_value, _rfr_source, safe_pct_change,
)
from athenaeum.models.sector import is_financial_sector, classify_sector_profile
from athenaeum.analysis.sentiment import scan_news_sentiment, extract_order_book_signal
from athenaeum.models.fundamentals import (
    valuation_checks, past_performance_checks, financial_health_checks, dividend_checks,
    continuous_valuation_score, continuous_past_score, continuous_health_score,
    score_from_checks, compute_fundamental_score,
)
from athenaeum.models.valuation import justified_pb_fair_value, ddm_fair_value
from athenaeum.models.pipeline import run_predictive_pipeline
from athenaeum.data.rfr import get_dynamic_risk_free_rate
from athenaeum.config import (
    STANDARD_REVENUE_KEYS, BANK_REVENUE_KEYS, INTEREST_INCOME_KEYS,
    EQUITY_RISK_PREMIUM,
)

logger = logging.getLogger("athenaeum")

SECTOR_PEERS = {
    "financial": ["BAJFINANCE.NS", "CHOLAFIN.NS", "SHRIRAMFIN.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS"],
    "capex_intensive": ["LT.NS", "HAL.NS", "BEL.NS", "SIEMENS.NS", "ABB.NS", "CUMMINSIND.NS"],
    "cyclical": ["BOSCHLTD.NS", "MOTHERSON.NS", "UNOMINDA.NS", "MRF.NS", "TATAMOTORS.NS", "M&M.NS"],
    "materials": ["TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "VEDL.NS", "ULTRACEMCO.NS"],
    "standard": ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HUL.NS", "ITC.NS"] 
}

def resolve_name_to_ticker(stock_input):
    stock_str = str(stock_input).strip()
    if stock_str.isdigit():
        return stock_str + '.BO'
    try:
        res = requests.get(
            f"https://query2.finance.yahoo.com/v1/finance/search?q={stock_str}",
            headers={'User-Agent': 'Mozilla/5.0'}, timeout=5)
        if res.status_code == 200:
            for q in res.json().get('quotes', []):
                sym = q.get('symbol', '').upper()
                if sym.endswith('.NS') or sym.endswith('.BO'):
                    return sym
    except Exception as e:
        logger.warning("Ticker resolve failed for %s: %s", stock_input, e)
    upper = stock_str.upper().replace(" ", "")
    return upper if upper.endswith(('.NS', '.BO')) else upper + '.NS'


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
    except Exception as e:
        logger.warning("Google News fetch failed for %s: %s", query_term, e)
    return []

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fmp_data(ticker_clean: str) -> dict:
    FMP_KEY = st.secrets.get("FMP_API_KEY", "")
    if not FMP_KEY:
        return {}
    BASE = "https://financialmodelingprep.com/api/v3"
    out = {}
    headers = {"Accept": "application/json"}
    def _get(path):
        try:
            r = requests.get(f"{BASE}/{path}&apikey={FMP_KEY}", headers=headers, timeout=7)
            if r.status_code != 200:
                logger.debug("FMP endpoint non-200 (%s) for %s: %s", r.status_code, ticker_clean, path.split("?")[0])
                return None
            return r.json()
        except Exception as e:
            logger.debug("FMP endpoint failed for %s (%s): %s", ticker_clean, path.split("?")[0], e)
            return None

    # PERFORMANCE UPGRADE (Phase 1): these 7 endpoints are independent HTTP calls — dispatch
    # them concurrently instead of one-at-a-time. Result PROCESSING below stays in the exact
    # original sequential order (q -> p -> km -> inc -> bs -> ae -> hist), which matters
    # because the `bs` block reads out["sharesOutstanding"], set while processing `q`. Only
    # the network *dispatch* moved earlier; what's computed and the order it's merged in is
    # byte-for-byte the same as before.
    paths = {
        "q": f"quote/{ticker_clean}?",
        "p": f"profile/{ticker_clean}?",
        "km": f"key-metrics-ttm/{ticker_clean}?",
        "inc": f"income-statement/{ticker_clean}?limit=4",
        "bs": f"balance-sheet-statement/{ticker_clean}?limit=1",
        "ae": f"analyst-estimates/{ticker_clean}?limit=2",
        "hist": f"historical-price-full/{ticker_clean}?timeseries=252",
    }
    with ThreadPoolExecutor(max_workers=len(paths)) as executor:
        futures = {key: executor.submit(_get, path) for key, path in paths.items()}
        r = {key: fut.result() for key, fut in futures.items()}
    q, p, km, inc, bs, ae, hist = r["q"], r["p"], r["km"], r["inc"], r["bs"], r["ae"], r["hist"]

    if q and isinstance(q, list) and q:
        out.update({k: q[0].get(v) for k, v in [
            ("currentPrice","price"),("marketCap","marketCap"),
            ("sharesOutstanding","sharesOutstanding"),("pe_ratio","pe"),
            ("eps","eps"),("fiftyTwoWeekHigh","yearHigh"),
            ("fiftyTwoWeekLow","yearLow")]})

    if p and isinstance(p, list) and p:
        out.update({k: p[0].get(v) for k, v in [
            ("longName","companyName"),("sector","sector"),("industry","industry"),
            ("longBusinessSummary","description"),("website","website"),("beta","beta")]})

    if km and isinstance(km, list) and km:
        out.update({k: km[0].get(v) for k, v in [
            ("returnOnEquity","roeTTM"),("returnOnAssets","returnOnTangibleAssetsTTM"),
            ("debtToEquity","debtToEquityTTM"),("currentRatio","currentRatioTTM"),
            ("ev_ebitda","enterpriseValueOverEBITDATTM"),("priceToBook","pbRatioTTM"),
            ("dividendYieldTTM","dividendYieldTTM")]})

    if inc and isinstance(inc, list) and len(inc) > 0:
        l = inc[0]
        out.update({k: l.get(v) for k, v in [
            ("totalRevenue","revenue"),("ebit","operatingIncome"),
            ("netIncome","netIncome"),("ebitda","ebitda"),
            ("eps","eps"),("interestExpense","interestExpense")]})
        if len(inc) >= 2:
            ni_now = l.get("netIncome")
            ni_p_raw = inc[1].get("netIncome")
            pat_yoy = safe_pct_change(ni_now, ni_p_raw)
            if pat_yoy is not None:
                out["pat_yoy"] = round(pat_yoy, 2)
                if len(inc) >= 3:
                    ni_p2_raw = inc[2].get("netIncome")
                    # Previously `inc[2].get("netIncome") or 1` with no zero
                    # guard at all — a genuinely zero two-periods-back net
                    # income silently substituted a fake ₹1 divisor instead of
                    # correctly skipping the metric. safe_pct_change() handles
                    # this the same way pat_yoy above does.
                    pat_yoy_prior = safe_pct_change(ni_p_raw, ni_p2_raw)
                    if pat_yoy_prior is not None:
                        out["pat_yoy_prior"] = round(pat_yoy_prior, 2)

    if bs and isinstance(bs, list) and bs:
        b = bs[0]
        out.update({k: b.get(v) for k, v in [
            ("totalDebt","totalDebt"),("totalCash","cashAndCashEquivalents"),
            ("totalEquity","totalStockholdersEquity"),("totalAssets","totalAssets")]})
        if out.get("sharesOutstanding") and b.get("totalStockholdersEquity"):
            out["bookValue"] = b["totalStockholdersEquity"] / out["sharesOutstanding"]

    if ae and isinstance(ae, list) and len(ae) > 0:
        out["forwardEps"] = ae[0].get("estimatedEpsAvg")
        if len(ae) >= 2:
            # Was `ae[1]["estimatedEpsAvg"] or 1` — same zero-base fix as pat_yoy above.
            analyst_growth = safe_pct_change(ae[0].get("estimatedEpsAvg"), ae[1].get("estimatedEpsAvg"))
            if analyst_growth is not None:
                out["analyst_growth_pct"] = round(analyst_growth, 2)

    if hist and hist.get("historical"):
        hdf = pd.DataFrame(hist["historical"])
        hdf["date"] = pd.to_datetime(hdf["date"])
        hdf = hdf.sort_values("date").rename(columns={
            "date":"Date","open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        out["fmp_history"] = hdf[["Date","Open","High","Low","Close","Volume"]]
    return out


def _fmp_ticker(resolved_ticker: str) -> str:
    return resolved_ticker.replace(".NS","").replace(".BO","").upper()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_stock_data(resolved_ticker, raw_input, scan_for_alternative=True):
    warnings = []

    # PERFORMANCE UPGRADE (Phase 1): fmp + every independent yfinance property this
    # function needs are dispatched concurrently instead of one call waiting on the
    # last before starting. Each yfinance call gets its OWN Ticker instance rather
    # than sharing one across threads, since yfinance's lazy-loaded internal caches
    # aren't documented as thread-safe for concurrent access on the same object.
    # Every value is still consumed via .result() at its exact original call site,
    # inside its exact original try/except — an exception raised in a worker thread
    # is re-raised by .result() and caught there exactly as a direct property-access
    # failure would be. What's computed, and the order it's merged, is unchanged;
    # only when the network requests fire has moved earlier.
    _executor = ThreadPoolExecutor(max_workers=9)
    fmp_future = _executor.submit(fetch_fmp_data, _fmp_ticker(resolved_ticker))
    info_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).info)
    hist_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).history(period="1y"))
    qf_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).quarterly_financials)
    fin_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).financials)
    bal_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).balance_sheet)
    cf_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).cashflow)
    mf_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).mutualfund_holders)
    cal_future = _executor.submit(lambda: yf.Ticker(resolved_ticker).calendar)
    _executor.shutdown(wait=False)  # queued work still runs; .result() below still blocks per-future as needed

    fmp = fmp_future.result()

    # --- STRICT ISOLATION LOGIC ---
    if fmp and fmp.get("currentPrice") is not None:
        info = fmp.copy()
        data_source = "FMP"
    else:
        warnings.append("FMP quote unavailable — falling back entirely to yfinance.")
        try:
            info = info_future.result() or {}
        except Exception as e:
            logger.warning("yfinance info failed for %s: %s", resolved_ticker, e)
            info = {}
            warnings.append("yfinance company info failed.")
        data_source = "yfinance"

    # Scrub NaN values safely
    for k, v in list(info.items()):
        if isinstance(v, (int, float)) and pd.isna(v):
            info[k] = None
        elif isinstance(v, str) and v.lower() == "nan":
            info[k] = None

    # Price History
    if fmp.get("fmp_history") is not None and not fmp["fmp_history"].empty:
        hist_full = fmp["fmp_history"].copy().reset_index(drop=True)
    else:
        try:
            hist_full = hist_future.result()
        except Exception as e:
            logger.warning("yfinance history failed for %s: %s", resolved_ticker, e)
            hist_full = pd.DataFrame()
            warnings.append(f"Price history unavailable: {e}")
            
    if hist_full.empty:
        raise ValueError(f"Could not find '{raw_input}'.")

    # --- BULLETPROOF PRICE EXTRACTION ---
    fallback_price = None
    if not hist_full.empty:
        valid_closes = hist_full['Close'].dropna()
        if not valid_closes.empty:
            fallback_price = round(float(valid_closes.iloc[-1]), 2)
            
    raw_price = info.get("currentPrice") if data_source == "FMP" else (info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"))
    
    if raw_price is None or pd.isna(raw_price) or str(raw_price).lower() == "nan":
        current_price = fallback_price
    else:
        current_price = round(float(raw_price), 2)
        
    # --- ADD THIS LINE TO FORCE THE PIPELINE TO USE THE CLEAN PRICE ---
    info['currentPrice'] = current_price
    
    currency_symbol = "₹"
    
    # --- KEY UNIFICATION FOR PIPELINE ---
    # Map differing keys so pipeline.py receives the exact standard variables it expects
    if data_source == "FMP":
        info["trailingEps"] = info.get("eps")
        info["trailingPE"] = info.get("pe_ratio")
        if info.get("dividendYieldTTM") and current_price:
            info["dividendRate"] = float(info["dividendYieldTTM"]) * current_price
    else:
        info["eps"] = info.get("trailingEps")
        info["pe_ratio"] = info.get("trailingPE")
        info["ev_ebitda"] = info.get("enterpriseToEbitda")

    # dividendYield is read directly downstream (dividend checklist, metric display),
    # but FMP only ever supplies dividendYieldTTM under a different key — without this,
    # a company with a correctly-mapped dividendRate (used by the DDM model) would still
    # show "N/A" on the dividend checklist/metric. Derive it whenever it's missing but a
    # rate + price are available, regardless of which source populated dividendRate.
    if not is_valid_metric(info.get("dividendYield")) and info.get("dividendRate") and current_price:
        try:
            info["dividendYield"] = float(info["dividendRate"]) / float(current_price)
        except (TypeError, ZeroDivisionError):
            pass

    # --- RECONCILIATION: backfill fields FMP structurally never returns ---
    # "Strict isolation" avoids mixing price/financial figures across sources (which
    # could create internally-inconsistent numbers), but a handful of fields simply
    # don't exist anywhere in the FMP endpoints this app calls: analyst price targets,
    # recommendation consensus, insider/institutional ownership %, and company officers.
    # Silently leaving these blank whenever FMP is primary means those UI sections
    # (analyst consensus, ownership donut, management table) disappear for the majority
    # of Indian-equity searches. Backfill ONLY these specific gaps from yfinance —
    # never touch price, EPS, revenue, or anything FMP already supplied.
    if data_source == "FMP":
        _reconcile_keys = ("targetMeanPrice", "recommendationMean", "heldPercentInsiders",
                            "heldPercentInstitutions", "companyOfficers")
        if any(info.get(k) in (None, [], "") for k in _reconcile_keys):
            try:
                yf_supplement = info_future.result() or {}
            except Exception as e:
                yf_supplement = {}
                logger.debug("Supplementary yfinance fetch failed for %s: %s", resolved_ticker, e)
            filled = []
            for k in _reconcile_keys:
                if info.get(k) in (None, [], "") and yf_supplement.get(k) not in (None, [], ""):
                    info[k] = yf_supplement[k]
                    filled.append(k)
            if not is_valid_metric(info.get("dividendYield")) and is_valid_metric(yf_supplement.get("dividendYield")):
                info["dividendYield"] = yf_supplement["dividendYield"]
                filled.append("dividendYield")
            if filled:
                warnings.append(f"FMP primary — supplemented from yfinance: {', '.join(filled)}.")

    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")
    is_fin = is_financial_sector(sector, industry)
    sector_profile = classify_sector_profile(sector, industry)
    revenue_keys = BANK_REVENUE_KEYS if is_fin else STANDARD_REVENUE_KEYS

    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    net_inc, total_eq, total_assets_latest, ebitda_val = None, None, None, info.get('ebitda')
    revenue_latest, ebit_latest, interest_exp_latest, interest_income_latest = None, None, None, None
    effective_tax_rate_pct = None
    fcf_history = None
    pat_qoq, pat_yoy_pct, net_margin_final = None, None, None
    latest_quarter_net_income = None
    revenue_cagr_pct = None
    total_debt_yf = None

    try:
        q_fin = qf_future.result()
        if q_fin is not None and not q_fin.empty and 'Net Income' in q_fin.index:
            ni_series = q_fin.loc['Net Income'].dropna()
            if len(ni_series) > 0:
                net_inc = float(ni_series.iloc[:4].sum())
                latest_quarter_net_income = float(ni_series.iloc[0])
            if len(ni_series) >= 2:
                pat_qoq = safe_pct_change(ni_series.iloc[0], ni_series.iloc[1])
                if pat_qoq is not None:
                    pat_qoq = round(pat_qoq, 2)
            if len(ni_series) >= 5:
                pat_yoy_pct = safe_pct_change(ni_series.iloc[0], ni_series.iloc[4])
                if pat_yoy_pct is not None:
                    pat_yoy_pct = round(pat_yoy_pct, 2)
            rev_key_found = next((k for k in revenue_keys if k in q_fin.index), None)
            if rev_key_found and len(ni_series) > 0:
                rev_series = q_fin.loc[rev_key_found].dropna()
                if len(rev_series) > 0 and rev_series.iloc[0] != 0:
                    net_margin_final = round((ni_series.iloc[0] / rev_series.iloc[0]) * 100, 2)

        fin = fin_future.result()
        if fin is not None and not fin.empty:
            rev_key_found = next((k for k in revenue_keys if k in fin.index), None)
            if rev_key_found and pd.notna(fin.loc[rev_key_found].iloc[0]):
                revenue_latest = float(fin.loc[rev_key_found].iloc[0])
                rev_series_annual = fin.loc[rev_key_found].dropna()
                if len(rev_series_annual) >= 2 and rev_series_annual.iloc[-1] > 0:
                    years = len(rev_series_annual) - 1
                    revenue_cagr_pct = round((((rev_series_annual.iloc[0] / rev_series_annual.iloc[-1]) ** (1 / years)) - 1) * 100, 2)
            for k in ['EBIT', 'Operating Income']:
                if k in fin.index and pd.notna(fin.loc[k].iloc[0]):
                    ebit_latest = float(fin.loc[k].iloc[0]); break
            if 'Interest Expense' in fin.index:
                ie_series = fin.loc['Interest Expense'].dropna()
                if len(ie_series) > 0:
                    interest_exp_latest = float(ie_series.iloc[0])
            ii_key_found = next((k for k in INTEREST_INCOME_KEYS if k in fin.index), None)
            if ii_key_found:
                ii_series = fin.loc[ii_key_found].dropna()
                if len(ii_series) > 0:
                    interest_income_latest = float(ii_series.iloc[0])
            # Effective tax rate, for WACC/ROIC (NOPAT) — prefer yfinance's own
            # pre-computed "Tax Rate For Calcs" row when present (already a
            # decimal fraction); otherwise derive Tax Provision / Pretax
            # Income directly. Left as None (caller falls back to the
            # statutory default) if neither is available or the result is
            # implausible — this is real company data when present, not a
            # generic assumption presented as one.
            if 'Tax Rate For Calcs' in fin.index:
                tr_series = fin.loc['Tax Rate For Calcs'].dropna()
                if len(tr_series) > 0 and 0 < float(tr_series.iloc[0]) < 0.60:
                    effective_tax_rate_pct = float(tr_series.iloc[0]) * 100
            if effective_tax_rate_pct is None and 'Tax Provision' in fin.index and 'Pretax Income' in fin.index:
                tp_series = fin.loc['Tax Provision'].dropna()
                pti_series = fin.loc['Pretax Income'].dropna()
                if len(tp_series) > 0 and len(pti_series) > 0 and pti_series.iloc[0]:
                    implied_rate = float(tp_series.iloc[0]) / float(pti_series.iloc[0])
                    if 0 < implied_rate < 0.60:
                        effective_tax_rate_pct = implied_rate * 100

        bs = bal_future.result()
        if bs is not None and not bs.empty:
            for k in ['Stockholders Equity', 'Total Stockholder Equity', 'Common Stock Equity']:
                if k in bs.index:
                    eq_series = bs.loc[k].dropna()
                    if len(eq_series) > 0:
                        total_eq = float(eq_series.iloc[0]); break
            if 'Total Assets' in bs.index:
                ta_series = bs.loc['Total Assets'].dropna()
                if len(ta_series) > 0:
                    total_assets_latest = float(ta_series.iloc[0])
            for k in ['Total Debt', 'Long Term Debt']:
                if k in bs.index:
                    td_series = bs.loc[k].dropna()
                    if len(td_series) > 0:
                        total_debt_yf = float(td_series.iloc[0]); break

        cf = cf_future.result()
        if cf is not None and not cf.empty and 'Free Cash Flow' in cf.index:
            fcf_history = cf.loc['Free Cash Flow'].dropna()

        if fin is not None and not fin.empty:
            col = fin.columns[0]
            rev_key_found = next((k for k in revenue_keys if k in fin.index), None)
            pnl_df = pd.DataFrame([
                {"Particulars": "Net Sales / Total Income", "Amount (₹ Cr)": round(fin.loc[rev_key_found, col] / 10000000, 2) if rev_key_found else "—"},
                {"Particulars": "Operating Profit", "Amount (₹ Cr)": round(fin.loc['Operating Income', col] / 10000000, 2) if 'Operating Income' in fin.index else "—"},
                {"Particulars": "Net Profit", "Amount (₹ Cr)": round(fin.loc['Net Income', col] / 10000000, 2) if 'Net Income' in fin.index else "—"}
            ])
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            bs_df = pd.DataFrame([
                {"Particulars": "Total Equity", "Amount (₹ Cr)": round(total_eq / 10000000, 2) if total_eq is not None else "—"},
                {"Particulars": "Total Debt", "Amount (₹ Cr)": round(total_debt_yf / 10000000, 2) if total_debt_yf is not None else "—"},
                {"Particulars": "Total Assets", "Amount (₹ Cr)": round(total_assets_latest / 10000000, 2) if total_assets_latest is not None else "—"}
            ])
        if cf is not None and not cf.empty:
            col = cf.columns[0]
            cf_df = pd.DataFrame([
                {"Particulars": "Operating Cash Flow", "Amount (₹ Cr)": round(cf.loc['Operating Cash Flow', col] / 10000000, 2) if 'Operating Cash Flow' in cf.index else "—"},
                {"Particulars": "Free Cash Flow", "Amount (₹ Cr)": round(cf.loc['Free Cash Flow', col] / 10000000, 2) if 'Free Cash Flow' in cf.index else "—"}
            ])
    except Exception as e:
        logger.warning("Financial statement parse failed for %s: %s", resolved_ticker, e)
        warnings.append(f"Statement parse issue: {e}")

    shares_out = info.get("sharesOutstanding")
    mcap = info.get("marketCap")
    
    if mcap and shares_out and current_price:
        calculated_mcap = shares_out * current_price
        # Only override if the discrepancy is >15% (data source inconsistency)
        if mcap > 0 and abs(calculated_mcap - mcap) / mcap > 0.15:
            mcap = calculated_mcap
    elif current_price and shares_out:
        mcap = current_price * shares_out

    operating_margin = round((ebit_latest / revenue_latest) * 100, 2) if (ebit_latest is not None and revenue_latest) else None

    nim_proxy = None
    if is_fin and interest_income_latest is not None and interest_exp_latest is not None and total_assets_latest:
        nim_proxy = round(((interest_income_latest - interest_exp_latest) / total_assets_latest) * 100, 2)

    trailing_earnings_negative = (net_inc is not None and net_inc < 0) or (info.get('trailingEps') and info.get('trailingEps') < 0)

    # A single big QoQ % swing off a deep loss (e.g. -100cr -> -45cr is a "55%
    # improvement" while still losing money) is not evidence of a turnaround on
    # its own. Require the swing to be corroborated by the latest quarter actually
    # being profitable at the operating level, not just "less unprofitable".
    latest_q_profitable = (
        latest_quarter_net_income is not None and latest_quarter_net_income > 0
        and ebit_latest is not None and ebit_latest > 0
    )
    is_turnaround = bool(trailing_earnings_negative and latest_q_profitable and (
        pat_qoq is None or pat_qoq > 0
    ))

    recent_news = fetch_google_news(f"{info.get('longName', resolved_ticker)} stock news")
    business_summary = info.get("longBusinessSummary")
    qualitative_bonus, qualitative_notes = scan_news_sentiment(recent_news, business_summary)
    order_book_hits, growth_pct_from_news = extract_order_book_signal(recent_news, business_summary)

    pe_raw = info.get("trailingPE")
    if not is_valid_metric(pe_raw) and net_inc and mcap:
        pe_raw = round(mcap / net_inc, 2)
    elif is_valid_metric(pe_raw):
        pe_raw = round(float(pe_raw), 2)

    pb_raw = info.get("priceToBook")
    if not is_valid_metric(pb_raw) and total_eq and mcap and total_eq > 0:
        pb_raw = round(mcap / total_eq, 2)
    elif is_valid_metric(pb_raw):
        pb_raw = round(float(pb_raw), 2)

    roe_raw = info.get("returnOnEquity")
    if not is_valid_metric(roe_raw) and net_inc and total_eq and total_eq > 0:
        roe_raw = net_inc / total_eq
    roe_is_known = is_valid_metric(roe_raw)

    peg_raw = info.get("pegRatio") if data_source == "yfinance" else None
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and pat_yoy_pct and pat_yoy_pct > 0:
        peg_raw = round(to_float(pe_raw) / pat_yoy_pct, 2)
    elif is_valid_metric(peg_raw):
        peg_raw = round(float(peg_raw), 2)

    ev_ebitda = "N/A"
    if is_fin:
        ev_ebitda = "N/A (Financial Sector)"
    else:
        if data_source == "FMP" and is_valid_metric(info.get("ev_ebitda")):
            ev_ebitda = round(float(info.get("ev_ebitda")), 2)
        else:
            ev_val = info.get("enterpriseValue")
            if not is_valid_metric(ev_val) and mcap:
                # Was `(info.get('totalDebt') or total_debt_yf or 0)` — if
                # totalDebt is a genuine 0 (a debt-free company), that falsy 0
                # would fall through to total_debt_yf instead of being kept,
                # letting a possibly-stale/differently-classified secondary
                # figure silently override a correct zero. None-check instead
                # of truthy-check so a real 0 is respected.
                debt_for_ev = info.get('totalDebt')
                if debt_for_ev is None:
                    debt_for_ev = total_debt_yf if total_debt_yf is not None else 0
                ev_val = mcap + debt_for_ev - (info.get('totalCash') or 0)
            if is_valid_metric(ebitda_val) and is_valid_metric(ev_val) and float(ebitda_val) != 0:
                ev_ebitda = round(float(ev_val) / float(ebitda_val), 2)
            elif is_valid_metric(info.get("ev_ebitda")):
                ev_ebitda = round(float(info.get("ev_ebitda")), 2)

    ebitda_margin = round((ebitda_val / revenue_latest) * 100, 2) if (is_valid_metric(ebitda_val) and revenue_latest) else "N/A"
    interest_coverage = round(ebit_latest / interest_exp_latest, 2) if (ebit_latest is not None and interest_exp_latest) else "N/A"
    
    # Bulletproof D/E Calculation
    dte_raw = info.get("debtToEquity")
    if is_valid_metric(dte_raw):
        dte_num = float(dte_raw)
        # yfinance sometimes returns D/E as a percentage (e.g. 150 means 1.5x).
        # Use a high threshold (20) so genuinely leveraged companies (D/E=6-12)
        # are not incorrectly divided by 100. Financial companies rarely appear here
        # because their D/E comes from key-metrics-ttm which already gives a ratio.
        if data_source == "yfinance" and dte_num > 20.0:
            debt_to_equity = round(dte_num / 100.0, 2)
        else:
            debt_to_equity = round(dte_num, 2)
    else:
        t_debt = info.get("totalDebt") if info.get("totalDebt") is not None else total_debt_yf
        t_eq = info.get("totalEquity") if info.get("totalEquity") is not None else total_eq
        if is_valid_metric(t_debt) and is_valid_metric(t_eq) and float(t_eq) != 0:
            debt_to_equity = round(float(t_debt) / float(t_eq), 2)
        else:
            debt_to_equity = "N/A"

    temp_metrics = {
        'pe_ratio': pe_raw, 'peg_ratio': peg_raw, 'pb_ratio': pb_raw,
        'pat_yoy': pat_yoy_pct, 'roe': (roe_raw * 100) if roe_is_known else None,
        'ev_ebitda': ev_ebitda, 'is_financial_sector': is_fin, 'debt_to_equity': debt_to_equity,
        'interest_coverage': interest_coverage, 'net_margin': net_margin_final, 'pat_qoq': pat_qoq,
        'operating_margin': operating_margin, 'revenue_cagr': revenue_cagr_pct,
        'sector_profile': sector_profile, 'nim_proxy': nim_proxy,
    }

    v_bin, v_avail, v_poss = score_from_checks(valuation_checks(temp_metrics))
    p_bin, p_avail, p_poss = score_from_checks(past_performance_checks(temp_metrics))
    h_bin, h_avail, h_poss = score_from_checks(financial_health_checks(temp_metrics))
    v_score = continuous_valuation_score(temp_metrics)
    p_score = continuous_past_score(temp_metrics)
    h_score = continuous_health_score(temp_metrics)
    
    if v_score is None: v_score = v_bin
    if p_score is None: p_score = p_bin
    if h_score is None: h_score = h_bin
    
    fundamental_score, data_completeness = compute_fundamental_score(
        v_score, p_score, h_score, is_fin,
        v_avail, p_avail, h_avail, v_poss, p_poss, h_poss
    )

    if is_turnaround and fundamental_score is not None:
        pass  # Nudge handled in pipeline

    bvps = info.get('bookValue')
    if not is_valid_metric(bvps) and total_eq and shares_out:
        bvps = total_eq / shares_out
    bvps = bvps if is_valid_metric(bvps) else None
    div_per_share = info.get("dividendRate")

    analyst_growth_pct = info.get("analyst_growth_pct")

    jpb_ratio = jpb_value = ddm_val = None
    if is_fin:
        beta_preview = info.get('beta') if info.get('beta') and pd.notna(info.get('beta')) and info.get('beta') > 0 else 1.0
        current_rfr = _rfr_value(get_dynamic_risk_free_rate())
        ke_preview = min(max((current_rfr + beta_preview * EQUITY_RISK_PREMIUM) * 100, 9), 20)
        
        if analyst_growth_pct and float(analyst_growth_pct) > 0:
            growth_preview = min(max(float(analyst_growth_pct), 5), 30)
        elif pat_yoy_pct and pat_yoy_pct > 0:
            growth_preview = pat_yoy_pct
        else:
            growth_preview = 8.0
            
        jpb_ratio, jpb_value = justified_pb_fair_value(roe_raw * 100 if roe_is_known else None, ke_preview, growth_preview, bvps)
        ddm_val = ddm_fair_value(div_per_share, ke_preview, growth_preview)
    temp_metrics["justified_pb"] = jpb_ratio

    # Threaded through `info` (rather than new positional params) since
    # run_predictive_pipeline already receives `info` and reads from it —
    # these feed compute_wacc/compute_roic inside the pipeline, using the
    # SAME ke_pct the pipeline already computes rather than a new redundant
    # CAPM calculation (see the ke_preview/growth_preview divergence noted
    # elsewhere for why a second, independent Ke is worth avoiding).
    info['ebit_latest'] = ebit_latest
    info['interest_exp_latest'] = interest_exp_latest
    info['effective_tax_rate_pct'] = effective_tax_rate_pct

    predictive_data = run_predictive_pipeline(
        info, hist_full, fcf_history, sector, industry, fundamental_score,
        bvps, div_per_share, roe_raw * 100 if roe_is_known else None, pat_yoy_pct, analyst_growth_pct,
        precomputed_jpb=(jpb_ratio, jpb_value), precomputed_ddm=ddm_val,
        resolved_pe=to_float(pe_raw), is_turnaround=is_turnaround,
        latest_quarter_net_income=latest_quarter_net_income, shares_outstanding=shares_out,
        qualitative_bonus=qualitative_bonus, qualitative_notes=qualitative_notes,
        sector_profile=sector_profile, order_book_hits=order_book_hits, growth_pct_from_news=growth_pct_from_news,
    )

    promoters_raw = info.get("heldPercentInsiders") or 0
    institutions_raw = info.get("heldPercentInstitutions") or 0
    # yfinance returns these as fractions (0.0–1.0); clamp to valid range before scaling
    promoters = round(min(max(float(promoters_raw), 0.0), 1.0) * 100, 2)
    institutions = round(min(max(float(institutions_raw), 0.0), 1.0) * 100, 2)
    total_known = promoters + institutions
    if total_known == 0:
        shareholding_dict = {"Data Unavailable": 100}
    else:
        # Cap total to 100% in case of data overlap; public is the remainder
        public = max(0.0, round(100.0 - min(total_known, 100.0), 2))
        shareholding_dict = {
            "Promoters": min(promoters, 100.0),
            "Institutions": min(institutions, 100.0 - min(promoters, 100.0)),
            "Public": public,
        }

    try:
        mf_df = mf_future.result()
    except Exception as e:
        logger.warning("Mutual fund holders unavailable for %s: %s", resolved_ticker, e)
        mf_df = None
        
    try:
        cal = cal_future.result()
        if isinstance(cal, dict):
            cal_df = pd.DataFrame(list(cal.items()), columns=['Event', 'Date'])
        else:
            cal_df = cal
    except Exception as e:
        logger.warning("Calendar unavailable for %s: %s", resolved_ticker, e)
        cal_df = None

    metrics = {
        "name": info.get("longName", resolved_ticker), "price": current_price,
        "pe_ratio": pe_raw if is_valid_metric(pe_raw) else "N/A",
        "pb_ratio": pb_raw if is_valid_metric(pb_raw) else "N/A",
        "peg_ratio": peg_raw if is_valid_metric(peg_raw) else "N/A",
        "ev_ebitda": ev_ebitda if is_valid_metric(ev_ebitda) else ev_ebitda,
        "roe": f"{round(roe_raw*100, 2)}%" if roe_is_known else "N/A",
        "ebitda_margin": f"{ebitda_margin}%" if ebitda_margin != "N/A" else "N/A",
        "operating_margin": operating_margin, "revenue_cagr": revenue_cagr_pct, "nim_proxy": nim_proxy,
        "debt_to_equity": debt_to_equity,
        "interest_coverage": interest_coverage,
        "net_margin": f"{net_margin_final}%" if net_margin_final is not None else "N/A",
        "dividend_yield": f"{round(info.get('dividendYield',0)*100,2)}%" if is_valid_metric(info.get('dividendYield')) else "N/A",
        "pat_yoy": f"{pat_yoy_pct}%" if pat_yoy_pct is not None else "N/A",
        "pat_qoq": f"{pat_qoq}%" if pat_qoq is not None else "N/A",
        "market_cap": mcap, "sector": sector, "industry": industry,
        "is_financial_sector": is_fin, "justified_pb": jpb_ratio, "is_turnaround": is_turnaround,
        "sector_profile": sector_profile, "order_book_hits": order_book_hits,
        "growth_pct_from_news": growth_pct_from_news,
        "data_completeness": data_completeness,
        "rfr_source": predictive_data.get("rfr_source"),
        "audit": predictive_data.get("audit"),
        "data_warnings": warnings,
        "warnings": warnings,
        "v_score": v_score, "q_score": p_score, "f_score": h_score,
        "valuation_checks": valuation_checks(temp_metrics),
        "past_checks": past_performance_checks(temp_metrics),
        "health_checks": financial_health_checks(temp_metrics),
        "metric_provenance": {
            "pe_ratio": make_metric(pe_raw if is_valid_metric(pe_raw) else None,
                                    source=data_source,
                                    period="TTM", confidence=0.8 if is_valid_metric(pe_raw) else 0.3),
            "pb_ratio": make_metric(pb_raw if is_valid_metric(pb_raw) else None,
                                    source=data_source,
                                    period="TTM", confidence=0.8 if is_valid_metric(pb_raw) else 0.3),
            "roe": make_metric((roe_raw * 100) if roe_is_known else None,
                               source=data_source, period="TTM", confidence=0.75 if roe_is_known else 0.2),
            "pat_yoy": make_metric(pat_yoy_pct, source="quarterly_financials", period="YoY quarter",
                                   confidence=0.7 if pat_yoy_pct is not None else 0.2),
        },
        "fifty_two_high": info.get("fiftyTwoWeekHigh", "N/A"),
        "fifty_two_low": info.get("fiftyTwoWeekLow", "N/A"),
        "business_summary": business_summary,
        "website": info.get("website", "N/A"),
        "company_officers": info.get("companyOfficers", []),
        "recent_news": recent_news,
        "shareholding": shareholding_dict,
        "mutual_funds": mf_df,
        "calendar": cal_df,
        "target_mean_price": info.get("targetMeanPrice"),
        "recommendation_mean": info.get("recommendationMean"),
        "working_ticker": resolved_ticker, "history": hist_full.reset_index(),
        "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": predictive_data, "fair_value": predictive_data['target_price'],
        "currency": currency_symbol, "fundamental_score": fundamental_score,
        "data_source": data_source,
    }

    # --- SECTOR ALTERNATIVE SCANNER ---
    # Finds a peer that, when independently analysed through this SAME pipeline,
    # actually comes back STRONG BUY — not just one that looks decent on a few
    # raw ratios. scan_for_alternative=False (used for the recursive peer calls
    # below) prevents each peer from launching its own nested scan.
    metrics['best_alternative'] = None
    if scan_for_alternative and predictive_data['verdict'] in ["DON'T BUY", "OBSERVE"]:
        peers = SECTOR_PEERS.get(sector_profile, SECTOR_PEERS["standard"])
        for peer in peers:
            if peer == resolved_ticker:
                continue
            try:
                p_metrics = fetch_stock_data(peer, peer, scan_for_alternative=False)
            except Exception as e:
                logger.warning("Alternative-scan fetch failed for %s: %s", peer, e)
                continue
            if not p_metrics:
                continue
            p_pred = p_metrics.get("predictive") or {}
            if p_pred.get("verdict") != "STRONG BUY":
                continue  # only a REAL, fully-computed STRONG BUY qualifies

            p_price = p_metrics.get("price")
            p_target = p_pred.get("target_price")
            upside_pct = (
                round((float(p_target) / float(p_price) - 1) * 100, 1)
                if p_price and p_target and float(p_price) > 0 else None
            )
            p_pe_raw = p_metrics.get("pe_ratio")
            p_pb_raw = p_metrics.get("pb_ratio")
            metrics['best_alternative'] = {
                "name": p_metrics.get("name") or peer,
                "ticker": peer,
                "price": round(float(p_price), 2) if p_price else None,
                "pe": round(float(p_pe_raw), 1) if is_valid_metric(p_pe_raw) else "N/A",
                "pb": round(float(p_pb_raw), 1) if is_valid_metric(p_pb_raw) else "N/A",
                "verdict": p_pred.get("verdict"),
                "composite_score": p_pred.get("composite_score"),
                "target_price": p_target,
                "upside_pct": upside_pct,
                "entry_range": p_pred.get("entry_range"),
                "time_horizon": p_pred.get("time_horizon"),
            }
            break  # first verified STRONG BUY wins — no need to scan further peers

    return metrics


# ============================================================
# PHASE 2 — ELITE GRAPHICAL UI: SUPPORTING DATA FETCHERS (additive)
# ============================================================

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_extended_price_history(resolved_ticker: str, years: int = 5) -> pd.DataFrame:
    """Longer-window price history for the Historical Valuation Bands and Seasonality
    Heatmap charts. Fetched and cached SEPARATELY from the 1-year `hist_full` used by
    every existing technical/drift/scoring calculation in fetch_stock_data, so none of
    that math is affected by pulling a longer window here."""
    try:
        df = yf.Ticker(resolved_ticker).history(period=f"{years}y")
        if df is None or df.empty:
            return pd.DataFrame()
        return df.reset_index()
    except Exception as e:
        logger.warning("Extended price history fetch failed for %s: %s", resolved_ticker, e)
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_peer_comparison_data(sector_profile: str, exclude_ticker: str = None) -> list:
    """Lightweight ROE / P/E / Market-Cap snapshot for each sector peer, for the
    Interactive Peer Scatter Plot. Reuses the existing FMP-primary/yfinance-fallback
    pattern per peer, fetched concurrently — this is display-only data and never
    feeds fetch_stock_data's own return structure or scoring pipeline."""
    peers = [p for p in SECTOR_PEERS.get(sector_profile, SECTOR_PEERS["standard"]) if p != exclude_ticker]

    def _fetch_one(peer):
        try:
            fmp = fetch_fmp_data(_fmp_ticker(peer))
            if fmp and fmp.get("currentPrice") is not None:
                roe, pe, mcap, name = fmp.get("returnOnEquity"), fmp.get("pe_ratio"), fmp.get("marketCap"), fmp.get("longName")
            else:
                yinfo = yf.Ticker(peer).info or {}
                roe, pe, mcap, name = yinfo.get("returnOnEquity"), yinfo.get("trailingPE"), yinfo.get("marketCap"), yinfo.get("longName")
            if roe is None or pe is None or mcap is None or pe <= 0:
                return None
            return {"ticker": peer, "name": name or peer, "roe": round(float(roe) * 100, 2),
                    "pe": round(float(pe), 2), "market_cap": float(mcap)}
        except Exception as e:
            logger.debug("Peer comparison fetch failed for %s: %s", peer, e)
            return None

    if not peers:
        return []
    with ThreadPoolExecutor(max_workers=len(peers)) as executor:
        results = list(executor.map(_fetch_one, peers))
    return [r for r in results if r]
