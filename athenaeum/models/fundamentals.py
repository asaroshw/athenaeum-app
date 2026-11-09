"""Checklist and continuous fundamental scoring."""
from __future__ import annotations
from athenaeum.utils.helpers import to_float, _clamp01, _piecewise_score

def valuation_checks(m):
    """Each check appends a (label, True/False/None, detail) tuple. None
    means "structurally applicable to this company but the underlying data
    isn't available" — distinct from True/False, which both mean the
    metric WAS available and a real determination was made (even when that
    determination is a confirmed negative, like a negative P/E). This
    distinction used to not exist: a check whose data was missing was
    simply never appended at all, which silently made
    score_from_checks/compute_fundamental_score's completeness tracking
    always read 100% (see score_from_checks's docstring) regardless of how
    much data was actually missing for a given company.
    """
    pe = to_float(m.get('pe_ratio'))
    peg = to_float(m.get('peg_ratio'))
    pat_yoy = to_float(m.get('pat_yoy'))
    pb = to_float(m.get('pb_ratio'))
    ev_ebitda = to_float(m.get('ev_ebitda'))
    is_fin = m.get('is_financial_sector', False)
    checks = []

    if pe is not None:
        if pe < 0:
            checks.append(("Profitable on a P/E basis", False,
                            f"P/E is negative ({pe:.2f}x) — the company is currently loss-making."))
        else:
            threshold = 45 if (pat_yoy is not None and pat_yoy > 30) else 25
            checks.append((f"Reasonable P/E (<{threshold}x{' — growth-adjusted' if threshold==45 else ''})",
                            pe < threshold, f"Trailing P/E of {pe:.2f}x"))
    else:
        checks.append(("Reasonable P/E", None, "P/E ratio not available from this data source."))

    if peg is not None:
        if peg < 0:
            checks.append(("Positive PEG", False,
                            f"PEG is negative ({peg:.2f}) — implies shrinking earnings or a loss-making company."))
        elif pe is not None and pe > 0 and pat_yoy is not None and pat_yoy > 0:
            checks.append(("Attractive PEG (<1.5)", peg < 1.5, f"PEG ratio of {peg:.2f}"))
        else:
            checks.append(("Attractive PEG", None,
                            f"PEG of {peg:.2f} available, but P/E or earnings growth context needed "
                            "to interpret it isn't — not counted either way."))
    else:
        checks.append(("Attractive PEG", None, "PEG ratio not available from this data source."))

    if pb is not None:
        threshold = 3.0 if is_fin else 5.0
        checks.append((f"Reasonable P/B (<{threshold:g}x)", 0 < pb < threshold, f"Price-to-Book of {pb:.2f}x"))
    else:
        checks.append(("Reasonable P/B", None, "P/B ratio not available from this data source."))

    # JPB-vs-actual is a financial-sector-only supplementary check — only
    # "possible" at all for financial companies, not a universal check.
    if is_fin:
        if pb is not None and m.get('justified_pb'):
            jpb = m['justified_pb']
            checks.append(("P/B vs Excess-ROE Justified P/B", pb < jpb,
                            f"Actual P/B {pb:.2f}x vs a model-justified P/B of {jpb:.2f}x"))
        else:
            checks.append(("P/B vs Excess-ROE Justified P/B", None,
                            "Justified P/B could not be computed (needs ROE, Ke, growth, and book value per share)."))

    # EV/EBITDA is only meaningful for non-financials (a bank's "debt" is
    # largely deposits, not capital-structure debt) — sector-excluded for
    # financials, not "possible but missing," so it's simply never added
    # to the checklist at all for is_fin=True companies.
    if not is_fin:
        if ev_ebitda is not None:
            if ev_ebitda < 0:
                checks.append(("Positive EV/EBITDA", False,
                                f"EV/EBITDA is negative ({ev_ebitda:.2f}x) — implies operating losses."))
            else:
                checks.append(("Reasonable EV/EBITDA (<15x)", ev_ebitda < 15, f"EV/EBITDA of {ev_ebitda:.2f}x"))
        else:
            checks.append(("Reasonable EV/EBITDA", None, "EV/EBITDA not available from this data source."))

    return checks


def past_performance_checks(m):
    yoy, qoq = to_float(m.get('pat_yoy')), to_float(m.get('pat_qoq'))
    roe, margin = to_float(m.get('roe')), to_float(m.get('net_margin'))
    opm = to_float(m.get('operating_margin'))
    rev_cagr = to_float(m.get('revenue_cagr'))
    sector_profile = m.get('sector_profile', 'standard')
    checks = []

    if yoy is not None:
        checks.append(("Positive Earnings Growth (YoY)", yoy > 0, f"PAT YoY growth of {yoy:.2f}%"))
    else:
        checks.append(("Positive Earnings Growth (YoY)", None, "YoY earnings growth not available."))

    if yoy is not None and qoq is not None:
        # Fix 4: compare recent YoY to prior YoY (same timescale)
        prior_yoy = to_float(m.get("pat_yoy_prior"))
        if prior_yoy is not None:
            checks.append(("Accelerating YoY Earnings Growth", yoy > prior_yoy,
                            f"Recent YoY {yoy:.1f}% vs prior year YoY {prior_yoy:.1f}%"))
        else:
            checks.append(("Positive YoY Earnings Growth", yoy > 0,
                            f"PAT YoY growth of {m.get('pat_yoy')} (prior year unavailable for acceleration check)"))
    elif yoy is not None or qoq is not None:
        # One of the two exists but not both — genuinely missing the other,
        # not a data type this metric doesn't apply to.
        checks.append(("Accelerating Earnings Growth", None,
                        "Both YoY and QoQ earnings figures are needed to assess acceleration; only one is available."))

    if roe is not None:
        checks.append(("Strong Return on Equity (>15%)", roe > 15, f"ROE of {roe:.2f}%"))
    else:
        checks.append(("Strong Return on Equity", None, "ROE not available from this data source."))

    if margin is not None:
        if sector_profile in ["cyclical", "materials", "capex_intensive"] and opm is not None and opm > 15 and rev_cagr is not None and rev_cagr > 8:
            checks.append(("Healthy Net Margin (cyclical-adjusted, >6%)", margin > 6,
                            f"Net margin of {margin:.2f}% — bar relaxed given strong OPM/CAGR."))
        else:
            checks.append(("Healthy Net Margin (>10%)", margin > 10, f"Net margin of {margin:.2f}%"))
    else:
        checks.append(("Healthy Net Margin", None, "Net margin not available from this data source."))

    return checks


def financial_health_checks(m):
    de = to_float(m.get('debt_to_equity'))
    ic = to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    checks = []

    if de is not None:
        if de < 0:
            checks.append(("Positive Shareholder Equity", False,
                            f"Debt-to-equity is negative ({de:.2f}) — implies negative shareholders' equity."))
        else:
            threshold, label = (10.0, "Leverage in line with a lending-book business model (D/E < 10x)") if is_fin \
                else (1.0, "Low Leverage (D/E < 1.0)")
            checks.append((label, de < threshold, f"Debt-to-equity of {de:.2f}"))
    else:
        checks.append(("Leverage / Debt-to-Equity", None, "Debt-to-equity not available from this data source."))

    if ic is not None:
        checks.append(("Comfortable Interest Coverage (>3x)", ic > 3, f"EBIT covers interest expense {ic:.2f}x"))
    else:
        checks.append(("Comfortable Interest Coverage", None, "Interest coverage not available from this data source."))

    # NIM is a financial-sector-only supplementary check — only "possible"
    # at all for financial companies.
    if is_fin:
        nim = m.get('nim_proxy')
        if nim is not None:
            checks.append(("Positive Net Interest Margin (approx.)", nim > 0,
                            f"Approximate NIM of {nim:.2f}%"))
        else:
            checks.append(("Positive Net Interest Margin (approx.)", None,
                            "NIM could not be approximated (needs interest income, interest expense, and total assets)."))

    return checks


def dividend_checks(m):
    """Notable-dividend check. An explicit "doesn't pay dividends" is
    confirmed, real information about the company (correctly a fail, not
    N/A). A missing/unparseable dividend_yield field, however, previously
    ALSO evaluated to False via `dy is not None and dy > 1.5` (dy=None
    makes the `and` short-circuit to False) — indistinguishable in the UI
    from a company that genuinely pays no dividend. For a stock a data
    vendor simply has thin coverage on (illiquid micro-caps in particular),
    that silently penalized the checklist for a data gap, not a real
    negative about the company. Now returns None (not counted) when the
    yield truly isn't available, and only returns a confirmed False for an
    explicit "doesn't pay" signal or a real yield at or below the bar.

    `dividend_yield` itself used to be stored as a pre-formatted string
    ("2.34%" or the literal "N/A") rather than a raw float — one instance
    of a wider pattern (metrics stored as formatted display strings rather
    than typed values) that made functions like this one need string
    matching and to_float() just to read a number back out of what should
    have just been a number. It's now a raw float in percentage points (or
    None), read directly below; the str()/to_float() handling remains only
    to stay compatible with the literal "doesn't pay" sentinel string,
    which is confirmed information rather than a missing value.
    """
    dy_raw = m.get('dividend_yield')
    if isinstance(dy_raw, str) and "doesn't pay" in dy_raw.lower():
        return [("Notable Dividend (>1.5%)", False, "Stock doesn't pay dividends")]
    dy = to_float(dy_raw)
    if dy is None:
        return [("Notable Dividend (>1.5%)", None, "Dividend yield not available from this data source.")]
    return [("Notable Dividend (>1.5%)", dy > 1.5, f"Dividend yield: {dy:.2f}%")]


def future_growth_checks(m):
    """Forward-looking checklist — previously the "Future Growth" AI
    narrative section had no accompanying checklist card the way Valuation/
    Past Performance/Financial Health/Dividend all did. Built from data
    already available elsewhere in the app rather than anything new:
    analyst consensus growth (falling back to trailing PAT growth when no
    analyst coverage exists — same hierarchy `growth_pct` itself already
    uses), revenue CAGR, the growth assumption actually driving the DCF
    target price, and the ROIC-WACC economic-profit spread.

    `growth_used` and `economic_profit_pct` are pipeline outputs, not raw
    fetched data — callers should set `m['growth_used']` and
    `m['economic_profit_pct']` from the predictive-pipeline result before
    calling this, the same way `m['justified_pb']` is already threaded
    through after the pipeline runs.
    """
    analyst_g = to_float(m.get('analyst_growth_pct'))
    pat_yoy = to_float(m.get('pat_yoy'))
    rev_cagr = to_float(m.get('revenue_cagr'))
    growth_used = to_float(m.get('growth_used'))
    econ_profit = to_float(m.get('economic_profit_pct'))
    checks = []

    growth_signal = analyst_g if analyst_g is not None else pat_yoy
    growth_source = "analyst consensus" if analyst_g is not None else "trailing PAT growth"
    if growth_signal is not None:
        checks.append(("Positive Earnings Growth Outlook (>10%)", growth_signal > 10,
                        f"Growth outlook ({growth_source}): {growth_signal:.1f}%"))
    else:
        checks.append(("Positive Earnings Growth Outlook", None,
                        "No analyst coverage or trailing earnings growth figure available."))

    if rev_cagr is not None:
        checks.append(("Healthy Revenue Growth Trajectory (>8% CAGR)", rev_cagr > 8,
                        f"Revenue CAGR: {rev_cagr:.1f}%"))
    else:
        checks.append(("Healthy Revenue Growth Trajectory", None, "Revenue CAGR not available."))

    if growth_used is not None:
        checks.append(("Robust Growth Assumption Backing the Valuation (>15%)", growth_used > 15,
                        f"Growth rate used in the valuation model: {growth_used:.1f}%"))
    else:
        checks.append(("Robust Growth Assumption Backing the Valuation", None,
                        "Valuation model growth assumption not available."))

    if econ_profit is not None:
        checks.append(("Creating Economic Value (ROIC above Cost of Capital)", econ_profit > 0,
                        f"ROIC exceeds WACC by {econ_profit:+.1f} percentage points"
                        if econ_profit >= 0 else f"ROIC trails WACC by {abs(econ_profit):.1f} percentage points"))
    else:
        checks.append(("Creating Economic Value (ROIC above Cost of Capital)", None,
                        "ROIC/WACC could not be computed (needs EBIT, tax rate, debt, equity, and cash)."))

    return checks


def continuous_valuation_score(m):
    """Continuous 0–100 valuation score (sector-aware thresholds)."""
    pe = to_float(m.get('pe_ratio'))
    peg = to_float(m.get('peg_ratio'))
    pb = to_float(m.get('pb_ratio'))
    ev_ebitda = to_float(m.get('ev_ebitda'))
    is_fin = m.get('is_financial_sector', False)
    pat_yoy = to_float(m.get('pat_yoy'))
    parts = []
    if pe is not None and pe > 0:
        good_pe = 35 if (pat_yoy and pat_yoy > 25) else 22
        parts.append(_piecewise_score(pe, good=good_pe, excellent=good_pe * 0.55, higher_is_better=False))
    if peg is not None and peg > 0:
        parts.append(_piecewise_score(peg, good=1.8, excellent=0.9, higher_is_better=False))
    if pb is not None and pb > 0:
        good_pb = 2.5 if is_fin else 4.0
        parts.append(_piecewise_score(pb, good=good_pb, excellent=good_pb * 0.45, higher_is_better=False))
    if not is_fin and ev_ebitda is not None and ev_ebitda > 0:
        parts.append(_piecewise_score(ev_ebitda, good=14, excellent=7, higher_is_better=False))
    if is_fin and pb is not None and m.get('justified_pb'):
        jpb = to_float(m.get('justified_pb'))
        if jpb and jpb > 0:
            ratio = pb / jpb
            parts.append(_piecewise_score(ratio, good=1.15, excellent=0.7, higher_is_better=False))
    valid = [p for p in parts if p is not None]
    return round(sum(valid) / len(valid)) if valid else None


def continuous_past_score(m):
    yoy = to_float(m.get('pat_yoy'))
    roe = to_float(m.get('roe'))
    margin = to_float(m.get('net_margin'))
    rev_cagr = to_float(m.get('revenue_cagr'))
    sector_profile = m.get('sector_profile', 'standard')
    parts = []
    if yoy is not None:
        parts.append(_piecewise_score(yoy, good=12, excellent=30, higher_is_better=True))
    if roe is not None:
        parts.append(_piecewise_score(roe, good=15, excellent=25, higher_is_better=True))
    if margin is not None:
        good_m = 6 if sector_profile in ["cyclical", "materials", "capex_intensive"] else 10
        parts.append(_piecewise_score(margin, good=good_m, excellent=good_m * 1.8, higher_is_better=True))
    if rev_cagr is not None:
        parts.append(_piecewise_score(rev_cagr, good=8, excellent=18, higher_is_better=True))
    valid = [p for p in parts if p is not None]
    return round(sum(valid) / len(valid)) if valid else None


def continuous_health_score(m):
    de = to_float(m.get('debt_to_equity'))
    ic = to_float(m.get('interest_coverage'))
    is_fin = m.get('is_financial_sector', False)
    parts = []
    if de is not None:
        if de < 0:
            parts.append(10.0)
        else:
            good_de = 8.0 if is_fin else 0.8
            parts.append(_piecewise_score(de, good=good_de, excellent=good_de * 0.35, higher_is_better=False))
    if ic is not None and ic > 0:
        parts.append(_piecewise_score(ic, good=4, excellent=12, higher_is_better=True))
    if is_fin and m.get('nim_proxy') is not None:
        nim = to_float(m.get('nim_proxy'))
        if nim is not None:
            parts.append(_piecewise_score(nim, good=2.0, excellent=4.0, higher_is_better=True))
    valid = [p for p in parts if p is not None]
    return round(sum(valid) / len(valid)) if valid else None


def score_from_checks(checks):
    """Return (score 0-100 or None, n_available, n_possible).
    Binary checklist retained for UI transparency; continuous scores drive the model.
    Missing checks still count against completeness.
    """
    if not checks:
        return None, 0, 0
    vals = [c[1] for c in checks if c[1] is not None]
    n_available = len(vals)
    n_possible = len(checks)
    if n_available == 0:
        return None, 0, n_possible
    raw = 100 * sum(1 for v in vals if v) / n_available
    completeness = n_available / max(n_possible, 1)
    adjusted = raw * (0.70 + 0.30 * completeness)
    return round(adjusted), n_available, n_possible


def compute_fundamental_score(val_score, past_score, health_score, is_financial,
                              val_avail=0, past_avail=0, health_avail=0,
                              val_poss=0, past_poss=0, health_poss=0):
    weights = {"val": 0.45, "past": 0.35, "health": 0.20} if is_financial else {"val": 0.35, "past": 0.35, "health": 0.30}
    scores = {"val": val_score, "past": past_score, "health": health_score}
    available = {k: v for k, v in scores.items() if v is not None}
    if not available:
        return None, 0.0  # unknown fundamentals — not a zero score
    total_w = sum(weights[k] for k in available)
    fund = round(sum(weights[k] * v for k, v in available.items()) / total_w, 1)
    # Overall data completeness across the three pillars
    total_avail = val_avail + past_avail + health_avail
    total_poss = val_poss + past_poss + health_poss
    completeness = round(100 * total_avail / max(total_poss, 1), 1) if total_poss else 0.0
    return fund, completeness

# ============================================================
# 6. QUANTITATIVE COMPOSITE ENGINE  (constants live in config.py)
# ============================================================

