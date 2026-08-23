"""Composite scoring and predictive pipeline."""
from __future__ import annotations
import re
import logging
import numpy as np
import pandas as pd
from athenaeum.config import EQUITY_RISK_PREMIUM
from athenaeum.models.sector import is_financial_sector
from athenaeum.models.valuation import compute_multi_model_values, compute_wacc, compute_roic, economic_profit_spread
from athenaeum.models.technical import compute_technical_score, calculate_atr, calculate_vwap_support
from athenaeum.data.rfr import get_dynamic_risk_free_rate
from athenaeum.utils.helpers import _rfr_value, _rfr_source, compute_entry_stop_range

logger = logging.getLogger("athenaeum")
try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except Exception:
    HAS_ARIMA = False

def composite_verdict(fundamental_score, margin_of_safety, tech_score,
                      forced_intrinsic_adjustment=0, qualitative_bonus=0):
    """
    Single source of truth for the blended composite score and verdict tier.

    Takes the ALREADY-FINALIZED technical score (the multi-factor SMA/momentum/
    drawdown/volume/drift score computed by compute_technical_score, with its
    ARIMA confirmatory nudge already applied by the caller) rather than deriving
    its own separate drift-only score.

    This function previously computed its own internal tech_score from raw
    `drift` and applied its own ARIMA nudge (+/-5). run_predictive_pipeline
    called it, discarded that tech_score AND the composite/verdict it produced,
    and silently recomputed an inline duplicate using the real multi-factor
    tech_score with a DIFFERENT ARIMA nudge magnitude (+/-3) and slightly
    different verdict-tier logic. The two copies had drifted apart and nothing
    exercised this function's real output — a test calling composite_verdict()
    directly would have been testing dead logic. Consolidated here so there is
    exactly one implementation, and it is the one actually used.
    """
    W_FUNDAMENTAL, W_INTRINSIC, W_TECHNICAL = 0.42, 0.38, 0.20
    intrinsic_score = min(max(50 + margin_of_safety * 120, 0), 100)
    intrinsic_score = min(max(intrinsic_score + forced_intrinsic_adjustment, 0), 100)
    tech_score = min(max(tech_score or 0, 0), 100)
    # Qualitative bonus/penalty capped so it can nudge, never dominate
    q_bonus = max(min(qualitative_bonus or 0, 5), -5)

    if fundamental_score is None:
        # Insufficient fundamental pillars: drop the W_FUNDAMENTAL term entirely
        # (don't silently treat missing as neutral-50), apply an extra 0.85
        # conservatism multiplier, and unconditionally cap the verdict at
        # OBSERVE — a data-sparse company should never clear BUY/STRONG BUY on
        # intrinsic+technical alone, regardless of what the raw weighted math
        # would otherwise suggest.
        composite = min(max(W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score + q_bonus, 0) * 0.85, 100)
        verdict = "OBSERVE"
    else:
        composite = W_FUNDAMENTAL * fundamental_score + W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score
        composite = min(max(composite + q_bonus, 0), 100)
        if composite >= 75: verdict = "STRONG BUY"
        elif composite >= 60: verdict = "BUY"
        elif composite >= 40: verdict = "OBSERVE"
        else: verdict = "DON'T BUY"

    return round(composite, 1), verdict, round(intrinsic_score, 1), round(tech_score, 1)

VERDICT_RANK = {"DON'T BUY": 0, "OBSERVE": 1, "BUY": 2, "STRONG BUY": 3}


def apply_tiered_sanity_veto(verdict, target_price, current_price, notes, growth_pct=None):
    if target_price is None or not current_price:
        return verdict
        
    downside_pct = (current_price - target_price) / current_price
    upside_pct = (target_price - current_price) / current_price
    
    if downside_pct > 0.15:
        if verdict != "DON'T BUY":
            notes.append(f"Model indicates {round(downside_pct*100,1)}% downside. Treated as OBSERVE or DON'T BUY based on fundamental strength.")
        return "DON'T BUY" if downside_pct > 0.40 else "OBSERVE"
        
    elif downside_pct > 0:
        if VERDICT_RANK.get(verdict, 1) > VERDICT_RANK["OBSERVE"]:
            notes.append(f"Downgraded to OBSERVE: the modeled target price is {round(downside_pct*100,1)}% below the current price.")
            return "OBSERVE"
            
    # Fix 2: ceiling scales with verified growth_pct instead of a static 150%
    g = growth_pct if (growth_pct and growth_pct > 0) else 0
    max_upside = 2.50 if g >= 25 else (2.00 if g >= 15 else 1.50)
    if upside_pct > max_upside:
        notes.append(
            f"Upside of +{round(upside_pct*100,1)}% exceeds the growth-adjusted ceiling "
            f"of {round(max_upside*100,0):.0f}% (verified growth={round(g,1)}%). "
            f"Flagged as possible data anomaly — downgraded to OBSERVE for human review."
        )
        if VERDICT_RANK.get(verdict, 1) > VERDICT_RANK["OBSERVE"]:
            return "OBSERVE"   # OBSERVE not DON'T BUY: surfaces signal, doesn't bury it
            
    return verdict


def run_predictive_pipeline(info, hist, fcf_history, sector, industry, fundamental_score,
                              book_value_per_share, dividend_per_share, roe_pct,
                              pat_yoy_pct, analyst_growth_pct,
                              precomputed_jpb=None, precomputed_ddm=None,
                              resolved_pe=None, is_turnaround=False, latest_quarter_net_income=None,
                              shares_outstanding=None, qualitative_bonus=0, qualitative_notes=None,
                              sector_profile="standard", order_book_hits=None, growth_pct_from_news=None):
    current_price = info.get('currentPrice')
    if not current_price and hist is not None and not hist.empty:
        current_price = float(hist['Close'].iloc[-1])

    result = {
        "verdict": "OBSERVE", "target_price": None, "entry_range": "N/A", "stop_loss": None,
        "time_horizon": "N/A", "note": None, "model_used": "N/A",
        "composite_score": None, "fundamental_score": fundamental_score,
        "intrinsic_score": None, "technical_score": None, "margin_of_safety": None,
        "discount_rate": None, "growth_used": None, "is_turnaround": is_turnaround,
        "wacc_pct": None, "roic_pct": None, "economic_profit_pct": None,
    }
    if not current_price:
        return result

    notes = list(qualitative_notes or [])
    order_book_hits = order_book_hits or []
    beta = info.get('beta')
    if beta is None or pd.isna(beta) or beta <= 0:
        beta = 1.0
        
    rfr_pack = get_dynamic_risk_free_rate()
    current_rfr = _rfr_value(rfr_pack)
    rfr_source = _rfr_source(rfr_pack)
    ke_pct = min(max((current_rfr + beta * EQUITY_RISK_PREMIUM) * 100, 9), 20)
    notes.append(f"RFR source: {rfr_source} ({current_rfr * 100:.2f}%).")

    # WACC / ROIC — real figures from the company's own balance sheet and
    # income statement (market cap, debt, cash, EBIT, interest expense,
    # effective tax rate where available), using the SAME ke_pct computed
    # above rather than a redundant second CAPM pass. Previously there was no
    # WACC anywhere in the codebase; every discount-rate calculation used Ke
    # alone. This does not change the existing "Simplified FCF DCF" model's
    # discount rate — that model's FCF input is yfinance's 'Free Cash Flow'
    # (Operating Cash Flow - Capex), a levered, equity-side proxy, so Ke
    # remains the conceptually appropriate rate for it (see the note appended
    # below). WACC is exposed here as a real, correctly-computed, separately
    # displayed metric — the foundation for a genuine unlevered FCFF model
    # (EBIT(1-t)+D&A-Capex-ΔNWC discounted at WACC) if that's built later,
    # and immediately useful on its own via the ROIC-WACC economic-profit
    # spread below.
    wacc_info = compute_wacc(
        market_cap=info.get('marketCap'), total_debt=info.get('totalDebt'),
        total_cash=info.get('totalCash'), ke_pct=ke_pct,
        interest_expense=info.get('interest_exp_latest'),
        effective_tax_rate_pct=info.get('effective_tax_rate_pct'),
    )
    roic_pct = compute_roic(
        ebit=info.get('ebit_latest'), total_debt=info.get('totalDebt'),
        total_equity=info.get('totalEquity'), total_cash=info.get('totalCash'),
        effective_tax_rate_pct=info.get('effective_tax_rate_pct'),
    )
    economic_profit_pct = economic_profit_spread(roic_pct, wacc_info['wacc_pct'] if wacc_info else None)
    result["wacc_pct"] = wacc_info['wacc_pct'] if wacc_info else None
    result["roic_pct"] = roic_pct
    result["economic_profit_pct"] = economic_profit_pct
    if wacc_info and not wacc_info['kd_is_estimated']:
        notes.append(f"WACC {wacc_info['wacc_pct']:.1f}% (Kd measured from actual interest expense, "
                     f"tax rate {wacc_info['tax_rate_pct']:.1f}%).")
    elif wacc_info:
        notes.append(f"WACC {wacc_info['wacc_pct']:.1f}% (Kd estimated — no interest expense data; "
                     f"tax rate {wacc_info['tax_rate_pct']:.1f}% is the statutory default, not company-specific).")

    growth_pct = 8.0
    growth_source = "default (8%)"
    if analyst_growth_pct and analyst_growth_pct > 0:
        growth_pct = min(max(float(analyst_growth_pct), 5), 30)
        growth_source = f"analyst consensus ({float(analyst_growth_pct):.1f}%)"
    elif pat_yoy_pct and pat_yoy_pct > 0:
        growth_pct = min(max(pat_yoy_pct, 5), 22)
        growth_source = f"trailing PAT YoY ({pat_yoy_pct:.1f}%)"

    # Turnaround: modest lift only — do not force 20% growth automatically.
    if is_turnaround:
        prev = growth_pct
        growth_pct = min(max(growth_pct, 12.0), 22.0)
        if growth_pct > prev:
            notes.append(f"Turnaround flagged: growth assumption nudged from {prev:.0f}% to {growth_pct:.0f}% (scenario, not a hard override).")

    if order_book_hits:
        # Order wins ≠ earnings growth. Only a small additive nudge; never replace fundamentals.
        bump = 2.0
        if growth_pct_from_news:
            bump = min(float(growth_pct_from_news) * 0.25, 4.0)  # further discount soft hint
        new_growth = min(growth_pct + bump, 20)
        if new_growth > growth_pct:
            notes.append(
                f"Order-book/guidance keywords: growth nudged +{new_growth - growth_pct:.1f}pp "
                f"(to {new_growth:.1f}%). Headline order value is not mapped 1:1 to earnings."
            )
        growth_pct = new_growth

    # Near-term growth may exceed Ke; models fade toward terminal growth < Ke internally.
    # Soft cap only extreme outliers that break multi-year compounding stability.
    soft_ceiling = max(ke_pct + 8.0, 18.0)  # allow analyst-like near-term high growth
    if growth_pct > soft_ceiling:
        notes.append(
            f"Near-term growth soft-capped at {soft_ceiling:.1f}% (was {growth_pct:.1f}%); "
            f"DCF/forward models still fade to terminal g < Ke ({ke_pct:.1f}%)."
        )
        growth_pct = soft_ceiling
    elif growth_pct > ke_pct:
        notes.append(
            f"Near-term growth {growth_pct:.1f}% > Ke {ke_pct:.1f}% — allowed for explicit forecast; "
            "terminal growth remains below discount rate inside valuation models."
        )

    financial = is_financial_sector(sector, industry)
    forced_intrinsic_adjustment = 0

    shares = info.get('sharesOutstanding') or shares_outstanding
    trailing_eps = info.get('trailingEps')
    effective_eps = trailing_eps
    if is_turnaround and latest_quarter_net_income and latest_quarter_net_income > 0 and shares:
        # Annualizing one quarter is seasonal-sensitive — blend 50/50 with trailing if available
        annualized_q = (latest_quarter_net_income / shares) * 4
        if trailing_eps and trailing_eps > 0:
            effective_eps = 0.5 * float(trailing_eps) + 0.5 * annualized_q
            notes.append(
                f"Turnaround EPS: blended trailing ({float(trailing_eps):.2f}) with "
                f"annualized latest quarter ({annualized_q:.2f}) — seasonality risk remains."
            )
        else:
            effective_eps = annualized_q
            notes.append(
                f"Turnaround EPS: annualized latest quarter only ({annualized_q:.2f}) — "
                "high seasonality risk; treat as scenario input."
            )

    multi = compute_multi_model_values(
        current_price, ke_pct, growth_pct, financial, book_value_per_share, dividend_per_share,
        roe_pct, effective_eps, resolved_pe, fcf_history, shares, precomputed_jpb, precomputed_ddm,
        sector_profile, pat_yoy_pct,
    )
    # Prefer sector-weighted blend when ≥2 core models; else primary
    if multi["n_models"] >= 2 and multi.get("blended_value"):
        intrinsic_value = multi["blended_value"]
        result["model_used"] = (
            f"Weighted blend ({multi['n_models']} models; primary={multi['primary_name']}; "
            f"CV={multi.get('valuation_cv')})"
        )
    else:
        intrinsic_value = multi.get("primary_value")
        result["model_used"] = multi.get("primary_name") or "Defensive Haircut"
    if "Defensive" in str(result["model_used"]):
        forced_intrinsic_adjustment = -25
    result["valuation_models"] = multi.get("models", {})

    if not intrinsic_value or intrinsic_value <= 0:
        intrinsic_value = round(current_price * 0.75, 2) if current_price else None
        result["model_used"] = "Defensive Haircut (no reliable data)"
        forced_intrinsic_adjustment = -30

    base_value = intrinsic_value

    # True scenario revaluation: re-run models under Bear / Bull assumptions
    bear_g = max(growth_pct * 0.55, 3.0)
    bull_g = min(growth_pct * 1.35, growth_pct + 10.0)
    bear_ke = min(ke_pct + 1.5, 20.0)
    bull_ke = max(ke_pct - 1.0, 9.0)
    if current_price:
        multi_bear = compute_multi_model_values(
            current_price, bear_ke, bear_g, financial, book_value_per_share, dividend_per_share,
            roe_pct, effective_eps, resolved_pe, fcf_history, shares, None, None,
            sector_profile, pat_yoy_pct,
        )
        multi_bull = compute_multi_model_values(
            current_price, bull_ke, bull_g, financial, book_value_per_share, dividend_per_share,
            roe_pct, effective_eps, resolved_pe, fcf_history, shares, None, None,
            sector_profile, pat_yoy_pct,
        )
        bear_value = multi_bear.get("blended_value") or multi_bear.get("primary_value")
        bull_value = multi_bull.get("blended_value") or multi_bull.get("primary_value")
        # Do not force monotonic scenarios — flag inconsistency for audit
        if bear_value and base_value and bear_value > base_value:
            notes.append(
                f"Scenario inconsistency: Bear ({bear_value}) > Base ({base_value}). Review assumptions."
            )
        if bull_value and base_value and bull_value < base_value:
            notes.append(
                f"Scenario inconsistency: Bull ({bull_value}) < Base ({base_value}). Review assumptions."
            )
    else:
        bear_value = bull_value = None

    target_price = base_value
    margin_of_safety = (base_value - current_price) / current_price if current_price and base_value else 0

    atr = calculate_atr(hist)
    support = calculate_vwap_support(hist) or (current_price * 0.92)
    entry_low, entry_high, stop_loss = compute_entry_stop_range(support, current_price, atr)

    # Multi-factor technical score (SMA / momentum / drawdown / volume / capped drift)
    tech_score, momentum, horizon, drift = compute_technical_score(hist, current_price)

    # ARIMA is confirmatory only — small nudge, never dominates
    arima_dir = None
    try:
        closes_clean = hist['Close'].dropna() if hist is not None else pd.Series(dtype=float)
        if HAS_ARIMA and len(closes_clean) > 100:
            fitted = ARIMA(closes_clean.values, order=(5, 1, 0)).fit()
            forecast = fitted.forecast(steps=20)
            arima_dir = "UP" if forecast[-1] > forecast[0] else "DOWN"
            if arima_dir == "UP" and momentum != "DOWN":
                tech_score = min(100, tech_score + 3)
            elif arima_dir == "DOWN" and momentum != "UP":
                tech_score = max(0, tech_score - 3)
            elif arima_dir != momentum and momentum in ("UP", "DOWN"):
                momentum = "MIXED"
                horizon = "3-5 Years (Mixed Signals)"
    except Exception as e:
        logger.warning("ARIMA technical step failed: %s", e)

    # Single call, single source of truth — see composite_verdict's docstring
    # for why this used to be two independently-drifting implementations.
    composite, verdict, intrinsic_score, tech_score = composite_verdict(
        fundamental_score, margin_of_safety, tech_score,
        forced_intrinsic_adjustment=forced_intrinsic_adjustment, qualitative_bonus=qualitative_bonus,
    )
    if fundamental_score is None:
        notes.append("Insufficient fundamental data: investment verdict capped at OBSERVE.")

    verdict = apply_tiered_sanity_veto(verdict, target_price, current_price, notes, growth_pct=growth_pct)
    # Completeness / sector gates on assertive verdicts
    # (data_completeness is not in pipeline scope — use fundamental None + model flags)
    if verdict == "STRONG BUY" and fundamental_score is None:
        verdict = "OBSERVE"
        notes.append("STRONG BUY blocked: insufficient fundamental pillars.")
    if verdict in ("STRONG BUY", "BUY") and financial:
        notes.append(
            "Financial-sector model is screening-level only (GNPA/CRAR/CASA etc. unavailable). "
            "Do not treat as a full bank/NBFC research opinion."
        )
        if verdict == "STRONG BUY":
            verdict = "BUY"
            notes.append("STRONG BUY capped to BUY for financials without regulatory asset-quality inputs.")


    # Confidence: valuation dispersion (CV) + defensive fallback flag
    n_m = multi.get("n_models", 0)
    conf = multi.get("valuation_confidence") or "Low"
    if "Defensive Haircut" in str(result.get("model_used", "")):
        conf = "Low"

    result.update({
        "verdict": verdict, "target_price": target_price,
        "bear_value": bear_value, "base_value": base_value, "bull_value": bull_value,
        "entry_range": f"₹{entry_low:,.2f} - ₹{entry_high:,.2f}",
        "entry_low": entry_low, "entry_high": entry_high, "stop_loss": stop_loss,
        "time_horizon": horizon, "note": " ".join(notes) if notes else None,
        "composite_score": composite, "intrinsic_score": intrinsic_score, "technical_score": tech_score,
        "margin_of_safety": round(margin_of_safety * 100, 1),
        "discount_rate": round(ke_pct, 1), "growth_used": round(growth_pct, 1),
        "growth_source": growth_source, "confidence": conf, "rfr_source": rfr_source,
        "momentum": momentum,
        "valuation_models": multi.get("models", {}),
        "n_valuation_models": n_m,
        "audit": {
            "growth_source": growth_source,
            "growth_used": round(growth_pct, 1),
            "ke": round(ke_pct, 1),
            "wacc": wacc_info,
            "roic_pct": roic_pct,
            "economic_profit_pct": economic_profit_pct,
            "rfr_source": rfr_source,
            "valuation_confidence": conf,
            "valuation_cv": multi.get("valuation_cv"),
            "near_term_growth": multi.get("near_term_growth_used"),
            "terminal_growth": multi.get("terminal_growth_used"),
            "model_used": result.get("model_used"),
            "fundamental_score": fundamental_score,
            "technical_score": tech_score,
            "intrinsic_score": intrinsic_score,
            "notes": notes,
        },
    })
    return result

# ============================================================
