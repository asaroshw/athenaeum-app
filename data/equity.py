"""Equity market data: FMP primary, yfinance fallback, news, RFR."""
from __future__ import annotations
import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta
import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf
from athenaeum.utils.helpers import (
    to_float, is_valid_metric, make_metric, _rfr_value, _rfr_source,
)
from athenaeum.models.sector import is_financial_sector, classify_sector_profile
from athenaeum.analysis.sentiment import scan_news_sentiment, extract_order_book_signal
from athenaeum.models.fundamentals import (
    valuation_checks, past_performance_checks, financial_health_checks,
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

@st.cache_data(ttl=3600)


@st.cache_data(ttl=3600)

def fetch_fmp_data(ticker_clean: str) -> dict:
    """Primary data fetch from Financial Modeling Prep. Returns a flat dict;
    absent keys mean FMP did not have that field — yfinance fills the gap."""
    FMP_KEY = st.secrets.get("FMP_API_KEY", "")
    if not FMP_KEY:
        return {}
    BASE = "https://financialmodelingprep.com/api/v3"
    out = {}
    headers = {"Accept": "application/json"}
    def _get(path):
        try:
            r = requests.get(f"{BASE}/{path}&apikey={FMP_KEY}", headers=headers, timeout=7)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    q = _get(f"quote/{ticker_clean}?")
    if q and isinstance(q, list) and q:
        out.update({k: q[0].get(v) for k, v in [
            ("currentPrice","price"),("marketCap","marketCap"),
            ("sharesOutstanding","sharesOutstanding"),("pe_ratio","pe"),
            ("eps","eps"),("fiftyTwoWeekHigh","yearHigh"),
            ("fiftyTwoWeekLow","yearLow")]})

    p = _get(f"profile/{ticker_clean}?")
    if p and isinstance(p, list) and p:
        out.update({k: p[0].get(v) for k, v in [
            ("longName","companyName"),("sector","sector"),("industry","industry"),
            ("longBusinessSummary","description"),("website","website"),("beta","beta")]})

    km = _get(f"key-metrics-ttm/{ticker_clean}?")
    if km and isinstance(km, list) and km:
        out.update({k: km[0].get(v) for k, v in [
            ("returnOnEquity","roeTTM"),("returnOnAssets","returnOnTangibleAssetsTTM"),
            ("debtToEquity","debtToEquityTTM"),("currentRatio","currentRatioTTM"),
            ("ev_ebitda","enterpriseValueOverEBITDATTM"),("priceToBook","pbRatioTTM"),
            ("dividendYieldTTM","dividendYieldTTM")]})

    inc = _get(f"income-statement/{ticker_clean}?limit=4")
    if inc and isinstance(inc, list) and len(inc) > 0:
        l = inc[0]
        out.update({k: l.get(v) for k, v in [
            ("totalRevenue","revenue"),("ebit","operatingIncome"),
            ("netIncome","netIncome"),("ebitda","ebitda"),
            ("eps","eps"),("interestExpense","interestExpense")]})
        if len(inc) >= 2 and inc[1].get("netIncome"):
            ni_now, ni_p = (l.get("netIncome") or 0), (inc[1].get("netIncome") or 1)
            out["pat_yoy"] = round(((ni_now - ni_p) / abs(ni_p)) * 100, 2)
            if len(inc) >= 3:
                ni_p2 = inc[2].get("netIncome") or 1
                out["pat_yoy_prior"] = round(((ni_p - ni_p2) / abs(ni_p2)) * 100, 2)

    bs = _get(f"balance-sheet-statement/{ticker_clean}?limit=1")
    if bs and isinstance(bs, list) and bs:
        b = bs[0]
        out.update({k: b.get(v) for k, v in [
            ("totalDebt","totalDebt"),("totalCash","cashAndCashEquivalents"),
            ("totalEquity","totalStockholdersEquity"),("totalAssets","totalAssets")]})
        if out.get("sharesOutstanding") and b.get("totalStockholdersEquity"):
            out["bookValue"] = b["totalStockholdersEquity"] / out["sharesOutstanding"]

    ae = _get(f"analyst-estimates/{ticker_clean}?limit=2")
    if ae and isinstance(ae, list) and len(ae) > 0:
        out["forwardEps"] = ae[0].get("estimatedEpsAvg")
        if len(ae) >= 2 and ae[1].get("estimatedEpsAvg") and ae[0].get("estimatedEpsAvg"):
            eps_now = ae[0]["estimatedEpsAvg"]; eps_p = ae[1]["estimatedEpsAvg"] or 1
            out["analyst_growth_pct"] = round(((eps_now - eps_p) / abs(eps_p)) * 100, 2)

    hist = _get(f"historical-price-full/{ticker_clean}?timeseries=252")
    if hist and hist.get("historical"):
        hdf = pd.DataFrame(hist["historical"])
        hdf["date"] = pd.to_datetime(hdf["date"])
        hdf = hdf.sort_values("date").rename(columns={
            "date":"Date","open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"})
        out["fmp_history"] = hdf[["Date","Open","High","Low","Close","Volume"]]
    return out


def _fmp_ticker(resolved_ticker: str) -> str:
    """Convert WELSPUNCORP.NS -> WELSPUNCORP for FMP."""
    return resolved_ticker.replace(".NS","").replace(".BO","").upper()

@st.cache_data(ttl=1800)


@st.cache_data(ttl=1800)
def fetch_stock_data(resolved_ticker, raw_input):
    # FMP primary, yfinance fallback — collect data-quality warnings for the UI
    warnings = []
    fmp = fetch_fmp_data(_fmp_ticker(resolved_ticker))
    if not fmp:
        warnings.append("FMP returned no data — relying on yfinance fallback only.")
    elif not fmp.get("currentPrice"):
        warnings.append("FMP quote incomplete — price/ratios filled from yfinance where needed.")

    stock = yf.Ticker(resolved_ticker)
    if fmp.get("fmp_history") is not None and not fmp["fmp_history"].empty:
        hist_full = fmp["fmp_history"].copy().reset_index(drop=True)
    else:
        try:
            hist_full = stock.history(period="1y")
            warnings.append("Price history from yfinance (FMP history unavailable).")
        except Exception as e:
            logger.warning("yfinance history failed for %s: %s", resolved_ticker, e)
            hist_full = pd.DataFrame()
            warnings.append(f"Price history unavailable: {e}")
    if hist_full.empty:
        raise ValueError(f"Could not find '{raw_input}'.")

    try:
        yf_info = stock.info or {}
    except Exception as e:
        logger.warning("yfinance info failed for %s: %s", resolved_ticker, e)
        yf_info = {}
        warnings.append("yfinance company info failed — some fields may be missing.")

    # Merge: FMP wins, yfinance fills gaps. Periods may mix TTM vs annual — flagged below.
    info = {**yf_info, **{k: v for k, v in fmp.items() if v is not None and k != "fmp_history"}}
    current_price = info.get("currentPrice") or round(float(hist_full['Close'].iloc[-1], 2))
    currency_symbol = "₹"
    if fmp and yf_info:
        warnings.append(
            "Metrics may mix FMP TTM fields with yfinance annual/quarterly statements — "
            "treat cross-source ratios with care."
        )

    sector = info.get("sector", "N/A")
    industry = info.get("industry", "N/A")
    is_fin = is_financial_sector(sector, industry)
    sector_profile = classify_sector_profile(sector, industry)
    revenue_keys = BANK_REVENUE_KEYS if is_fin else STANDARD_REVENUE_KEYS
    if is_fin:
        warnings.append(
            "Financial-sector coverage is incomplete: GNPA/NNPA, CRAR/CET1, PCR, CASA, "
            "credit cost and slippage are not available from current free data sources. "
            "Bank/NBFC scores should be treated as screening-only."
        )

    pnl_df, bs_df, cf_df = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    net_inc, total_eq, total_assets_latest, ebitda_val = None, None, None, info.get('ebitda')
    revenue_latest, ebit_latest, interest_exp_latest, interest_income_latest = None, None, None, None
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

        bs = stock.balance_sheet
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

        cf = stock.cashflow
        if cf is not None and not cf.empty and 'Free Cash Flow' in cf.index:
            # Dropna and reverse to ensure chronological or at least clean iteration
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
                {"Particulars": "Total Equity", "Amount (₹ Cr)": round(total_eq / 10000000, 2) if total_eq else "—"},
                {"Particulars": "Total Debt", "Amount (₹ Cr)": round(bs.loc['Total Debt', col] / 10000000, 2) if 'Total Debt' in bs.index else "—"},
                {"Particulars": "Total Assets", "Amount (₹ Cr)": round(bs.loc['Total Assets', col] / 10000000, 2) if 'Total Assets' in bs.index else "—"}
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
    
    # --- FIX: Indian Market Cap / Shares Sanity Check ---
    if mcap and shares_out and current_price:
        calculated_mcap = shares_out * current_price
        if abs(calculated_mcap - mcap) / mcap > 0.15:
            mcap = calculated_mcap
    elif current_price and shares_out:
        mcap = current_price * shares_out

    operating_margin = round((ebit_latest / revenue_latest) * 100, 2) if (ebit_latest is not None and revenue_latest) else None

    nim_proxy = None
    if is_fin and interest_income_latest is not None and interest_exp_latest is not None and total_assets_latest:
        nim_proxy = round(((interest_income_latest - interest_exp_latest) / total_assets_latest) * 100, 2)

    trailing_earnings_negative = (net_inc is not None and net_inc < 0) or (info.get('trailingEps') and info.get('trailingEps') < 0)
    
    # --- FIX: Turnaround strictly needs positive operating profit ---
    is_turnaround = bool(trailing_earnings_negative and (
        (pat_qoq is not None and pat_qoq > 50) or 
        (latest_quarter_net_income is not None and latest_quarter_net_income > 0 and ebit_latest is not None and ebit_latest > 0)
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

    peg_raw = info.get("pegRatio")
    if not is_valid_metric(peg_raw) and is_valid_metric(pe_raw) and pat_yoy_pct and pat_yoy_pct > 0:
        peg_raw = round(to_float(pe_raw) / pat_yoy_pct, 2)
    elif is_valid_metric(peg_raw):
        peg_raw = round(float(peg_raw), 2)

    ev_ebitda = "N/A"
    if is_fin:
        ev_ebitda = "N/A (Financial Sector)"
    else:
        ev_val = info.get("enterpriseValue")
        if not is_valid_metric(ev_val) and mcap:
            ev_val = mcap + (info.get('totalDebt') or 0) - (info.get('totalCash') or 0)
        if is_valid_metric(ebitda_val) and is_valid_metric(ev_val) and ebitda_val != 0:
            ev_ebitda = round(ev_val / ebitda_val, 2)
        elif is_valid_metric(ev_ebitda):
            ev_ebitda = round(float(ev_ebitda), 2)

    ebitda_margin = round((ebitda_val / revenue_latest) * 100, 2) if (is_valid_metric(ebitda_val) and revenue_latest) else "N/A"
    interest_coverage = round(ebit_latest / interest_exp_latest, 2) if (ebit_latest is not None and interest_exp_latest) else "N/A"
    dte_raw = info.get("debtToEquity")
    # yfinance often stores D/E as percent (e.g. 80 for 0.80); FMP may already be ratio
    if is_valid_metric(dte_raw):
        dte_num = float(dte_raw)
        # yfinance debtToEquity is typically percent (e.g. 85.3). True ratio >10 is rare
        # but possible for distressed firms — prefer source-aware rule when schema known.
        # Heuristic: values in (5, 30] could be either; treat >15 as percent (common yf range).
        if dte_num > 15:
            debt_to_equity = round(dte_num / 100.0, 2)
        else:
            debt_to_equity = round(dte_num, 2)
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
    # Binary checklists kept for transparent UI; continuous scores drive the model
    v_bin, v_avail, v_poss = score_from_checks(valuation_checks(temp_metrics))
    p_bin, p_avail, p_poss = score_from_checks(past_performance_checks(temp_metrics))
    h_bin, h_avail, h_poss = score_from_checks(financial_health_checks(temp_metrics))
    v_score = continuous_valuation_score(temp_metrics)
    p_score = continuous_past_score(temp_metrics)
    h_score = continuous_health_score(temp_metrics)
    # Fall back to binary if continuous has nothing
    if v_score is None:
        v_score = v_bin
    if p_score is None:
        p_score = p_bin
    if h_score is None:
        h_score = h_bin
    fundamental_score, data_completeness = compute_fundamental_score(
        v_score, p_score, h_score, is_fin,
        v_avail, p_avail, h_avail, v_poss, p_poss, h_poss
    )
    # Turnaround: single centralized note — growth nudge only inside pipeline (no double bonus)
    if is_turnaround and fundamental_score is not None:
        pass  # do not add a second fundamental bonus

    bvps = info.get('bookValue')
    if not is_valid_metric(bvps) and total_eq and shares_out:
        bvps = total_eq / shares_out
    bvps = bvps if is_valid_metric(bvps) else None
    div_per_share = info.get("dividendRate")

    jpb_ratio = jpb_value = ddm_val = None
    if is_fin:
        beta_preview = info.get('beta') if info.get('beta') and pd.notna(info.get('beta')) and info.get('beta') > 0 else 1.0
        current_rfr = _rfr_value(get_dynamic_risk_free_rate())
        ke_preview = min(max((current_rfr + beta_preview * EQUITY_RISK_PREMIUM) * 100, 9), 20)
        analyst_growth_pct_pre = None
        if isinstance(fmp, dict):
            analyst_growth_pct_pre = fmp.get("analyst_growth_pct")
        if analyst_growth_pct_pre is None:
            analyst_growth_pct_pre = info.get("analyst_growth_pct")
        if analyst_growth_pct_pre and float(analyst_growth_pct_pre) > 0:
            growth_preview = min(max(float(analyst_growth_pct_pre), 5), 30)
        elif pat_yoy_pct and pat_yoy_pct > 0:
            growth_preview = pat_yoy_pct
        else:
            growth_preview = 8.0
        jpb_ratio, jpb_value = justified_pb_fair_value(roe_raw * 100 if roe_is_known else None, ke_preview, growth_preview, bvps)
        ddm_val = ddm_fair_value(div_per_share, ke_preview, growth_preview)
    temp_metrics["justified_pb"] = jpb_ratio

    analyst_growth_pct = fmp.get("analyst_growth_pct") if isinstance(fmp, dict) else None
    if analyst_growth_pct is None:
        analyst_growth_pct = info.get("analyst_growth_pct")

    predictive_data = run_predictive_pipeline(
        info, hist_full, fcf_history, sector, industry, fundamental_score,
        bvps, div_per_share, roe_raw * 100 if roe_is_known else None, pat_yoy_pct, analyst_growth_pct,
        precomputed_jpb=(jpb_ratio, jpb_value), precomputed_ddm=ddm_val,
        resolved_pe=to_float(pe_raw), is_turnaround=is_turnaround,
        latest_quarter_net_income=latest_quarter_net_income, shares_outstanding=shares_out,
        qualitative_bonus=qualitative_bonus, qualitative_notes=qualitative_notes,
        sector_profile=sector_profile, order_book_hits=order_book_hits, growth_pct_from_news=growth_pct_from_news,
    )

    promoters = (info.get("heldPercentInsiders") or 0) * 100
    institutions = (info.get("heldPercentInstitutions") or 0) * 100
    if promoters == 0 and institutions == 0:
        shareholding_dict = {"Data Unavailable": 100}
    else:
        shareholding_dict = {
            "Promoters": promoters,
            "Institutions": institutions,
            "Public": max(0, 100 - (promoters + institutions))
        }

    try:
        mf_df = stock.mutualfund_holders
    except Exception as e:
        logger.warning("Mutual fund holders unavailable for %s: %s", resolved_ticker, e)
        mf_df = None
        
    try:
        cal = stock.calendar
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
                                    source="FMP" if fmp.get("pe_ratio") else "yfinance/derived",
                                    period="TTM", confidence=0.8 if is_valid_metric(pe_raw) else 0.3),
            "pb_ratio": make_metric(pb_raw if is_valid_metric(pb_raw) else None,
                                    source="FMP" if fmp.get("priceToBook") else "yfinance/derived",
                                    period="TTM", confidence=0.8 if is_valid_metric(pb_raw) else 0.3),
            "roe": make_metric((roe_raw * 100) if roe_is_known else None,
                               source="FMP/yfinance", period="TTM", confidence=0.75 if roe_is_known else 0.2),
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
        "v_score": v_score,
        "p_score": p_score,
        "h_score": h_score,
        "working_ticker": resolved_ticker, "history": hist_full.reset_index(),
        "pnl_df": pnl_df, "bs_df": bs_df, "cf_df": cf_df,
        "predictive": predictive_data, "fair_value": predictive_data['target_price'],
        "currency": currency_symbol, "fundamental_score": fundamental_score,
    }

    # --- SECTOR ALTERNATIVE SCANNER — FMP PRIMARY, yfinance FALLBACK ---
    metrics['best_alternative'] = None
    if predictive_data['verdict'] in ["DON'T BUY", "OBSERVE"]:
        peers = SECTOR_PEERS.get(sector_profile, SECTOR_PEERS["standard"])
        best_peer = None

        for peer in peers:
            if peer == resolved_ticker:
                continue
            try:
                # PRIMARY: FMP data for the peer ticker
                p_fmp = fetch_fmp_data(_fmp_ticker(peer))

                # Merge FMP with yfinance fallback for any missing fields
                p_yf_info = {}
                p_hist = pd.DataFrame()
                if not p_fmp or p_fmp.get("currentPrice") is None:
                    try:
                        p_stock = yf.Ticker(peer)
                        p_yf_info = p_stock.info
                        p_hist = p_stock.history(period="1y")
                    except Exception:
                        pass
                else:
                    # FMP has data — only fetch yfinance history if FMP history missing
                    if p_fmp.get("fmp_history") is not None and not p_fmp["fmp_history"].empty:
                        p_hist = p_fmp["fmp_history"]
                    else:
                        try:
                            p_hist = yf.Ticker(peer).history(period="1y")
                        except Exception:
                            pass

                if p_hist is None or (hasattr(p_hist, 'empty') and p_hist.empty):
                    continue

                # Merge: FMP wins, yfinance fills gaps
                p_info = {**p_yf_info, **{k: v for k, v in p_fmp.items()
                                            if v is not None and k != "fmp_history"}}

                # Resolve price
                p_current_price = (p_info.get("currentPrice") or
                                    p_info.get("price") or
                                    float(p_hist['Close'].iloc[-1]))

                p_sector   = p_info.get("sector", "N/A")
                p_industry = p_info.get("industry", "N/A")
                p_is_fin   = is_financial_sector(p_sector, p_industry)

                # Ratios — FMP fields take priority (already merged into p_info)
                p_pe  = p_info.get("pe_ratio") or p_info.get("trailingPE")
                p_pb  = p_info.get("priceToBook")
                p_roe = p_info.get("returnOnEquity")
                p_dte = p_info.get("debtToEquity")

                pe_val  = float(p_pe) if p_pe and float(p_pe) > 0 else 999
                # FMP returns ROE as a ratio (0.18) when from key-metrics-ttm
                roe_raw = float(p_roe) if p_roe and pd.notna(p_roe) else 0
                roe_val = roe_raw * 100 if roe_raw < 5 else roe_raw   # normalise fraction→%
                dte_raw = float(p_dte) if p_dte and pd.notna(p_dte) else 999
                dte_val = (dte_raw / 100.0) if dte_raw > 15 else dte_raw  # yf percent vs ratio

                closes = p_hist['Close'].dropna()
                is_uptrend = (closes.iloc[-1] > closes.rolling(50).mean().iloc[-1]
                               if len(closes) > 50 else True)

                qualifies = (0 < pe_val < 30 and
                              roe_val > 15 and
                              dte_val < (2.0 if p_is_fin else 0.8) and
                              is_uptrend)

                if qualifies:
                    # Economically meaningful relative score: high ROE / reasonable PE
                    # (avoid the nonsensical ROE - PE arithmetic)
                    earnings_yield = (100.0 / pe_val) if pe_val > 0 else 0
                    score = (0.55 * min(roe_val, 40) / 40.0 * 100) + (0.45 * min(earnings_yield, 12) / 12.0 * 100)
                    candidate = {
                        "name": p_info.get("longName") or p_info.get("shortName") or peer,
                        "ticker": peer,
                        "price": p_current_price,
                        "pe":    round(pe_val, 1),
                        "pb":    round(float(p_pb), 1) if p_pb and pd.notna(p_pb) else "N/A",
                        "_score": score,
                        "source": "FMP" if p_fmp.get("currentPrice") else "yfinance",
                    }
                    if best_peer is None or score > best_peer.get("_score", -999):
                        best_peer = candidate

            except Exception as e:
                logger.warning("Peer scan failed for %s: %s", peer, e)
                continue

        metrics['best_alternative'] = best_peer

    return metrics

# ============================================================
# 8. UI PLOTLY CHARTS & NEW ANGEL ONE COMPONENTS
# ============================================================

