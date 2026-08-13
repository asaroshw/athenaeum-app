"""Composite scoring and predictive pipeline."""
from __future__ import annotations
import re
import logging
import numpy as np
import pandas as pd
from athenaeum.config import EQUITY_RISK_PREMIUM
from athenaeum.models.sector import is_financial_sector
from athenaeum.models.valuation import compute_multi_model_values
from athenaeum.models.technical import compute_technical_score, calculate_atr, calculate_vwap_support
from athenaeum.data.rfr import get_dynamic_risk_free_rate
from athenaeum.utils.helpers import _rfr_value, _rfr_source

logger = logging.getLogger("athenaeum")
try:
    from statsmodels.tsa.arima.model import ARIMA
    HAS_ARIMA = True
except Exception:
    HAS_ARIMA = False

def composite_verdict(fundamental_score, margin_of_safety, drift, arima_direction=None,
                      forced_intrinsic_adjustment=0, qualitative_bonus=0):
    W_FUNDAMENTAL, W_INTRINSIC, W_TECHNICAL = 0.42, 0.38, 0.20
    intrinsic_score = min(max(50 + margin_of_safety * 120, 0), 100)
    intrinsic_score = min(max(intrinsic_score + forced_intrinsic_adjustment, 0), 100)
    # Cap drift contribution so extreme trends cannot dominate
    capped_drift = max(min(drift or 0, 0.40), -0.40)
    tech_score = min(max(50 + capped_drift * 80, 0), 100)
    # ARIMA is a weak confirmatory signal only (±5, not ±10)
    if arima_direction == "UP":
        tech_score = min(100, tech_score + 5)
    elif arima_direction == "DOWN":
        tech_score = max(0, tech_score - 5)
    # Qualitative bonus/penalty also capped
    q_bonus = max(min(qualitative_bonus or 0, 5), -5)
    # Missing fundamentals should not silently become zero in this helper
    fund = 50.0 if fundamental_score is None else fundamental_score  # neutral placeholder; pipeline overrides
    composite = W_FUNDAMENTAL * fund + W_INTRINSIC * intrinsic_score + W_TECHNICAL * tech_score
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
    entry_low = round(max(support, current_price * 0.85), 2)
    entry_high = round(support + (0.5 * atr if atr else current_price * 0.02), 2)
    if entry_low > current_price:
        entry_low, entry_high = round(current_price * 0.95, 2), round(current_price, 2)
    raw_stop_loss = entry_low - (1.5 * atr if atr else entry_low * 0.05)
    stop_loss = round(max(entry_low * 0.80, raw_stop_loss), 2)

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

    composite, verdict, intrinsic_score, _ = composite_verdict(
        fundamental_score, margin_of_safety, drift, arima_direction=arima_dir or momentum,
        forced_intrinsic_adjustment=forced_intrinsic_adjustment, qualitative_bonus=qualitative_bonus,
    )
    # Prefer the multi-factor tech score over the drift-only one from composite_verdict
    tech_score = round(tech_score, 1)
    # Recompute composite with multi-factor tech
    W_F, W_I, W_T = 0.42, 0.38, 0.20
    q_bonus = max(min(qualitative_bonus or 0, 5), -5)
    if fundamental_score is None:
        # Insufficient fundamental pillars — cap at OBSERVE regardless of intrinsic/technical scores.
        notes.append("Insufficient fundamental data: investment verdict capped at OBSERVE.")
        composite = round(min(max(
            W_I * intrinsic_score + W_T * tech_score + q_bonus, 0) * 0.85, 100), 1)
        verdict = "OBSERVE"  # always OBSERVE when fundamentals are missing
    else:
        composite = round(min(max(
            W_F * fundamental_score + W_I * intrinsic_score + W_T * tech_score + q_bonus, 0), 100), 1)
        if composite >= 75:
            verdict = "STRONG BUY"
        elif composite >= 60:
            verdict = "BUY"
        elif composite >= 40:
            verdict = "OBSERVE"
        else:
            verdict = "DON'T BUY"

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
        "entry_range": f"₹{entry_low:,.2f} - ₹{entry_high:,.2f}", "stop_loss": stop_loss,
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
